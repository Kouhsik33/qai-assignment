-- The application role and what it may touch. The app is not the table owner,
-- not a superuser, and cannot bypass row-level security.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sc_app') THEN
        CREATE ROLE sc_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sc_app;
GRANT USAGE ON SCHEMA public TO sc_app;

-- Directory tables: read only.
GRANT SELECT ON shops, staff, memberships TO sc_app;

-- Business tables: read, and update ONLY the changeable fields. No INSERT, no DELETE,
-- and shop_id, deposit_held_pence, hired_at, etc. cannot be updated at all.
GRANT SELECT ON customers, items, rentals TO sc_app;
GRANT UPDATE (phone, email, address, id_check_ref) ON customers TO sc_app;
GRANT UPDATE (status, service_note) ON items TO sc_app;
GRANT UPDATE (returned_at, return_condition, hire_days, damage_charge_pence, deposit_refund_pence) ON rentals TO sc_app;

-- Workflow tables.
GRANT SELECT, INSERT ON notes, abstentions TO sc_app;
GRANT SELECT, INSERT ON ai_tasks TO sc_app;
GRANT UPDATE (status, error_kind, error_detail, response_raw, flags, finished_at, latency_ms) ON ai_tasks TO sc_app;
GRANT SELECT, INSERT ON proposals TO sc_app;
GRANT UPDATE (status, final_value, decided_by, decided_at, decision_reason) ON proposals TO sc_app;

-- Audit: append and read only.
GRANT SELECT, INSERT ON audit_log TO sc_app;
GRANT SELECT ON audit_head TO sc_app;
GRANT UPDATE (last_seq, last_hash) ON audit_head TO sc_app;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO sc_app;

-- Row-level security: every shop table only shows rows of the shop set for this
-- transaction (SET LOCAL app.shop_id). If it is not set, current_setting returns
-- NULL and no row matches: fail closed.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['customers', 'items', 'rentals', 'notes', 'ai_tasks',
                             'proposals', 'abstentions', 'audit_log', 'audit_head']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS shop_isolation ON %I', t);
        EXECUTE format($p$CREATE POLICY shop_isolation ON %I
                         USING (shop_id = current_setting('app.shop_id', true))
                         WITH CHECK (shop_id = current_setting('app.shop_id', true))$p$, t);
    END LOOP;
END $$;

-- Proposal lifecycle, enforced in the database as well as in the app:
-- inserted only as pending or blocked; only pending may change; terminal states are final;
-- every decision has who, when and why; nobody decides their own pending change (R4).
CREATE OR REPLACE FUNCTION proposals_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('pending', 'blocked') THEN
            RAISE EXCEPTION 'proposals must be created as pending or blocked, not %', NEW.status;
        END IF;
        IF NEW.decided_by IS NOT NULL OR NEW.decided_at IS NOT NULL OR NEW.final_value IS NOT NULL THEN
            RAISE EXCEPTION 'a new proposal cannot carry a decision';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'proposal % is already % and cannot change', OLD.id, OLD.status;
    END IF;
    IF NEW.status NOT IN ('approved', 'rejected') THEN
        RAISE EXCEPTION 'a pending proposal can only become approved or rejected';
    END IF;
    IF NEW.decided_by IS NULL OR NEW.decided_at IS NULL OR coalesce(btrim(NEW.decision_reason), '') = '' THEN
        RAISE EXCEPTION 'a decision needs who, when and why';
    END IF;
    IF NEW.requested_by IS NOT NULL AND NEW.decided_by = NEW.requested_by THEN
        RAISE EXCEPTION 'nobody decides their own pending change (R4)';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS proposals_guard ON proposals;
CREATE TRIGGER proposals_guard BEFORE INSERT OR UPDATE ON proposals
    FOR EACH ROW EXECUTE FUNCTION proposals_guard();

-- Audit rows are never updated or deleted (belt and braces: sc_app has no such grant).
CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END $$;

DROP TRIGGER IF EXISTS audit_append_only ON audit_log;
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_append_only();
