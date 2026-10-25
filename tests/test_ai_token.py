"""Level 2F: the AI as a separate process holding one narrow, proposal-only token (D27).

A real uvicorn server (for the callback) and a real scribe agent run in this process on
local ports. The agent's received tokens are recorded so the tests can try to misuse them.
"""

import hashlib
import socket
import threading
import time

import httpx
import pytest
import uvicorn
import yaml

from conftest import H, owner_query, post_note, record
from safecounter import config, scribe_agent
from safecounter.api import app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servers():
    api_port, agent_port = free_port(), free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    agent = scribe_agent.serve("127.0.0.1", agent_port)
    threading.Thread(target=agent.serve_forever, daemon=True).start()
    yield api_port, agent_port
    server.should_exit = True
    agent.shutdown()


@pytest.fixture
def agent(servers, monkeypatch):
    """Agent transport on. `hold=True` makes the agent receive the task but not answer."""
    api_port, agent_port = servers
    monkeypatch.setattr(config, "AI_TRANSPORT", "agent")
    monkeypatch.setattr(config, "SCRIBE_AGENT_URL", f"http://127.0.0.1:{agent_port}/tasks")
    monkeypatch.setattr(config, "CALLBACK_BASE_URL", f"http://127.0.0.1:{api_port}")
    state = {"seen": [], "hold": False}
    original = scribe_agent.work

    def recording(envelope):
        state["seen"].append(envelope)
        if not state["hold"]:
            original(envelope)
    monkeypatch.setattr(scribe_agent, "work", recording)
    return state


def submit(envelope, body, token=None, url=None):
    """token=None: the task's own token; token="": no Authorization header at all."""
    tok = envelope["token"] if token is None else token
    return httpx.post(url or envelope["result_url"], json=body, timeout=5,
                      headers={"Authorization": f"Bearer {tok}"} if tok else {})


def empty_reply(envelope, proposals=()):
    return {"reply": {"task_id": envelope["task"]["task_id"], "proposals": list(proposals),
                      "abstentions": [], "flags": []}}


def in_background(client, nid, mode=None):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", post_note(client, nid, mode)))
    t.start()
    return t, out


def wait_for_task(state, n=1):
    for _ in range(100):
        if len(state["seen"]) >= n:
            return state["seen"][n - 1]
        time.sleep(0.05)
    raise AssertionError("agent never received the task")


def test_note_flows_through_the_task_token(client, agent):
    r = post_note(client, "N01")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transport"] == "agent"
    assert [(p["field"], p["status"]) for p in body["proposals"]] == [
        ("rental.returned_at", "pending"), ("rental.return_condition", "pending"),
        ("rental.deposit_refund_pence", "pending")]
    token = agent["seen"][0]["token"]
    task = owner_query("SELECT token_sha256, status FROM ai_tasks")[0]
    assert task == {"token_sha256": hashlib.sha256(token.encode()).hexdigest(), "status": "succeeded"}
    dump = owner_query("SELECT string_agg(t::text, ' ') AS d FROM ai_tasks t")[0]["d"]
    assert token not in dump                                        # only the hash is stored
    assert record("shop_millbrook", "r_m5004")["returned_at"] is None


def test_token_is_single_use(client, agent):
    post_note(client, "N01")
    env = agent["seen"][0]
    r = submit(env, empty_reply(env))
    assert r.status_code == 409 and r.json()["error"] == "task_closed"
    assert owner_query("SELECT count(*) AS n FROM proposals")[0]["n"] == 3


def test_token_grants_nothing_else(client, agent):
    post_note(client, "N01")
    tok = {"Authorization": f"Bearer {agent['seen'][0]['token']}"}
    m = "/shops/shop_millbrook"
    assert client.get(f"{m}/records/r_m5004", headers=tok).status_code == 401
    assert client.get(f"{m}/proposals", headers=tok).status_code == 401
    assert client.post(f"{m}/proposals/1/approve", headers=tok, json={"reason": "ai"}).status_code == 401
    assert client.post(f"{m}/changes", headers=tok, json={"record_id": "r_m5004", "field": "rental.hire_days",
                                                          "value": 3, "reason": "ai"}).status_code == 401
    assert client.get(f"{m}/audit", headers=tok).status_code == 401


