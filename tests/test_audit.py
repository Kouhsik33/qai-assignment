"""Audit chain: same-transaction writes, hash links, verify finds the first broken row."""

import psycopg
import pytest

from conftest import H, app_conn, owner_query, record
from safecounter import admin, audit, rules
from safecounter.db import shop_tx
from safecounter.gate import Actor, GateError, submit_change

M = "/shops/shop_millbrook"


def make_changes(client, n=3):
    values = ["needs_cleaning", "damaged", "good", "parts_missing"]
    for v in values[:n]:
        r = client.post(f"{M}/changes", headers=H("amira"),
                        json={"record_id": "r_m5004", "field": "rental.return_condition", "value": v, "reason": "t"})
        assert r.status_code == 200, r.text


def test_chain_links_and_verifies(client):
    make_changes(client, 3)
    rows = client.get(f"{M}/audit", headers=H("chloe")).json()
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert rows[0]["prev_hash"] == audit.GENESIS
    assert rows[1]["prev_hash"] == rows[0]["row_hash"] and rows[2]["prev_hash"] == rows[1]["row_hash"]
    assert rows[2]["old_value"] == '"damaged"' and rows[2]["new_value"] == '"good"'
    v = client.get(f"{M}/audit/verify", headers=H("chloe")).json()
    assert v == {"shop_id": "shop_millbrook", "ok": True, "rows": 3, "first_broken": None}


def test_edited_row_is_reported(client):
    make_changes(client, 3)
    with admin.connect() as conn:
        admin.tamper(conn, "shop_millbrook", 2)
    v = client.get(f"{M}/audit/verify", headers=H("chloe")).json()
    assert v["ok"] is False and v["first_broken"]["seq"] == 2
    assert "edited" in v["first_broken"]["problem"]


def test_deleted_middle_row_is_reported(client):
    make_changes(client, 3)
    with psycopg.connect(admin.config.OWNER_DSN) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute("DELETE FROM audit_log WHERE shop_id = 'shop_millbrook' AND seq = 2")
    v = client.get(f"{M}/audit/verify", headers=H("chloe")).json()
    assert v["first_broken"]["seq"] == 3 and "gap" in v["first_broken"]["problem"]


def test_truncated_tail_is_reported(client):
    make_changes(client, 3)
    with psycopg.connect(admin.config.OWNER_DSN) as conn:
        conn.execute("SET session_replication_role = replica")
        conn.execute("DELETE FROM audit_log WHERE shop_id = 'shop_millbrook' AND seq = 3")
    v = client.get(f"{M}/audit/verify", headers=H("chloe")).json()
    assert v["ok"] is False and "tail" in v["first_broken"]["problem"]


def test_chains_are_per_shop(client):
    make_changes(client, 2)
    r = client.post("/shops/shop_eastfield/changes", headers=H("dev"),
                    json={"record_id": "r_e7002", "field": "rental.hire_days", "value": 4, "reason": "t"})
    assert r.json()["audit_seq"] == 1
    assert client.get("/shops/shop_eastfield/audit/verify", headers=H("ewa")).json()["rows"] == 1


@pytest.mark.parametrize("stmt", [
    "UPDATE audit_log SET new_value = 'x'",
    "DELETE FROM audit_log",
])
def test_app_role_cannot_rewrite_audit(client, stmt):
    make_changes(client, 1)
    with app_conn() as conn:
        conn.execute("SELECT set_config('app.shop_id', 'shop_millbrook', false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(stmt)


def test_change_and_audit_are_one_transaction():
    """If the audit append fails, the business change must roll back too (R7)."""
    owner_query("DELETE FROM audit_head WHERE shop_id = 'shop_millbrook'", fetch=False)
    actor = Actor("u_amira", "shop_millbrook", "counter")
    with pytest.raises(RuntimeError, match="no audit head"):
        with shop_tx("shop_millbrook") as cur:
            submit_change(cur, actor, rules.load(), record_id="r_m5004", field="rental.return_condition",
                          value="good", reason="t")
    assert record("shop_millbrook", "r_m5004")["return_condition"] is None


def test_rejection_is_audited(client):
    r = client.post(f"{M}/changes", headers=H("amira"),
                    json={"record_id": "r_m5004", "field": "rental.damage_charge_pence", "value": 900, "reason": "t"})
    pid = r.json()["proposal_id"]
    client.post(f"{M}/proposals/{pid}/reject", headers=H("chloe"), json={"reason": "no damage seen"})
    rows = client.get(f"{M}/audit", headers=H("chloe")).json()
    assert rows[-1]["action"] == "proposal_rejected" and rows[-1]["actor_id"] == "u_chloe"
    assert '"no damage seen"' in rows[-1]["detail"]
