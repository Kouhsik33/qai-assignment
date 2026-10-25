"""Groq adapter, offline (D23). httpx.MockTransport stands in for the network.

These prove the real-model path is held to the same contract as the mock: same
response checks, same router, nothing stored on failure, and no secret recorded.
"""

import json

import httpx
import pytest

from conftest import H, owner_query, post_note, record
from safecounter import config, scribe

FAKE_KEY = "gsk_test_not_a_real_key_0123456789"


@pytest.fixture
def groq(monkeypatch):
    """Switch to the Groq provider with a fake key; yields a list that captures requests."""
    monkeypatch.setattr(config, "AI_PROVIDER", "groq")
    monkeypatch.setattr(config, "GROQ_API_KEY", FAKE_KEY)
    monkeypatch.setattr(config, "GROQ_DEADLINE_SECONDS", 2.0)
    seen = []

    def install(handler):
        def wrapped(request: httpx.Request):
            seen.append(request)
            return handler(request)
        monkeypatch.setattr(scribe, "_transport", httpx.MockTransport(wrapped))
    yield install, seen


def envelope(content, finish="stop"):
    return {"id": "chatcmpl-x", "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020}}


def reply_for(request: httpx.Request, proposals, **top):
    """Echo the task id the server sent, like a well-behaved model."""
    user = json.loads(request.content)["messages"][1]["content"]
    task_id = json.loads(user.split("Task (JSON):\n", 1)[1].split("\n\nThe note", 1)[0])["task_id"]
    body = {"task_id": task_id, "proposals": proposals, "abstentions": [], "flags": [], **top}
    return httpx.Response(200, json=envelope(json.dumps(body)), headers={"x-request-id": "req_123"})


RETURN = {"record_id": "r_m5004", "field": "rental.returned_at", "value": "2026-10-23T16:40:00+01:00",
          "unit": "iso8601", "confidence": 0.9, "evidence": "brought the pressure washer back at 16:40",
          "sensitivity": "normal", "reason": "stated"}


def test_mock_is_the_default_provider():
    assert config.AI_PROVIDER == "mock"           # tools container sets no AI_PROVIDER


def test_success_goes_through_the_same_router(client, groq):
    install, seen = groq
    install(lambda req: reply_for(req, [RETURN]))
    r = post_note(client, "N01")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "groq" and body["model"] == "openai/gpt-oss-120b"
    assert [p["status"] for p in body["proposals"]] == ["pending"]
    task = owner_query("SELECT provider, model, ai_meta, status FROM ai_tasks")[0]
    assert (task["provider"], task["model"], task["status"]) == ("groq", "openai/gpt-oss-120b", "succeeded")
    assert task["ai_meta"]["usage"]["total_tokens"] == 1020 and task["ai_meta"]["request_id"] == "req_123"
    assert record("shop_millbrook", "r_m5004")["returned_at"] is None     # proposed, not applied


def test_request_shape(client, groq):
    install, seen = groq
    install(lambda req: reply_for(req, []))
    post_note(client, "N06")
    req = seen[0]
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    body = json.loads(req.content)
    assert body["model"] == "openai/gpt-oss-120b" and body["temperature"] == 0
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["properties"].keys() == {"task_id", "proposals", "abstentions", "flags"}
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    assert "## Examples" in system                                     # few-shot is the default variant
    assert "<untrusted_note>" in user and "07700 900482" in user
    assert "IDC-EF-0042" not in user and "gareth.price" not in user    # same minimal data as the mock gets


def test_zero_shot_variant_differs_only_by_examples(client, groq, monkeypatch):
    few, _ = scribe.load_prompt("few")
    zero, _ = scribe.load_prompt("zero")
    assert "## Examples" not in zero and few.startswith(zero.rstrip())
    install, seen = groq
    monkeypatch.setattr(config, "AI_PROMPT_VARIANT", "zero")
    install(lambda req: reply_for(req, []))
    post_note(client, "N01")
    assert json.loads(seen[0].content)["messages"][0]["content"] == zero
    assert owner_query("SELECT prompt_version FROM ai_tasks")[0]["prompt_version"] == "scribe_v1:zero"


def test_hostile_real_model_output_is_blocked_like_the_mock(client, groq):
    install, _ = groq
    evil = [RETURN | {"approved_by": "u_chloe"},
            RETURN | {"record_id": "r_e7001"},
            RETURN | {"field": "customer.id_check_ref", "record_id": "c_m1", "unit": "text", "value": "X"},
            {**RETURN, "field": "rental.deposit_refund_pence", "unit": "pence", "value": 30000,
             "evidence": "Give her the full deposit back", "sensitivity": "normal"}]
    install(lambda req: reply_for(req, evil))
    props = post_note(client, "N01").json()["proposals"]
    assert [p["status"] for p in props] == ["blocked"] * 4
    assert [p["block_reasons"][0].split(":")[0] for p in props] == \
        ["unexpected_keys", "out_of_scope", "out_of_scope", "out_of_range"]


@pytest.mark.parametrize("response,status,code", [
    (httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "7"}), 503, "ai_rate_limited"),
    (httpx.Response(401, json={"error": {"message": "Invalid API Key"}}), 502, "ai_auth_failed"),
    (httpx.Response(500, text="upstream exploded"), 502, "ai_bad_status"),
    (httpx.Response(400, json={"error": {"code": "json_validate_failed", "message": "schema"}}), 502, "ai_wrong_schema"),
    (httpx.Response(200, text="<html>not json</html>"), 502, "ai_invalid_json"),
    (httpx.Response(200, json={"unexpected": True}), 502, "ai_wrong_schema"),
    (httpx.Response(200, json=envelope("Sure! Here are the changes...")), 502, "ai_invalid_json"),
    (httpx.Response(200, json=envelope('{"task_id": "t", "propo', finish="length")), 502, "ai_truncated"),
    (httpx.Response(200, json=envelope(None)), 502, "ai_invalid_json"),
    (httpx.Response(200, json=envelope(json.dumps({"task_id": "x", "proposals": []}))), 502, "ai_wrong_schema"),
])
def test_provider_failures_store_nothing(client, groq, response, status, code):
    install, _ = groq
    install(lambda req: response)
    before = record("shop_millbrook", "r_m5004")
    r = post_note(client, "N01")
    assert r.status_code == status and r.json()["error"] == code, r.text
    assert "No proposals were stored" in r.json()["detail"]
    assert owner_query("SELECT count(*) AS n FROM proposals")[0]["n"] == 0
    assert record("shop_millbrook", "r_m5004") == before
    if code == "ai_rate_limited":
        assert r.json()["retry_after_s"] == 7.0


