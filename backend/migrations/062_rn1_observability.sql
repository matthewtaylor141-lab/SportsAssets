-- RN1 FORWARD OBSERVABILITY -- run 83.
--
-- WHY THIS EXISTS. Run 82 established that physical actionable latency is NOT
-- IDENTIFIABLE from anything already retained: every boundary anchored at the
-- source fill crosses a clock domain, in every lane, and 92.04% of the chain
-- lane's detected_at - ts values are negative, which an elapsed time cannot be.
-- That cannot be repaired after the fact. It can only be recorded going forward,
-- which is what these tables are for.
--
-- THESE TABLES NEVER CAUSE AN ORDER. They are written by a passive collector
-- that has no order path. A hypothetical order recorded here is a description of
-- what would have been constructed, and nothing reads it back to act on.
--
-- APPEND-ONLY, ENFORCED BY THE DATABASE AND NOT BY CONVENTION. Every table below
-- carries a trigger that raises on UPDATE and on DELETE. The reason is exactly
-- the defect run 82 found: a timing field that can be rewritten later is a timing
-- field whose meaning cannot be established afterwards. State changes append a
-- new row; they never edit an old one.
--
-- TWO CLOCK FAMILIES, KEPT APART BY THE COLUMN NAMES THEMSELVES. A column ending
-- _wall is a wall-clock timestamp, for cross-system correlation and audit. A
-- column ending _monotonic is seconds from a monotonic source, for durations.
-- A monotonic reading is comparable ONLY against another reading carrying the
-- same process_boot_id -- across a restart the monotonic origin moves, and
-- subtracting across that boundary is meaningless. The boot id is stored on every
-- row that carries a monotonic value so the comparison can be checked rather
-- than assumed.
--
-- NO SILENT SUBSTITUTION, EVER. source_ts is NULL when the source did not supply
-- one. It is never filled with our own clock -- that is precisely the
-- chain.py:642 defect this work exists to stop repeating. Provenance, clock
-- domain and status travel beside the value so a later reader never has to infer
-- which clock produced it.

