# Decision log

One entry per real decision, in the order made: the choice, the options, why, and what it costs. Later entries may supersede earlier ones; earlier entries are not rewritten.

## D01 · Stack: Python, FastAPI, psycopg 3, raw SQL, Postgres 16 in Docker Compose
- **Options:** Python/FastAPI; Node/TypeScript with zod; Django with its ORM; Go.
- **Why:** the mock is Python, so one language for everything; raw SQL makes "show the SQL behind the gate and the approval" literal; FastAPI is small. Django's ORM would hide the SQL being assessed.
- **Cost:** no migration framework or ORM conveniences; plain ordered SQL files and hand-written queries.

## D02 · The gate is a Python function with database guardrails underneath
- **Options:** (a) Python `apply_change()` plus RLS, column-level grants, CHECKs; (b) a PL/pgSQL `SECURITY DEFINER` function, with the app role granted only EXECUTE.
- **Why (a):** readable and testable line by line within the time box; value checks such as the London offset and E.164 are clearer in Python.
- **Cost:** the app role must hold UPDATE on the eleven changeable columns, so code outside the gate *could* write them. Mitigated by code structure and a test that scans for business-table writes outside the gate, not by the database. (b) is the upgrade that closes this.

## D03 · The API calls the AI; the AI holds no credentials
- **Options:** (a) the API calls the AI and parses its HTTP response; (b) a separate scribe worker that holds a per-task token and posts proposals to the API.
- **Why (a):** it matches how the mock is built, and an AI with no credential at all is narrower than one with a narrow token. All of its output is checked before acceptance either way.
- **Cost:** does not literally match the brief's "holding one narrow token" wording; (b) is a stretch item if time allows.

## D04 · Synchronous AI call with a total deadline, outside any transaction
- **Options:** synchronous call in the request; background queue.
- **Why:** deterministic and easy to demo. The deadline covers the whole response (not just each read), so a slow drip cannot hold a worker; a size cap stops huge responses. The note and task are committed first, the AI is called with no transaction open, and proposals are written in a second transaction.
- **Cost:** a request waits up to the deadline. Production would use a queue or outbox.

## D05 · AI output is validated per proposal; a tainted response is flagged
- **Options:** reject the whole response if any part is bad; block only the bad proposals.
- **Why:** an invalid top-level shape or mismatched task id rejects the whole response. Inside a well-formed response each proposal is checked alone; bad ones are stored as `blocked` with a reason, and the task is flagged so the approver sees that the response contained hostile parts. The surviving proposals still need a human.
- **Cost:** a good-looking proposal from a manipulated response can survive to pending; the flag and human review are the defence.

