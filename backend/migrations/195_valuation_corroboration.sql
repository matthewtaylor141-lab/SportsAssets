-- 195 · VALUATION CORROBORATION (independent multi-book evidence for a
-- PinnAPI raw-websocket valuation; sportsassets/valuation_corroboration.py)
--
-- A PinnAPI read is Pinnacle alone, so its valuation carries outcome_books = 1
-- and bettor_external_shadow refuses it OUTCOME_DEPTH_BELOW_FLOOR
-- (MIN_OUTCOME_BOOKS = 2, unchanged). An independent odds-API observation of
-- the EXACT same event/outcome/period/line/market/settlement, stamped within
-- PINNACLE_MAX_AGE_S of the decision, may satisfy that floor. This table is
-- the separate, explicit record of that evidence -- one row per PinnAPI
-- valuation, qualified or refused, keyed by the valuation id. The PinnAPI
-- read's own count stays 1 (pinnapi_outcome_books, constrained below); the
-- corroborating count is a different column and is never copied onto it.
--
-- Written in the SAME transaction as the valuation row, so a valuation that
-- carries a corroboration block never exists without this row. No account,
-- control, order or funded table is touched. Like paper_decisions, the
-- valuation id is kept without a foreign key.
CREATE TABLE IF NOT EXISTS valuation_corroboration (
    valuation_id bigint PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    version text NOT NULL,
    qualified boolean NOT NULL,
    refusal text,
    why text,
    -- THE PINNAPI READ (the primary probability and reference)
    pinnapi_provider text NOT NULL,
    pinnapi_outcome_books integer NOT NULL,
    pinnapi_probability double precision,
    pinnapi_source_change_ms double precision,
    pinnapi_received_ms double precision,
    pinnapi_epoch bigint,
    pinnapi_runtime_id text,
    -- THE INDEPENDENT OBSERVATION
    corroborating_provider text,
    corroborating_source text,
    corroborating_observed_at timestamptz,   -- the provider's own stamp
    corroborating_received_at timestamptz,   -- our receipt of that read
    corroborating_outcome_books integer,
    corroborating_books text[] NOT NULL DEFAULT '{}',
    corroboration_age_s double precision,     -- at the decision instant
    max_age_s double precision NOT NULL,
    max_age_basis text NOT NULL,
    decision_at timestamptz NOT NULL,
    -- THE IDENTITY / MAPPING PROOF
    discovery_event_id text,
    feed_event_id text,
    corroborating_event_id text,
    outcome text,
    period text,
    market text,
    line double precision,
    us_market_slug text,
    settlement_rule text,
    identity jsonb NOT NULL,
    detail jsonb NOT NULL,
    CONSTRAINT valuation_corroboration_pinnapi_is_one_book
        CHECK (pinnapi_outcome_books = 1),
    CONSTRAINT valuation_corroboration_refusal_iff_unqualified
        CHECK ((qualified AND refusal IS NULL)
               OR (NOT qualified AND refusal IS NOT NULL)),
    CONSTRAINT valuation_corroboration_qualified_evidence
        CHECK (NOT qualified OR (
            corroborating_outcome_books >= 2
            AND corroborating_provider IS NOT NULL
            AND corroborating_provider <> pinnapi_provider
            AND corroborating_observed_at IS NOT NULL
            AND corroboration_age_s IS NOT NULL
            AND corroboration_age_s >= 0
            AND corroboration_age_s <= max_age_s))
);
CREATE INDEX IF NOT EXISTS valuation_corroboration_created
    ON valuation_corroboration (created_at DESC);
