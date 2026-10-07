-- 315 · RED TEAM CLOSEOUT V1: APPEND-ONLY SAFETY / READINESS RECEIPTS.
-- The package's sql/314_red_team_closeout.sql, renumbered (314 is the Kalshi
-- canonical venue), with the receipts the bindings need beside it. These
-- are readiness and safety receipts only: no order, cancel, funding, sizing
-- or capital authority, no P&L, no trade ledger (the PAPER ledger and
-- Adriana's migration-265 tables stay the systems of record), no historical
-- PAPER mutation. Every red_team_* table refuses UPDATE and DELETE.

-- ── the package's five tables (columns as delivered; the pair and
--    exposure tables carry the extra fields the directive names) ──────────
CREATE TABLE IF NOT EXISTS red_team_readiness_receipts (
    receipt_id text PRIMARY KEY,
    computed_at timestamptz NOT NULL DEFAULT now(),
    implementation_sha text NOT NULL,
    status text NOT NULL CHECK (status IN ('PAPER_SHADOW_ONLY','CAPITAL_CANDIDATE')),
    blockers jsonb NOT NULL DEFAULT '[]'::jsonb,
    checks jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_hash text NOT NULL
);

CREATE TABLE IF NOT EXISTS red_team_profit_breaker_receipts (
    receipt_id text PRIMARY KEY,
    computed_at timestamptz NOT NULL DEFAULT now(),
    implementation_sha text NOT NULL,
    mechanism text NOT NULL,
    status text NOT NULL CHECK (status IN ('ELIGIBLE','SHADOW_ONLY','DISABLED')),
    independent_events integer NOT NULL CHECK (independent_events >= 0),
    mean_residual numeric,
    confidence_bound numeric,
    cumulative_residual numeric,
    reason text,
    evidence_hash text NOT NULL,
    expected_pnl numeric,
    realized_pnl numeric
);

CREATE TABLE IF NOT EXISTS red_team_pair_execution_receipts (
    receipt_id text PRIMARY KEY,
    observed_at timestamptz NOT NULL DEFAULT now(),
    implementation_sha text NOT NULL,
    pair_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('DISCOVERED','REVALIDATED','ARMED',
        'PARTIAL','MATCHED','LOCKED','REPAIR_REQUIRED','FROZEN')),
    target_qty numeric NOT NULL,
    leg_a_filled numeric NOT NULL DEFAULT 0,
    leg_b_filled numeric NOT NULL DEFAULT 0,
    matched_qty numeric NOT NULL DEFAULT 0,
    unmatched_qty numeric NOT NULL DEFAULT 0,
    execution_locked boolean NOT NULL DEFAULT false,
    reason text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    leg_a_target numeric,
    leg_b_target numeric,
    unmatched_risk_usd numeric,
    max_unmatched_loss_usd numeric,
    economics_label text CHECK (economics_label IN (
        'GUARANTEED_AFTER_COSTS_IF_FILLED', 'NOT_GUARANTEED')),
    execution_label text NOT NULL DEFAULT 'NOT_EXECUTION_LOCKED'
        CHECK (execution_label IN ('EXECUTION_LOCKED', 'NOT_EXECUTION_LOCKED')),
    mode text NOT NULL DEFAULT 'SHADOW' CHECK (mode = 'SHADOW'),
    CHECK (execution_locked = (execution_label = 'EXECUTION_LOCKED')),
    -- SHADOW: no pair is ever execution-locked without both legs at target
    CHECK (NOT execution_locked OR (leg_a_filled = target_qty
                                    AND leg_b_filled = target_qty))
);
CREATE INDEX IF NOT EXISTS red_team_pair_execution_receipts_at
    ON red_team_pair_execution_receipts (observed_at DESC);

CREATE TABLE IF NOT EXISTS red_team_claim_exposure_receipts (
    receipt_id text PRIMARY KEY,
    observed_at timestamptz NOT NULL DEFAULT now(),
    implementation_sha text NOT NULL,
    claim_key text NOT NULL,
    event_key text NOT NULL,
    payoff_fingerprint text NOT NULL,
    alias_count integer NOT NULL CHECK (alias_count >= 1),
    signed_notional numeric NOT NULL,
    aliases jsonb NOT NULL,
    evidence_hash text NOT NULL,
    event_notional numeric,
    claim_limit_usd numeric,
    event_limit_usd numeric,
    eligible boolean,
    blockers jsonb NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS red_team_claim_exposure_receipts_at
    ON red_team_claim_exposure_receipts (observed_at DESC);

CREATE TABLE IF NOT EXISTS red_team_release_receipts (
    receipt_id text PRIMARY KEY,
    observed_at timestamptz NOT NULL DEFAULT now(),
    accepted_base_sha text NOT NULL,
    tested_sha text NOT NULL,
    release_sha text NOT NULL,
    deployed_sha text,
    descendant_of_base boolean NOT NULL,
    backend_tests_green boolean NOT NULL,
    capital_critical_green boolean NOT NULL,
    commit_guard_green boolean NOT NULL,
    engine_diagnostic_green boolean NOT NULL,
    migration_fingerprint_match boolean NOT NULL,
    blockers jsonb NOT NULL DEFAULT '[]'::jsonb
);

-- ── the bindings' receipts ──────────────────────────────────────────────

-- every other control's verdict, per pass (truth quorum, venue health,
-- fee evidence, settlement certificates, twin, sample integrity, capacity,
-- Karen value, attribution, credential classes, migration integrity, UI
-- truth): status GREEN / RED / UNKNOWN with the exact blockers
CREATE TABLE IF NOT EXISTS red_team_control_receipts (
    receipt_id text PRIMARY KEY,
    computed_at timestamptz NOT NULL DEFAULT now(),
    implementation_sha text NOT NULL,
    control text NOT NULL,
    status text NOT NULL CHECK (status IN ('GREEN', 'RED', 'UNKNOWN')),
    blockers jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_hash text NOT NULL
);
CREATE INDEX IF NOT EXISTS red_team_control_receipts_control_at
    ON red_team_control_receipts (control, computed_at DESC);

