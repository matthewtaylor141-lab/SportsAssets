-- UNSELECTED PROSPECTIVE PMUS STATE CAPTURE. Read only.
--
-- Owner directive 2026-09-20, "APPROVED -- START THE READ-ONLY
-- PROSPECTIVE PMUS UNSELECTED CAPTURE":
--
--   §3 "No orders. No capital. No shadow fill fabrication. No mandate
--      activation. Freeze the sampling rule BEFORE row 1. ...
--      Selection must be independent of future economics."
--
--   §6 "Do not call any future observation a fill."
--
-- WHAT THIS MEASURES, AND WHAT IT DOES NOT.
--
-- Object A of the owner's §2 taxonomy -- E[SETTLEMENT - QUOTE | MARKET
-- STATE] -- and nothing else. It does NOT observe whether a
-- hypothetical resting order would have filled, so it solves
-- STATE-selection bias and leaves FILL-selection bias exactly where it
-- was. The quantity it yields is UNCONDITIONAL_QUOTE_TO_SETTLEMENT_
-- VALUE. The name UNCONDITIONAL_MAKER_ADVERSE_SELECTION is refused in
-- code (bettor_state_capture.forbidden_name) because a statistic's
-- NAME is the part that survives into a summary.
--
-- WHY STATE AND OUTCOME ARE TWO TABLES.
--
-- The state row is written before any outcome exists and is never
-- updated. Outcomes land in a separate table keyed to the observation
-- id. There is no UPDATE path from a future value onto a recorded
-- state, so no outcome can silently rewrite the state that preceded
-- it -- the retrospective-insertion failure, made structurally
-- impossible rather than forbidden by convention.
--
-- WHY UNREADABLE BOOKS ARE STORED.
--
-- A market chosen by the rotation writes a row whether or not its book
-- parses. Requiring a readable book would condition the dataset on
-- readability; readability tracks liquidity; liquidity tracks the
-- economics being measured. book_readability_status carries the named
-- reason instead.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_state_observations (
    observation_id      TEXT PRIMARY KEY,

    -- WHEN, and the bucket that makes one row mean one market-interval
    -- rather than one loop iteration.
    observed_at         TIMESTAMPTZ NOT NULL,
    observation_bucket  TIMESTAMPTZ NOT NULL,
    written_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- THE FROZEN RULE, ON EVERY ROW. A rule edited after collection
    -- began without a version bump shows up as a second rule_sha
    -- against the same universe_version -- which §4 forbids, made
    -- detectable rather than trusted.
    universe_version    TEXT NOT NULL,
    rule_sha            TEXT NOT NULL,
    selection_cycle     INTEGER,
    slice_truncated     BOOLEAN NOT NULL DEFAULT FALSE,
    slice_truncated_by  INTEGER NOT NULL DEFAULT 0,

    -- IDENTITY: venue-native deterministic fields only. No fuzzy title
    -- matching, no approximate team-name matching, no price matching.
    event_id            TEXT,
    market_id           TEXT,
    instrument_id       TEXT,
    condition_id        TEXT,
    outcome_leg         TEXT,
    sport               TEXT,
    league              TEXT,
    market_type         TEXT,
    -- The venue's own strings, kept beside the mapped values so a row
    -- can be re-derived when the mapping changes. It will: the venue
    -- adds leagues.
    sport_source_raw    TEXT,
    league_source_raw   TEXT,
    identity_status     TEXT NOT NULL,

    -- TIMING. time_to_event filters nothing; it is recorded on every
    -- row precisely because filtering on it would select states by how
    -- close they are to resolution.
    time_to_event_s     TEXT,
    live_status         TEXT,
    book_source_ts      TEXT,
    book_received_ts    TIMESTAMPTZ,
    book_age_s          TEXT,

    -- THE BOOK. TEXT, not NUMERIC, because NOT_IDENTIFIED is a legal
    -- value and a numeric column would force it to become NULL -- and
    -- a NULL cannot say WHY it is absent.
    yes_bid             TEXT,
    yes_ask             TEXT,
    yes_depth           JSONB,
    no_bid              TEXT,
    no_ask              TEXT,
    no_depth            JSONB,
    spread              TEXT,
    mid                 TEXT,
    multi_level_depth   JSONB,
    book_imbalance      TEXT,
    recent_price_move   TEXT,
    realised_volatility TEXT,
    stats_shares_traded TEXT,

    venue_state         TEXT,
    book_readability_status TEXT NOT NULL,
    missing_field_reasons JSONB NOT NULL,

    -- LOSSLESS. Every derived column above is recomputable from this.
    raw_source          JSONB,

    -- NO ORDER EXISTS. Checked, not asserted: a row claiming anything
    -- else cannot be inserted.
    fill_status         TEXT NOT NULL DEFAULT 'NO_ORDER_EXISTS'
        CHECK (fill_status = 'NO_ORDER_EXISTS')
);

CREATE INDEX IF NOT EXISTS bettor_state_obs_bucket_idx
    ON bettor_state_observations (observation_bucket DESC);
CREATE INDEX IF NOT EXISTS bettor_state_obs_market_idx
    ON bettor_state_observations (market_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS bettor_state_obs_rule_idx
    ON bettor_state_observations (universe_version, rule_sha);


-- ── §6. FUTURE OUTCOMES, APPENDED SEPARATELY ────────────────────────
--
-- "Do not call any future observation a fill." Hence is_not_a_fill,
-- CHECKed, on every outcome row: the column exists so that a later
-- reader who joins this table to a strategy backtest is told, by the
-- schema, what these rows are not.

CREATE TABLE IF NOT EXISTS bettor_state_mids (
    observation_id      TEXT NOT NULL
        REFERENCES bettor_state_observations (observation_id),
    horizon_s           INTEGER NOT NULL,
    read_at             TIMESTAMPTZ,
    -- THE ACTUAL LAG IS THE RECORD. A read landing 74s after T0 is a
    -- 60s-horizon row whose actual_lag_s is 74. It is never relabelled
    -- as 60 and never interpolated toward it.
    actual_lag_s        TEXT,
    within_tolerance    BOOLEAN,
    mid                 TEXT,
    status              TEXT NOT NULL,
    written_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_not_a_fill       BOOLEAN NOT NULL DEFAULT TRUE
        CHECK (is_not_a_fill),
    PRIMARY KEY (observation_id, horizon_s)
);

CREATE TABLE IF NOT EXISTS bettor_state_settlements (
    observation_id      TEXT PRIMARY KEY
        REFERENCES bettor_state_observations (observation_id),
    settlement_outcome  TEXT,
    settlement_ts       TEXT,
    settlement_status   TEXT NOT NULL,
    -- "Did this slug settle at 1 for the side we quoted" is a venue
    -- CONVENTION, not an arithmetic fact. A dataset that assumed it
    -- would carry a sign error nobody could see, so the status of that
    -- assumption is its own column.
    settlement_semantics_status TEXT NOT NULL,
    written_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_not_a_fill       BOOLEAN NOT NULL DEFAULT TRUE
        CHECK (is_not_a_fill)
);

CREATE INDEX IF NOT EXISTS bettor_state_mids_due_idx
    ON bettor_state_mids (horizon_s, read_at);

COMMIT;
