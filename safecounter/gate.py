"""THE GATE. The only code that writes customers, items or rentals.

`submit_change` is the one function every change goes through: a person's direct
edit, and an approved proposal (AI or human). Inside the caller's single
transaction it:

  1. looks up the field's rule (unknown field -> refused),
  2. locks the target row in the actor's shop (other shops are invisible: RLS),
  3. validates the value and derives sensitivity from the rules + value (R6),
  4. checks the actor's role for that field in that shop,
  5. decides: apply now (R1/R2), wait for approval (R2), or refuse,
  6. writes the change AND its audit row in the same transaction (R7).

`approve_proposal` / `reject_proposal` lock the proposal row first, so two
concurrent decisions queue and the second sees who decided first.
"""

from dataclasses import dataclass

from psycopg import sql

from . import audit, outbox
from .rules import Rules
from .validate import Invalid, check_value


@dataclass(frozen=True)
class Actor:
    staff_id: str
    shop_id: str
    role: str


class GateError(Exception):
    def __init__(self, status: int, code: str, detail: str, **extra):
        super().__init__(f"{code}: {detail}")
        self.status, self.code, self.detail, self.extra = status, code, detail, extra


def shop_time_zone(cur, shop_id: str) -> str:
    cur.execute("SELECT time_zone FROM shops WHERE id = %s", (shop_id,))
    row = cur.fetchone()
    if row is None:
        raise GateError(404, "unknown_shop", f"no shop {shop_id}")
    return row["time_zone"]


def load_record(cur, table: str, record_id: str, shop_id: str, *, lock: bool) -> dict | None:
    query = sql.SQL("SELECT * FROM {} WHERE id = %s AND shop_id = %s" + (" FOR UPDATE" if lock else "")).format(
        sql.Identifier(table))
    cur.execute(query, (record_id, shop_id))
    return cur.fetchone()


def insert_proposal(cur, **cols) -> int:
    names = list(cols)
    cur.execute(
        sql.SQL("INSERT INTO proposals ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(", ").join(map(sql.Identifier, names)),
            sql.SQL(", ").join(sql.Placeholder() * len(names))),
        [cols[n] for n in names],
    )
    return cur.fetchone()["id"]


def _jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def submit_change(cur, actor: Actor, rules: Rules, *, record_id: str, field: str, value, reason: str,
                  approving: dict | None = None) -> dict:
    """The gate. `approving` is the locked, pending proposal row when this is an approval."""
    rule = rules.get(field)
    if rule is None:
        raise GateError(422, "unknown_field", f"{field!r} is not a changeable field")
    if not reason or not str(reason).strip():
        raise GateError(422, "reason_required", "every change needs a reason")

    tz = shop_time_zone(cur, actor.shop_id)
    row = load_record(cur, rule.table, record_id, actor.shop_id, lock=True)
    if row is None:
        # Same answer whether it does not exist or belongs to another shop: no leaking.
        raise GateError(404, "record_not_found", f"no {rule.entity} {record_id} in {actor.shop_id}")

    try:
        new_value = check_value(rule, value, record=row, time_zone=tz)
    except Invalid as exc:
        raise GateError(422, exc.code, exc.detail)
    eff = rules.effective(rule, new_value)
    old_value = row[rule.column]

    if approving is None:
        # --- A person's direct edit (R1, R2) ---
        if actor.role not in eff.may_change:
            raise GateError(403, "not_permitted",
                            f"{actor.role} may not change {field}" + (f" to {new_value!r}" if eff.meaning else ""),
                            allowed_roles=sorted(eff.may_change))
        if old_value == new_value:
            raise GateError(422, "no_change", f"{field} is already {new_value!r}")
        if eff.sensitive and actor.role not in eff.approvers:
            pid = insert_proposal(
                cur, shop_id=actor.shop_id, origin="human", requested_by=actor.staff_id,
                record_id=record_id, field=field, value=_jsonb(new_value), unit=rule.unit,
                sensitive=True, approver_roles=sorted(eff.approvers), base_value=_jsonb(old_value),
                status="pending", ai_reason=reason)
            return {"outcome": "pending_approval", "proposal_id": pid, "field": field, "value": new_value,
                    "approver_roles": sorted(eff.approvers),
                    "why": f"{field} is sensitive and {actor.role} is not an approver for it (R2)"}
        origin, proposal_id = "direct_edit", None
    else:
        # --- Approving a pending proposal (R3, R4) ---
        if approving.get("requested_by") == actor.staff_id:
            raise GateError(403, "own_change", "nobody approves their own pending change (R4)")
        if approving["origin"] == "ai" and not rule.ai_may_propose:
            raise GateError(403, "not_proposable", f"the AI may never propose {field}")
        if actor.role not in eff.approvers:
            label = "sensitive" if eff.sensitive else "normal"
            raise GateError(403, "not_an_approver",
                            f"{field} = {new_value!r} is {label}" + (f" ({eff.meaning})" if eff.meaning else "")
                            + f"; approvers: {', '.join(sorted(eff.approvers))}; you are {actor.role}",
                            approver_roles=sorted(eff.approvers))
        if old_value != approving["base_value"]:
            raise GateError(409, "stale_proposal",
                            f"{record_id} {field} changed since this was proposed "
                            f"(was {approving['base_value']!r}, now {old_value!r}); reject it and propose again")
        origin, proposal_id = approving["origin"], approving["id"]

    # --- Apply: the change and its audit row, same transaction (R7) ---
    cur.execute(
        sql.SQL("UPDATE {} SET {} = %s WHERE id = %s AND shop_id = %s").format(
            sql.Identifier(rule.table), sql.Identifier(rule.column)),
        (new_value, record_id, actor.shop_id))
    if cur.rowcount != 1:
        raise GateError(500, "write_failed", f"expected to update 1 row, updated {cur.rowcount}")

    detail = {"reason": str(reason).strip(), "origin": origin, "sensitive": eff.sensitive}
    if approving is not None:
        detail["proposed_value"] = approving["value"]
        detail["requested_by"] = approving.get("requested_by")
        detail["task_id"] = approving.get("task_id")
    entry = audit.append(
        cur, shop_id=actor.shop_id, time_zone=tz, actor_id=actor.staff_id, action="change_applied",
        entity=rule.entity, record_id=record_id, field=field, old_value=old_value, new_value=new_value,
        proposal_id=proposal_id, detail=detail, rules_sha256=rules.sha256)
    outbox.enqueue(cur, shop_id=actor.shop_id, entry=entry)     # same transaction: no lost events

    if approving is not None:
        cur.execute(
            """UPDATE proposals SET status = 'approved', final_value = %s, decided_by = %s,
                      decided_at = now(), decision_reason = %s
               WHERE id = %s AND status = 'pending'""",
            (_jsonb(new_value), actor.staff_id, str(reason).strip(), proposal_id))
        if cur.rowcount != 1:
            raise GateError(409, "already_decided", f"proposal {proposal_id} is no longer pending")

    return {"outcome": "applied", "record_id": record_id, "field": field, "old_value": old_value,
            "new_value": new_value, "sensitive": eff.sensitive, "audit_seq": entry["seq"],
            "proposal_id": proposal_id}