## D06 · Evidence must appear word for word in the note
- **Options:** block when evidence is not a verbatim quote (after normalising case, whitespace and quote marks); or only flag it.
- **Why block:** the brief asks for evidence quoted from the note; a paraphrase cannot be checked. For money fields, evidence naming a non-GBP currency is blocked, and a £ amount in the evidence must equal the value; amounts in words are flagged as unverifiable.
- **Cost:** a correct value with paraphrased evidence is lost (N05's refund). Staff can enter it directly, and a refund by counter staff waits for a manager anyway.

## D07 · The AI's sensitivity label is overridden and flagged, not used to block
- **Why:** the server derives sensitivity from the rules and the value. Blocking on a mismatch would give the AI's label meaning, and would block a legitimate write-off (N09) because the AI called it normal.
- **Cost:** none in authority; the mismatch is shown to the approver as a flag.

## D08 · Injection tripwire on notes
- **Choice:** phrases aimed at the AI ("note to the AI", "pre-approved", "mark ... approved", "ignore previous instructions") block sensitive proposals from that task and flag the normal ones.
- **Why:** cheap, and it stops N08's attacker email from reaching an approver as an ordinary request.
- **Cost:** a heuristic, easy to evade by rephrasing. It is a tripwire, not a security boundary: the boundary is that every proposal needs an authorised human and passes the same validation.

## D09 · One audit hash chain per shop
- **Options:** one global chain; one chain per shop.
- **Why per shop:** with RLS a request can only see its own shop's rows, so it cannot chain to other shops'; shops also append in parallel. A locked `audit_head` row per shop plus unique `(shop_id, seq)` stops forks.
- **Cost:** no tamper-evident ordering across shops.

## D10 · A return does not free the item
- **Why:** a returned item has not been inspected (N07), and making a damaged scaffold tower available for hire is a safety risk. Item status is changed explicitly, usually by a technician (N03).
- **Cost:** items stay `on_hire` after return until someone updates them.

## D11 · A damage charge does not automatically reduce the refund
- **Why:** both are explicit, sensitive, manager-approved amounts. The server only checks refund ≤ deposit held; the approver sees the charge beside the refund.
- **Cost:** a person could double-count; not prevented by the system.

## D12 · Allowed fields follow the note author's role
- **Choice:** allowed fields = fields the AI may propose AND fields the note author's role may change AND whose record type is in scope.
- **Why:** stops the AI being used to get a field changed that the author could not change. Checked: every legitimate proposal in N01 to N09 is still allowed.
- **Cost:** a counter's note cannot yield a service-note proposal even if the note describes a repair.

## D13 · Rejected and blocked proposals are terminal
- **Choice:** `rejected`, `blocked` and `approved` are terminal; a database trigger forbids moving out of them, and approval requires `status = 'pending'` under a row lock. A later direct edit by a person is a new change under normal rules.
- **Cost:** none significant; re-running the scribe on a note is allowed only if its earlier tasks failed.

## D14 · Rules loaded from the data-pack JSON at startup
- **Why:** the file is the stated source of truth; loading it read-only keeps it out of reach of any endpoint. Its hash is recorded on each AI task and audit row. Key limits are also CHECK constraints in the schema.
- **Cost:** a rules change needs a restart; no history of rule versions beyond the hash.

## D15 · Times: shop time with offset, offset must match Europe/London at that instant
- **Why:** the notes are +01:00; clocks went back on 25 October 2026, so edits made from then on are +00:00. A timestamp whose offset is wrong for London at that instant is rejected.
- **Cost:** shops outside Europe/London would need their zone from the shop row (already stored).

## D16 · Who may approve an AI proposal
- **Choice:** following R3, the approver needs the field's approver role if sensitive, or may-change permission if normal, in that shop. The author of the note may approve normal-field proposals. Nobody approves a pending change they requested (R4).
- **Cost:** none; stated so the tests can check it.

## D17 · Times stored as ISO 8601 text with offset, not timestamptz
- **Options:** `timestamptz` (normalises to UTC and drops the offset); timestamptz plus a separate offset column; text in one canonical form.
- **Why text:** the brief says "store times with their offset", and the hash chain must hash exactly what is stored. The app writes one canonical form (`YYYY-MM-DDTHH:MM:SS+HH:MM`, offset checked against Europe/London at that instant) and a CHECK enforces the shape.
- **Cost:** the database cannot sort or compare these natively; at scale add a `timestamptz` column written alongside.

## D18 · Who may reject
- **Choice:** the same people who may approve that proposal; nobody rejects their own pending change. A rejection needs a reason and writes an audit row.
- **Why:** otherwise a counter could reject a manager-level refund, and a rejection is final (R5), so it deserves the same authority as an approval.
- **Cost:** a requester cannot withdraw their own pending change; it waits for an approver.

## D19 · A failed AI call keeps the note and a failed task row
- **Choice:** on timeout, drop, 5xx, garbage or wrong schema: no proposals, no record changes, but the note and an `ai_tasks` row with `status = failed` and the error kind are kept. The note can be retried; a note whose task succeeded cannot be reprocessed.
- **Why:** "nothing saved" is read as no business data and no proposals; keeping the failure is needed to see and retry it.
- **Cost:** failed rows accumulate; no automatic retry.

## D20 · Stale proposals are refused, not re-applied
- **Choice:** each proposal stores the record's value when it was proposed (`base_value`). If the value has changed by approval time, approval fails with 409 and the approver must reject and re-propose.
- **Why:** hire_days 5 means "3 + 2"; applying it after someone else set 6 would silently undo their change.
- **Cost:** a little friction when two notes touch the same field.

## D21 · Evidence must agree with the value where it can be checked
- **Choice:** money: a £ or p amount in the evidence must equal the value, any other currency blocks, amounts in words only flag. Times: an HH:MM in the evidence must equal the value's local time. Phones: the digits must match after UK normalisation. A return time after the note was written is blocked.
- **Why:** a verbatim quote can still be attached to the wrong value; these cheap checks catch N04's euros and invented times.
- **Cost:** a value that is right but stated in words ("quarter past nine") is only flagged, not verified.

## D22 · Ports 8080 and 5433 on the host
- **Why:** 8000 and 5432 were already in use on the development machine; avoids clashing with other local services.
- **Cost:** none; set in docker-compose.yml.
