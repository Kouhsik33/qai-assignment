"""Level 3: reliable event delivery to the pack's flaky receiver (run unmodified, as a subprocess).

Proves: no lost events (every applied change has exactly one outbox row, written in the
same transaction), retries survive failures and a receiver that is down, duplicates
caused by lost acknowledgements carry the same Idempotency-Key, and the worker role
can do nothing but deliver.
"""

import json
import socket
import subprocess
import sys
import time
import urllib.request

import psycopg
import pytest
from psycopg.rows import dict_row

from conftest import H, app_conn, owner_query
from safecounter import config, outbox, rules
from safecounter.db import shop_tx
from safecounter.gate import Actor, submit_change

RECEIVER = str(config.ROOT / "mock_ai" / "flaky_receiver.py")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_receiver(port, *args):
    proc = subprocess.Popen([sys.executable, RECEIVER, "--port", str(port), "--slow-rate", "0", *args],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/events", timeout=0.2)
            return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("receiver did not start")


def received(port) -> dict:
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/events").read())


def delivery_conn():
    return psycopg.connect(outbox.DELIVERY_DSN, row_factory=dict_row, autocommit=True)


def make_changes(client, n):
    values = ["needs_cleaning", "damaged", "good", "parts_missing"]
    for i in range(n):
        r = client.post("/shops/shop_millbrook/changes", headers=H("amira"),
                        json={"record_id": "r_m5004", "field": "rental.return_condition",
                              "value": values[i % 4], "reason": f"change {i}"})
        assert r.status_code == 200, r.text


def drain(conn, url, rounds=60):
    for _ in range(rounds):
        outbox.deliver(conn, url, backoff_base=0, timeout=2)
        if outbox.pending(conn) == 0:
            return
    raise AssertionError(f"{outbox.pending(conn)} events still pending")


def test_every_applied_change_has_one_event(client):
    make_changes(client, 3)
    client.post("/shops/shop_millbrook/changes", headers=H("amira"),       # a pending change: no event yet
                json={"record_id": "r_m5004", "field": "rental.damage_charge_pence", "value": 100, "reason": "x"})
    rows = owner_query("SELECT event_id, payload FROM outbox ORDER BY id")
    audit = owner_query("SELECT seq FROM audit_log WHERE action = 'change_applied' ORDER BY seq")
    assert [r["event_id"] for r in rows] == [f"shop_millbrook:{a['seq']}" for a in audit]
    assert rows[0]["payload"]["field"] == "rental.return_condition"


def test_rolled_back_change_has_no_event():
    owner_query("DELETE FROM audit_head WHERE shop_id = 'shop_millbrook'", fetch=False)
    with pytest.raises(RuntimeError):
        with shop_tx("shop_millbrook") as cur:
            submit_change(cur, Actor("u_amira", "shop_millbrook", "counter"), rules.load(),
                          record_id="r_m5004", field="rental.return_condition", value="good", reason="t")
    assert owner_query("SELECT count(*) AS n FROM outbox")[0]["n"] == 0


def test_flaky_receiver_gets_every_event_and_duplicates_share_a_key(client):
    make_changes(client, 12)
    port = free_port()
    proc = start_receiver(port, "--fail-rate", "0.3", "--lost-ack-rate", "0.3", "--seed", "4")
    try:
        with delivery_conn() as conn:
            drain(conn, f"http://127.0.0.1:{port}/events")
        got = received(port)
    finally:
        proc.kill()
    keys = {r["event_id"] for r in owner_query("SELECT event_id FROM outbox")}
    assert got["unique_keys"] == 12 and {e["key"] for e in got["log"]} == keys      # nothing lost
    assert got["received"] >= 12
    # Lost acknowledgements make the receiver see some events twice, always with the same key.
    assert got["duplicate_keys"] and all(k in keys for k in got["duplicate_keys"])
    attempts = owner_query("SELECT sum(attempts) AS n FROM outbox")[0]["n"]
    assert attempts > 12                                                              # retries happened


def test_receiver_down_then_up(client):
    make_changes(client, 3)
    port = free_port()
    url = f"http://127.0.0.1:{port}/events"
    with delivery_conn() as conn:
        counts = outbox.deliver(conn, url, backoff_base=0, timeout=1)                 # nobody listening
        assert counts == {"delivered": 0, "failed": 3} and outbox.pending(conn) == 3
        proc = start_receiver(port, "--fail-rate", "0", "--lost-ack-rate", "0")
        try:
            drain(conn, url)
            assert received(port)["unique_keys"] == 3
        finally:
            proc.kill()
    errors = owner_query("SELECT attempts, last_error, delivered_at FROM outbox")
    assert all(r["attempts"] == 2 and r["delivered_at"] is not None for r in errors)


def test_backoff_delays_the_next_attempt(client):
    make_changes(client, 1)
    with delivery_conn() as conn:
        outbox.deliver(conn, f"http://127.0.0.1:{free_port()}/events", backoff_base=60, timeout=1)
        assert outbox.deliver(conn, "http://unused", backoff_base=60) == {"delivered": 0, "failed": 0}  # not due yet
    row = owner_query("SELECT attempts, next_attempt_at > now() + interval '50 seconds' AS later FROM outbox")[0]
    assert row == {"attempts": 1, "later": True}


@pytest.mark.parametrize("stmt", [
    "SELECT * FROM outbox",
    "UPDATE outbox SET delivered_at = now()",
    "DELETE FROM outbox",
])
def test_app_role_cannot_read_or_alter_delivery(stmt):
    with app_conn() as conn:
        conn.execute("SELECT set_config('app.shop_id', 'shop_millbrook', false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(stmt)


@pytest.mark.parametrize("stmt", [
    "SELECT * FROM rentals",
    "UPDATE outbox SET payload = '{}'",
    "INSERT INTO outbox (shop_id, event_id, kind, payload) VALUES ('shop_millbrook', 'x', 'k', '{}')",
    "DELETE FROM outbox",
])
def test_delivery_role_can_only_deliver(stmt):
    with delivery_conn() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(stmt)
