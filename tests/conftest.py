import json

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from safecounter import admin, config
from safecounter.api import app

NOTES = {n["id"]: n for n in json.loads(config.NOTES_PATH.read_text())["notes"]}


@pytest.fixture(scope="session", autouse=True)
def migrated():
    with admin.connect() as conn:
        admin.migrate(conn)


@pytest.fixture(autouse=True)
def fresh_db(migrated):
    with admin.connect() as conn:
        admin.reset(conn)


@pytest.fixture
def client():
    return TestClient(app)


def H(user: str, mode: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer tok_{user}"}
    if mode:
        headers["X-AI-Mode"] = mode
    return headers


def note_body(nid: str) -> dict:
    n = NOTES[nid]
    return {k: n[k] for k in ("id", "text", "written_at", "scope", "source")}


def post_note(client, nid: str, mode: str | None = None):
    n = NOTES[nid]
    return client.post(f"/shops/{n['shop_id']}/notes", json=note_body(nid), headers=H(n["author_id"][2:], mode))


def owner_query(query: str, params=(), fetch=True):
    with psycopg.connect(config.OWNER_DSN, row_factory=dict_row) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if fetch and cur.description else None


def app_conn():
    return psycopg.connect(config.APP_DSN, row_factory=dict_row)


def record(shop: str, rid: str, user: str = "femi") -> dict:
    return TestClient(app).get(f"/shops/{shop}/records/{rid}", headers=H(user)).json()
