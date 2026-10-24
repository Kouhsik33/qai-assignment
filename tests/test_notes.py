"""The nine data-pack notes through the real mock AI (normal mode) and our router.

Expected outcomes are what the SERVER must do with the mock's answers, including
the answers the mock gets wrong on purpose (N04, N05, N08, N09).
"""

import pytest

from conftest import post_note

P, B = "pending", "blocked"

EXPECTED = {
    "N01": {("r_m5004", "rental.returned_at"): (P, False), ("r_m5004", "rental.return_condition"): (P, False),
            ("r_m5004", "rental.deposit_refund_pence"): (P, True)},
    "N02": {("r_m5002", "rental.returned_at"): (P, False), ("r_m5002", "rental.return_condition"): (P, False),
            ("r_m5002", "rental.damage_charge_pence"): (P, True), ("r_m5003", "rental.returned_at"): (P, False),
            ("r_m5003", "rental.return_condition"): (P, False), ("r_m5001", "rental.hire_days"): (P, False)},
    "N03": {("r_m5005", "rental.return_condition"): (P, False), ("i_m107", "item.status"): (P, False),
            ("i_m107", "item.service_note"): (P, False)},
    "N04": {("r_e7002", "rental.hire_days"): (B, "out_of_range"),
            ("r_e7002", "rental.damage_charge_pence"): (B, "non_gbp_currency")},
    "N05": {("r_m5001", "rental.returned_at"): (P, False), ("r_m5001", "rental.return_condition"): (P, False),
            ("r_m5001", "rental.damage_charge_pence"): (P, True),
            ("r_m5001", "rental.deposit_refund_pence"): (B, "evidence_not_in_note")},
    "N06": {("r_e7001", "rental.returned_at"): (P, False), ("r_e7001", "rental.return_condition"): (P, False),
            ("c_e1", "customer.phone"): (P, True)},
    "N07": {},
    "N08": {("r_m5007", "rental.returned_at"): (P, False), ("r_m5007", "rental.return_condition"): (P, False),
            ("c_m6", "customer.email"): (B, "suspected_injection"),
            ("r_m5007", "rental.deposit_refund_pence"): (B, "out_of_range")},
    "N09": {("i_m109", "item.status"): (P, True), ("i_m109", "item.service_note"): (P, False)},
}
ABSTENTIONS = {"N01": 1, "N02": 0, "N03": 1, "N04": 0, "N05": 0, "N06": 1, "N07": 3, "N08": 0, "N09": 0}


@pytest.mark.parametrize("nid", sorted(EXPECTED))
def test_note(client, nid):
    r = post_note(client, nid)
    assert r.status_code == 200, r.text
    body = r.json()
    got = {(p["record_id"], p["field"]): p for p in body["proposals"]}
    assert set(got) == set(EXPECTED[nid])
    for key, (status, extra) in EXPECTED[nid].items():
        p = got[key]
        assert p["status"] == status, (key, p)
        if status == P:
            assert p["sensitive"] is extra, (key, p)
        else:
            assert p["block_reasons"][0].startswith(extra), (key, p)
    assert len(body["abstentions"]) == ABSTENTIONS[nid]


def test_n09_write_off_relabelled_and_needs_area_admin(client):
    p = [x for x in post_note(client, "N09").json()["proposals"] if x["field"] == "item.status"][0]
    assert p["value"] == "retired" and p["approver_roles"] == ["area_admin"]
    assert any(f.startswith("ai_label_mismatch") for f in p["flags"])


def test_n08_injection_flagged_and_approval_claim_ignored(client):
    body = post_note(client, "N08").json()
    assert any(f["type"] == "suspected_injection" and f["from"] == "server" for f in body["flags"])
    assert all(p["status"] in ("pending", "blocked") for p in body["proposals"])   # nothing "approved"


def test_n05_flags_dictation_and_low_confidence(client):
    props = {p["field"]: p for p in post_note(client, "N05").json()["proposals"]}
    assert "source_voice_to_text" in props["rental.returned_at"]["flags"]
    assert any(f.startswith("low_confidence") for f in props["rental.return_condition"]["flags"])
    assert "amount_in_words_unverified" in props["rental.damage_charge_pence"]["flags"]


def test_note_cannot_be_processed_twice(client):
    assert post_note(client, "N01").status_code == 200
    r = post_note(client, "N01")
    assert r.status_code == 409 and r.json()["error"] == "already_processed"


def test_note_scope_must_be_in_the_shop(client):
    from conftest import H, note_body
    body = note_body("N01") | {"id": "X1", "scope": ["r_e7001"]}
    r = client.post("/shops/shop_millbrook/notes", json=body, headers=H("amira"))
    assert r.status_code == 422 and r.json()["error"] == "scope_record_not_found"
