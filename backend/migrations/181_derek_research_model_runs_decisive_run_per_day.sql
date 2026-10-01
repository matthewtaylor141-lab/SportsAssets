-- ══════════════════════════════════════════════════════════════════════
-- 181 · ONE DECISIVE DEREK RESEARCH RUN PER UTC DAY; A SHORTFALL OR A
--       FAILED LABEL READ NO LONGER CLOSES THE DAY
-- ══════════════════════════════════════════════════════════════════════
--
-- `sportsassets.agents.derek_research.daily_model_run`.
--
-- Migration 170 allowed ONE derek_research_model_runs row per UTC day
-- (UNIQUE (run_day)), whatever its outcome. On a fresh deploy the day's
-- first run comes before any collection step: the paper path
-- (`paper_derek._context` -> `ensure_model_attempt`) reaches it within
-- seconds of boot, on an empty observations table, and the scheduled step's
-- first run follows ONE bounded backfill step (BACKFILL_PER_CYCLE = 2000
-- stored valuations). That run records INSUFFICIENT_LABELLED_FIXTURES (or
-- LABELS_UNREADABLE when the label read failed), and every later call that
-- day returned `already_ran`. A backfill that brought a cohort to
-- MIN_TRAIN_EVENTS the same day was therefore not fitted until 00:00Z.
--
-- THIS REPLACES THAT CONSTRAINT WITH A PARTIAL UNIQUE INDEX:
--
--   * at most ONE DECISIVE run per UTC day -- FITTED_AND_EVALUATED,
--     EVALUATED_WITHOUT_REFIT or FIT_REFUSED. INSUFFICIENT_LABELLED_FIXTURES
--     and LABELS_UNREADABLE are NOT decisive: they record that nothing could
--     be fitted at that instant, and the day stays open;
--   * `daily_model_run` writes a NEW row ('derek-research-run:<day>:<n>')
--     later the same day only when a cohort HAS reached MIN_TRAIN_EVENTS
--     (threshold unchanged, in code), with that run's own instant as the
--     training cutoff and the evaluation cohort declared and frozen before
--     the fit. Otherwise it writes nothing. The day's first row keeps its id
--     ('derek-research-run:<day>', primary key). Callers are serialized per
--     day by pg_advisory_xact_lock(hashtext('derek_research_daily_run:' ||
--     run_day)), held from the read of the day's rows through the attempt
--     and the run insert in one transaction.
--
-- NOTHING ELSE CHANGES. No row is touched; the append-only trigger of 170
-- stays, so an INSUFFICIENT / LABELS_UNREADABLE row remains exactly as
-- written. `promoted` is still CHECKed FALSE. The thresholds live in code
-- (bettor_funded_model) and are unchanged.
--
-- APPLIES OVER SAME-DAY ROWS. A database that ran an earlier draft of this
-- change (index derek_research_model_runs_one_decisive_per_day, which
-- treated LABELS_UNREADABLE as decisive) may already hold same-day rows;
-- that index is dropped and replaced, and the new index's predicate admits
-- a subset of the rows the old one did, so it cannot fail on them.
--
-- NO BEGIN/COMMIT HERE: the migration runner applies each file in its own
-- transaction (scripts/migrate.py).
--
-- ── ROLLBACK ─────────────────────────────────────────────────────────────
--   * CODE-ONLY ROLLBACK to ddd4050 WITH 181 LEFT IN PLACE WORKS. That
--     code reads `SELECT ... WHERE run_day = $1` (any row of the day ->
--     `already_ran`) and inserts the day's FIRST row only, under the
--     primary key 'derek-research-run:<day>' with ON CONFLICT DO NOTHING;
--     neither needs UNIQUE (run_day). The only difference: on a day that
--     already holds an INSUFFICIENT row and a same-day decisive row, its
--     `latest_model_run` may show either row of that day (workspace
--     display only). Nothing else reads the constraint.
--   * DB ROLLBACK: rollback/181_derek_research_model_runs_decisive_run_per_
--     day.down.sql restores UNIQUE (run_day) and REFUSES (RAISE, nothing
--     changed) while any UTC day holds more than one row: the table is
--     append-only, so those rows cannot be removed to make room for it.
--   * RE-APPLYING 181 AFTER A DB ROLLBACK NEEDS A MANUAL STEP: the runner
--     skips a version recorded in schema_migrations, and the down script
--     does not remove that record. Run
--       DELETE FROM schema_migrations WHERE version =
--         '181_derek_research_model_runs_decisive_run_per_day.sql';
--     then the migration runner. This file is idempotent.

-- the earlier draft's index, on THIS table only
DO $$
DECLARE
    ix text;
BEGIN
    FOR ix IN SELECT i.indexrelid::regclass::text
                FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
               WHERE i.indrelid = to_regclass('derek_research_model_runs')
                 AND c.relname = 'derek_research_model_runs_one_decisive_per_day'
    LOOP
        EXECUTE 'DROP INDEX ' || ix;
    END LOOP;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS
    derek_research_model_runs_one_decisive_run_per_day
    ON derek_research_model_runs (run_day)
    WHERE outcome NOT IN ('INSUFFICIENT_LABELLED_FIXTURES',
                          'LABELS_UNREADABLE');

ALTER TABLE derek_research_model_runs
    DROP CONSTRAINT IF EXISTS derek_research_model_runs_one_per_day;

-- reads by day, which 170's constraint index used to serve
CREATE INDEX IF NOT EXISTS derek_research_model_runs_run_day_ran_at_idx
    ON derek_research_model_runs (run_day, ran_at);

COMMENT ON INDEX derek_research_model_runs_one_decisive_run_per_day IS
    'At most one DECISIVE Derek research model run per UTC day. '
    'INSUFFICIENT_LABELLED_FIXTURES and LABELS_UNREADABLE rows do not close '
    'the day: a same-day run is a NEW row, written only once a cohort has '
    'reached MIN_TRAIN_EVENTS (migration 181).';
