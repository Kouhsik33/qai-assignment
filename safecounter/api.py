"""HTTP API. Every business route is under /shops/{shop_id}: one shop per request.

Auth: `Authorization: Bearer <token>` for a seeded staff member (demo tokens, no
sign-up). The caller must hold a membership in the shop named in the path; their
role comes from that membership, never from the request body.
"""

import hashlib
from typing import Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import audit, config, rules as rules_mod, scribe
from .db import plain_tx, shop_tx
from .gate import Actor, GateError, approve_proposal, load_record, reject_proposal, submit_change
from .rules import ENTITY_TABLE

app = FastAPI(title="Safe Counter")


@app.exception_handler(GateError)
def gate_error(_: Request, exc: GateError):
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.detail, **exc.extra})


def current_actor(shop_id: str, authorization: str | None = Header(default=None)) -> Actor:
    if not authorization or not authorization.startswith("Bearer "):
        raise GateError(401, "unauthenticated", "send Authorization: Bearer <token>")
    digest = hashlib.sha256(authorization.removeprefix("Bearer ").strip().encode()).hexdigest()
    with plain_tx() as cur:
        cur.execute("SELECT id FROM staff WHERE token_sha256 = %s", (digest,))
        staff = cur.fetchone()
        if staff is None:
            raise GateError(401, "unauthenticated", "unknown token")
        cur.execute("SELECT role FROM memberships WHERE staff_id = %s AND shop_id = %s", (staff["id"], shop_id))
        m = cur.fetchone()
    if m is None:
        raise GateError(403, "not_a_member", f"{staff['id']} has no role in {shop_id}")
    return Actor(staff_id=staff["id"], shop_id=shop_id, role=m["role"])


class NoteIn(BaseModel):
    id: str
    text: str
    written_at: str
    scope: list[str]
    source: str = "typed"


class ChangeIn(BaseModel):
    record_id: str
    field: str
    value: Any
    reason: str


class DecisionIn(BaseModel):
    reason: str
    final_value: Any = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/shops/{shop_id}/notes")
def post_note(note: NoteIn, actor: Actor = Depends(current_actor),
              x_ai_mode: str | None = Header(default=None)):
    mode = x_ai_mode if config.ALLOW_AI_MODE_OVERRIDE else None
    return scribe.run_note(actor, rules_mod.load(), note.model_dump(), ai_mode=mode)


@app.post("/shops/{shop_id}/changes")
def post_change(change: ChangeIn, actor: Actor = Depends(current_actor)):
    with shop_tx(actor.shop_id) as cur:
        return submit_change(cur, actor, rules_mod.load(), record_id=change.record_id, field=change.field,
                             value=change.value, reason=change.reason)


@app.get("/shops/{shop_id}/proposals")
def list_proposals(status: str | None = None, task_id: str | None = None, actor: Actor = Depends(current_actor)):
    with shop_tx(actor.shop_id) as cur:
        cur.execute("""SELECT id, origin, task_id, requested_by, record_id, field, value, final_value, unit,
                              confidence, evidence, ai_sensitivity, sensitive, approver_roles, base_value, status,
                              block_reasons, flags, decided_by, decided_at, decision_reason
                       FROM proposals
                       WHERE (%(s)s::text IS NULL OR status = %(s)s) AND (%(t)s::text IS NULL OR task_id = %(t)s)
                       ORDER BY id""", {"s": status, "t": task_id})
        return cur.fetchall()


@app.post("/shops/{shop_id}/proposals/{proposal_id}/approve")
def approve(proposal_id: int, decision: DecisionIn, actor: Actor = Depends(current_actor)):
    with shop_tx(actor.shop_id) as cur:
        return approve_proposal(cur, actor, rules_mod.load(), proposal_id, reason=decision.reason,
                                final_value=decision.final_value)


@app.post("/shops/{shop_id}/proposals/{proposal_id}/reject")
def reject(proposal_id: int, decision: DecisionIn, actor: Actor = Depends(current_actor)):
    with shop_tx(actor.shop_id) as cur:
        return reject_proposal(cur, actor, rules_mod.load(), proposal_id, reason=decision.reason)


@app.get("/shops/{shop_id}/records/{record_id}")
def get_record(record_id: str, actor: Actor = Depends(current_actor)):
    with shop_tx(actor.shop_id) as cur:
        for entity, table in ENTITY_TABLE.items():
            row = load_record(cur, table, record_id, actor.shop_id, lock=False)
            if row:
                if entity == "customer" and actor.role != "area_admin":
                    row.pop("id_check_ref", None)   # ID-check references are area-admin business
                return {"type": entity, **row}
    raise GateError(404, "record_not_found", f"no record {record_id} in {actor.shop_id}")


@app.get("/shops/{shop_id}/audit")
def list_audit(actor: Actor = Depends(current_actor)):
    with shop_tx(actor.shop_id) as cur:
        cur.execute("SELECT * FROM audit_log WHERE shop_id = %s ORDER BY seq", (actor.shop_id,))
        return cur.fetchall()


@app.get("/shops/{shop_id}/audit/verify")
def verify_audit(actor: Actor = Depends(current_actor)):
    with shop_tx(actor.shop_id) as cur:
        return audit.verify(cur, actor.shop_id)
