-- ══════════════════════════════════════════════════════════════════════
-- 146 · THE OWNER'S AUTHORIZATION: EVERY ATTEMPT TO RECORD, REVOKE OR
--       INVALIDATE IT
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP. `bettor_funded_activation.authorize()` reads the owner's
-- authorization from `ingestion_state[bettor_funded_owner_authorization]`
-- and nothing wrote it: the only way to satisfy the last funded refusal was a
-- hand-written row with no record of who wrote it, what they were shown, or
-- which factors they presented. Nothing could revoke it, and nothing noticed
-- when the account binding or the approved limits moved underneath it.
--
-- WHAT THIS RECORDS. One row per attempt through
-- `bettor_owner_authorization` -- accepted, idempotent, refused or raised --
-- with the scope it named (account, venue, effective-limit digest and their
-- sha256), the operator the resolution key authenticates, and WHICH factors
-- were verified. Never the factors themselves.
--
--   action   RECORD      the owner signs a scope
--            REVOKE      the owner withdraws it
--            INVALIDATE  the binding or the approved limits moved, so the
--                        signed scope no longer describes what is in force
--
--   outcome  ACCEPTED | REFUSED | ALREADY_RECORDED | ALREADY_REVOKED
--
-- THE CONSUMER READS IT. `authorize()` accepts an owner record only if an
-- ACCEPTED RECORD row carries the same authorization_id and scope_sha, so a
-- hand-written ingestion_state row that never went through the writer is
-- refused as unauthenticated.
--
-- APPEND-ONLY. ADDITIVE.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_funded_owner_authorization_audit (
    audit_id          bigserial   PRIMARY KEY,
    at                timestamptz NOT NULL DEFAULT now(),
    action            text        NOT NULL,
    outcome           text        NOT NULL,
    refusal           text,
    authorization_id  text,
    account_id        text,
    venue             text,
    effective_digest  text,
    scope_sha         text,
    operator          text,
    statement         text,
    reason            text,
    authenticated_by  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    effect            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT bettor_owner_auth_audit_action_ck CHECK (
        action IN ('RECORD', 'REVOKE', 'INVALIDATE')),
    CONSTRAINT bettor_owner_auth_audit_outcome_ck CHECK (
        outcome IN ('ACCEPTED', 'REFUSED', 'ALREADY_RECORDED',
                    'ALREADY_REVOKED')),
    CONSTRAINT bettor_owner_auth_audit_refusal_ck CHECK (
        (outcome = 'REFUSED') = (refusal IS NOT NULL)),
    -- AN ACCEPTED RECORD NAMES EVERYTHING IT BINDS. This is the row the
    -- consumer looks for, so it cannot exist without the full scope.
    CONSTRAINT bettor_owner_auth_audit_accepted_scope_ck CHECK (
        NOT (action = 'RECORD' AND outcome = 'ACCEPTED')
        OR (authorization_id IS NOT NULL AND account_id IS NOT NULL
            AND venue IS NOT NULL AND effective_digest IS NOT NULL
            AND scope_sha IS NOT NULL AND operator IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS bettor_owner_auth_audit_at_idx
    ON bettor_funded_owner_authorization_audit (at DESC);
CREATE INDEX IF NOT EXISTS bettor_owner_auth_audit_id_idx
    ON bettor_funded_owner_authorization_audit (authorization_id)
    WHERE authorization_id IS NOT NULL;

CREATE OR REPLACE FUNCTION bettor_owner_auth_audit_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'owner-authorization audit row % records an attempt; it '
                    'is neither edited nor deleted', OLD.audit_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_owner_auth_audit_is_append_only_trg
    ON bettor_funded_owner_authorization_audit;
CREATE TRIGGER bettor_owner_auth_audit_is_append_only_trg
    BEFORE UPDATE OR DELETE ON bettor_funded_owner_authorization_audit
    FOR EACH ROW EXECUTE FUNCTION bettor_owner_auth_audit_is_append_only();

COMMIT;
