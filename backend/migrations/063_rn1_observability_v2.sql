-- RUN 83.2 -- the V2 instrument. research/RUN83_PREREGISTRATION_AMENDMENT_V2.md
--
-- Three things migration 062 could not do, learned from
-- RUN83_ACTIVATION_FAILED_V1:
--
-- 1. IT COULD NOT SAY WHOSE EVENT A ROW WAS. rn1_obs_events has 51 columns and
--    not one of them carries an account identity, so auditing the population
--    required joining through trades.dedupe_key after the fact. 94.671% of the
--    first cohort turned out to belong to other wallets and nothing in the
--    instrument could have said so. Subject provenance is added here.
--
-- 2. IT CONFLATED A PLAN WITH A RESULT. One row per (event, offset) carried
--    both the intention and the outcome, so "scheduled" and "measured" were the
--    same integer -- 125,270 rows of which 337 were measurements. Worse, the
--    only way to fill in an outcome later is an UPDATE, and these tables are
--    append-only by trigger. So the plan and the result are now SEPARATE
--    append-only tables (owner decision 6, option A).
--
-- 3. IT HAD NO SLOT IDENTITY. A missing result and a slot that never existed
--    were indistinguishable. rn1_obs_plan gives every planned slot a stable,
--    deterministic observation_slot_id, so absence is now a fact about a
--    specific known slot.
--
-- NOTHING HERE ALTERS OR DROPS A 062 TABLE. RUN83_ACTIVATION_FAILED_V1 lives in
-- those four tables and is sealed; this migration only ADDs columns that default
-- NULL and CREATEs new tables. The failed cohort remains byte-identical, and its
-- digests (research/RUN83_ACTIVATION_FAILED_V1_MANIFEST.md) stay valid.

-- ===========================================================================
-- 1. SUBJECT PROVENANCE ON THE EVENT ROW
-- ===========================================================================
-- Immutable id first, name second. The ADMISSION KEY is subject_whale_id; the
-- username is retained because it is what a human recognises in a log, and is
-- explicitly NOT what anything decides on -- a renamed account must not silently
-- change the population.
ALTER TABLE rn1_obs_events
    ADD COLUMN IF NOT EXISTS subject_whale_id        INTEGER,
    ADD COLUMN IF NOT EXISTS subject_wallet_address  TEXT,
    ADD COLUMN IF NOT EXISTS subject_username        TEXT,
    ADD COLUMN IF NOT EXISTS subject_admission_reason TEXT,
    -- The canonical production dedupe answer, carried verbatim. NOT recomputed
    -- here: the research instrument must not own a second first-seen authority
    -- (owner decision 4).
    ADD COLUMN IF NOT EXISTS canonical_was_insert    BOOLEAN,
    -- The anchor is stamped before the insert; admission happens after it. Both
    -- are kept so the insert's cost is visible and can never be folded into the
    -- anchor.
    ADD COLUMN IF NOT EXISTS admission_monotonic     NUMERIC(20,9),
    ADD COLUMN IF NOT EXISTS admission_delay_ms      NUMERIC(12,3),
    ADD COLUMN IF NOT EXISTS preregistration_version TEXT;

COMMENT ON COLUMN rn1_obs_events.subject_whale_id IS
    'Immutable subject identity and the admission key. NULL on every '
    'RUN83_ACTIVATION_FAILED_V1 row: that cohort had no subject filter.';
COMMENT ON COLUMN rn1_obs_events.canonical_was_insert IS
    'The (xmax = 0) answer from the canonical trades INSERT. TRUE means this '
    'was BETTOR''s first receipt of the fill. Never recomputed by the '
    'instrument.';

