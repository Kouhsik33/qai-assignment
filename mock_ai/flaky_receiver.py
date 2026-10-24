#!/usr/bin/env python3
"""
Safe Counter: flaky event receiver (optional, for Level 3 event delivery).

Python standard library only.

    python3 flaky_receiver.py                 # http://127.0.0.1:8098
    python3 flaky_receiver.py --fail-rate 0.3 --slow-rate 0.1 --port 8098

POST /events      Accepts one event (JSON). Behaves badly on purpose:
                  - fails with HTTP 500 about --fail-rate of the time
                  - sometimes waits 5 to 15 s before answering (--slow-rate)
                  - sometimes stores the event but then returns 500, so a
                    correct sender retries and the receiver sees a duplicate
GET  /events      What was received: every delivery, per idempotency key,
                  and which keys arrived more than once.
POST /reset       Clears the log.

Send an idempotency key in the Idempotency-Key header, or as "event_id" in
the body. Stop and start this process to simulate the receiver being down.
"""

import argparse
import json
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
LOG = []          # every delivery attempt that reached the store
SEEN = {}         # idempotency key -> count


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cfg = None

    def log_message(self, fmt, *args):
        sys.stderr.write("[receiver] %s\n" % (fmt % args))

    def _send(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/events"):
            with LOCK:
                dups = {k: v for k, v in SEEN.items() if v > 1}
                return self._send(200, {"received": len(LOG), "unique_keys": len(SEEN),
                                        "duplicate_keys": dups, "log": LOG[-200:]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path.startswith("/reset"):
            with LOCK:
                LOG.clear()
                SEEN.clear()
            return self._send(200, {"ok": True})
        if not self.path.startswith("/events"):
            return self._send(404, {"error": "not found"})
        try:
            event = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return self._send(400, {"error": "invalid JSON"})

        cfg = self.cfg
        if random.random() < cfg.slow_rate:
            time.sleep(random.uniform(5, 15))
        if random.random() < cfg.fail_rate:
            return self._send(500, {"error": "receiver failed (on purpose)"})

        key = self.headers.get("Idempotency-Key") or (event.get("event_id") if isinstance(event, dict) else None)
        with LOCK:
            SEEN[key] = SEEN.get(key, 0) + 1
            LOG.append({"key": key, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event,
                        "times_seen": SEEN[key]})
        if random.random() < cfg.lost_ack_rate:
            return self._send(500, {"error": "stored, but the acknowledgement was lost (on purpose)"})
        return self._send(200, {"ok": True, "key": key, "times_seen": SEEN[key]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8098)
    ap.add_argument("--fail-rate", type=float, default=0.3)
    ap.add_argument("--slow-rate", type=float, default=0.1)
    ap.add_argument("--lost-ack-rate", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=None)
    cfg = ap.parse_args()
    if cfg.seed is not None:
        random.seed(cfg.seed)
    Handler.cfg = cfg
    srv = ThreadingHTTPServer(("127.0.0.1", cfg.port), Handler)
    srv.daemon_threads = True
    print("Flaky receiver on http://127.0.0.1:%d/events (fail %.0f%%, slow %.0f%%, lost ack %.0f%%)"
          % (cfg.port, cfg.fail_rate * 100, cfg.slow_rate * 100, cfg.lost_ack_rate * 100), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