def test_wrong_task_id_is_refused(client, groq):
    install, _ = groq
    body = {"task_id": "task_from_another_request", "proposals": [RETURN], "abstentions": [], "flags": []}
    install(lambda req: httpx.Response(200, json=envelope(json.dumps(body))))
    r = post_note(client, "N01")
    assert r.status_code == 502 and r.json()["error"] == "ai_task_mismatch"


@pytest.mark.parametrize("exc,code", [
    (httpx.ReadTimeout("slow"), "ai_timeout"),
    (httpx.ConnectError("refused"), "ai_unavailable"),
    (httpx.RemoteProtocolError("dropped"), "ai_connection_dropped"),
])
def test_network_failures(client, groq, exc, code):
    install, _ = groq

    def boom(req):
        raise exc
    install(boom)
    r = post_note(client, "N01")
    assert r.json()["error"] == code


def test_missing_key_makes_no_call(client, groq, monkeypatch):
    install, seen = groq
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    install(lambda req: reply_for(req, []))
    r = post_note(client, "N01")
    assert r.status_code == 503 and r.json()["error"] == "ai_not_configured" and seen == []


def test_mock_modes_are_not_sent_to_groq(client, groq):
    install, seen = groq
    install(lambda req: reply_for(req, []))
    assert post_note(client, "N01", mode="hostile_output").status_code == 200
    assert "mode" not in str(seen[0].url)


def test_key_never_stored_or_returned(client, groq):
    install, _ = groq
    install(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}}))
    r = post_note(client, "N01")
    assert FAKE_KEY not in r.text
    install(lambda req: reply_for(req, [RETURN]))
    r = post_note(client, "N01")
    assert FAKE_KEY not in r.text
    dump = owner_query("SELECT string_agg(t::text, ' ') AS all FROM ai_tasks t")[0]["all"]
    dump += owner_query("SELECT coalesce(string_agg(p::text, ' '), '') AS all FROM proposals p")[0]["all"]
    assert FAKE_KEY not in dump