-- ===========================================================================
-- 2. THE SCHEDULE PLAN -- one immutable row per planned slot
-- ===========================================================================
-- WRITTEN ONCE, AT SCHEDULE TIME, AND NEVER TOUCHED AGAIN. This table answers
-- "what did the instrument intend to observe?" and nothing else. It carries no
-- status column on purpose: a status would have to be updated, and an UPDATE is
-- what the append-only trigger refuses.
--
-- A slot with a plan row and no result row is a KNOWN MISSING OBSERVATION. A
-- slot with neither never existed. Those are different facts and V1 could not
-- tell them apart.
CREATE TABLE IF NOT EXISTS rn1_obs_plan (
    -- Deterministic, derived from the four things that identify a slot. Computed
    -- in code (obs/slot.py) as a SHA-256 over the canonical tuple, so the same
    -- slot always gets the same id and a replay cannot mint a second identity
    -- for the same intention.
    observation_slot_id TEXT        PRIMARY KEY,

    obs_event_id        UUID        NOT NULL REFERENCES rn1_obs_events(obs_event_id),
    source_event_id     TEXT        NOT NULL,
    observation_channel TEXT        NOT NULL,
    target_offset_ms    INTEGER     NOT NULL,
    preregistration_version TEXT    NOT NULL,

    -- THE MONOTONIC EXPERIMENT'S IDENTITY. target_monotonic is only meaningful
    -- inside process_boot_id; that is why the boot id is NOT NULL here and why
    -- a new process must never reconstruct this deadline (owner decision 7).
    process_boot_id     UUID        NOT NULL,
    receipt_wall        TIMESTAMPTZ NOT NULL,
    receipt_monotonic   NUMERIC(20,9) NOT NULL,
    target_monotonic    NUMERIC(20,9) NOT NULL,
    window_ms           INTEGER     NOT NULL,

    planned_at_wall     TIMESTAMPTZ NOT NULL DEFAULT now(),
    planned_at_monotonic NUMERIC(20,9) NOT NULL,

    source_token_id     TEXT,
    source_market_id    TEXT,

    CONSTRAINT rn1_obs_plan_channel_ck CHECK (observation_channel IN (
        'LOCAL_CACHE_BATCH_POLL_PATH',   -- V2 primary: sampled from local state
        'LEGACY_COMPARABLE_BOOK_PATH',   -- secondary diagnostic, HTTP
        'FAST_STREAM_PATH')),            -- reserved; no vendor feed exists
    -- One intention per (event, channel, offset, preregistration version). The
    -- version is IN the key: re-running the same event under an amended
    -- preregistration is a different intention, not a duplicate of the old one.
    CONSTRAINT rn1_obs_plan_uk UNIQUE
        (source_event_id, observation_channel, target_offset_ms,
         preregistration_version)
);

CREATE INDEX IF NOT EXISTS rn1_obs_plan_event_idx
    ON rn1_obs_plan (obs_event_id);
CREATE INDEX IF NOT EXISTS rn1_obs_plan_boot_idx
    ON rn1_obs_plan (process_boot_id, target_monotonic);

