"""Reliable event delivery (Level 3): a transactional outbox.

    enqueue()  called by the gate, inside the change's own transaction: no change without
               its event, and no event for a change that rolled back.
    deliver()  run by a worker as the sc_delivery role: claims one due event at a time
               (FOR UPDATE SKIP LOCKED, so several workers never send the same row at once),
               POSTs it with Idempotency-Key = event_id, and records the outcome.

Guarantee: at-least-once delivery. A receiver that stores an event but loses the
acknowledgement, or answers after our timeout, will see the same event again, always with
the same Idempotency-Key, so it can drop the duplicate. Order is not guaranteed under
retries; each payload carries the shop's audit seq so a receiver can order or spot gaps.

    python -m safecounter.outbox --url http://127.0.0.1:8098/events --until-empty
"""

import argparse
import os
import sys
import time

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DELIVERY_DSN = os.environ.get("DELIVERY_DATABASE_URL", "postgresql://sc_delivery:delivery_pw@localhost:5433/safecounter")


def enqueue(cur, *, shop_id: str, entry: dict, kind: str = "record.changed"):
    """Add the event for one applied change. `entry` is the audit row just written."""
    payload = {k: entry[k] for k in ("shop_id", "seq", "occurred_at", "actor_id", "entity", "record_id",
                                     "field", "old_value", "new_value", "proposal_id", "row_hash")}
    cur.execute("INSERT INTO outbox (shop_id, event_id, kind, payload) VALUES (%s, %s, %s, %s)",
                (shop_id, f"{shop_id}:{entry['seq']}", kind, Jsonb(payload)))


def backoff(attempts: int, base: float) -> float:
    return min(base * 2 ** attempts, 300.0)


def deliver(conn, url: str, *, limit: int = 50, timeout: float = 3.0, backoff_base: float = 2.0) -> dict:
    """Try up to `limit` due events, one transaction each. Returns counts."""
    counts = {"delivered": 0, "failed": 0}
    tried: list[int] = []                       # each event at most once per call
    for _ in range(limit):
        with conn.transaction():
            row = conn.execute("""SELECT id, event_id, kind, payload, attempts FROM outbox
                                  WHERE delivered_at IS NULL AND next_attempt_at <= now() AND id <> ALL(%s)
                                  ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""", (tried,)).fetchone()
            if row is None:
                break
            tried.append(row["id"])
            try:
                r = httpx.post(url, json={"event_id": row["event_id"], "kind": row["kind"], **row["payload"]},
                               headers={"Idempotency-Key": row["event_id"]}, timeout=timeout)
                error = None if 200 <= r.status_code < 300 else f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                error = exc.__class__.__name__
            if error is None:
                conn.execute("UPDATE outbox SET delivered_at = now(), attempts = attempts + 1, last_error = NULL "
                             "WHERE id = %s", (row["id"],))
                counts["delivered"] += 1
            else:
                conn.execute("""UPDATE outbox SET attempts = attempts + 1, last_error = %s,
                                       next_attempt_at = now() + make_interval(secs => %s) WHERE id = %s""",
                             (error, backoff(row["attempts"], backoff_base), row["id"]))
                counts["failed"] += 1
    return counts


def pending(conn) -> int:
    return conn.execute("SELECT count(*) AS n FROM outbox WHERE delivered_at IS NULL").fetchone()["n"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--until-empty", action="store_true", help="keep going until every event is delivered")
    ap.add_argument("--max-seconds", type=float, default=120)
    ap.add_argument("--backoff-base", type=float, default=0.5)
    a = ap.parse_args(argv)
    start = time.monotonic()
    with psycopg.connect(DELIVERY_DSN, row_factory=dict_row, autocommit=True) as conn:
        while True:
            counts = deliver(conn, a.url, backoff_base=a.backoff_base)
            left = pending(conn)
            if counts["delivered"] or counts["failed"] or left == 0:
                print(f"delivered {counts['delivered']}, failed {counts['failed']}, still pending {left}", flush=True)
            if not a.until_empty or left == 0 or time.monotonic() - start > a.max_seconds:
                return 0 if left == 0 else 1
            time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
