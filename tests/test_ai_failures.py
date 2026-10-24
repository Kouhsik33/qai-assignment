"""Level 2C: the AI hangs, is slow, is down, drops the connection or returns garbage.

Each must give a clear error within a bounded time, store no proposals, change no
record, and leave the other endpoints responding. AI_DEADLINE_SECONDS is 2 in tests.
"""

import threading
import time

import pytest

from conftest import H, owner_query, post_note, record
from safecounter import config

BOUND = config.AI_DEADLINE_SECONDS + 1.5


@pytest.mark.parametrize("mode,status,code", [
    ("hang", 504, "ai_timeout"),
    ("slow", 504, "ai_timeout"),
    ("error", 502, "ai_bad_status"),
    ("drop", 502, "ai_connection_dropped"),
    ("garbage", 502, "ai_invalid_json"),
    ("wrong_schema", 502, "ai_wrong_schema"),
])
def test_ai_failure_is_bounded_and_saves_nothing(client, mode, status, code):
    before = record("shop_millbrook", "r_m5004")
    start = time.monotonic()
    r = post_note(client, "N01", mode)
    took = time.monotonic() - start
    assert r.status_code == status and r.json()["error"] == code, r.text
    assert "No proposals were stored and no records were changed" in r.json()["detail"]
    assert took < BOUND, f"took {took:.1f}s"
    assert owner_query("SELECT count(*) AS n FROM proposals")[0]["n"] == 0
    assert owner_query("SELECT status, error_kind FROM ai_tasks") == [{"status": "failed", "error_kind": code[3:]}]
    assert record("shop_millbrook", "r_m5004") == before


def test_ai_down(client, monkeypatch):
    monkeypatch.setattr(config, "AI_URL", "http://127.0.0.1:9/v1/propose")
    r = post_note(client, "N01")
    assert r.status_code == 502 and r.json()["error"] == "ai_unavailable"


def test_retry_after_failure_succeeds(client):
    assert post_note(client, "N01", "error").status_code == 502
    r = post_note(client, "N01")
    assert r.status_code == 200 and len(r.json()["proposals"]) == 3


def test_other_endpoints_respond_while_ai_hangs(client):
    t = threading.Thread(target=post_note, args=(client, "N01", "hang"))
    t.start()
    time.sleep(0.3)
    start = time.monotonic()
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/shops/shop_millbrook/records/r_m5004", headers=H("amira")).status_code == 200
    assert time.monotonic() - start < 1.0
    t.join()
