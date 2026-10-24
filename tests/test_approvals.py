"""Approve / reject: R3, R4, R5, concurrent approvals (2B), edit-before-approve (2D), stale proposals."""

import threading

import psycopg
import pytest

from conftest import H, app_conn, owner_query, post_note, record
from safecounter import rules
from safecounter.db import shop_tx
from safecounter.gate import Actor, GateError, approve_proposal

M = "/shops/shop_millbrook"


def pending(client, user="chloe", **match):
    rows = client.get(f"{M}/proposals", params={"status": "pending"}, headers=H(user)).json()
    return [p for p in rows if all(p[k] == v for k, v in match.items())]


def human_pending(client):
    r = client.post(f"{M}/changes", headers=H("amira"),
                    json={"record_id": "r_m5004", "field": "rental.damage_charge_pence", "value": 1500, "reason": "t"})
    return r.json()["proposal_id"]


def test_two_approvals_at_once_exactly_one_wins():
    """Two approvers decide the same proposal at the same moment (Level 2B)."""
    from fastapi.testclient import TestClient
    from safecounter.api import app
    pid = human_pending(TestClient(app))
    barrier = threading.Barrier(2)
    results = {}

    def approve(staff, role):
        actor = Actor(staff, "shop_millbrook", role)
        barrier.wait()
        try:
            with shop_tx("shop_millbrook") as cur:
                results[staff] = approve_proposal(cur, actor, rules.load(), pid, reason=f"{staff} approves")
        except GateError as exc:
            results[staff] = exc

    threads = [threading.Thread(target=approve, args=a) for a in [("u_chloe", "manager"), ("u_femi", "area_admin")]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = [s for s, r in results.items() if isinstance(r, dict)]
    losses = [r for r in results.values() if isinstance(r, GateError)]
    assert len(wins) == 1 and len(losses) == 1
    assert losses[0].status == 409 and losses[0].code == "already_decided"
    assert f"approved by {wins[0]}" in losses[0].detail          # names who decided first
    changes = owner_query("SELECT count(*) AS n FROM audit_log WHERE proposal_id = %s", (pid,))
    assert changes[0]["n"] == 1                                    # applied exactly once


def test_rejected_is_final_by_any_path(client):
    pid = human_pending(client)
    r = client.post(f"{M}/proposals/{pid}/reject", headers=H("chloe"), json={"reason": "no damage"})
    assert r.status_code == 200
    again = client.post(f"{M}/proposals/{pid}/approve", headers=H("femi"), json={"reason": "override"})
    assert again.status_code == 409 and "rejected by u_chloe" in again.json()["detail"]
    # Even a direct SQL update by the app role is refused by the database trigger.
    with app_conn() as conn:
        conn.execute("SELECT set_config('app.shop_id', 'shop_millbrook', false)")
        with pytest.raises(psycopg.errors.RaiseException, match="already rejected"):
            conn.execute("UPDATE proposals SET status = 'approved', decided_by = 'u_femi', decided_at = now(),"
                         " decision_reason = 'x' WHERE id = %s", (pid,))
    assert record("shop_millbrook", "r_m5004")["damage_charge_pence"] is None


def test_database_refuses_inserted_decisions():
    with app_conn() as conn:
        conn.execute("SELECT set_config('app.shop_id', 'shop_millbrook', false)")
        with pytest.raises(psycopg.errors.RaiseException, match="pending or blocked"):
            conn.execute("""INSERT INTO proposals (shop_id, origin, requested_by, record_id, field, status)
                            VALUES ('shop_millbrook', 'human', 'u_amira', 'r_m5004', 'rental.hire_days', 'approved')""")


def test_database_refuses_self_approval():
    from fastapi.testclient import TestClient
    from safecounter.api import app
    pid = human_pending(TestClient(app))
    with app_conn() as conn:
        conn.execute("SELECT set_config('app.shop_id', 'shop_millbrook', false)")
        with pytest.raises(psycopg.errors.RaiseException, match="R4"):
            conn.execute("UPDATE proposals SET status = 'approved', decided_by = 'u_amira', decided_at = now(),"
                         " decision_reason = 'x' WHERE id = %s", (pid,))


def test_decisions_need_a_reason(client):
    pid = human_pending(client)
    r = client.post(f"{M}/proposals/{pid}/reject", headers=H("chloe"), json={"reason": "  "})
    assert r.status_code == 422 and r.json()["error"] == "reason_required"


def test_note_author_may_approve_normal_ai_proposal_but_not_sensitive(client):
    post_note(client, "N01")
    ret = pending(client, field="rental.returned_at")[0]
    refund = pending(client, field="rental.deposit_refund_pence")[0]
    assert client.post(f"{M}/proposals/{ret['id']}/approve", headers=H("amira"), json={"reason": "I wrote it"}).status_code == 200
    r = client.post(f"{M}/proposals/{refund['id']}/approve", headers=H("amira"), json={"reason": "I wrote it"})
    assert r.status_code == 403 and r.json()["error"] == "not_an_approver"
    r = client.post(f"{M}/proposals/{refund['id']}/approve", headers=H("chloe"), json={"reason": "checked"})
    assert r.status_code == 200 and record("shop_millbrook", "r_m5004")["deposit_refund_pence"] == 3000


def test_manager_may_approve_ai_proposal_in_own_shop_only(client):
    post_note(client, "N06")      # Eastfield
    phone = [p for p in client.get("/shops/shop_eastfield/proposals", headers=H("ewa")).json()
             if p["field"] == "customer.phone"][0]
    r = client.post(f"/shops/shop_millbrook/proposals/{phone['id']}/approve", headers=H("chloe"), json={"reason": "x"})
    assert r.status_code == 404                                   # invisible from Millbrook
    r = client.post(f"/shops/shop_eastfield/proposals/{phone['id']}/approve", headers=H("ewa"), json={"reason": "rang him"})
    assert r.status_code == 200 and record("shop_eastfield", "c_e1")["phone"] == "+447700900482"


def test_edit_before_approve_keeps_both_values(client):
    post_note(client, "N02")
    hd = pending(client, field="rental.hire_days")[0]
    assert hd["value"] == 5
    r = client.post(f"{M}/proposals/{hd['id']}/approve", headers=H("chloe"),
                    json={"reason": "Priya said one more day, not two", "final_value": 4})
    assert r.status_code == 200 and r.json()["edited"] is True
    p = [x for x in client.get(f"{M}/proposals", headers=H("chloe")).json() if x["id"] == hd["id"]][0]
    assert (p["value"], p["final_value"], p["status"]) == (5, 4, "approved")
    audit_row = client.get(f"{M}/audit", headers=H("chloe")).json()[-1]
    assert audit_row["new_value"] == "4" and '"proposed_value":5' in audit_row["detail"]
    assert record("shop_millbrook", "r_m5001")["hire_days"] == 4


def test_edited_value_is_rechecked_for_sensitivity(client):
    post_note(client, "N03")
    st = pending(client, field="item.status")[0]                  # AI proposed "available"
    r = client.post(f"{M}/proposals/{st['id']}/approve", headers=H("chloe"),
                    json={"reason": "actually scrap it", "final_value": "retired"})
    assert r.status_code == 403 and "write-off" in r.json()["detail"]
    r = client.post(f"{M}/proposals/{st['id']}/approve", headers=H("chloe"),
                    json={"reason": "bad value", "final_value": "broken"})
    assert r.status_code == 422


def test_stale_proposal_is_refused(client):
    post_note(client, "N02")
    hd = pending(client, field="rental.hire_days")[0]               # based on hire_days = 3
    client.post(f"{M}/changes", headers=H("amira"),
                json={"record_id": "r_m5001", "field": "rental.hire_days", "value": 6, "reason": "customer rang"})
    r = client.post(f"{M}/proposals/{hd['id']}/approve", headers=H("chloe"), json={"reason": "ok"})
    assert r.status_code == 409 and r.json()["error"] == "stale_proposal"
    assert record("shop_millbrook", "r_m5001")["hire_days"] == 6


def test_blocked_proposal_can_never_be_approved(client):
    post_note(client, "N08")
    rows = client.get(f"{M}/proposals", params={"status": "blocked"}, headers=H("chloe")).json()
    refund = [p for p in rows if p["field"] == "rental.deposit_refund_pence"][0]
    r = client.post(f"{M}/proposals/{refund['id']}/approve", headers=H("femi"), json={"reason": "x", "final_value": 2500})
    assert r.status_code == 409 and r.json()["error"] == "blocked"
