"""`sc`: a small command-line client for the Safe Counter API. Standard library only.

    python3 -m safecounter.cli note N01                      # send a data-pack note as its author
    python3 -m safecounter.cli note N01 --mode hang           # same, with a mock AI failure mode
    python3 -m safecounter.cli note-text --as amira --id X1 --scope r_m5005 --text "..."   # your own note
    python3 -m safecounter.cli proposals --as chloe [--status pending]
    python3 -m safecounter.cli approve --as chloe --pick rental.deposit_refund_pence --reason "checked"
    python3 -m safecounter.cli approve 7 --as chloe --reason "..." --value 4
    python3 -m safecounter.cli reject 7 --as chloe --reason "..."
    python3 -m safecounter.cli edit r_m5004 rental.hire_days 3 --as amira --reason "..."
    python3 -m safecounter.cli show r_m5004 --as amira
    python3 -m safecounter.cli pending-id rental.hire_days --as chloe   # id of the latest pending proposal
    python3 -m safecounter.cli audit --as chloe | verify --as chloe
    python3 -m safecounter.cli run-notes                      # all nine notes, markdown table

Users are seeded staff names (amira, ben, chloe, dev, gus, ewa, femi); the token is tok_<name>.
The shop is inferred from the user's membership; pass --shop for femi (two shops).
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = os.environ.get("SC_API", "http://localhost:8080")
SEED = json.loads((ROOT / "data" / "seed_records.json").read_text())
NOTES = {n["id"]: n for n in json.loads((ROOT / "data" / "notes.json").read_text())["notes"]}


def shop_for(user: str, shop: str | None) -> str:
    if shop:
        return shop
    shops = [m["shop_id"] for m in SEED["memberships"] if m["staff_id"] == f"u_{user}"]
    if len(shops) != 1:
        sys.exit(f"{user} belongs to {shops}; pass --shop")
    return shops[0]


def call(method: str, path: str, user: str, body=None, mode: str | None = None):
    headers = {"Authorization": f"Bearer tok_{user}", "Content-Type": "application/json"}
    if mode:
        headers["X-AI-Mode"] = mode
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def show_error(status, body):
    print(f"  ✗ HTTP {status}  {body.get('error')}: {body.get('detail')}")
    return 1


def fmt_proposals(rows):
    for p in rows:
        mark = {"pending": "⏳", "blocked": "⛔", "approved": "✅", "rejected": "✖"}.get(p["status"], "?")
        sens = "" if p.get("sensitive") is None else (" SENSITIVE" if p["sensitive"] else " normal")
        print(f"  {mark} #{p['id']:<3} {p['status']:<8} {p['record_id']} {p['field']} = {json.dumps(p.get('value'))}{sens}")
        if p.get("approver_roles") and p["status"] == "pending":
            print(f"        approvers: {', '.join(p['approver_roles'])}")
        if p.get("evidence"):
            print(f"        evidence: \"{p['evidence']}\"  (confidence {p.get('confidence')})")
        for r in p.get("block_reasons") or []:
            print(f"        blocked: {r}")
        if p.get("flags"):
            print(f"        flags: {', '.join(p['flags'])}")
        if p.get("decided_by"):
            print(f"        decided by {p['decided_by']}: {p.get('decision_reason')}  final={json.dumps(p.get('final_value'))}")


def cmd_note(a):
    if a.cmd == "note-text":
        shop = shop_for(a.as_user, a.shop)
        n = {"id": a.id, "shop_id": shop, "text": a.text, "scope": a.scope.split(","), "source": "typed",
             "written_at": a.written_at, "author_id": f"u_{a.as_user}"}
    else:
        n = NOTES[a.note_id]
    user = a.as_user or n["author_id"].removeprefix("u_")
    body = {k: n[k] for k in ("id", "text", "written_at", "scope", "source")}
    print(f"→ {user} sends {n['id']} to {n['shop_id']}" + (f"  [AI mode: {a.mode}]" if a.mode else ""))
    print(f"  \"{n['text']}\"")
    for _ in range(4):
        status, res = call("POST", f"/shops/{n['shop_id']}/notes", user, body, a.mode)
        if status == 503 and res.get("error") == "ai_rate_limited":
            wait = float(res.get("retry_after_s") or 10) + 1
            print(f"  … provider rate limit (nothing stored); retrying in {wait:.0f}s")
            time.sleep(wait)
            continue
        break
    if status != 200:
        return show_error(status, res)
    print(f"  AI: {res.get('provider')} / {res.get('model')} / prompt {res.get('prompt_version')}")
    print(f"  task {res['task_id']}  ({res['latency_ms']} ms)  allowed fields: {', '.join(res['allowed_fields'])}")
    for f in res["flags"]:
        print(f"  ⚑ {f['from']} flag {f['type']}: {f['detail']}")
    fmt_proposals(res["proposals"])
    for ab in res["abstentions"]:
        print(f"  ∅ abstained {ab['record_id']} {ab['field']}: {ab['reason']}")
    return 0


def pick(user, shop, field, record):
    _, rows = call("GET", f"/shops/{shop}/proposals?status=pending", user)
    rows = [p for p in rows if p["field"] == field and (record is None or p["record_id"] == record)]
    if not rows:
        sys.exit(f"no pending proposal for {field}")
    return rows[-1]["id"]


def cmd_decide(a, verb):
    shop = shop_for(a.as_user, a.shop)
    pid = a.proposal_id or pick(a.as_user, shop, a.pick, a.record)
    body = {"reason": a.reason}
    if getattr(a, "value", None) is not None:
        body["final_value"] = parse_value(a.value)
    print(f"→ {a.as_user} {verb}s proposal #{pid}: {a.reason}")
    status, res = call("POST", f"/shops/{shop}/proposals/{pid}/{verb}", a.as_user, body)
    if status != 200:
        return show_error(status, res)
    print(f"  ✓ {json.dumps(res)}")
    return 0


def parse_value(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def cmd_edit(a):
    shop = shop_for(a.as_user, a.shop)
    body = {"record_id": a.record_id, "field": a.field, "value": parse_value(a.value), "reason": a.reason}
    print(f"→ {a.as_user} sets {a.record_id} {a.field} = {a.value}")
    status, res = call("POST", f"/shops/{shop}/changes", a.as_user, body)
    if status != 200:
        return show_error(status, res)
    print(f"  ✓ {json.dumps(res)}")
    return 0


def cmd_simple(a, path_fmt, printer=None):
    shop = shop_for(a.as_user, a.shop)
    status, res = call("GET", path_fmt.format(shop=shop, a=a), a.as_user)
    if status != 200:
        return show_error(status, res)
    (printer or (lambda r: print(json.dumps(r, indent=1))))(res)
    return 0


def print_audit(rows):
    for r in rows:
        print(f"  #{r['seq']:<3} {r['occurred_at'][:19]} {r['actor_id']:<8} {r['action']:<17} "
              f"{r['record_id']} {r['field']}: {r['old_value']} → {r['new_value']}")
        print(f"        prev {r['prev_hash'][:16]}…  hash {r['row_hash'][:16]}…")


def print_verify(res):
    if res["ok"]:
        print(f"  ✓ {res['shop_id']}: chain intact, {res['rows']} rows")
    else:
        b = res["first_broken"]
        print(f"  ✗ {res['shop_id']}: FIRST BROKEN ROW seq {b['seq']}: {b['problem']}")


def cmd_run_notes(a):
    print("| Note | Record | Field | AI value | Outcome | Server reason / flags |")
    print("| --- | --- | --- | --- | --- | --- |")
    for nid, n in NOTES.items():
        status, res = call("POST", f"/shops/{n['shop_id']}/notes", n["author_id"].removeprefix("u_"),
                           {k: n[k] for k in ("id", "text", "written_at", "scope", "source")})
        if status != 200:
            print(f"| {nid} | | | | HTTP {status} | {res.get('error')}: {res.get('detail')} |")
            continue
        for p in res["proposals"]:
            if p["status"] == "pending":
                who = "/".join(p["approver_roles"]) if p["sensitive"] else "any who may change"
                outcome = f"pending ({'sensitive' if p['sensitive'] else 'normal'}; {who})"
                why = ", ".join(p["flags"]) or "—"
            else:
                outcome, why = "**blocked**", p["block_reasons"][0]
            print(f"| {nid} | {p['record_id']} | {p['field']} | `{json.dumps(p['value'])}` | {outcome} | {why} |")
        for ab in res["abstentions"]:
            print(f"| {nid} | {ab['record_id']} | {ab['field']} | — | abstained | {ab['reason']} |")
        for f in res["flags"]:
            print(f"| {nid} | | | | flag ({f['from']}) | {f['type']}: {f['detail']} |")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sc", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_user(p, required=True):
        p.add_argument("--as", dest="as_user", required=required)
        p.add_argument("--shop")
        return p

    p = with_user(sub.add_parser("note"), required=False)
    p.add_argument("note_id")
    p.add_argument("--mode")
    p = with_user(sub.add_parser("note-text", help="send your own note text"))
    p.add_argument("--id", required=True)
    p.add_argument("--scope", required=True, help="comma-separated record ids")
    p.add_argument("--text", required=True)
    p.add_argument("--written-at", dest="written_at", default="2026-10-24T12:00:00+01:00")
    p.add_argument("--mode")
    p = with_user(sub.add_parser("proposals"))
    p.add_argument("--status")
    for verb in ("approve", "reject"):
        p = with_user(sub.add_parser(verb))
        p.add_argument("proposal_id", nargs="?", type=int)
        p.add_argument("--pick", help="field of the latest pending proposal to decide")
        p.add_argument("--record")
        p.add_argument("--reason", required=True)
        if verb == "approve":
            p.add_argument("--value", help="corrected value (JSON), edit-before-approve")
    p = with_user(sub.add_parser("edit"))
    p.add_argument("record_id")
    p.add_argument("field")
    p.add_argument("value")
    p.add_argument("--reason", required=True)
    p = with_user(sub.add_parser("show"))
    p.add_argument("record_id")
    p = with_user(sub.add_parser("pending-id"))
    p.add_argument("field")
    p.add_argument("--record")
    with_user(sub.add_parser("audit"))
    with_user(sub.add_parser("verify"))
    sub.add_parser("run-notes")

    a = ap.parse_args(argv)
    if a.cmd in ("note", "note-text"):
        return cmd_note(a)
    if a.cmd == "proposals":
        q = f"?status={a.status}" if a.status else ""
        return cmd_simple(a, "/shops/{shop}/proposals" + q, fmt_proposals)
    if a.cmd in ("approve", "reject"):
        return cmd_decide(a, a.cmd)
    if a.cmd == "edit":
        return cmd_edit(a)
    if a.cmd == "show":
        return cmd_simple(a, "/shops/{shop}/records/{a.record_id}")
    if a.cmd == "pending-id":
        print(pick(a.as_user, shop_for(a.as_user, a.shop), a.field, a.record))
        return 0
    if a.cmd == "audit":
        return cmd_simple(a, "/shops/{shop}/audit", print_audit)
    if a.cmd == "verify":
        return cmd_simple(a, "/shops/{shop}/audit/verify", print_verify)
    if a.cmd == "run-notes":
        return cmd_run_notes(a)


if __name__ == "__main__":
    sys.exit(main())
