-- PER-TICK COLLECTION LOSSES, MADE OBSERVABLE.
--
-- Owner 2026-09-20: "Persist scheduled, attempted, skipped,
-- rate-limited, unreadable and successful counts per tick, with
-- capture/pacing version. Include follow-ups and initial observations
-- separately."
--
-- WHY THIS TABLE HAS TO EXIST. Two of the three ways the capture loses
-- a read write NOTHING to bettor_state_observations:
--
--   RATE LIMITED   -> writes a row, marked unreadable. Visible.
--   BUDGET SHRINK  -> the tail of the tick's share is never attempted.
--                     No row. Invisible.
--   ABANDONMENT    -> same. No row. Invisible.
--
-- So the gap between what the rotation SCHEDULED and what landed could
-- not be measured from the observations table at all -- it can only be
-- measured by the tick recording its own intent before it acts. That is
-- what this table is: the denominator.
--
-- Without it, "coverage" can only ever be computed against itself.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_capture_ticks (
    tick_id             TEXT PRIMARY KEY,
    tick_at             TIMESTAMPTZ NOT NULL,
    written_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- WHICH RULES WERE IN FORCE. Both, because the sampling version and
    -- the pacing version change independently and a coverage figure
    -- that pools them describes neither.
    universe_version    TEXT NOT NULL,
    rule_sha            TEXT NOT NULL,
    pacing_version      TEXT NOT NULL,
    pacing_s            TEXT NOT NULL,

    selection_cycle     INTEGER,
    tick_index          INTEGER,

    -- ── the initial-observation pass ─────────────────────────────────
    -- SCHEDULED is the count the frozen rule picked for this tick,
    -- computed BEFORE any read. It is the denominator every other
    -- figure here is a fraction of.
    obs_scheduled       INTEGER NOT NULL DEFAULT 0,
    obs_attempted       INTEGER NOT NULL DEFAULT 0,
    obs_skipped_budget  INTEGER NOT NULL DEFAULT 0,
    obs_skipped_abandon INTEGER NOT NULL DEFAULT 0,
    obs_readable        INTEGER NOT NULL DEFAULT 0,
    obs_rate_limited    INTEGER NOT NULL DEFAULT 0,
    obs_unreadable_other INTEGER NOT NULL DEFAULT 0,
    obs_written         INTEGER NOT NULL DEFAULT 0,
    obs_duplicate_bucket INTEGER NOT NULL DEFAULT 0,

    -- ── the follow-up pass, counted APART ────────────────────────────
    -- Pooling these with initial reads would hide exactly the failure
    -- that was found: 187 initial rows looked healthy while follow-up
    -- coverage sat at 3.4%.
    fu_due              INTEGER NOT NULL DEFAULT 0,
    fu_attempted        INTEGER NOT NULL DEFAULT 0,
    fu_skipped_budget   INTEGER NOT NULL DEFAULT 0,
    fu_on_time          INTEGER NOT NULL DEFAULT 0,
    fu_late             INTEGER NOT NULL DEFAULT 0,
    fu_failed           INTEGER NOT NULL DEFAULT 0,

    status              TEXT NOT NULL,

    -- The scheduled-minus-attempted difference, as a stored fact rather
    -- than a derivation, so a later reader cannot compute it a second,
    -- different way.
    obs_never_attempted INTEGER NOT NULL DEFAULT 0
        CHECK (obs_never_attempted >= 0)
);

CREATE INDEX IF NOT EXISTS bettor_capture_ticks_at_idx
    ON bettor_capture_ticks (tick_at DESC);
CREATE INDEX IF NOT EXISTS bettor_capture_ticks_version_idx
    ON bettor_capture_ticks (universe_version, pacing_version);

-- ── LATE IS NOT ON TIME ──────────────────────────────────────────────
--
-- "A 600-second recovery window does not turn a late read into a valid
-- 60-second outcome."
--
-- Correct, and the schema now says so rather than relying on a column
-- being read carefully. bettor_state_mids already stores ACTUAL_LAG_S
-- and WITHIN_TOLERANCE; this adds a derived, stored classification so
-- no query has to re-derive the rule, and so the gate can filter on one
-- column whose meaning is fixed.
ALTER TABLE bettor_state_mids
    ADD COLUMN IF NOT EXISTS timing_class TEXT;

COMMENT ON COLUMN bettor_state_mids.timing_class IS
    'ON_TIME: read within the horizon''s original tolerance and the '
    'ONLY class admissible to that horizon''s gate. LATE_RECOVERY: read '
    'inside the wider recovery window, preserved with its true elapsed '
    'time, NEVER counted toward the original horizon. NOT_OBSERVABLE: '
    'the horizon has no read behind it at this cadence.';

COMMIT;