-- the settlement-rule fingerprint each canonical alias was CERTIFIED
-- against; a later pass reading a different fingerprint appends
-- INVALIDATED (RULES_CHANGED_SINCE_CERTIFICATION) and the alias loses
-- eligibility until a full re-mapping certifies the new rules
CREATE TABLE IF NOT EXISTS red_team_settlement_certificates (
    certificate_id text PRIMARY KEY,
    alias_key text NOT NULL,
    venue text NOT NULL,
    market_id text NOT NULL,
    claim_fingerprint text,
    rules_sha256 text,
    status text NOT NULL CHECK (status IN ('CERTIFIED', 'INVALIDATED')),
    reason text,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS red_team_settlement_certificates_alias
    ON red_team_settlement_certificates (alias_key, at DESC);

-- Kalshi's own per-series fee terms (GET /series fee_type,
-- fee_multiplier) as first observed: the fee evidence a Kalshi route needs
-- (schedule id, version, effective-since); a series whose terms change
-- gets a new row, never an edit
CREATE TABLE IF NOT EXISTS red_team_kalshi_series_fees (
    fee_id text PRIMARY KEY,
    series_ticker text NOT NULL,
    fee_type text,
    fee_multiplier numeric,
    first_observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS red_team_kalshi_series_fees_series
    ON red_team_kalshi_series_fees (series_ticker, first_observed_at DESC);

-- preregistration and holdout use: a candidate set registered BEFORE any
-- result is read, and every opening of a holdout, appended
CREATE TABLE IF NOT EXISTS red_team_holdout_registry (
    entry_id text PRIMARY KEY,
    study text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('PREREGISTER', 'HOLDOUT_OPEN',
                                       'SELECTION')),
    candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
    candidate_count integer NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    implementation_sha text NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS red_team_holdout_registry_study
    ON red_team_holdout_registry (study, at);

-- the canonical aliases carry the rules fingerprint they were built from
-- and the certificate state (forward-only: 314 is not edited)
ALTER TABLE canonical_claim_aliases
    ADD COLUMN IF NOT EXISTS rules_sha256 text;
ALTER TABLE canonical_claim_aliases
    ADD COLUMN IF NOT EXISTS certificate_status text;
ALTER TABLE canonical_route_receipts
    ADD COLUMN IF NOT EXISTS fee_evidence jsonb;

-- ── append-only, in the database ────────────────────────────────────────
CREATE OR REPLACE FUNCTION red_team_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'red-team evidence is append-only';
END $$;

DROP TRIGGER IF EXISTS red_team_readiness_append_only ON red_team_readiness_receipts;
CREATE TRIGGER red_team_readiness_append_only
BEFORE UPDATE OR DELETE ON red_team_readiness_receipts
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();

DROP TRIGGER IF EXISTS red_team_profit_append_only ON red_team_profit_breaker_receipts;
CREATE TRIGGER red_team_profit_append_only
BEFORE UPDATE OR DELETE ON red_team_profit_breaker_receipts
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();

DROP TRIGGER IF EXISTS red_team_pair_append_only ON red_team_pair_execution_receipts;
CREATE TRIGGER red_team_pair_append_only
BEFORE UPDATE OR DELETE ON red_team_pair_execution_receipts
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();

DROP TRIGGER IF EXISTS red_team_claim_append_only ON red_team_claim_exposure_receipts;
CREATE TRIGGER red_team_claim_append_only
BEFORE UPDATE OR DELETE ON red_team_claim_exposure_receipts
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();

DROP TRIGGER IF EXISTS red_team_release_append_only ON red_team_release_receipts;
CREATE TRIGGER red_team_release_append_only
BEFORE UPDATE OR DELETE ON red_team_release_receipts
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();

DROP TRIGGER IF EXISTS red_team_control_append_only ON red_team_control_receipts;
CREATE TRIGGER red_team_control_append_only
BEFORE UPDATE OR DELETE ON red_team_control_receipts
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();

DROP TRIGGER IF EXISTS red_team_certificate_append_only ON red_team_settlement_certificates;
CREATE TRIGGER red_team_certificate_append_only
BEFORE UPDATE OR DELETE ON red_team_settlement_certificates
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();

DROP TRIGGER IF EXISTS red_team_series_fee_append_only ON red_team_kalshi_series_fees;
CREATE TRIGGER red_team_series_fee_append_only
BEFORE UPDATE OR DELETE ON red_team_kalshi_series_fees
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();

DROP TRIGGER IF EXISTS red_team_holdout_append_only ON red_team_holdout_registry;
CREATE TRIGGER red_team_holdout_append_only
BEFORE UPDATE OR DELETE ON red_team_holdout_registry
FOR EACH ROW EXECUTE FUNCTION red_team_append_only();
