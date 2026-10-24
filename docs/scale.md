# Scaling to 2,000 shops

2,000 shops is not a big-data problem: a few thousand staff and some tens of thousands of notes a day fit one Postgres primary. The harder problems are keeping isolation and the audit trail trustworthy, keeping AI cost and latency off the request path, and not drowning approvers. What I would change:

**Isolation and the gate.** Keep one shared schema with `shop_id` on every row and row-level security, rather than a database per shop (2,000 databases are hard to migrate and monitor). Move the gate into a `SECURITY DEFINER` database function and grant the app role only EXECUTE plus SELECT, so "only the gate writes" is enforced by Postgres instead of by code review. Put PgBouncer in transaction mode in front; our `SET LOCAL` per transaction already works with that. Lead every index with `shop_id`, and partition the growing tables (`audit_log`, `proposals`, `ai_tasks`) by shop hash or by month.

**AI off the request path.** Today a note waits for the AI inside the HTTP request. At scale, the note and an outbox row are committed together, workers call the model, and results are routed by the same validator. Add per-shop rate limits and spend budgets, retries with idempotency keys, a circuit breaker per provider, and a fallback model. Each task keeps recording the prompt version, rules hash and model id, so any proposal can be traced to exactly what produced it.

**Audit you can trust.** Per-shop chains already append in parallel. Add periodic anchoring: sign each shop's head hash with a key the database never holds and write it to append-only storage outside the database. Verification becomes a background job that checks incrementally from the last anchored point, with alerts on failure.

**Rules and people.** Move rules from one JSON file to a versioned table with effective dates, so regions can differ and every audit row points to the rule version it was decided under. Replace demo tokens with real sign-in and short-lived tokens; area admins with many shops still act in one shop per request. Give approvers a queue per shop with ageing alerts, so pending changes do not pile up or get rubber-stamped. Shops outside London use the time zone already stored on each shop.

**Watching the AI.** Track block rates by reason, abstention rates and approver edit rates per model and prompt version. Any prompt or model change must pass the evaluation suite (with a larger, held-out note set than our nine) before rollout, then go out to a few shops first.
