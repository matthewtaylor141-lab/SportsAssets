-- ══════════════════════════════════════════════════════════════════════
-- 139 · A LOST ACKNOWLEDGEMENT GETS A DURABLE INVESTIGATION, AND IS CLOSED
--       ONLY BY AN AUTHENTICATED, AUDITED RESOLUTION
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT EXISTED. A send that raised with no answer leaves the intent UNRESOLVED
-- (venue_order_id NULL) and, when a reservation was taken, the reservation
-- AMBIGUOUS. Both keep their exposure counted, which is right. Nothing could
-- close them: this venue accepts no client order identity, so recovery never
-- adopts a term-matched order, and the only other exit from AMBIGUOUS needs a
-- search of TERMINAL orders the venue does not offer. A safe permanent freeze
-- is containment, not recovery -- and the one-open-group rule means one frozen
-- row stops the lane.
--
-- WHAT THIS ADDS:
--
--   bettor_funded_investigations    one row per lost acknowledgement. The
--                                   scheduled pass refreshes it with what the
--                                   venue shows on that market (resting
--                                   orders, term matches, the account's own
--                                   executions since the send). It never
--                                   changes the intent.
--
--   bettor_funded_resolution_audit  every authenticated resolution ATTEMPT,
--                                   accepted or refused, with what was read
--                                   at the venue to decide it. Append-only:
--                                   UPDATE and DELETE raise.
--
--   evidence kind OPERATOR_ATTESTED_NO_EXPOSURE
--                                   a HUMAN's statement that no exposure-
--                                   bearing order exists, recorded beside the
--                                   reads that support it. It is a separate
--                                   kind precisely so it is never mistaken for
--                                   the venue's own VENUE_HAS_NO_SUCH_ORDER.
--
-- ADDITIVE. New tables; the evidence CHECK is widened by one kind.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_funded_investigations (
    investigation_id  text PRIMARY KEY,
    intent_id         text        NOT NULL UNIQUE,
    operation_id      text,
    account_id        text        NOT NULL,
    venue             text        NOT NULL,
    us_market_slug    text        NOT NULL,
    intent_kind       text        NOT NULL,
    venue_intent      text        NOT NULL,
    limit_price       numeric     NOT NULL,
    quantity          numeric     NOT NULL,
    sent_at           timestamptz,
    opened_at         timestamptz NOT NULL DEFAULT now(),
    state             text        NOT NULL DEFAULT 'OPEN',
    reads             integer     NOT NULL DEFAULT 0,
    last_read_at      timestamptz,
    last_read_sha     text,
    last_read         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    resolved_at       timestamptz,
    resolution        jsonb,
    CONSTRAINT bettor_funded_investigation_state_ck CHECK (
        state IN ('OPEN', 'RESOLVED_ORDER_NAMED', 'RESOLVED_NO_EXPOSURE')),
    CONSTRAINT bettor_funded_investigation_resolved_ck CHECK (
        (state = 'OPEN') = (resolved_at IS NULL)
        AND (resolved_at IS NULL) = (resolution IS NULL)),
    CONSTRAINT bettor_funded_investigation_kind_ck CHECK (
        intent_kind IN ('ENTRY', 'EXIT'))
);

CREATE INDEX IF NOT EXISTS bettor_funded_investigations_open_idx
    ON bettor_funded_investigations (account_id, venue) WHERE state = 'OPEN';

-- A RESOLVED INVESTIGATION IS NOT REOPENED OR REWRITTEN. The resolution is what
-- released or claimed exposure; editing it afterwards would rewrite why.
CREATE OR REPLACE FUNCTION bettor_funded_investigation_resolution_is_final()
RETURNS trigger AS $$
BEGIN
    IF OLD.state <> 'OPEN' THEN
        RAISE EXCEPTION 'investigation % was resolved (%) at %; it is not '
                        'edited afterwards',
            OLD.investigation_id, OLD.state, OLD.resolved_at;
    END IF;
    IF NEW.intent_id IS DISTINCT FROM OLD.intent_id
       OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
       OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.venue IS DISTINCT FROM OLD.venue
       OR NEW.us_market_slug IS DISTINCT FROM OLD.us_market_slug
       OR NEW.venue_intent IS DISTINCT FROM OLD.venue_intent
       OR NEW.limit_price IS DISTINCT FROM OLD.limit_price
       OR NEW.quantity IS DISTINCT FROM OLD.quantity
       OR NEW.sent_at IS DISTINCT FROM OLD.sent_at
       OR NEW.opened_at IS DISTINCT FROM OLD.opened_at THEN
        RAISE EXCEPTION 'investigation % describes one request; what was sent '
                        'is not edited', OLD.investigation_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_investigation_resolution_is_final_trg
    ON bettor_funded_investigations;
