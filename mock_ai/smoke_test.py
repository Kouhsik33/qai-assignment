#!/usr/bin/env python3
"""
Safe Counter: smoke test for the mock AI. Python standard library only.

Start the mock first:   python3 mock_ai_server.py
Then run:               python3 smoke_test.py   [--url http://127.0.0.1:8099]

It sends each data-pack note in normal mode, then exercises every other mode,
and prints PASS or FAIL per check. It takes about 10 seconds. It only checks
that the mock behaves as documented; it does not check whether the mock's
answers are right. Deciding that is your system's job.
"""

import argparse
import http.client
import json
import os
import socket
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
NOTES = os.path.join(HERE, "..", "data", "notes.json")

TOP_KEYS = {"task_id", "proposals", "abstentions", "flags"}
PROP_KEYS = {"record_id", "field", "value", "unit", "confidence", "evidence", "sensitivity", "reason"}
UNITS = {"iso8601", "enum", "days", "pence", "text", "e164", "email"}


def shape_ok(body):
    """Minimal check against proposal_schema.json (no third-party libraries)."""
    if not isinstance(body, dict) or set(body) != TOP_KEYS:
        return False
    for p in body["proposals"]:
        if not isinstance(p, dict) or set(p) != PROP_KEYS:
            return False
        if p["unit"] not in UNITS or p["sensitivity"] not in ("normal", "sensitive"):
            return False
        if not isinstance(p["value"], (str, int)) or isinstance(p["value"], bool):
            return False
        if not (0 <= p["confidence"] <= 1) or not p["evidence"]:
            return False
    for a in body["abstentions"]:
        if not isinstance(a, dict) or set(a) != {"record_id", "field", "reason"}:
            return False
    for f in body["flags"]:
        if not isinstance(f, dict) or set(f) != {"type", "detail"}:
            return False
    return True


def post(base, mode, payload, timeout):
    u = urlparse(base)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        conn.request("POST", "/v1/propose?mode=" + mode, body=json.dumps(payload),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8099")
    args = ap.parse_args()

    notes = json.load(open(NOTES, encoding="utf-8"))["notes"]
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print("%s  %-34s %s" % ("PASS" if ok else "FAIL", name, detail))

    def req_for(note):
        return {"task_id": "smoke_" + note["id"], "shop_id": note["shop_id"],
                "note": {"id": note["id"], "text": note["text"], "written_at": note["written_at"],
                         "author_role": note["author_role"], "source": note["source"], "scope": note["scope"]},
                "records": [{"id": rid, "fields": {}} for rid in note["scope"]]}

    for note in notes:
        try:
            status, text = post(args.url, "normal", req_for(note), 5)
            body = json.loads(text)
            check("normal " + note["id"], status == 200 and shape_ok(body),
                  "%d proposals, %d abstentions, %d flags" % (len(body["proposals"]), len(body["abstentions"]), len(body["flags"])))
        except Exception as exc:  # noqa: BLE001
            check("normal " + note["id"], False, repr(exc))

    sample = req_for(notes[0])

    for mode in ("hang", "slow"):
        try:
            post(args.url, mode, sample, 2)
            check(mode, False, "answered within 2 s (expected no answer yet)")
        except (socket.timeout, TimeoutError):
            check(mode, True, "no answer within 2 s, as expected")
        except Exception as exc:  # noqa: BLE001
            check(mode, False, repr(exc))

    try:
        status, _ = post(args.url, "error", sample, 5)
        check("error", status == 503, "HTTP %d" % status)
    except Exception as exc:  # noqa: BLE001
        check("error", False, repr(exc))

    try:
        post(args.url, "drop", sample, 5)
        check("drop", False, "got a response (expected a dropped connection)")
    except (http.client.RemoteDisconnected, ConnectionError, http.client.BadStatusLine) as exc:
        check("drop", True, type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        check("drop", False, repr(exc))

    try:
        status, text = post(args.url, "garbage", sample, 5)
        try:
            json.loads(text)
            check("garbage", False, "body parsed as JSON")
        except ValueError:
            check("garbage", status == 200, "HTTP 200 with invalid JSON, as expected")
    except Exception as exc:  # noqa: BLE001
        check("garbage", False, repr(exc))

    try:
        status, text = post(args.url, "wrong_schema", sample, 5)
        check("wrong_schema", status == 200 and not shape_ok(json.loads(text)), "valid JSON, wrong shape")
    except Exception as exc:  # noqa: BLE001
        check("wrong_schema", False, repr(exc))

    expected = {"hostile_scope": 3, "hostile_privilege": 3, "hostile_downgrade": 3, "hostile_output": 10}
    for mode, count in expected.items():
        try:
            status, text = post(args.url, mode, sample, 5)
            body = json.loads(text)
            n = len(body.get("proposals", []))
            check(mode, status == 200 and n == count, "%d proposals returned" % n)
        except Exception as exc:  # noqa: BLE001
            check(mode, False, repr(exc))

    passed = sum(results)
    print("\n%d/%d checks passed" % (passed, len(results)))
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