-- ===========================================================================
-- 3. THE RESULT -- one append-only row per outcome of a planned slot
-- ===========================================================================
-- Every terminal state of a slot is a row here, including the ones that are not
-- measurements. PROCESS_RESTARTED_BEFORE_CAPTURE is a result: it says the
-- original monotonic experiment for that slot cannot be completed by anyone.
CREATE TABLE IF NOT EXISTS rn1_obs_samples (
    sample_id           BIGSERIAL   PRIMARY KEY,
    observation_slot_id TEXT        NOT NULL REFERENCES rn1_obs_plan(observation_slot_id),
    obs_event_id        UUID        NOT NULL REFERENCES rn1_obs_events(obs_event_id),
    row_written_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The process that produced THIS row. Deliberately allowed to differ from
    -- the plan's process_boot_id, because that difference is exactly what
    -- PROCESS_RESTARTED_BEFORE_CAPTURE records.
    process_boot_id     UUID        NOT NULL,

    observation_channel TEXT        NOT NULL,
    target_offset_ms    INTEGER     NOT NULL,
    target_monotonic    NUMERIC(20,9),
    actual_sample_monotonic NUMERIC(20,9),
    actual_sample_wall  TIMESTAMPTZ,
    sample_lateness_ms  NUMERIC(12,3),

    status              TEXT        NOT NULL,
    miss_reason         TEXT,

    source_token_id     TEXT,
    source_market_id    TEXT,

    -- ---- WHAT THE LOCAL STATE WAS, AND HOW OLD -------------------------
    -- cache_age_ms IS THE HONESTY OF THIS CHANNEL. A polled cache resolves
    -- nothing finer than its refresh interval, so two offsets closer together
    -- than that interval return the SAME state. Without the age and the state
    -- identity below, they would read as two measurements of an unchanged price
    -- -- a flat curve by fiat, which is the defect price_path.py was rewritten
    -- to remove.
    state_received_wall      TIMESTAMPTZ,
    state_received_monotonic NUMERIC(20,9),
    cache_age_ms        NUMERIC(12,3),

    best_bid            NUMERIC(18,6),
    best_ask            NUMERIC(18,6),
    depth               JSONB,
    depth_levels        INTEGER,
    vwap_qa             NUMERIC(18,6),
    qa_depth_exhausted  BOOLEAN,
    vwap_qb             NUMERIC(18,6),
    qb_depth_exhausted  BOOLEAN,

    -- ---- WHAT THE VENUE SAID ABOUT ITS OWN STATE -----------------------
    -- The venue supplies a timestamp and a content hash and NO sequence number
    -- (py-clob-client 0.34.6: OrderBookSummary.timestamp, .hash). The hash
    -- proves CHANGE; it cannot prove ordering or the absence of a gap. So
    -- venue_sequence stays NULL and continuity stays unverified rather than
    -- being assumed from a hash that was never a sequence.
    venue_snapshot_ts   TIMESTAMPTZ,
    venue_sequence      TEXT,
    venue_book_hash     TEXT,
    book_provenance     TEXT,

    -- ---- THE FEED'S OWN STATE ------------------------------------------
    feed_session_id     TEXT,
    feed_bootstrap_status TEXT,
    feed_reconnect_count INTEGER,
    state_validity      TEXT,
    continuity_status   TEXT,

    -- ---- THE SECONDARY HTTP CHANNEL ONLY -------------------------------
    request_start_wall  TIMESTAMPTZ,
    request_start_monotonic NUMERIC(20,9),
    response_wall       TIMESTAMPTZ,
    response_monotonic  NUMERIC(20,9),

    CONSTRAINT rn1_obs_samples_status_ck CHECK (status IN (
        'CAPTURED_LOCAL',        -- primary: local state sampled inside its window
        'CAPTURED_HTTP',         -- secondary: a completed venue read
        'MISSED_WINDOW',         -- the window closed before the sampler reached it
        'MISSED_ADMISSION_LATE', -- admission itself landed after the window shut
        'CACHE_MISS',            -- no state for this token had ever arrived
        'CACHE_INVALID',         -- state present but not admissible (see state_validity)
        'VENUE_ERROR',           -- secondary channel only
        'PROCESS_RESTARTED_BEFORE_CAPTURE')),
    CONSTRAINT rn1_obs_samples_channel_ck CHECK (observation_channel IN (
        'LOCAL_CACHE_BATCH_POLL_PATH', 'LEGACY_COMPARABLE_BOOK_PATH',
        'FAST_STREAM_PATH')),
    CONSTRAINT rn1_obs_samples_validity_ck CHECK (state_validity IS NULL
        OR state_validity IN ('VALID', 'STALE_BEYOND_TOLERANCE',
                              'INVALIDATED_BY_DISCONNECT', 'NEVER_BOOTSTRAPPED')),
    CONSTRAINT rn1_obs_samples_continuity_ck CHECK (continuity_status IS NULL
        OR continuity_status IN ('STREAM_CONTINUITY_UNVERIFIED',
                                 'STREAM_CONTINUITY_VERIFIED')),
    -- A CAPTURE MUST CARRY ITS AGE. Without cache_age_ms a local sample is
    -- indistinguishable from a fresh reading, which is the single most
    -- misleading row this table could hold.
    CONSTRAINT rn1_obs_samples_capture_has_age_ck CHECK (
        status <> 'CAPTURED_LOCAL' OR cache_age_ms IS NOT NULL),
    -- A local sample never carries an HTTP request: that would mean the
    -- scheduled operation made a network call, which is the thing V2 exists to
    -- stop.
    CONSTRAINT rn1_obs_samples_local_has_no_request_ck CHECK (
        observation_channel <> 'LOCAL_CACHE_BATCH_POLL_PATH'
        OR (request_start_wall IS NULL AND response_wall IS NULL))
);

