-- ══════════════════════════════════════════════════════════════════════
-- 145 · A HISTORY OF RECONCILIATION REPORTS, AND A WAY TO BOOK A SETTLEMENT
--       CORRECTION THE VENUE HAS MADE
-- ══════════════════════════════════════════════════════════════════════
--
-- TWO GAPS, ONE MIGRATION (both are records an owner decides on).
--
-- ── 1 · THE RECONCILIATION HAD NO HISTORY ──────────────────────────────
--
-- `record_reconciliation` wrote ONE global `ingestion_state` key and
-- overwrote it on every run. The evidence an owner reads before deciding
-- whether an account may be unpaused was therefore whatever the last run
-- said, with nothing to compare it against and no record that an earlier
-- run disagreed. `bettor_account_reconciliation_reports` keeps every
-- discrepancy report, append-only, with its verdict fields beside the body
-- and a sha of the body so a later reader can tell the report they were
-- shown from any other. THE LATEST-EVIDENCE KEY IS STILL WRITTEN for its
-- consumers; this table is the record behind it, not a replacement.
--
-- A REPORT IS EVIDENCE FOR A DECISION, NOT THE DECISION. Nothing that writes
-- this table unpauses an account or changes its accounting status.
--
-- ── 2 · A SETTLEMENT RE-READ THAT DISAGREES COULD NEVER BE ANSWERED ────
--
-- 141 records every re-read of a settled leg, and a DISAGREES re-read refuses
-- the account new exposure while it is the leg's newest established one. But
-- nothing could book the correction, so once the leg left the seven-day
-- re-read window the disagreement stood FOREVER: the account was refused new
-- exposure permanently on a question nobody was able to close.
--
-- What this adds:
--
--   bettor_funded_economics.kind     widened by SETTLEMENT_CORRECTION: the
--                                    DELTA between the corrected payout and the
--                                    booked one, as its own signed event. Never
--                                    a second SETTLEMENT row -- the original
--                                    stays the record of what was booked when.
--
--   bettor_funded_settlement_corrections
--                                    one row per answered re-read: which
--                                    re-read it answers, from/to reading and
--                                    price, the delta, the economics event, who
--                                    authenticated it, their statement, and the
--                                    sha of the re-read they decided on.
--                                    Append-only.
--
--   bettor_funded_correction_audit   every ATTEMPT, accepted or refused, with
--                                    how the caller authenticated (which
--                                    factors were verified -- never the factors).
--                                    Append-only.
--
-- ADDITIVE: new tables; one CHECK widened by one kind.

BEGIN;

-- ── 1 · RECONCILIATION REPORTS ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS bettor_account_reconciliation_reports (
    report_id          text        PRIMARY KEY,
    account_id         text        NOT NULL,
    venue              text        NOT NULL,
    recorded_at        timestamptz NOT NULL,
    recorded_by        text        NOT NULL,
    authoritative      boolean     NOT NULL,
    would_be_eligible  boolean     NOT NULL,
    blocking_count     integer     NOT NULL DEFAULT 0,
    report             jsonb       NOT NULL,
    report_sha         text        NOT NULL,
    -- A REPORT THAT WAS NOT AUTHORITATIVE CANNOT SAY THE ACCOUNT WOULD BE
    -- ELIGIBLE: an incomplete read is not a clean one.
    CONSTRAINT bettor_recon_report_eligible_needs_authority_ck CHECK (
        NOT would_be_eligible OR (authoritative AND blocking_count = 0)),
    CONSTRAINT bettor_recon_report_sha_ck CHECK (length(report_sha) = 64)
);

CREATE INDEX IF NOT EXISTS bettor_recon_reports_account_idx
    ON bettor_account_reconciliation_reports (account_id, recorded_at DESC);

CREATE OR REPLACE FUNCTION bettor_recon_report_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'reconciliation report % is a record; it is not edited',
        OLD.report_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_recon_report_is_append_only_trg
    ON bettor_account_reconciliation_reports;
CREATE TRIGGER bettor_recon_report_is_append_only_trg
    BEFORE UPDATE OR DELETE ON bettor_account_reconciliation_reports
    FOR EACH ROW EXECUTE FUNCTION bettor_recon_report_is_append_only();

-- ── 2a · THE ECONOMICS LEDGER LEARNS ONE MORE KIND ────────────────────
ALTER TABLE bettor_funded_economics
    DROP CONSTRAINT IF EXISTS bettor_funded_economics_kind_check;