def test_token_is_bound_to_its_task_and_shop(client, agent):
    agent["hold"] = True
    t, out = in_background(client, "N01")
    env = wait_for_task(agent)
    base = env["result_url"].rsplit("/ai/", 1)[0]
    task_id = env["task"]["task_id"]
    assert submit(env, empty_reply(env), url=f"{base}/ai/shops/shop_millbrook/tasks/task_other/result").status_code == 401
    assert submit(env, empty_reply(env), url=f"{base}/ai/shops/shop_eastfield/tasks/{task_id}/result").status_code == 401
    assert submit(env, empty_reply(env), token="").status_code == 401
    assert submit(env, empty_reply(env), token="guessed-token").status_code == 401
    assert submit(env, empty_reply(env), token=H("chloe")["Authorization"][7:]).status_code == 401   # staff token
    assert submit(env, empty_reply(env)).json() == {"accepted": True}
    t.join()
    assert out["r"].status_code == 200


def test_hostile_reply_with_a_valid_token_is_still_blocked(client, agent):
    agent["hold"] = True
    t, out = in_background(client, "N01")
    env = wait_for_task(agent)
    base = {"record_id": "r_m5004", "field": "rental.return_condition", "value": "good", "unit": "enum",
            "confidence": 0.9, "evidence": "no damage", "sensitivity": "normal", "reason": "r"}
    hostile = [base | {"approved_by": "u_chloe", "status": "approved"},
               base | {"record_id": "r_e7001"},
               base | {"field": "customer.id_check_ref", "record_id": "c_m1", "unit": "text", "value": "X"}]
    assert submit(env, empty_reply(env, hostile)).json() == {"accepted": True}
    t.join()
    props = out["r"].json()["proposals"]
    assert [p["status"] for p in props] == ["blocked"] * 3
    assert owner_query("SELECT count(*) AS n FROM audit_log")[0]["n"] == 0


def test_wrong_shape_submitted_with_token_is_refused(client, agent):
    agent["hold"] = True
    t, out = in_background(client, "N01")
    env = wait_for_task(agent)
    assert submit(env, ["not", "an", "object"]).status_code == 422          # task stays open
    r = submit(env, {"reply": {"task_id": env["task"]["task_id"], "approved": True}})
    assert r.json() == {"accepted": False, "error": "ai_wrong_schema"}
    t.join()
    assert out["r"].status_code == 502 and out["r"].json()["error"] == "ai_wrong_schema"


@pytest.mark.parametrize("mode,status,code", [("hang", 504, "ai_timeout"), ("garbage", 502, "ai_invalid_json"),
                                              ("error", 502, "ai_bad_status")])
def test_ai_failure_reported_by_the_agent(client, agent, mode, status, code):
    start = time.monotonic()
    r = post_note(client, "N01", mode)
    assert r.status_code == status and r.json()["error"] == code, r.text
    assert time.monotonic() - start < config.AI_DEADLINE_SECONDS + config.AGENT_MARGIN_SECONDS + 1.5
    assert owner_query("SELECT count(*) AS n FROM proposals")[0]["n"] == 0


def test_late_result_after_the_deadline_is_refused(client, agent):
    agent["hold"] = True
    r = post_note(client, "N01")                                           # the agent never answers
    assert r.status_code == 504 and r.json()["error"] == "ai_timeout"
    env = agent["seen"][0]
    late = submit(env, empty_reply(env))
    assert late.status_code == 409 and late.json()["error"] == "task_closed"
    assert owner_query("SELECT count(*) AS n FROM proposals")[0]["n"] == 0


def test_agent_down(client, agent, monkeypatch):
    monkeypatch.setattr(config, "SCRIBE_AGENT_URL", f"http://127.0.0.1:{free_port()}/tasks")
    start = time.monotonic()
    r = post_note(client, "N01")
    assert r.status_code == 502 and r.json()["error"] == "ai_unavailable" and time.monotonic() - start < 3


def test_compose_gives_the_agent_no_database_access():
    doc = yaml.safe_load((config.ROOT / "docker-compose.yml").read_text())
    svc = doc["services"]
    agent_env = " ".join(f"{k}={v}" for k, v in svc["scribe_agent"]["environment"].items())
    assert "postgresql://" not in agent_env and "DATABASE" not in agent_env and "GROQ" not in agent_env
    assert svc["scribe_agent"]["networks"] == ["ai"] and svc["mock_ai"]["networks"] == ["ai"]
    assert svc["db"]["networks"] == ["backend"]
    assert svc["api"]["environment"]["AI_TRANSPORT"] == "agent"