-- ===========================================================================
-- 1. THE PER-SOURCE-EVENT OBSERVABILITY RECORD
-- ===========================================================================
CREATE TABLE IF NOT EXISTS rn1_obs_events (
    obs_event_id        UUID PRIMARY KEY,
    -- Causal identifiers. Attribution is by explicit id, never by timestamp
    -- proximity -- proximity is what fails when clocks disagree.
    parent_event_id     UUID REFERENCES rn1_obs_events(obs_event_id),
    supersedes_event_id UUID REFERENCES rn1_obs_events(obs_event_id),
    collector_version   TEXT        NOT NULL,
    row_written_at      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- Postgres clock, audit only

    -- ---- SOURCE IDENTITY -------------------------------------------------
    -- source_event_id is the stable identity that must survive a restart or a
    -- replay; the UNIQUE index below is what makes re-ingesting the same source
    -- event a no-op rather than a second observation of one event.
    source_event_id     TEXT        NOT NULL,
    source_fill_id      TEXT,
    source_tx_hash      TEXT,
    source_order_id     TEXT,
    source_venue        TEXT        NOT NULL,
    source_lane         TEXT        NOT NULL,
    source_market_id    TEXT,
    source_token_id     TEXT,
    source_outcome_index INTEGER,

    -- ---- SOURCE EVENT ----------------------------------------------------
    source_price        NUMERIC(18,6),
    source_size         NUMERIC(24,6),
    source_side         TEXT,
    source_ts           TIMESTAMPTZ,          -- NULL when MISSING. Never substituted.
    source_ts_provenance TEXT       NOT NULL, -- e.g. polygon_block_timestamp
    source_ts_clock_domain TEXT     NOT NULL, -- e.g. C1_SOURCE_CHAIN
    source_ts_status    TEXT        NOT NULL, -- SUPPLIED | MISSING | FALLBACK_SUBSTITUTED
    source_ts_fallback  BOOLEAN     NOT NULL DEFAULT false,
    source_ts_sync_status TEXT,               -- against BETTOR wall, where known

    -- ---- BETTOR RECEIPT --------------------------------------------------
    receipt_wall        TIMESTAMPTZ NOT NULL,
    receipt_monotonic   NUMERIC(20,9) NOT NULL,
    process_boot_id     UUID        NOT NULL, -- monotonic values compare only within this
    process_identity    TEXT        NOT NULL, -- host:pid
    ingest_worker       TEXT        NOT NULL,

    -- ---- NORMALIZATION ---------------------------------------------------
    normalize_start_monotonic NUMERIC(20,9),
    normalize_done_monotonic  NUMERIC(20,9),

    -- ---- MAPPING ---------------------------------------------------------
    map_start_monotonic NUMERIC(20,9),
    map_done_monotonic  NUMERIC(20,9),
    mapping_provenance  TEXT,
    mapping_status      TEXT,
    mapping_confidence  TEXT,
    dest_venue          TEXT,
    dest_market_id      TEXT,
    dest_token_repr     TEXT,
    cross_venue_mapping_id TEXT,

    -- ---- DECISION --------------------------------------------------------
    decision_start_monotonic NUMERIC(20,9),
    decision_done_monotonic  NUMERIC(20,9),
    eligibility_result  TEXT,
    rejection_reason    TEXT,
    hypothetical_side   TEXT,
    hypothetical_qty    NUMERIC(24,6),
    hypothetical_limit_price NUMERIC(18,6),

    -- ---- SHADOW EXECUTION ------------------------------------------------
    -- Hypothetical only. No column here has ever been the input to an order,
    -- and the collector that writes them cannot reach an order API at all.
    -- There is deliberately NO fill-probability column: a fill probability
    -- cannot be measured from a book snapshot, and a fabricated one would be
    -- indistinguishable from a measured one once stored.
    shadow_executable_qty NUMERIC(24,6),
    shadow_vwap         NUMERIC(18,6),
    shadow_depth_exhausted BOOLEAN,
    shadow_queue_state  TEXT,                 -- only if actually measurable; else NULL
    shadow_notes        TEXT,

    CONSTRAINT rn1_obs_events_ts_status_ck
        CHECK (source_ts_status IN ('SUPPLIED', 'MISSING', 'FALLBACK_SUBSTITUTED')),
    -- The invariant that makes a missing source timestamp legible rather than
    -- invisible: MISSING must carry a NULL, and a NULL must be declared MISSING.
    CONSTRAINT rn1_obs_events_ts_null_ck
        CHECK ((source_ts_status = 'MISSING') = (source_ts IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS rn1_obs_events_source_uk
    ON rn1_obs_events (source_event_id);
CREATE INDEX IF NOT EXISTS rn1_obs_events_receipt_idx
    ON rn1_obs_events (receipt_wall);
CREATE INDEX IF NOT EXISTS rn1_obs_events_lane_idx
    ON rn1_obs_events (source_lane, receipt_wall);

-- ===========================================================================
-- 2. FORWARD MARKET-DATA SNAPSHOTS
-- ===========================================================================
-- One row per (event, pre-specified offset). A row exists whether or not the
-- read succeeded: a miss is RECORDED as a miss with its reason, never left
-- absent, because an absent row and a failed read look identical later and only
-- one of them is honest.
--
-- A SAMPLE IS A READING AT t, NOT THE FIRST READING AFTER t. That rule is
-- carried verbatim from workers/price_path.py, which learned it the hard way:
-- taking every overdue offset whenever the worker could collapsed six offsets
-- into one instant and produced a flat curve by fiat.
CREATE TABLE IF NOT EXISTS rn1_obs_snapshots (
    snapshot_id         BIGSERIAL PRIMARY KEY,
    obs_event_id        UUID        NOT NULL REFERENCES rn1_obs_events(obs_event_id),
    process_boot_id     UUID        NOT NULL,
    row_written_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    offset_label        TEXT        NOT NULL,   -- '0ms' | '100ms' | ... | '60s'
    offset_target_s     NUMERIC(12,6) NOT NULL,
    scheduled_for_monotonic NUMERIC(20,9) NOT NULL,

    status              TEXT        NOT NULL,   -- CAPTURED | MISSED_WINDOW | VENUE_ERROR
                                                -- | NOT_ATTEMPTED | SKIPPED_PACING
    miss_reason         TEXT,

    request_start_wall  TIMESTAMPTZ,
    request_start_monotonic NUMERIC(20,9),
    response_wall       TIMESTAMPTZ,
    response_monotonic  NUMERIC(20,9),
    -- The ACTUAL offset achieved, measured monotonically. Analysis uses this,
    -- never offset_target_s -- the target is what was asked for and this is what
    -- happened, and conflating them is how a scheduler's jitter disappears.
    actual_offset_s     NUMERIC(20,9),

    venue_snapshot_ts   TIMESTAMPTZ,            -- if the venue supplies one
    venue_sequence      TEXT,                   -- if the venue supplies one
    book_provenance     TEXT,

    best_bid            NUMERIC(18,6),
    best_ask            NUMERIC(18,6),
    depth               JSONB,                  -- exact retained price/size pairs
    depth_levels        INTEGER,

    vwap_qa             NUMERIC(18,6),
    qa_depth_exhausted  BOOLEAN,
    vwap_qb             NUMERIC(18,6),
    qb_depth_exhausted  BOOLEAN,

    CONSTRAINT rn1_obs_snapshots_status_ck
        CHECK (status IN ('CAPTURED', 'MISSED_WINDOW', 'VENUE_ERROR',
                          'NOT_ATTEMPTED', 'SKIPPED_PACING'))
);

-- One row per offset per event: this is what makes a replay idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS rn1_obs_snapshots_uk
    ON rn1_obs_snapshots (obs_event_id, offset_label);
CREATE INDEX IF NOT EXISTS rn1_obs_snapshots_event_idx
    ON rn1_obs_snapshots (obs_event_id, offset_target_s);

-- ===========================================================================
-- 3. STATE TRANSITIONS -- the append-only substitute for an in-place update
-- ===========================================================================
CREATE TABLE IF NOT EXISTS rn1_obs_transitions (
    transition_id       BIGSERIAL PRIMARY KEY,
    obs_event_id        UUID        NOT NULL REFERENCES rn1_obs_events(obs_event_id),
    seq                 INTEGER     NOT NULL,
    process_boot_id     UUID        NOT NULL,
    row_written_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    at_wall             TIMESTAMPTZ NOT NULL,
    at_monotonic        NUMERIC(20,9) NOT NULL,
    stage               TEXT        NOT NULL,
    state               TEXT        NOT NULL,
    detail              JSONB
);

CREATE UNIQUE INDEX IF NOT EXISTS rn1_obs_transitions_uk
    ON rn1_obs_transitions (obs_event_id, seq);

-- ===========================================================================
-- 4. CLOCK SYNCHRONISATION OBSERVABILITY
-- ===========================================================================
-- Recorded, never assumed. Run 82's central negative result is that an
-- unmeasured offset cannot be recovered afterwards; this table is the measurement
-- that would have made it recoverable. No cross-clock precision may be claimed
-- finer than offset_uncertainty_s here.
CREATE TABLE IF NOT EXISTS rn1_obs_clock_sync (
    sync_id             BIGSERIAL PRIMARY KEY,
    process_boot_id     UUID        NOT NULL,
    process_identity    TEXT        NOT NULL,
    row_written_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    observed_wall       TIMESTAMPTZ NOT NULL,
    observed_monotonic  NUMERIC(20,9) NOT NULL,

    method              TEXT        NOT NULL,   -- how the figures below were obtained
    host_sync_status    TEXT,                   -- NTP/system sync state, if readable
    host_offset_s       NUMERIC(20,9),
    host_error_s        NUMERIC(20,9),          -- max error / dispersion, if readable

    peer_name           TEXT,                   -- venue/server whose time was read
    peer_server_time    TIMESTAMPTZ,
    rtt_s               NUMERIC(20,9),
    offset_estimate_s   NUMERIC(20,9),
    -- If this is NULL the offset is an estimate of unknown quality and must not
    -- be used to claim any interval.
    offset_uncertainty_s NUMERIC(20,9),
    detail              JSONB
);

CREATE INDEX IF NOT EXISTS rn1_obs_clock_sync_time_idx
    ON rn1_obs_clock_sync (observed_wall);

-- ===========================================================================
-- 5. APPEND-ONLY ENFORCEMENT
-- ===========================================================================
-- A convention that says "do not update" is not an invariant. This is.
CREATE OR REPLACE FUNCTION rn1_obs_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'rn1_obs_* is append-only: % on % is refused. Append a new row '
        '(rn1_obs_transitions) instead of rewriting a timing field.',
        TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS rn1_obs_events_append_only ON rn1_obs_events;
CREATE TRIGGER rn1_obs_events_append_only
    BEFORE UPDATE OR DELETE ON rn1_obs_events
    FOR EACH ROW EXECUTE FUNCTION rn1_obs_append_only();

DROP TRIGGER IF EXISTS rn1_obs_snapshots_append_only ON rn1_obs_snapshots;
CREATE TRIGGER rn1_obs_snapshots_append_only
    BEFORE UPDATE OR DELETE ON rn1_obs_snapshots
    FOR EACH ROW EXECUTE FUNCTION rn1_obs_append_only();

DROP TRIGGER IF EXISTS rn1_obs_transitions_append_only ON rn1_obs_transitions;
CREATE TRIGGER rn1_obs_transitions_append_only
    BEFORE UPDATE OR DELETE ON rn1_obs_transitions
    FOR EACH ROW EXECUTE FUNCTION rn1_obs_append_only();

DROP TRIGGER IF EXISTS rn1_obs_clock_sync_append_only ON rn1_obs_clock_sync;
CREATE TRIGGER rn1_obs_clock_sync_append_only
    BEFORE UPDATE OR DELETE ON rn1_obs_clock_sync
    FOR EACH ROW EXECUTE FUNCTION rn1_obs_append_only();
