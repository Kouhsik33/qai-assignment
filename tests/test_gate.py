"""The gate: approval rules R1, R2, R4, R6, field permissions and value validation."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import H, owner_query, record

M = "/shops/shop_millbrook"


def change(client, user, rid, field, value, reason="test", shop=M):
    return client.post(f"{shop}/changes", headers=H(user),
                       json={"record_id": rid, "field": field, "value": value, "reason": reason})


def test_r1_normal_field_applies_now(client):
    r = change(client, "amira", "r_m5004", "rental.returned_at", "2026-10-23T16:40:00+01:00")
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "applied"
    assert record("shop_millbrook", "r_m5004")["returned_at"] == "2026-10-23T16:40:00+01:00"


def test_r2_sensitive_change_by_non_approver_waits_then_manager_approves(client):
    r = change(client, "amira", "r_m5004", "rental.damage_charge_pence", 1500, "scratched lance")
    assert r.status_code == 200 and r.json()["outcome"] == "pending_approval"
    assert r.json()["approver_roles"] == ["area_admin", "manager"]
    assert record("shop_millbrook", "r_m5004")["damage_charge_pence"] is None   # not applied yet
    pid = r.json()["proposal_id"]
    a = client.post(f"{M}/proposals/{pid}/approve", headers=H("chloe"), json={"reason": "seen the lance"})
    assert a.status_code == 200, a.text
    assert record("shop_millbrook", "r_m5004")["damage_charge_pence"] == 1500


def test_r2_sensitive_change_by_approver_applies_now(client):
    r = change(client, "chloe", "r_m5004", "rental.damage_charge_pence", 1500)
    assert r.json()["outcome"] == "applied"


@pytest.mark.parametrize("user,field,value,shop", [
    ("amira", "item.service_note", "greased", M),          # counter may not change service notes
    ("ben", "rental.hire_days", 4, M),                      # technician may not change hire days
    ("chloe", "customer.id_check_ref", "IDC-1", M),         # only area admin
    ("amira", "item.status", "retired", M),                 # write-off: counter may not
])
def test_role_may_not_change_field(client, user, field, value, shop):
    rid = {"item": "i_m109", "rental": "r_m5004", "customer": "c_m1"}[field.split(".")[0]]
    r = change(client, user, rid, field, value, shop=shop)
    assert r.status_code == 403 and r.json()["error"] == "not_permitted", r.text


def test_area_admin_sets_id_check_ref(client):
    r = change(client, "femi", "c_m1", "customer.id_check_ref", "IDC-MB-0001")
    assert r.json()["outcome"] == "applied"


def test_write_off_is_sensitive_from_its_value_and_needs_area_admin(client):
    r = change(client, "ben", "i_m109", "item.status", "retired", "crankcase cracked")
    assert r.json()["outcome"] == "pending_approval" and r.json()["approver_roles"] == ["area_admin"]
    pid = r.json()["proposal_id"]
    denied = client.post(f"{M}/proposals/{pid}/approve", headers=H("chloe"), json={"reason": "ok"})
    assert denied.status_code == 403 and denied.json()["error"] == "not_an_approver"
    ok = client.post(f"{M}/proposals/{pid}/approve", headers=H("femi"), json={"reason": "write-off agreed"})
    assert ok.status_code == 200
    assert record("shop_millbrook", "i_m109")["status"] == "retired"


def test_non_write_off_status_is_normal(client):
    assert change(client, "ben", "i_m109", "item.status", "available").json()["outcome"] == "applied"


@pytest.mark.parametrize("field,value,code", [
    ("rental.hire_days", 29, "out_of_range"),
    ("rental.hire_days", 0, "out_of_range"),
    ("rental.hire_days", True, "wrong_type"),               # JSON true is not a number of days
    ("rental.hire_days", "5", "wrong_type"),
    ("rental.damage_charge_pence", 50001, "out_of_range"),
    ("rental.damage_charge_pence", 12.5, "wrong_type"),
    ("rental.deposit_refund_pence", 3001, "out_of_range"),  # deposit held is 3000
    ("rental.return_condition", "fine", "not_allowed_value"),
    ("rental.returned_at", "2026-10-23T16:40:00", "bad_datetime"),        # no offset
    ("rental.returned_at", "2026-10-23T16:40:00+00:00", "wrong_offset"),  # London was +01:00
    ("rental.returned_at", "2026-10-20T09:00:00+01:00", "before_hire"),
    ("rental.returned_at", "2027-01-01T09:00:00+00:00", "in_future"),
])
def test_value_rules(client, field, value, code):
    r = change(client, "chloe", "r_m5004", field, value)
    assert r.status_code == 422 and r.json()["error"] == code, r.text


@pytest.mark.parametrize("field,value,code", [
    ("customer.phone", "07700 900123", "bad_phone"),
    ("customer.email", "not-an-email", "bad_email"),
    ("customer.address", "1 High St\nMillbrook", "not_one_line"),
])
def test_customer_value_rules(client, field, value, code):
    r = change(client, "chloe", "c_m1", field, value)
    assert r.status_code == 422 and r.json()["error"] == code, r.text


def test_after_clocks_go_back_london_offset_is_zero(client):
    # 25 Oct 2026 onwards London is UTC+00:00; a return "now" must carry +00:00.
    now = datetime.now(ZoneInfo("Europe/London")).replace(microsecond=0)
    r = change(client, "amira", "r_m5004", "rental.returned_at", now.isoformat())
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("field", ["rental.shop_id", "rental.deposit_held_pence", "item.asset_tag", "ai.system_prompt"])
def test_only_listed_fields_can_change(client, field):
    r = change(client, "femi", "r_m5004", field, "x")
    assert r.status_code == 422 and r.json()["error"] == "unknown_field"


def test_no_change_and_reason_required(client):
    assert change(client, "ben", "i_m109", "item.status", "in_repair").json()["error"] == "no_change"
    assert change(client, "ben", "i_m109", "item.status", "available", reason=" ").json()["error"] == "reason_required"


def test_r4_nobody_approves_their_own_pending_change(client):
    r = change(client, "amira", "r_m5004", "rental.damage_charge_pence", 1500)
    pid = r.json()["proposal_id"]
    # Amira is later promoted: she now holds an approver role, but it is still her own change.
    owner_query("UPDATE memberships SET role = 'manager' WHERE staff_id = 'u_amira' AND shop_id = 'shop_millbrook'",
                fetch=False)
    a = client.post(f"{M}/proposals/{pid}/approve", headers=H("amira"), json={"reason": "mine"})
    assert a.status_code == 403 and a.json()["error"] == "own_change"


def test_cross_shop_requests(client):
    # Not a member of Eastfield: refused before anything is read.
    r = change(client, "amira", "r_e7001", "rental.hire_days", 4, shop="/shops/shop_eastfield")
    assert r.status_code == 403 and r.json()["error"] == "not_a_member"
    # Femi is a member of both, but acting in Millbrook cannot touch an Eastfield record.
    r = change(client, "femi", "r_e7001", "rental.hire_days", 4)
    assert r.status_code == 404 and r.json()["error"] == "record_not_found"
    assert record("shop_eastfield", "r_e7001")["hire_days"] == 3


def test_bad_token(client):
    r = client.post(f"{M}/changes", headers={"Authorization": "Bearer nope"},
                    json={"record_id": "r_m5004", "field": "rental.hire_days", "value": 3, "reason": "x"})
    assert r.status_code == 401
