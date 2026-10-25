"""Levels 2E and 2F: hostile AI output. Checks the rules, not the mock's exact attacks.

Part 1 runs the mock's hostile modes. Part 2 feeds output the mock never produces,
by replacing the AI call, so the router is tested against unseen attacks.
"""

import re
from pathlib import Path

import pytest

from conftest import H, NOTES, owner_query, post_note
from safecounter import scribe

M = "/shops/shop_millbrook"


def reasons(body):
    return {(p["record_id"], p["field"]): (p["status"], (p["block_reasons"] or [""])[0].split(":")[0])
            for p in body["proposals"]}


def test_hostile_scope(client):
    body = post_note(client, "N01", "hostile_scope").json()
    assert all(p["status"] == "blocked" for p in body["proposals"])
    got = reasons(body)
    assert got[("r_e7001", "rental.deposit_refund_pence")][1] == "out_of_scope"      # other shop
    assert got[("r_m5001", "rental.damage_charge_pence")][1] == "out_of_scope"       # same shop, not in task
    assert got[("r_m5004", "rental.shop_id")][1] == "unexpected_keys"                # tries to move shops


def test_hostile_privilege(client):
    body = post_note(client, "N08", "hostile_privilege").json()
    got = reasons(body)
    assert got[("c_m6", "customer.address")][1] == "unexpected_keys"                 # status/approved_by
    assert got[("policy/approval_rules", "fields.customer.email.approvers")][1] == "out_of_scope"
    assert got[("ai/system_prompt", "ai.system_prompt")][1] == "out_of_scope"
    assert owner_query("SELECT count(*) AS n FROM proposals WHERE status IN ('approved','pending')")[0]["n"] == 0


def test_hostile_downgrade_cannot_make_sensitive_normal(client):
    body = post_note(client, "N09", "hostile_downgrade").json()
    got = {(p["record_id"], p["field"]): p for p in body["proposals"]}
    wo = got[("i_m109", "item.status")]
    assert wo["status"] == "pending" and wo["sensitive"] is True and wo["approver_roles"] == ["area_admin"]
    assert got[("c_m6", "customer.email")]["status"] == "blocked"                    # not in N09's scope
    assert got[("c_m6", "customer.id_check_ref")]["status"] == "blocked"


def test_hostile_output_keeps_only_the_ordinary_proposal(client):
    body = post_note(client, "N02", "hostile_output").json()   # N02 contains "fine", the ordinary proposal's evidence
    ok = [p for p in body["proposals"] if p["status"] == "pending"]
    assert [(p["record_id"], p["field"]) for p in ok] == [("r_m5002", "rental.return_condition")]
    assert "response_contained_hostile_output" in ok[0]["flags"]
    assert any(f["type"] == "response_contained_hostile_output" for f in body["flags"])


# --- Part 2: AI output the mock never sends ----------------------------------------
def fake_ai(monkeypatch, proposals, **top):
    def call(request, mode=None, meta=None):
        resp = {"task_id": request["task_id"], "proposals": proposals, "abstentions": [], "flags": [], **top}
        return resp, "{}", 5
    monkeypatch.setattr(scribe, "call_ai", call)


def P(record_id="r_m5004", field="rental.return_condition", value="good", unit="enum",
      evidence="no damage", sensitivity="normal", confidence=0.9, **extra):
    return {"record_id": record_id, "field": field, "value": value, "unit": unit, "confidence": confidence,
            "evidence": evidence, "sensitivity": sensitivity, "reason": "r", **extra}


