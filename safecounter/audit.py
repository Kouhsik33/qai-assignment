"""Hash-chained audit log, one chain per shop.

row_hash = sha256(canonical JSON of the row's content INCLUDING prev_hash).
prev_hash of row n = row_hash of row n-1 (or GENESIS for the first row).

Every hashed column is stored as text or bigint and hashed exactly as stored, so a
round trip through Postgres cannot change the bytes being hashed.

What this protects: any edit, insert or delete in the middle of the chain is
detected by `verify`, and a truncated tail is detected against audit_head.
What it does NOT protect: someone with write access to both tables can recompute
every hash from the edited row onward. Stopping that needs the head hash anchored
outside the database (see docs/design.md).
"""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

GENESIS = "0" * 64

HASHED = ["shop_id", "seq", "occurred_at", "actor_id", "action", "entity", "record_id", "field",
          "old_value", "new_value", "proposal_id", "detail", "rules_sha256", "prev_hash"]


def to_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_hash(row: dict) -> str:
    payload = json.dumps([row[c] for c in HASHED], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append(cur, *, shop_id, time_zone, actor_id, action, entity, record_id, field,
           old_value, new_value, proposal_id, detail: dict, rules_sha256) -> dict:
    """Append one row. Must run inside the caller's transaction (same tx as the change, R7)."""
    # Lock the chain tip: concurrent appends for this shop queue here, so the chain never forks.
    cur.execute("SELECT last_seq, last_hash FROM audit_head WHERE shop_id = %s FOR UPDATE", (shop_id,))
    head = cur.fetchone()
    if head is None:
        raise RuntimeError(f"no audit head for shop {shop_id}")
    row = {
        "shop_id": shop_id,
        "seq": head["last_seq"] + 1,
        "occurred_at": datetime.now(ZoneInfo(time_zone)).isoformat(timespec="microseconds"),
        "actor_id": actor_id,
        "action": action,
        "entity": entity,
        "record_id": record_id,
        "field": field,
        "old_value": to_json(old_value),
        "new_value": to_json(new_value),
        "proposal_id": proposal_id,
        "detail": to_json(detail),
        "rules_sha256": rules_sha256,
        "prev_hash": head["last_hash"],
    }
    row["row_hash"] = row_hash(row)
    cols = HASHED + ["row_hash"]
    cur.execute(
        f"INSERT INTO audit_log ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        [row[c] for c in cols],
    )
    cur.execute("UPDATE audit_head SET last_seq = %s, last_hash = %s WHERE shop_id = %s",
                (row["seq"], row["row_hash"], shop_id))
    return row


def verify(cur, shop_id: str) -> dict:
    """Walk the shop's chain from the start and report the first broken row, if any."""
    cur.execute(f"SELECT {', '.join(HASHED)}, row_hash FROM audit_log WHERE shop_id = %s ORDER BY seq", (shop_id,))
    rows = cur.fetchall()
    expected_prev, expected_seq = GENESIS, 1
    for row in rows:
        problem = None
        if row["seq"] != expected_seq:
            problem = f"sequence gap: expected seq {expected_seq}, found {row['seq']} (row missing or inserted)"
        elif row["prev_hash"] != expected_prev:
            problem = "prev_hash does not match the previous row's hash (row before it changed, removed or inserted)"
        elif row_hash(row) != row["row_hash"]:
            problem = "row_hash does not match the row's content (this row was edited)"
        if problem:
            return {"shop_id": shop_id, "ok": False, "rows": len(rows),
                    "first_broken": {"seq": row["seq"], "problem": problem}}
        expected_prev, expected_seq = row["row_hash"], expected_seq + 1

    cur.execute("SELECT last_seq, last_hash FROM audit_head WHERE shop_id = %s", (shop_id,))
    head = cur.fetchone()
    if head and (head["last_seq"] != len(rows) or head["last_hash"] != expected_prev):
        return {"shop_id": shop_id, "ok": False, "rows": len(rows),
                "first_broken": {"seq": len(rows) + 1,
                                 "problem": f"chain ends at seq {len(rows)} but head says {head['last_seq']} (tail removed?)"}}
    return {"shop_id": shop_id, "ok": True, "rows": len(rows), "first_broken": None}
