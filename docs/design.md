# Safe Counter: Design (Level 0)

Written before any code. The "What changed and why" section at the end is added at the end; this part is not rewritten.

## Main parts

| Part | Responsibility |
| --- | --- |
| **API** (Python, FastAPI, raw SQL via psycopg) | HTTP endpoints. Resolves a bearer token to a seeded staff member and checks they hold a membership in the shop named in the path (`/shops/{shop_id}/...`). One shop per request. |
| **Rules** (`data/roles_and_fields.json`) | Read-only server config loaded at startup. Field formats, limits, who may change, who approves, value rules (write-off). Never sent to or writable by the AI. |
| **Task builder** | Turns a note into an AI task: the note's scope records (checked to belong to the shop), only the fields the task needs, and the allowed fields = fields the AI may propose AND the note author's role may change. |
| **Scribe client** | Calls the AI over HTTP with a total deadline and a response size cap. Never holds a DB transaction open while waiting. |
| **Validator and router** | Treats AI output as hostile input. Checks shape, task id, scope, field allowlist, value rules, verbatim evidence; derives sensitivity on the server. Each proposal becomes `pending`, `blocked` (with reason) or an abstention. |
| **Approval service** | Approve, reject, or edit-then-approve a pending proposal. Records who, when, why, the AI's value and the final value. |
| **The gate** `apply_change()` | The only code that writes business tables. Locks the row, validates the value, derives sensitivity, checks the actor's role in that shop, decides apply-now or wait, then writes the change and its audit row in one transaction. |
| **Audit chain** | Append-only `audit_log`, one hash chain per shop; `verify` reports the first broken row. |
| **Postgres** | Business tables, proposals, audit. Second line of defence: RLS, column-level grants, CHECK constraints, composite shop foreign keys. |

## How a note becomes an applied change

```mermaid
flowchart LR
  U["Staff (CLI / curl)<br/>bearer token"] --> AUTH["Auth: token → staff →<br/>membership in path shop"]
  AUTH -->|POST note| TASK["Task builder<br/>scope + allowed fields<br/>minimal data"]
  TASK --> SCRIBE["Scribe client<br/>deadline + size cap"]
  SCRIBE -->|note + minimal fields| AI["AI process (mock_ai)<br/>no DB, no credentials"]
  AI -.->|untrusted JSON| SCRIBE --> ROUTER["Validator & router<br/>schema · scope · field · value ·<br/>evidence · server sensitivity"]
  ROUTER -->|pending / blocked / abstained| PROP[("proposals")]
  AUTH -->|approve / reject| APPROVE["Approval service"] --> GATE[["THE GATE<br/>lock → decide →<br/>UPDATE + audit row<br/>one transaction"]]
  AUTH -->|direct edit| GATE
  RULES[/"roles_and_fields.json"/] --> ROUTER & GATE
  GATE --> BIZ[("customers · items · rentals")]
  GATE --> AUD[("audit_log<br/>hash chain per shop")]
```

1. A staff member posts a note with its scope. The note is stored; an `ai_task` row records the prompt version and rules hash.
2. Outside any transaction, the scribe client sends the AI the note and only the needed fields. Timeout, connection drop, 5xx, invalid JSON or wrong shape: the task is marked failed, no proposals are stored, no business row changes, the caller gets a clear error.
3. The router checks each proposal on its own. Anything outside the task's records, shop or allowed fields, any extra key (such as `approved_by`), any bad value, or evidence not found word for word in the note is stored as `blocked` with a reason and can never be approved. The AI's sensitivity label is stored but ignored.
4. A valid proposal is stored as `pending` with the server-derived approver roles. Confidence only adds a `low_confidence` flag; it never relaxes who may approve.
5. An approver approves (optionally correcting the value). In one transaction the proposal row is locked (a second approver waits, then gets "already approved by X"), the gate re-checks everything with the final value, updates the record, appends the audit row, and marks the proposal approved. A rejection is terminal.
6. A person's direct edit goes straight to the gate: applies now (normal field, or they are an approver for the sensitive field), waits as a human proposal (sensitive, not an approver), or is refused (role may not change the field). Nobody approves their own pending change.

## Where shop isolation is enforced

- **API:** every route is under one shop; the actor must be a member of it; every query filters `shop_id` as well as `id`.
- **Database:** the app connects as a non-owner role without BYPASSRLS. Row-level security on every shop table checks `current_setting('app.shop_id')`, set with `SET LOCAL` per transaction (unset means no rows). The app role can update only the eleven changeable columns, cannot insert or delete business rows, and can only insert and read audit rows. Composite foreign keys on `(shop_id, id)` stop a rental pointing at another shop's customer or item.

## Where the AI runs and what it can reach

The AI is a separate process (the supplied mock, or a compatible model) reached over HTTP. It has no database credentials and no credential to our API: its only output channel is the HTTP response, which we parse as untrusted. It receives one note and the minimum fields for that task (never `id_check_ref`, no contact details unless a customer is in scope). It cannot see or change its prompt, the rules, or approval settings; there is no endpoint for any of those.

## The three failures that worry me most

1. **An unauthorized change is applied**: hostile or mislabelled AI output, a scope escape, or a code path that skips the gate. Answered by one gate, server-derived sensitivity, allowlists, and database grants, RLS and CHECKs underneath. Known gap: the app role can still update the changeable columns directly, so a bug outside the gate could skip policy checks.
2. **A plausible but wrong proposal is approved by a person**: a charge on the wrong rental (N04), an attacker's email from pasted text (N08). No rule catches every semantic error. Answered by verbatim evidence checks, evidence-value agreement for money, flags, before→after values and a required reason. This is the residual risk, and I will say so.
3. **Integrity under concurrency or failure**: two approvals at once, a change without its audit row, a forked audit chain, a shop setting leaking through a pooled connection, partial state after an AI timeout. Answered by row locks, one transaction per change, a locked per-shop chain head with unique `(shop_id, seq)`, `SET LOCAL` only, and no transaction open during the AI call. Limitation: a hash chain detects edits but anyone with database write access can recompute it from the edited row onward; stopping that needs an anchor outside the database.