def _lock_pending(cur, actor: Actor, proposal_id: int) -> dict:
    cur.execute("SELECT * FROM proposals WHERE id = %s AND shop_id = %s FOR UPDATE", (proposal_id, actor.shop_id))
    p = cur.fetchone()
    if p is None:
        raise GateError(404, "proposal_not_found", f"no proposal {proposal_id} in {actor.shop_id}")
    if p["status"] in ("approved", "rejected"):
        raise GateError(409, "already_decided",
                        f"proposal {proposal_id} was already {p['status']} by {p['decided_by']} "
                        f"at {p['decided_at'].isoformat()}: {p['decision_reason']}",
                        decided_by=p["decided_by"], decision=p["status"])
    if p["status"] == "blocked":
        raise GateError(409, "blocked",
                        f"proposal {proposal_id} was blocked by server checks and can never be applied: "
                        + "; ".join(p["block_reasons"]))
    return p


def approve_proposal(cur, actor: Actor, rules: Rules, proposal_id: int, *, reason: str, final_value=None) -> dict:
    """Approve, optionally with a corrected value (edit-before-approve). Both values are kept."""
    p = _lock_pending(cur, actor, proposal_id)
    value = p["value"] if final_value is None else final_value
    result = submit_change(cur, actor, rules, record_id=p["record_id"], field=p["field"], value=value,
                           reason=reason, approving=p)
    result["proposed_value"] = p["value"]
    result["edited"] = final_value is not None and final_value != p["value"]
    return result


def reject_proposal(cur, actor: Actor, rules: Rules, proposal_id: int, *, reason: str) -> dict:
    """Reject for good (R5). The decision is recorded on the proposal and in the audit chain."""
    if not reason or not str(reason).strip():
        raise GateError(422, "reason_required", "a rejection needs a reason")
    p = _lock_pending(cur, actor, proposal_id)
    if p.get("requested_by") == actor.staff_id:
        raise GateError(403, "own_change", "nobody decides their own pending change (R4)")
    rule = rules.get(p["field"])
    eff = rules.effective(rule, p["value"])
    if actor.role not in eff.approvers:
        raise GateError(403, "not_an_approver",
                        f"only {', '.join(sorted(eff.approvers))} may decide this proposal; you are {actor.role}")
    cur.execute(
        """UPDATE proposals SET status = 'rejected', decided_by = %s, decided_at = now(), decision_reason = %s
           WHERE id = %s AND status = 'pending'""",
        (actor.staff_id, str(reason).strip(), proposal_id))
    entry = audit.append(
        cur, shop_id=actor.shop_id, time_zone=shop_time_zone(cur, actor.shop_id), actor_id=actor.staff_id,
        action="proposal_rejected", entity=rule.entity, record_id=p["record_id"], field=p["field"],
        old_value=None, new_value=p["value"], proposal_id=proposal_id,
        detail={"reason": str(reason).strip(), "origin": p["origin"]}, rules_sha256=rules.sha256)
    return {"outcome": "rejected", "proposal_id": proposal_id, "audit_seq": entry["seq"]}
