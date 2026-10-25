"""Owner-only maintenance: migrations, seed, reset, audit verify, and the tamper demo.

Runs with OWNER_DATABASE_URL. The API process never has these credentials.

    python -m safecounter.admin init     # migrate, then seed if empty
    python -m safecounter.admin reset    # wipe all data and reseed
    python -m safecounter.admin verify   # verify every shop's audit chain
    python -m safecounter.admin tamper SHOP SEQ   # edit one audit row (demo)
"""

import hashlib
import json
import sys

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from . import audit, config

MIGRATIONS = config.ROOT / "db" / "migrations"
ALL_TABLES = ["outbox", "audit_log", "audit_head", "abstentions", "proposals", "ai_tasks", "notes",
              "rentals", "items", "customers", "memberships", "staff", "shops"]


def token_for(staff_id: str) -> str:
    """Demo bearer token per seeded user: u_amira -> tok_amira."""
    return "tok_" + staff_id.removeprefix("u_")


def connect():
    if not config.OWNER_DSN:
        raise SystemExit("OWNER_DATABASE_URL is not set")
    return psycopg.connect(config.OWNER_DSN, row_factory=dict_row, autocommit=False)


def migrate(conn):
    if not config.APP_DB_PASSWORD or not config.DELIVERY_DB_PASSWORD:
        raise SystemExit("APP_DB_PASSWORD and DELIVERY_DB_PASSWORD must be set")
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        cur.execute("SELECT name FROM schema_migrations")
        done = {r["name"] for r in cur.fetchall()}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name not in done:
                cur.execute(path.read_text())
                cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                print(f"applied {path.name}")
        cur.execute(sql.SQL("ALTER ROLE sc_app PASSWORD {}").format(sql.Literal(config.APP_DB_PASSWORD)))
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'sc_delivery'")
        if cur.fetchone():
            cur.execute(sql.SQL("ALTER ROLE sc_delivery PASSWORD {}").format(sql.Literal(config.DELIVERY_DB_PASSWORD)))
    conn.commit()


def seed(conn):
    data = json.loads(config.SEED_PATH.read_text())
    with conn.cursor() as cur:
        for s in data["shops"]:
            cur.execute("INSERT INTO shops (id, name, time_zone) VALUES (%s, %s, %s)", (s["id"], s["name"], s["time_zone"]))
            cur.execute("INSERT INTO audit_head (shop_id, last_seq, last_hash) VALUES (%s, 0, %s)", (s["id"], audit.GENESIS))
        for s in data["staff"]:
            digest = hashlib.sha256(token_for(s["id"]).encode()).hexdigest()
            cur.execute("INSERT INTO staff (id, name, token_sha256) VALUES (%s, %s, %s)", (s["id"], s["name"], digest))
        for m in data["memberships"]:
            cur.execute("INSERT INTO memberships (staff_id, shop_id, role) VALUES (%s, %s, %s)", (m["staff_id"], m["shop_id"], m["role"]))
        for c in data["customers"]:
            cur.execute("INSERT INTO customers (id, shop_id, name, phone, email, address, id_check_ref) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (c["id"], c["shop_id"], c["name"], c["phone"], c["email"], c["address"], c["id_check_ref"]))
        for i in data["items"]:
            cur.execute("INSERT INTO items (id, shop_id, asset_tag, name, status, service_note) VALUES (%s,%s,%s,%s,%s,%s)",
                        (i["id"], i["shop_id"], i["asset_tag"], i["name"], i["status"], i["service_note"]))
        for r in data["rentals"]:
            cur.execute("""INSERT INTO rentals (id, shop_id, customer_id, item_id, hired_at, hire_days, daily_rate_pence,
                               deposit_held_pence, returned_at, return_condition, damage_charge_pence, deposit_refund_pence, closed)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (r["id"], r["shop_id"], r["customer_id"], r["item_id"], r["hired_at"], r["hire_days"],
                         r["daily_rate_pence"], r["deposit_held_pence"], r["returned_at"], r["return_condition"],
                         r["damage_charge_pence"], r["deposit_refund_pence"], bool(r.get("_closed", False))))
    conn.commit()
    print("seeded", data["_summary"])


def reset(conn):
    with conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(ALL_TABLES) + " RESTART IDENTITY CASCADE")
    conn.commit()
    seed(conn)


def init(conn):
    migrate(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM shops")
        empty = cur.fetchone()["n"] == 0
    if empty:
        seed(conn)
    else:
        print("data present; not reseeding")


def verify_all(conn) -> bool:
    ok = True
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM shops ORDER BY id")
        for row in cur.fetchall():
            result = audit.verify(cur, row["id"])
            print(json.dumps(result))
            ok = ok and result["ok"]
    return ok


def tamper(conn, shop_id: str, seq: int):
    """Demo only: change one audit row as the database owner, bypassing the append-only trigger."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL session_replication_role = replica")  # disables triggers; needs superuser
        cur.execute("UPDATE audit_log SET new_value = '\"tampered\"' WHERE shop_id = %s AND seq = %s", (shop_id, seq))
        print(f"tampered {cur.rowcount} row(s): {shop_id} seq {seq}")
    conn.commit()


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "init"
    with connect() as conn:
        if cmd == "init":
            init(conn)
        elif cmd == "migrate":
            migrate(conn)
        elif cmd == "seed":
            seed(conn)
        elif cmd == "reset":
            migrate(conn)
            reset(conn)
        elif cmd == "verify":
            return 0 if verify_all(conn) else 1
        elif cmd == "tamper":
            tamper(conn, argv[2], int(argv[3]))
        else:
            print(__doc__)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
