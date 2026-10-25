# Scaling to 2,000 shops

2,000 shops is not a data-volume problem; one Postgres primary holds it. The hard parts are keeping isolation and the audit trail trustworthy, keeping AI cost and latency off the request path, and not drowning approvers.

**Isolation and the gate.** Keep one shared schema with `shop_id` on every row and row-level security, not a database per shop (2,000 databases are hard to migrate and monitor). Move the gate into a `SECURITY DEFINER` function so the app role holds only EXECUTE and SELECT, and the database enforces that nothing else writes. Put PgBouncer in transaction mode in front (our `SET LOCAL` already fits), lead every index with `shop_id`, and partition `audit_log`, `proposals` and `ai_tasks`.

**AI off the request path.** Notes go into a queue; scribe agents (already separate, holding one task token each) pull work. Add per-shop rate limits and spend budgets, retries with idempotency, a circuit breaker and a fallback model. Every task keeps its prompt version, rules hash and model id.

**Audit you can trust.** Sign each shop's chain head with a key the database never sees and store it outside the database; verify incrementally from the last anchor, with alerts.

**Rules and people.** Version rules in a table with effective dates, referenced by every audit row. Use real sign-in with short-lived tokens. Give approvers per-shop queues with ageing alerts so pending changes are neither lost nor rubber-stamped.

**Watching the AI.** Track block, abstention and approver-edit rates per model and prompt; any prompt or model change must pass a larger held-out evaluation set before a staged rollout.
