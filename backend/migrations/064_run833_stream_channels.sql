-- RUN 83.3 -- the two stream channels, and the honesty columns they need.
--
-- NOTHING HERE TOUCHES rn1_obs_events. RUN83_ACTIVATION_FAILED_V1 lives in the
-- 062 tables with 12,535 sealed rows and four published digests; this migration
-- adds columns to the EMPTY 063 tables and widens two CHECK constraints on them.
-- rn1_obs_plan, rn1_obs_samples and rn1_obs_feed_events were all verified at 0
-- rows after 063, so the constraint swaps below rewrite nothing.
--
-- WHY THE CHANNEL NAMES SPLIT. 063 declared one reserved 'FAST_STREAM_PATH'.
-- Run 83.2A established that there are TWO feeds, not one, and that they differ
-- in every respect that matters to this instrument:
--
--     PMUS   subscribe by marketSlug   no hash   no sequence   auth required
--     CLOB   subscribe by token id     hash      no sequence   no auth
--
-- A single label would have pooled two measurements of different quality into
-- one column and made the difference unrecoverable afterwards. So the reserved
-- name is retired in favour of PMUS_FAST_STREAM_PATH and CLOB_FAST_STREAM_PATH.
-- 'FAST_STREAM_PATH' is KEPT in the CHECK lists: no row uses it, but removing a
-- value a previous migration allowed buys nothing and would make 063 and 064
-- disagree about history.

-- ===========================================================================
-- 1. THE TWO STREAM CHANNELS ARE ADMISSIBLE
-- ===========================================================================
ALTER TABLE rn1_obs_plan DROP CONSTRAINT IF EXISTS rn1_obs_plan_channel_ck;
ALTER TABLE rn1_obs_plan ADD CONSTRAINT rn1_obs_plan_channel_ck
    CHECK (observation_channel IN (
        'PMUS_FAST_STREAM_PATH',         -- PRIMARY EXECUTION CHANNEL
        'CLOB_FAST_STREAM_PATH',         -- SECONDARY SOURCE / COMPARABILITY
        'LOCAL_CACHE_BATCH_POLL_PATH',   -- diagnostic only
        'LEGACY_COMPARABLE_BOOK_PATH',   -- secondary diagnostic, HTTP
        'FAST_STREAM_PATH'));            -- retired; retained for 063 continuity

-- ===========================================================================
-- 2. THE SAMPLE ROW LEARNS WHAT A STREAM STATE IS
-- ===========================================================================
ALTER TABLE rn1_obs_samples
    -- THE CANONICAL AGE, AND THE ONLY ONE COMPUTED IN A SINGLE CLOCK DOMAIN.
    --     state_age_at_receipt_ms = source_receipt_monotonic
    --                               - latest_state_receive_monotonic
    -- Both terms are this process's monotonic clock. A venue timestamp is never
    -- an operand: venue_timestamp_raw below is TEXT precisely so that
    -- subtracting it from a local reading is awkward rather than easy.
    ADD COLUMN IF NOT EXISTS state_age_at_receipt_ms NUMERIC(12,3),

    -- MAY THIS LADDER PRICE A SIZE? Separate from state_validity, which asks
    -- whether the state may be used at all. A PMUS state can be perfectly fresh
    -- and still not support Q_A, because whether its arrays are a full
    -- replacement is unestablished.
    ADD COLUMN IF NOT EXISTS depth_authority TEXT,

    -- Provenance. PMUS transactTime / CLOB millisecond timestamp, verbatim,
    -- as the venue sent it. NEVER used to select the 0 ms state.
    ADD COLUMN IF NOT EXISTS venue_timestamp_raw TEXT,

    -- CLOB only: price_change frames received since the last full book and
    -- deliberately NOT applied, because their semantics are unresolved. A
    -- non-zero value says "this book is N messages behind and I am not
    -- entitled to guess what they meant".
    ADD COLUMN IF NOT EXISTS pending_unapplied_updates INTEGER,

    -- The mint that authorised the connection this state arrived on. AN
    -- IDENTIFIER ONLY. There is no column here for a signature, a timestamp
    -- header or an access key, and there must never be one: handshake material
    -- is discarded at connect and never reaches this table.
    ADD COLUMN IF NOT EXISTS handshake_mint_id TEXT;

COMMENT ON COLUMN rn1_obs_samples.state_age_at_receipt_ms IS
    'source_receipt_monotonic - latest_state_receive_monotonic, both local. '
    'A venue timestamp is never an operand.';
COMMENT ON COLUMN rn1_obs_samples.venue_timestamp_raw IS
    'Venue clock domain. Provenance only. Never used to choose the 0 ms state.';
COMMENT ON COLUMN rn1_obs_samples.handshake_mint_id IS
    'Broker mint id. An identifier, never authentication material.';

ALTER TABLE rn1_obs_samples DROP CONSTRAINT IF EXISTS rn1_obs_samples_channel_ck;
ALTER TABLE rn1_obs_samples ADD CONSTRAINT rn1_obs_samples_channel_ck
    CHECK (observation_channel IN (
        'PMUS_FAST_STREAM_PATH', 'CLOB_FAST_STREAM_PATH',
        'LOCAL_CACHE_BATCH_POLL_PATH', 'LEGACY_COMPARABLE_BOOK_PATH',
        'FAST_STREAM_PATH'));

