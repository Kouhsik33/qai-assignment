-- Safe Counter schema. Every row that belongs to a shop carries shop_id from the start.
-- Times are stored as ISO 8601 text with their offset (see decisions D17); the
-- application writes them in one canonical form and the CHECKs below enforce it.

CREATE TABLE shops (
    id        text PRIMARY KEY,
    name      text NOT NULL,
    time_zone text NOT NULL
);

CREATE TABLE staff (
    id           text PRIMARY KEY,
    name         text NOT NULL,
    token_sha256 text NOT NULL UNIQUE          -- demo bearer token, stored hashed
);

CREATE TABLE memberships (
    staff_id text NOT NULL REFERENCES staff(id),
    shop_id  text NOT NULL REFERENCES shops(id),
    role     text NOT NULL CHECK (role IN ('counter', 'technician', 'manager', 'area_admin')),
    PRIMARY KEY (staff_id, shop_id)
);

CREATE TABLE customers (
    id           text PRIMARY KEY,
    shop_id      text NOT NULL REFERENCES shops(id),
    name         text NOT NULL,
    phone        text CHECK (phone ~ '^\+[1-9][0-9]{7,14}$'),
    email        text CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' AND length(email) <= 254),
    address      text CHECK (address !~ '[\r\n]' AND length(address) BETWEEN 1 AND 200),
    id_check_ref text CHECK (length(id_check_ref) BETWEEN 1 AND 64),
    UNIQUE (shop_id, id)
);

CREATE TABLE items (
    id           text PRIMARY KEY,
    shop_id      text NOT NULL REFERENCES shops(id),
    asset_tag    text NOT NULL,
    name         text NOT NULL,
    status       text NOT NULL CHECK (status IN ('available', 'on_hire', 'in_repair', 'retired')),
    service_note text CHECK (length(service_note) <= 500),
    UNIQUE (shop_id, id),
    UNIQUE (shop_id, asset_tag)
);

CREATE TABLE rentals (
    id                   text PRIMARY KEY,
    shop_id              text NOT NULL REFERENCES shops(id),
    customer_id          text NOT NULL,
    item_id              text NOT NULL,
    hired_at             text NOT NULL CHECK (hired_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$'),
    hire_days            integer NOT NULL CHECK (hire_days BETWEEN 1 AND 28),
    daily_rate_pence     integer NOT NULL CHECK (daily_rate_pence >= 0),
    deposit_held_pence   integer NOT NULL CHECK (deposit_held_pence >= 0),
    returned_at          text CHECK (returned_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$'),
    return_condition     text CHECK (return_condition IN ('good', 'needs_cleaning', 'damaged', 'parts_missing')),
    damage_charge_pence  integer CHECK (damage_charge_pence BETWEEN 0 AND 50000),
    deposit_refund_pence integer CHECK (deposit_refund_pence >= 0 AND deposit_refund_pence <= deposit_held_pence),
    closed               boolean NOT NULL DEFAULT false,
    UNIQUE (shop_id, id),
    -- Composite keys: a rental can only point at a customer and item of its own shop.
    FOREIGN KEY (shop_id, customer_id) REFERENCES customers(shop_id, id),
    FOREIGN KEY (shop_id, item_id)     REFERENCES items(shop_id, id)
);

-- Notes as written at the counter. The author is the authenticated caller.
CREATE TABLE notes (
    shop_id     text NOT NULL REFERENCES shops(id),
    id          text NOT NULL,
    author_id   text NOT NULL REFERENCES staff(id),
    author_role text NOT NULL,
    written_at  text NOT NULL,
    source      text NOT NULL,
    text        text NOT NULL,
    scope       text[] NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shop_id, id)
);

-- One call to the AI for one note.
CREATE TABLE ai_tasks (
    id            text PRIMARY KEY,
    shop_id       text NOT NULL REFERENCES shops(id),
    note_id       text NOT NULL,
    status        text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error_kind    text,
    error_detail  text,
    prompt_version text NOT NULL,
    prompt_sha256 text NOT NULL,
    rules_sha256  text NOT NULL,
    ai_mode       text,
    request       jsonb NOT NULL,
    response_raw  text,
    flags         jsonb NOT NULL DEFAULT '[]',
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    latency_ms    integer,
    FOREIGN KEY (shop_id, note_id) REFERENCES notes(shop_id, id)
);

-- Pending changes: AI proposals, and people's sensitive edits that wait (rule R2).
CREATE TABLE proposals (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id         text NOT NULL REFERENCES shops(id),
    origin          text NOT NULL CHECK (origin IN ('ai', 'human')),
    task_id         text REFERENCES ai_tasks(id),
    requested_by    text REFERENCES staff(id),       -- NULL for AI proposals
    record_id       text NOT NULL,
    field           text NOT NULL,
    value           jsonb,                           -- the AI's (or requester's) value, as given
    unit            text,
    confidence      numeric,
    evidence        text,
    ai_reason       text,
    ai_sensitivity  text,                            -- the AI's own label: stored, never used
    sensitive       boolean,                         -- decided by the server
    approver_roles  text[],                          -- decided by the server
    base_value      jsonb,                           -- record value when proposed (stale check)
    status          text NOT NULL CHECK (status IN ('pending', 'blocked', 'approved', 'rejected')),
    block_reasons   text[] NOT NULL DEFAULT '{}',
    flags           text[] NOT NULL DEFAULT '{}',
    raw             jsonb,                           -- the proposal exactly as the AI sent it
    final_value     jsonb,                           -- the value actually applied
    decided_by      text REFERENCES staff(id),
    decided_at      timestamptz,
    decision_reason text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((origin = 'ai') = (task_id IS NOT NULL)),
    CHECK ((origin = 'human') = (requested_by IS NOT NULL))
);
CREATE INDEX proposals_shop_status ON proposals (shop_id, status);

CREATE TABLE abstentions (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id   text NOT NULL REFERENCES shops(id),
    task_id   text NOT NULL REFERENCES ai_tasks(id),
    record_id text NOT NULL,
    field     text NOT NULL,
    reason    text NOT NULL
);

-- Append-only audit trail, one hash chain per shop.
CREATE TABLE audit_log (
    shop_id      text NOT NULL REFERENCES shops(id),
    seq          bigint NOT NULL CHECK (seq >= 1),
    occurred_at  text NOT NULL,
    actor_id     text NOT NULL,
    action       text NOT NULL CHECK (action IN ('change_applied', 'proposal_rejected')),
    entity       text NOT NULL,
    record_id    text NOT NULL,
    field        text NOT NULL,
    old_value    text,          -- JSON text
    new_value    text,          -- JSON text
    proposal_id  bigint,
    detail       text NOT NULL, -- JSON text: reason, origin, AI value, ...
    rules_sha256 text NOT NULL,
    prev_hash    text NOT NULL,
    row_hash     text NOT NULL,
    PRIMARY KEY (shop_id, seq),
    UNIQUE (shop_id, prev_hash)  -- two rows can never claim the same predecessor: no forks
);

-- The tip of each shop's chain. Locked FOR UPDATE to serialise appends.
CREATE TABLE audit_head (
    shop_id   text PRIMARY KEY REFERENCES shops(id),
    last_seq  bigint NOT NULL,
    last_hash text NOT NULL
);
