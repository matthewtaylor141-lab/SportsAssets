-- THE DIRECT INSTITUTIONAL WORKER, and the latencies it can finally
-- measure separately.
--
-- Owner directive 2026-09-20 00:1xZ: a production credential now sits
-- directly on sportsassets-workers. "DO NOT USE THE GITHUB BRIDGE AS
-- THE PRIMARY MARKET-DATA PATH."
--
-- THE THIRD REGIME IS A THIRD REGIME, not a rename. GITHUB_BRIDGE rows
-- already exist and stay exactly as they are; a book the worker holds
-- in memory is a different execution environment from one a CI runner
-- fetched minutes late, and pooling them would describe neither. The
-- CHECK is widened rather than replaced so that every historical row
-- keeps meaning what it meant when it was written.
--
-- WHY §8 NEEDS THIS MANY COLUMNS. "Do not collapse these into one
-- latency number." One number cannot distinguish a slow venue from a
-- slow model from a slow persist, and the three have completely
-- different remedies. Each instant is recorded where it happened and
-- every interval is derived from two of them, so a later reader can
-- re-derive any of it and disagree with us if we were wrong.

BEGIN;

ALTER TABLE bettor_l2_evidence
    DROP CONSTRAINT IF EXISTS bettor_l2_evidence_regime;
ALTER TABLE bettor_l2_evidence
    ADD CONSTRAINT bettor_l2_evidence_regime
        CHECK (latency_regime IN ('GITHUB_BRIDGE',
                                  'PERSISTENT_INSTITUTIONAL_WORKER',
                                  'DIRECT_INSTITUTIONAL_WORKER'));

ALTER TABLE bettor_l2_evidence
    ADD COLUMN IF NOT EXISTS evidence_environment TEXT,
    ADD COLUMN IF NOT EXISTS market_data_lag_ms   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS freshness_status     TEXT;

-- ── §8: every instant, and every interval derived from two of them ──

ALTER TABLE bettor_experimental_decisions
    -- the instants
    ADD COLUMN IF NOT EXISTS venue_source_timestamp     TEXT,
    ADD COLUMN IF NOT EXISTS bettor_received_timestamp  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS features_sealed_timestamp  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS model_start_timestamp      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS model_end_timestamp        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS modeled_send_timestamp     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS modeled_arrival_timestamp  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS persisted_timestamp        TIMESTAMPTZ,
    -- the intervals, each from two named instants
    ADD COLUMN IF NOT EXISTS market_data_lag_ms         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS feature_compute_ms         DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS model_compute_ms           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS source_to_decision_ms      DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS modeled_execution_latency_ms DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS source_to_modeled_arrival_ms DOUBLE PRECISION,
    -- §6/§9: what the book was, and that the latency is MODELED
    ADD COLUMN IF NOT EXISTS book_freshness_status      TEXT,
    ADD COLUMN IF NOT EXISTS book_age_ms                DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS execution_latency_basis    TEXT,
    ADD COLUMN IF NOT EXISTS evidence_environment       TEXT;

ALTER TABLE bettor_experimental_decisions
    DROP CONSTRAINT IF EXISTS bettor_exp_latency_basis;
-- §9: "Never represent it as observed production execution latency."
-- The basis is a word on the row, and only two words are admissible
-- until an execution latency has actually been measured -- which it
-- has not been, because this lane has never sent an order.
ALTER TABLE bettor_experimental_decisions
    ADD CONSTRAINT bettor_exp_latency_basis
        CHECK (execution_latency_basis IS NULL
               OR execution_latency_basis IN (
                   'MODELED_EXECUTION_LATENCY',
                   'OBSERVED_TRANSPORT_LATENCY_NOT_EXECUTION'));

CREATE INDEX IF NOT EXISTS bettor_exp_decisions_environment_idx
    ON bettor_experimental_decisions (evidence_environment,
                                      decision_timestamp DESC);

COMMIT;
