"""The only HTTP surface the AI side can reach (Level 2F, D30).

Runs as its own service on both Docker networks. The main API is not on the AI network,
so the scribe agent cannot reach any staff route; it can only reach this app:

    POST /ai/shops/{shop}/tasks/{task}/result   the task token's one use (scribe.accept_agent_result)
    POST /dispatch                              the main API hands a task to the agent through here
    GET  /health

/dispatch only forwards to the configured agent, so an agent calling it gains nothing.
No staff routes, no staff authentication, no records, no approvals.

    uvicorn safecounter.ai_ingress:app
"""

from typing import Any

from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from . import config, rules as rules_mod, scribe
from .gate import GateError

app = FastAPI(title="Safe Counter AI ingress")


@app.exception_handler(GateError)
def gate_error(_: Request, exc: GateError):
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.detail, **exc.extra})


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ai/shops/{shop_id}/tasks/{task_id}/result")
def ai_task_result(shop_id: str, task_id: str, result: Any = Body(...),
                   authorization: str | None = Header(default=None)):
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else ""
    return scribe.accept_agent_result(rules_mod.load(), shop_id, task_id, token, result)


@app.post("/dispatch")
def dispatch(envelope: Any = Body(...)):
    """Relay a task from the main API to the scribe agent (bounded, 2 s)."""
    try:
        status, _, _, _ = scribe._post(config.INGRESS_AGENT_URL, envelope, limit=2.0)
    except scribe.AIError as exc:
        return JSONResponse(status_code=502, content={"error": "agent_unreachable", "detail": exc.detail})
    return JSONResponse(status_code=202 if status == 202 else 502, content={"agent_status": status})
