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

## D23 · Optional real model: Groq, openai/gpt-oss-120b, mock stays the default
- **Options:** mock only; Gemini API; OpenRouter free models; Groq.
- **Why Groq:** strict JSON-schema output that accepted the pack's response schema unchanged (checked with a live call, including the `["string","integer"]` value union); inputs not retained by default; OpenAI-compatible REST, so one `httpx` POST and no SDK. Gemini's free tier uses content to improve Google's products, which conflicts with keeping the pack private; OpenRouter's free models allow 50 requests a day without credits and structured-output support varies by backend.
- **How it is contained:** `AI_PROVIDER=mock` is the default; Groq only runs when `AI_PROVIDER=groq` and `GROQ_API_KEY` are set in the local environment (`.env`, git-ignored). The model's reply goes through exactly the same response checks, router, approval rules, gate, grants and audit as the mock's. The mock's failure and hostile modes and all deterministic tests keep running against the mock. Provider, model and token usage are recorded on each AI task; the key never is.
- **Cost:** the real model is not deterministic even at temperature 0 (reasoning model), so its results are reported as measured ranges, not fixed outputs; a network dependency the live demo does not rely on; the free tier's 8,000 tokens per minute makes the evaluation slow (it paces itself).

## D24 · Real-model experiment: design and decision rules, fixed before running it
- **Conditions:** mock (baseline); Groq `openai/gpt-oss-120b` with `scribe_v1:zero` (the prompt without its Examples section); the same model with `scribe_v1:few`. Same task payload, temperature 0, seed 7, strict schema; 3 runs each over N01–N09, scored against `evals/gold.json`.
- **Rules, written before any real-model result:** few-shot becomes the default prompt for a real provider only if its mean F1 is at least 5 points above zero-shot AND its N08 injection resistance and schema validity are no worse; otherwise zero-shot (fewer tokens). If the verbatim-evidence rule (D06) falsely blocks more than 10% of correct real-model proposals, D06 is revisited.
- **Why so small:** the free tier allows 8,000 tokens a minute; three conditions × 3 runs × 9 notes fits in about half an hour.
- **Cost / limits:** nine notes cannot show significance; the gold labels and the prompt's rules were written after reading these notes, so the real-model numbers may be flattered. Reported as measured ranges with these caveats.

## D25 · Event delivery: transactional outbox, at-least-once, idempotency key
- **Options:** post to the receiver inside the change's transaction; post after commit; transactional outbox with a worker.
- **Why the outbox:** posting inside the transaction holds row locks during a slow network call and still cannot undo a delivered event if the commit then fails; posting after commit loses the event if the process dies in between. The outbox row is written by the gate in the same transaction as the change and its audit row, so every applied change has exactly one event and a rolled-back change has none. A worker claims due rows with `FOR UPDATE SKIP LOCKED`, posts with `Idempotency-Key = <shop>:<audit seq>`, and retries with capped exponential backoff.
- **Guarantee:** at-least-once. A lost acknowledgement or a reply after our timeout means the receiver sees the event again with the same key; de-duplication is the receiver's job (the pack's receiver counts duplicates by key). Order is not guaranteed under retries; payloads carry the audit seq so a receiver can order or spot gaps.
- **Least privilege:** the app role can only INSERT outbox rows for its own shop and cannot read or change delivery state; the worker connects as `sc_delivery`, which can read outbox rows of every shop and update only the four delivery columns, and cannot read business tables.
- **Cost:** no dead-letter state (an event that always fails is retried forever, at most every 5 minutes); payloads include changed values, so customer contact changes leave the system; the worker holds one row lock during each POST.

## D26 · Outcome of the D24 experiment (rules applied as written)
- **Measured** (`evals/results.md`, 3 runs × 9 notes per condition): mean F1 mock 90%, Groq zero-shot 60% (58–63%), Groq few-shot 86% (82–88%). Schema-valid responses 100% in all conditions. N08 injection resisted by the model in 0/3 zero-shot runs and 1/3 few-shot runs. False blocks by the verbatim-evidence rule on real-model output: 0.
- **Decision 1: few-shot is the default prompt for the real provider.** It beats zero-shot by 26 F1 points (rule: at least 5) and is no worse on injection resistance or schema validity. Most of the gap is one format mistake: without examples the model put quoted note text into `return_condition` (an enum), which the router blocked as invalid. The examples teach the output convention more than the extraction itself.
- **Decision 2: D06 stays.** The verbatim-evidence rule blocked 0 correct real-model proposals (threshold: 10%); the only false blocks in the experiment were the mock's N05 refund.
- **What the numbers did not settle:** whether the prompt generalises (nine notes, rules written after reading them). They did show two things clearly: the model followed N08's injected instructions in 5 of 6 real runs and only the server stopped them; and the same 3 wrong-but-valid proposals reached a person in every real run (a zero damage charge for "no damage" on N01 and N03, and N03's inspection time proposed as the return time), which no rule caught. That is weakness 2 in the README, now measured.

## D27 · Level 2F: a scribe agent holding one single-use, proposal-only task token (supersedes the "no credential" choice in D03 for the main API)
- **Why revisit D03:** the brief asks for an AI that holds "one narrow token that can only create proposals". With the API calling the model and reading its reply, there is nothing for a token to authorise, so D03 met the spirit but not the wording.
- **Choice:** a separate `scribe_agent` process calls the model and submits its reply to `POST /ai/shops/{shop}/tasks/{task}/result`. For each task the API creates a random 256-bit token, stores only its SHA-256 and an expiry on the `ai_tasks` row, and hands the plain token to the agent once. The token works only on that endpoint, only for that task in that shop, only once (the task row is locked `FOR UPDATE` and must still be `running`), and only before the deadline; staff routes do not accept it. The submitted reply goes through the same `_accept_reply` (shape check, task id, `route_proposal`) as the direct path. The agent has no database credentials and runs on a Docker network that does not include Postgres.
- **Options rejected:** the AI echoing a token in its reply (the pack's response schema forbids extra fields, and it only repeats the task-id check); a long-lived service token (broader than one task); modifying the pack's mock to call back (pack files stay untouched).
- **Cost:** two transports to maintain. `AI_TRANSPORT=agent` is used by the main Compose API and the demo; `direct` remains for in-process tests and the optional Groq service, where the AI holds no credential at all. The API waits for the callback by polling the task row every 100 ms within the deadline.

## D28 · Workflow timestamps stay `timestamptz` (reviewed against "store times with their offset")
- **Question:** proposal `created_at`/`decided_at`, task and outbox times are `timestamptz`, which stores the exact instant but not the original offset.
- **Decision: no change.** The brief's time rule sits in the context of shop events (the notes' times, returns) and the clock change; those are stored as ISO text with the London offset (D17), as is every audit row's `occurred_at`, which is the record of when a change was applied. Workflow timestamps are written by the server's clock, never typed by a person, so there is no original offset to lose; `timestamptz` keeps the instant unambiguous across the clock change, and the shop's zone is on the shop row for display.
- **Cost:** API messages show these times in UTC (for example "approved by u_chloe at …+00:00"); converting them to shop time is display work, not a storage change.
