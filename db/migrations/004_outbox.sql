-- Reliable event delivery (Level 3): transactional outbox.
-- The gate inserts one row per applied change, in the same transaction as the change.
-- A separate worker role delivers rows to another system with retries.

CREATE TABLE outbox (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id         text NOT NULL REFERENCES shops(id),
    event_id        text NOT NULL UNIQUE,         -- idempotency key sent to the receiver
    kind            text NOT NULL,
    payload         jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    attempts        integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error      text,
    delivered_at    timestamptz
);
CREATE INDEX outbox_due ON outbox (next_attempt_at) WHERE delivered_at IS NULL;

-- The app may only add events for its own shop; it cannot read or alter delivery state.
GRANT INSERT ON outbox TO sc_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO sc_app;
ALTER TABLE outbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY shop_isolation ON outbox FOR INSERT TO sc_app
    WITH CHECK (shop_id = current_setting('app.shop_id', true));

-- The delivery worker: reads events of every shop and records delivery, nothing else.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sc_delivery') THEN
        CREATE ROLE sc_delivery LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO sc_delivery;
GRANT SELECT ON outbox TO sc_delivery;
GRANT UPDATE (attempts, next_attempt_at, last_error, delivered_at) ON outbox TO sc_delivery;
CREATE POLICY delivery_all_shops ON outbox TO sc_delivery USING (true);