CREATE TRIGGER bettor_funded_investigation_resolution_is_final_trg
    BEFORE UPDATE ON bettor_funded_investigations
    FOR EACH ROW EXECUTE FUNCTION
        bettor_funded_investigation_resolution_is_final();

CREATE OR REPLACE FUNCTION bettor_funded_investigation_is_not_deleted()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'investigation % is a record of a lost acknowledgement; '
                    'it is not deleted', OLD.investigation_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_investigation_is_not_deleted_trg
    ON bettor_funded_investigations;
CREATE TRIGGER bettor_funded_investigation_is_not_deleted_trg
    BEFORE DELETE ON bettor_funded_investigations
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_investigation_is_not_deleted();

CREATE TABLE IF NOT EXISTS bettor_funded_resolution_audit (
    audit_id          bigserial PRIMARY KEY,
    at                timestamptz NOT NULL DEFAULT now(),
    intent_id         text        NOT NULL,
    investigation_id  text,
    requested         text        NOT NULL,
    outcome           text        NOT NULL,
    refusal           text,
    attested_by       text,
    statement         text,
    authenticated_by  jsonb       NOT NULL,
    venue_reads       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    effect            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT bettor_funded_resolution_audit_outcome_ck CHECK (
        outcome IN ('ACCEPTED', 'REFUSED', 'ALREADY_RESOLVED')),
    CONSTRAINT bettor_funded_resolution_audit_refusal_ck CHECK (
        (outcome = 'REFUSED') = (refusal IS NOT NULL)),
    -- An unrecognised request is audited too, under its own word.
    CONSTRAINT bettor_funded_resolution_audit_requested_ck CHECK (
        requested IN ('NAME_THE_ORDER', 'NO_EXPOSURE_EXISTS', 'UNRECOGNISED'))
);

CREATE INDEX IF NOT EXISTS bettor_funded_resolution_audit_intent_idx
    ON bettor_funded_resolution_audit (intent_id, at);

CREATE OR REPLACE FUNCTION bettor_funded_resolution_audit_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit row % records a resolution attempt; it is neither '
                    'edited nor deleted', OLD.audit_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_resolution_audit_is_append_only_trg
    ON bettor_funded_resolution_audit;
CREATE TRIGGER bettor_funded_resolution_audit_is_append_only_trg
    BEFORE UPDATE OR DELETE ON bettor_funded_resolution_audit
    FOR EACH ROW EXECUTE FUNCTION
        bettor_funded_resolution_audit_is_append_only();

-- ── THE EVIDENCE TABLE LEARNS ONE MORE KIND ─────────────────────────
DO $$
BEGIN
    IF to_regclass('bettor_funded_operation_evidence') IS NOT NULL THEN
        ALTER TABLE bettor_funded_operation_evidence
            DROP CONSTRAINT IF EXISTS bettor_funded_evidence_kind_ck;
        ALTER TABLE bettor_funded_operation_evidence
            ADD CONSTRAINT bettor_funded_evidence_kind_ck CHECK (
                kind IN ('VENUE_NAMED_THE_ORDER', 'VENUE_HAS_NO_SUCH_ORDER',
                         'READ_ESTABLISHED_NOTHING',
                         'OPERATOR_ATTESTED_NO_EXPOSURE'));
        -- AN ATTESTATION NAMES WHO MADE IT, AND ITS READS FOUND NOTHING.
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = 'bettor_funded_evidence_attested_ck') THEN
            ALTER TABLE bettor_funded_operation_evidence
                ADD CONSTRAINT bettor_funded_evidence_attested_ck CHECK (
                    kind <> 'OPERATOR_ATTESTED_NO_EXPOSURE'
                    OR (results_returned = 0
                        AND coalesce(raw ->> 'attested_by', '') <> ''
                        AND coalesce(raw ->> 'audit_id', '') <> ''));
        END IF;
    END IF;
END $$;

COMMIT;