ALTER TABLE bettor_funded_economics
    ADD CONSTRAINT bettor_funded_economics_kind_check CHECK (
        kind IN ('ENTRY_COST', 'EXIT_PROCEEDS', 'FEE', 'SETTLEMENT',
                 'FEE_ADJUSTMENT', 'SETTLEMENT_CORRECTION'));
-- A CORRECTION IS NAMED BY THE RE-READ IT ANSWERS, so a retried correction
-- cannot be booked twice under two ids.
ALTER TABLE bettor_funded_economics
    DROP CONSTRAINT IF EXISTS bettor_funded_economics_correction_id_ck;
ALTER TABLE bettor_funded_economics
    ADD CONSTRAINT bettor_funded_economics_correction_id_ck CHECK (
        kind <> 'SETTLEMENT_CORRECTION'
        OR event_id LIKE 'fev:%:SETTLEMENT_CORRECTION:%');

-- ── 2b · THE CORRECTIONS ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bettor_funded_settlement_corrections (
    correction_id       text        PRIMARY KEY,
    -- NOT FOREIGN KEYS, for 141's reason: this is a record of a decision made
    -- about those ids at that instant, and a key would make it block every
    -- removal of the rows it names. The writer checks all three in the same
    -- transaction that inserts this row.
    intent_id           text        NOT NULL,
    recheck_id          bigint      NOT NULL UNIQUE,
    from_reading        text        NOT NULL,
    from_price          numeric,
    to_reading          text        NOT NULL,
    to_price            numeric,
    delta_usd           numeric     NOT NULL,
    economics_event_id  text        NOT NULL UNIQUE,
    audit_id            bigint      NOT NULL,
    operator            text        NOT NULL,
    statement           text        NOT NULL,
    seen_recheck_sha    text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT bettor_funded_correction_to_reading_ck CHECK (
        to_reading IN ('REPORTED_SETTLEMENT', 'EXPLICIT_VOID')),
    -- A PRICED READING NAMES ITS PRICE; A VOID HAS NONE.
    CONSTRAINT bettor_funded_correction_to_price_ck CHECK (
        (to_reading = 'REPORTED_SETTLEMENT') = (to_price IS NOT NULL)),
    CONSTRAINT bettor_funded_correction_operator_ck CHECK (
        length(btrim(operator)) > 0),
    CONSTRAINT bettor_funded_correction_statement_ck CHECK (
        length(btrim(statement)) >= 20),
    CONSTRAINT bettor_funded_correction_sha_ck CHECK (
        length(seen_recheck_sha) = 64),
    CONSTRAINT bettor_funded_correction_event_ck CHECK (
        economics_event_id = 'fev:' || intent_id || ':SETTLEMENT_CORRECTION:'
                             || recheck_id::text)
);

CREATE INDEX IF NOT EXISTS bettor_funded_corrections_intent_idx
    ON bettor_funded_settlement_corrections (intent_id, created_at);

CREATE OR REPLACE FUNCTION bettor_funded_correction_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'settlement correction % is a record of a booked '
                    'decision; it is neither edited nor deleted',
        OLD.correction_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_correction_is_append_only_trg
    ON bettor_funded_settlement_corrections;
CREATE TRIGGER bettor_funded_correction_is_append_only_trg
    BEFORE UPDATE OR DELETE ON bettor_funded_settlement_corrections
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_correction_is_append_only();

-- ── 2c · EVERY ATTEMPT, AUDITED ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS bettor_funded_correction_audit (
    audit_id          bigserial   PRIMARY KEY,
    at                timestamptz NOT NULL DEFAULT now(),
    intent_id         text        NOT NULL,
    recheck_id        bigint,
    outcome           text        NOT NULL,
    refusal           text,
    operator          text,
    statement         text,
    authenticated_by  jsonb       NOT NULL,
    seen              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    effect            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT bettor_funded_correction_audit_outcome_ck CHECK (
        outcome IN ('ACCEPTED', 'REFUSED', 'ALREADY_CORRECTED')),
    CONSTRAINT bettor_funded_correction_audit_refusal_ck CHECK (
        (outcome = 'REFUSED') = (refusal IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS bettor_funded_correction_audit_intent_idx
    ON bettor_funded_correction_audit (intent_id, at);

CREATE OR REPLACE FUNCTION bettor_funded_correction_audit_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'correction audit row % records an attempt; it is neither '
                    'edited nor deleted', OLD.audit_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_correction_audit_is_append_only_trg
    ON bettor_funded_correction_audit;
CREATE TRIGGER bettor_funded_correction_audit_is_append_only_trg
    BEFORE UPDATE OR DELETE ON bettor_funded_correction_audit
    FOR EACH ROW EXECUTE FUNCTION
        bettor_funded_correction_audit_is_append_only();

COMMIT;