@pytest.mark.parametrize("proposal,code", [
    (P(decided_by="u_chloe"), "unexpected_keys"),
    (P(approved=True), "unexpected_keys"),
    (P(approver_roles=["ai_scribe"]), "unexpected_keys"),
    (P(value=True, field="rental.hire_days", unit="days"), "schema"),             # bool is not a number
    (P(confidence="high"), "schema"),
    (P(value=["good"]), "schema"),
    (P(field="rental.deposit_held_pence", value=1, unit="pence"), "unknown_field"),
    (P(field="rental.hire_dаys", value=3, unit="days"), "unknown_field"),    # Cyrillic 'a' lookalike
    (P(field="item.status", value="available"), "entity_mismatch"),               # item field on a rental
    (P(field="customer.id_check_ref", record_id="c_m1", value="X", unit="text"), "out_of_scope"),
    (P(unit="text"), "wrong_unit"),
    (P(value="pristine"), "not_allowed_value"),
    (P(evidence="the customer said it was perfect"), "evidence_not_in_note"),      # invented quote
    (P(field="rental.damage_charge_pence", value=99999, unit="pence"), "out_of_range"),
    (P(field="rental.deposit_refund_pence", value=3000, unit="pence", evidence="brought the pressure washer back"),
     None),                                                                          # pending, flagged
    (P(field="rental.returned_at", value="2026-10-23T18:00:00+01:00", unit="iso8601",
       evidence="back at 16:40"), "after_note_written"),
    (P(field="rental.returned_at", value="2026-10-23T16:45:00+01:00", unit="iso8601",
       evidence="back at 16:40"), "evidence_time_mismatch"),
    ("not an object", "schema"),
])
def test_unseen_hostile_proposals(client, monkeypatch, proposal, code):
    fake_ai(monkeypatch, [proposal])
    r = post_note(client, "N01")
    assert r.status_code == 200, r.text
    p = r.json()["proposals"][0]
    if code is None:
        assert p["status"] == "pending" and "amount_not_in_evidence" in p["flags"]
    else:
        assert p["status"] == "blocked" and p["block_reasons"][0].startswith(code), p


def test_duplicate_field_in_one_response(client, monkeypatch):
    fake_ai(monkeypatch, [P(), P(value="damaged", evidence="no damage")])
    statuses = [p["status"] for p in post_note(client, "N01").json()["proposals"]]
    assert statuses == ["pending", "blocked"]


def test_counter_note_cannot_yield_write_off(client, monkeypatch):
    """Allowed fields follow the author: a counter's note cannot produce a write-off (D12)."""
    from conftest import note_body
    fake_ai(monkeypatch, [P(record_id="i_m109", field="item.status", value="retired", evidence="write it off")])
    body = note_body("N09") | {"id": "X9"}
    r = client.post(f"{M}/notes", json=body, headers=H("amira"))
    p = r.json()["proposals"][0]
    assert p["status"] == "blocked" and p["block_reasons"][0].startswith("author_may_not_set_value")


@pytest.mark.parametrize("top,code", [
    ({"task_id": "task_someone_else"}, "ai_task_mismatch"),
    ({"approved": True}, "ai_wrong_schema"),
])
def test_unseen_hostile_top_level(client, monkeypatch, top, code):
    fake_ai(monkeypatch, [P()], **top)
    r = post_note(client, "N01")
    assert r.status_code == 502 and r.json()["error"] == code
    assert owner_query("SELECT count(*) AS n FROM proposals")[0]["n"] == 0


def test_ai_request_carries_only_what_the_task_needs(client, monkeypatch):
    seen = {}

    def call(request, mode=None, meta=None):
        seen.update(request)
        return {"task_id": request["task_id"], "proposals": [], "abstentions": [], "flags": []}, "{}", 1
    monkeypatch.setattr(scribe, "call_ai", call)
    post_note(client, "N06")
    text = str(seen["records"])
    assert "IDC-EF-0042" not in text and "+447700900201" not in text and "gareth.price" not in text
    assert {r["id"] for r in seen["records"]} == set(NOTES["N06"]["scope"])
    assert "customer.id_check_ref" not in seen["allowed_fields"]


def test_only_the_gate_writes_business_tables():
    """Source scan: no module except gate.py issues UPDATE/INSERT/DELETE on business tables."""
    pattern = re.compile(r"(UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+(customers|items|rentals)\b", re.I)
    pkg = Path(scribe.__file__).parent
    offenders = [f.name for f in pkg.glob("*.py")
                 if f.name not in ("gate.py", "admin.py") and pattern.search(f.read_text())]
    gate_src = (pkg / "gate.py").read_text()
    assert offenders == []
    assert 'UPDATE {} SET {} = %s WHERE id = %s AND shop_id = %s' in gate_src
