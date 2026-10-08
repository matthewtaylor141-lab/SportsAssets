-- BETTOR RED TEAM CLOSEOUT V1
-- Additive append-only evidence only. Renumber this migration if 314 is taken.
-- No order authority, no live activation, no historical PAPER mutation.

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
    evidence_hash text NOT NULL
);

CREATE TABLE IF NOT EXISTS red_team_pair_execution_receipts (
    receipt_id text PRIMARY KEY,
    observed_at timestamptz NOT NULL DEFAULT now(),
    implementation_sha text NOT NULL,
    pair_id text NOT NULL,
    state text NOT NULL,
    target_qty numeric NOT NULL,
    leg_a_filled numeric NOT NULL DEFAULT 0,
    leg_b_filled numeric NOT NULL DEFAULT 0,
    matched_qty numeric NOT NULL DEFAULT 0,
    unmatched_qty numeric NOT NULL DEFAULT 0,
    execution_locked boolean NOT NULL DEFAULT false,
    reason text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);

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
    evidence_hash text NOT NULL
);

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