ALTER TABLE rn1_obs_samples DROP CONSTRAINT IF EXISTS rn1_obs_samples_status_ck;
ALTER TABLE rn1_obs_samples ADD CONSTRAINT rn1_obs_samples_status_ck
    CHECK (status IN (
        'CAPTURED_STREAM',       -- a stream channel sampled inside its window
        'CAPTURED_LOCAL',        -- the polled cache, sampled inside its window
        'CAPTURED_HTTP',         -- secondary: a completed venue read
        'MISSED_WINDOW',
        'MISSED_ADMISSION_LATE',
        'CACHE_MISS',            -- no frame for this token ever arrived
        'CACHE_INVALID',
        -- STATE EXISTED, BUT ALL OF IT ARRIVED AFTER RECEIPT. Distinct from
        -- CACHE_MISS: the channel was working and we were subscribed; there was
        -- simply nothing in memory at the anchor to have acted on. Recording
        -- these as CACHE_MISS would have understated how often the instrument
        -- was listening and still had no pre-receipt state.
        'NO_VALID_PRE_RECEIPT_STATE',
        'VENUE_ERROR',
        'PROCESS_RESTARTED_BEFORE_CAPTURE'));

ALTER TABLE rn1_obs_samples
    DROP CONSTRAINT IF EXISTS rn1_obs_samples_depth_authority_ck;
ALTER TABLE rn1_obs_samples ADD CONSTRAINT rn1_obs_samples_depth_authority_ck
    CHECK (depth_authority IS NULL OR depth_authority IN (
        'FULL_REPLACEMENT_CONFIRMED',
        'TOP_OF_BOOK_ONLY',
        'DEPTH_PENDING_SEMANTICS'));

-- A CAPTURED STREAM SAMPLE MUST CARRY ITS AGE, for the same reason a local one
-- must: without it, a sample against a state from 4 seconds ago is
-- indistinguishable from a sample against a state from 4 milliseconds ago.
ALTER TABLE rn1_obs_samples
    DROP CONSTRAINT IF EXISTS rn1_obs_samples_stream_has_age_ck;
ALTER TABLE rn1_obs_samples ADD CONSTRAINT rn1_obs_samples_stream_has_age_ck
    CHECK (status <> 'CAPTURED_STREAM' OR state_age_at_receipt_ms IS NOT NULL);

-- A DEPTH-WEIGHTED PRICE REQUIRES AN AUTHORITATIVE LADDER. This is the database
-- half of streamstate.vwap()'s refusal: even if the code were changed to emit a
-- number over a TOP_OF_BOOK_ONLY state, the row could not be stored. Q_A is the
-- PRIMARY size measure, so a partial-depth Q_A masquerading as a full-depth one
-- would corrupt the headline result and nothing downstream could detect it.
ALTER TABLE rn1_obs_samples
    DROP CONSTRAINT IF EXISTS rn1_obs_samples_vwap_needs_depth_ck;
ALTER TABLE rn1_obs_samples ADD CONSTRAINT rn1_obs_samples_vwap_needs_depth_ck
    CHECK ((vwap_qa IS NULL AND vwap_qb IS NULL)
           OR depth_authority IS NULL
           OR depth_authority = 'FULL_REPLACEMENT_CONFIRMED');

-- NO SCHEDULED SAMPLE ON A PUSHED CHANNEL MAKES A REQUEST. 063 said this for
-- the polled cache; it is at least as true for a stream, where the whole point
-- is that the state is already in memory. Widened to cover all three non-HTTP
-- channels.
ALTER TABLE rn1_obs_samples
    DROP CONSTRAINT IF EXISTS rn1_obs_samples_local_has_no_request_ck;
ALTER TABLE rn1_obs_samples ADD CONSTRAINT rn1_obs_samples_local_has_no_request_ck
    CHECK (observation_channel NOT IN ('LOCAL_CACHE_BATCH_POLL_PATH',
                                       'PMUS_FAST_STREAM_PATH',
                                       'CLOB_FAST_STREAM_PATH')
           OR (request_start_wall IS NULL AND response_wall IS NULL));

-- ===========================================================================
-- 3. THE FEED LIFECYCLE LEARNS THE HANDSHAKE
-- ===========================================================================
-- HANDSHAKE_MINTED records that a connection was authorised and by which mint.
-- HANDSHAKE_DISCARDED records that the material was dropped -- the operational
-- half of "single use", visible in the evidence rather than only in a docstring.
ALTER TABLE rn1_obs_feed_events
    ADD COLUMN IF NOT EXISTS handshake_mint_id TEXT;

ALTER TABLE rn1_obs_feed_events
    DROP CONSTRAINT IF EXISTS rn1_obs_feed_events_kind_ck;
ALTER TABLE rn1_obs_feed_events ADD CONSTRAINT rn1_obs_feed_events_kind_ck
    CHECK (kind IN (
        'SESSION_OPENED', 'BOOTSTRAP_COMPLETE', 'BOOTSTRAP_FAILED',
        'REFRESH_OK', 'REFRESH_FAILED', 'STATE_INVALIDATED',
        'TOKEN_PROMOTED', 'TOKEN_RETIRED', 'SESSION_CLOSED',
        'HANDSHAKE_MINTED', 'HANDSHAKE_DISCARDED', 'HANDSHAKE_REFUSED',
        'CONNECT_FAILED', 'DISCONNECTED',
        -- CLOB only: a price_change arrived and was deliberately not applied.
        'UNAPPLIED_UPDATE_RECORDED'));

-- ===========================================================================
-- 4. WHAT IS DELIBERATELY ABSENT
-- ===========================================================================
-- There is no column anywhere in this schema for X-PM-Signature,
-- X-PM-Timestamp, X-PM-Access-Key, an Ed25519 secret, or any other
-- authentication material. Security test 10 asserts that absence against the
-- migration text itself, so adding one later fails a test rather than passing
-- review unnoticed.
