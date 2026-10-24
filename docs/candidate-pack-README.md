# Safe Counter: Candidate Pack

This pack goes with the Safe Counter assessment brief (PDF). It gives you the rules, the starting data, nine handover notes and a mock AI scribe, so you can build and test without any API key, paid service or internet connection. Read the brief first; this file only explains what is in the pack and how to run the mock.

All data is synthetic. Please keep this pack private.

## What is inside

| Path | What it is |
| --- | --- |
| `data/roles_and_fields.json` | Roles, the AI's service identity, approval rules R1 to R7, and every changeable field with its unit, limits, sensitivity and approvers. The source of truth. |
| `data/seed_records.json` | Starting state: 2 shops (Millbrook, Eastfield), 7 staff and their memberships, 8 customers, 11 items, 9 open rentals and 1 closed rental. |
| `data/notes.json` | Nine handover notes, N01 to N09. Each has a `scope`: the records open on screen when it was written. |
| `mock_ai/mock_ai_server.py` | The mock AI scribe. Python 3.9+ standard library only. |
| `mock_ai/proposal_schema.json` | JSON Schema of every AI response. |
| `mock_ai/request_example.json` | An example request to the mock. |
| `mock_ai/smoke_test.py` | Checks every mock mode in about 10 seconds. |
| `mock_ai/flaky_receiver.py` | Optional: a deliberately unreliable event receiver for Level 3. |

## Run the mock

```bash
cd mock_ai
python3 mock_ai_server.py          # http://127.0.0.1:8099
python3 smoke_test.py              # in a second terminal
```

Send a note:

```bash
curl -s -X POST "http://127.0.0.1:8099/v1/propose?mode=normal" \
  -H "Content-Type: application/json" \
  -d @request_example.json
```

## Request and response

- **Request:** `task_id`, `shop_id`, `note` (`id`, `text`, `written_at`, `author_role`, `source`), and the `records` you choose to send with the fields the task needs. See `request_example.json`. What you send is your decision: send only what the task needs.
- **Response:** `task_id`, `proposals`, `abstentions` and `flags` (see `proposal_schema.json`). Each proposal carries `record_id`, `field`, `value` (in the record's unit), `unit`, `confidence`, `evidence` (words quoted from the note), `sensitivity` (the AI's own label) and `reason`.

## Modes

Choose a mode per request with `?mode=<mode>` or the `X-Mock-Mode` header, or set a default with the `MOCK_MODE` environment variable.

| Mode | Behaviour |
| --- | --- |
| `normal` | Answers for N01 to N09; simple rules for notes you write yourself. |
| `slow` / `hang` | Answers after `MOCK_SLOW_SECONDS` (default 20) / never answers. |
| `error` / `drop` | HTTP 503 / closes the connection with no response. For "down", stop the server. |
| `garbage` / `wrong_schema` | Not valid JSON / valid JSON in the wrong shape. |
| `hostile_scope` | Output that reaches outside the task. |
| `hostile_privilege` | Output that tries to approve itself or change its own rules. |
| `hostile_downgrade` | Output that labels sensitive changes as normal. |
| `hostile_output` | All hostile variants mixed with one ordinary proposal. |

**The mock is not an oracle.** In normal mode it behaves like a reasonable model: mostly right, but not always. A well-formed response is not necessarily a correct or allowed one. Deciding that is your system's job.

Prefer a real model? Any free one is fine. Keep the same response shape, and make sure the mock's failure and hostile modes still run against your system.

## Ports and settings

| Setting | Default |
| --- | --- |
| `MOCK_PORT` | 8099 |
| `MOCK_HOST` | 127.0.0.1 |
| `MOCK_MODE` | normal |
| `MOCK_SLOW_SECONDS` | 20 |
| Flaky receiver | `python3 flaky_receiver.py --port 8098 --fail-rate 0.3` |
