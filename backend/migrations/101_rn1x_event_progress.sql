-- Timestamped event-progress observations, for the RN1X second-half loss
-- exit and nothing else.
--
-- A ROW HERE IS A CLAIM THE FROZEN POLICY MAY READ, so the table enforces
-- what the policy assumes rather than trusting the writer:
--
--   * `observed_at` is NOT NULL and is the provider's observation instant,
--     never the ingest instant. Ageing against ingest time would make a
--     stale observation look fresh the moment we happened to fetch it.
--   * `in_play` is NOT NULL and the CHECK ties it to `status`, so a feed
--     cannot report SUSPENDED and in_play = true. That pair is one fact
--     stated twice and wrong once.
--   * `source` is CHECKed against the observed-source allow-list. The
--     dangerous input is not a missing source but a plausible one:
--     game_start + elapsed wall-clock yields a correctly typed,
--     correctly timestamped, wrong period index. It is refused by NAME.
--   * there is NO elapsed column. The policy's progress contract is
--     (observed_at, period, period_type, in_play) and a rule physically
--     cannot infer halfway from wall-clock if the column does not exist.
--
-- CORRECTIONS. (event_key, source, observed_at, revision) is unique, so a
-- provider restating the SAME instant with a corrected period inserts a
-- new row at revision + 1 instead of overwriting the original. Both are
-- kept: the reader orders by (observed_at, revision, ingested_at) and the
-- correction wins, while the superseded value stays auditable.
--
-- This migration creates a table that nothing writes to yet. That is the
-- point: PROGRESS_FEED_CONNECTED stays empty, so no sport is admitted to
-- the complete-policy experiment, and the plumbing is ready for a
-- provider the owner connects rather than one I invented.

CREATE TABLE IF NOT EXISTS rn1x_event_progress (
    event_key    text        NOT NULL,
    sport        text        NOT NULL,
    source       text        NOT NULL,
    observed_at  timestamptz NOT NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    revision     integer     NOT NULL DEFAULT 0,
    status       text        NOT NULL,
    period       integer,
    period_type  text        NOT NULL DEFAULT '',
    in_play      boolean     NOT NULL,

    CONSTRAINT rn1x_event_progress_pkey
        PRIMARY KEY (event_key, source, observed_at, revision),

    CONSTRAINT rn1x_event_progress_source_is_observed
        CHECK (source IN ('venue_event_state',
                          'licensed_scores_feed',
                          'official_scoreboard')),

    CONSTRAINT rn1x_event_progress_status_known
        CHECK (status IN ('IN_PLAY', 'HALFTIME_OR_BREAK',
                          'SUSPENDED', 'ABANDONED', 'FINAL')),

    -- in_play is true for exactly one status and false for the rest.
    CONSTRAINT rn1x_event_progress_in_play_matches_status
        CHECK (in_play = (status = 'IN_PLAY')),

    -- A period index below 1 is not a period. NULL is allowed: a feed may
    -- report a status with no period yet, and that is a named absence.
    CONSTRAINT rn1x_event_progress_period_positive
        CHECK (period IS NULL OR period >= 1),

    CONSTRAINT rn1x_event_progress_revision_nonneg
        CHECK (revision >= 0)
);

-- The reader's access path: newest-first for one event.
CREATE INDEX IF NOT EXISTS rn1x_event_progress_event_idx
    ON rn1x_event_progress (event_key, observed_at DESC, revision DESC);
