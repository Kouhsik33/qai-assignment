"""Level 2F container boundary (D30): the agent reaches only the AI ingress, never the main API.

The ingress app is started for real (uvicorn) together with a real scribe agent; the main
API is driven in-process, as in production it is on a network the agent is not on.
"""

import socket
import threading
import time

import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient

from conftest import H, owner_query, post_note
from safecounter import ai_ingress, config, scribe_agent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def ingress_and_agent():
    ingress_port, agent_port = free_port(), free_port()
    server = uvicorn.Server(uvicorn.Config(ai_ingress.app, host="127.0.0.1", port=ingress_port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    agent = scribe_agent.serve("127.0.0.1", agent_port)
    threading.Thread(target=agent.serve_forever, daemon=True).start()
    yield ingress_port, agent_port
    server.should_exit = True
    agent.shutdown()


def test_ingress_exposes_only_the_ai_routes():
    paths = {(r.path, tuple(sorted(r.methods))) for r in ai_ingress.app.routes if hasattr(r, "methods")}
    functional = {p for p in paths if not p[0].startswith(("/docs", "/openapi", "/redoc"))}
    assert functional == {("/health", ("GET",)), ("/dispatch", ("POST",)),
                          ("/ai/shops/{shop_id}/tasks/{task_id}/result", ("POST",))}


@pytest.mark.parametrize("method,path", [
    ("GET", "/shops/shop_millbrook/records/r_m5004"),
    ("GET", "/shops/shop_millbrook/proposals"),
    ("POST", "/shops/shop_millbrook/proposals/1/approve"),
    ("POST", "/shops/shop_millbrook/changes"),
    ("POST", "/shops/shop_millbrook/notes"),
    ("GET", "/shops/shop_millbrook/audit"),
])
def test_staff_routes_do_not_exist_on_the_ingress(method, path):
    r = TestClient(ai_ingress.app).request(method, path, headers=H("chloe"), json={"reason": "x"})
    assert r.status_code in (404, 405)                     # a valid staff token is useless here


def test_note_flows_through_the_ingress_and_agent(client, ingress_and_agent, monkeypatch):
    ingress_port, agent_port = ingress_and_agent
    monkeypatch.setattr(config, "AI_TRANSPORT", "agent")
    monkeypatch.setattr(config, "SCRIBE_AGENT_URL", f"http://127.0.0.1:{ingress_port}/dispatch")
    monkeypatch.setattr(config, "CALLBACK_BASE_URL", f"http://127.0.0.1:{ingress_port}")
    monkeypatch.setattr(config, "INGRESS_AGENT_URL", f"http://127.0.0.1:{agent_port}/tasks")
    r = post_note(client, "N01")
    assert r.status_code == 200, r.text
    assert [p["status"] for p in r.json()["proposals"]] == ["pending"] * 3
    assert owner_query("SELECT count(token_sha256) AS n FROM ai_tasks")[0]["n"] == 1


def test_dispatch_reports_an_unreachable_agent(monkeypatch):
    monkeypatch.setattr(config, "INGRESS_AGENT_URL", f"http://127.0.0.1:{free_port()}/tasks")
    r = TestClient(ai_ingress.app).post("/dispatch", json={"task": {}, "token": "t", "result_url": "x"})
    assert r.status_code == 502


def test_compose_keeps_the_agent_unmounted_and_away_from_the_api():
    svc = yaml.safe_load((config.ROOT / "docker-compose.yml").read_text())["services"]
    assert "volumes" not in svc["scribe_agent"] and "volumes" not in svc["ai_ingress"]
    assert "ports" not in svc["ai_ingress"] and "ports" not in svc["scribe_agent"]
    assert svc["scribe_agent"]["networks"] == ["ai"]
    assert svc["api"]["networks"] == ["backend"] and svc["api_real"]["networks"] == ["backend"]
    assert sorted(svc["ai_ingress"]["networks"]) == ["ai", "backend"]
    assert svc["api"]["environment"]["SCRIBE_AGENT_URL"] == "http://ai_ingress:8000/dispatch"
    assert svc["api"]["environment"]["CALLBACK_BASE_URL"] == "http://ai_ingress:8000"
    assert svc["ai_ingress"]["environment"]["INGRESS_AGENT_URL"] == "http://scribe_agent:8097/tasks"
    assert not any("DATABASE" in k or "GROQ" in k for k in svc["scribe_agent"]["environment"])


def test_dockerignore_keeps_secrets_and_compose_out_of_images():
    ignored = {line.strip() for line in (config.ROOT / ".dockerignore").read_text().splitlines()}
    assert {".env", ".env.*", "docker-compose.yml"} <= ignored
