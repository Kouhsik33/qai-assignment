"""The scribe agent: the AI side of the system as its own process (Level 2F, D27).

It holds no database credentials and runs on a Docker network without Postgres. For each
task the API sends it the task data and ONE token. The agent calls the model (the mock,
via scribe.call_ai) and submits the reply to the single endpoint that token works on:

    POST {result_url}   Authorization: Bearer <task token>
         {"reply": <model output>, "elapsed_ms", "meta"}   or   {"error": {"kind", "detail"}}

The token is single-use, bound to one task in one shop, expires at the deadline, and is
refused by every other endpoint. The API checks the reply exactly as if it had called the
model itself. The token is never logged.

    python -m safecounter.scribe_agent          # listens on SCRIBE_AGENT_HOST:SCRIBE_AGENT_PORT
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from . import scribe


def work(envelope: dict):
    meta: dict = {}
    try:
        reply, _, elapsed = scribe.call_ai(envelope["task"], envelope.get("mode"), meta)
        body = {"reply": reply, "elapsed_ms": elapsed, "meta": meta}
    except scribe.AIError as exc:
        body = {"error": {"kind": exc.kind, "detail": exc.detail, "retry_after": exc.retry_after},
                "elapsed_ms": exc.elapsed_ms, "meta": meta}
    try:
        r = httpx.post(envelope["result_url"], json=body, timeout=10,
                       headers={"Authorization": f"Bearer {envelope['token']}"})
        sys.stderr.write(f"[agent] task {envelope['task'].get('task_id')} -> HTTP {r.status_code}\n")
    except httpx.HTTPError as exc:
        sys.stderr.write(f"[agent] task {envelope['task'].get('task_id')}: could not submit ({exc.__class__.__name__})\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass                                   # request lines are not logged (no token in them, but keep it quiet)

    def _send(self, status: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/tasks":
            return self._send(404, {"error": "not found"})
        try:
            env = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            ok = (isinstance(env, dict) and isinstance(env.get("task"), dict)
                  and isinstance(env.get("token"), str) and isinstance(env.get("result_url"), str))
        except ValueError:
            ok = False
        if not ok:
            return self._send(400, {"error": "expected task, token and result_url"})
        self._send(202, {"accepted": True})
        threading.Thread(target=work, args=(env,), daemon=True).start()


def serve(host: str, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main():
    host = os.environ.get("SCRIBE_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("SCRIBE_AGENT_PORT", "8097"))
    print(f"scribe agent on http://{host}:{port}/tasks (provider: {scribe.config.AI_PROVIDER})", flush=True)
    serve(host, port).serve_forever()


if __name__ == "__main__":
    main()