-- One terminal result per slot. A retry that wanted a second row would be
-- claiming two outcomes for one intention.
CREATE UNIQUE INDEX IF NOT EXISTS rn1_obs_samples_slot_uk
    ON rn1_obs_samples (observation_slot_id);
CREATE INDEX IF NOT EXISTS rn1_obs_samples_event_idx
    ON rn1_obs_samples (obs_event_id);
CREATE INDEX IF NOT EXISTS rn1_obs_samples_status_idx
    ON rn1_obs_samples (status, target_offset_ms);

-- ===========================================================================
-- 4. THE FEED'S OWN LIFECYCLE, SEPARATELY
-- ===========================================================================
-- Connect, bootstrap, refresh, degrade, invalidate. Kept out of the sample rows
-- so that a sample references a feed state rather than restating it, and so the
-- feed's history survives even for periods in which nothing was sampled.
CREATE TABLE IF NOT EXISTS rn1_obs_feed_events (
    feed_event_id       BIGSERIAL   PRIMARY KEY,
    row_written_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    process_boot_id     UUID        NOT NULL,
    feed_session_id     TEXT        NOT NULL,
    observation_channel TEXT        NOT NULL,
    at_wall             TIMESTAMPTZ NOT NULL,
    at_monotonic        NUMERIC(20,9) NOT NULL,
    kind                TEXT        NOT NULL,
    tokens_tracked      INTEGER,
    tokens_refreshed    INTEGER,
    refresh_duration_ms NUMERIC(12,3),
    http_status         INTEGER,
    detail              JSONB,
    CONSTRAINT rn1_obs_feed_events_kind_ck CHECK (kind IN (
        'SESSION_OPENED', 'BOOTSTRAP_COMPLETE', 'BOOTSTRAP_FAILED',
        'REFRESH_OK', 'REFRESH_FAILED', 'STATE_INVALIDATED',
        'TOKEN_PROMOTED', 'TOKEN_RETIRED', 'SESSION_CLOSED'))
);

CREATE INDEX IF NOT EXISTS rn1_obs_feed_events_session_idx
    ON rn1_obs_feed_events (feed_session_id, at_monotonic);

-- ===========================================================================
-- 5. APPEND-ONLY, ON ALL THREE NEW TABLES
-- ===========================================================================
-- Same function migration 062 installed. Named triggers so the deployment gate
-- can assert them by name rather than inferring protection from a row count.
DROP TRIGGER IF EXISTS rn1_obs_plan_append_only ON rn1_obs_plan;
CREATE TRIGGER rn1_obs_plan_append_only
    BEFORE UPDATE OR DELETE ON rn1_obs_plan
    FOR EACH ROW EXECUTE FUNCTION rn1_obs_append_only();

DROP TRIGGER IF EXISTS rn1_obs_samples_append_only ON rn1_obs_samples;
CREATE TRIGGER rn1_obs_samples_append_only
    BEFORE UPDATE OR DELETE ON rn1_obs_samples
    FOR EACH ROW EXECUTE FUNCTION rn1_obs_append_only();

DROP TRIGGER IF EXISTS rn1_obs_feed_events_append_only ON rn1_obs_feed_events;
CREATE TRIGGER rn1_obs_feed_events_append_only
    BEFORE UPDATE OR DELETE ON rn1_obs_feed_events
    FOR EACH ROW EXECUTE FUNCTION rn1_obs_append_only();
