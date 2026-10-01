-- down for 181. Restores 170's UNIQUE (run_day) on derek_research_model_runs.
--
-- REFUSES SAFELY while any UTC day holds more than one run (RAISE: the whole
-- statement fails and nothing is changed). derek_research_model_runs is
-- append-only (170's trigger rejects UPDATE and DELETE: a research record is
-- what was known when it was written), so the rows that constraint would
-- reject cannot be removed to make room for it. Such a database stays on
-- 181; a code-only rollback to ddd4050 works with 181 in place (see the
-- header of migrations/181_derek_research_model_runs_decisive_run_per_day.sql).
--
-- After this down script, derek_research.daily_model_run finds UNIQUE
-- (run_day) and refuses a same-day run by name
-- (SAME_DAY_RUN_NEEDS_MIGRATION_181): an INSUFFICIENT day is then closed
-- until the next UTC day, as before 181.
--
-- RE-APPLYING 181 LATER NEEDS A MANUAL STEP. The migration runner skips a
-- version recorded in schema_migrations and this script does not remove the
-- record, so first run
--   DELETE FROM schema_migrations WHERE version =
--     '181_derek_research_model_runs_decisive_run_per_day.sql';
-- and then the runner.
--
-- Safe to run twice.
DO $$
DECLARE
    ix text;
BEGIN
    IF to_regclass('derek_research_model_runs') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM derek_research_model_runs
                    GROUP BY run_day HAVING count(*) > 1) THEN
            RAISE EXCEPTION 'derek_research_model_runs holds more than one '
                            'run for a UTC day (append-only rows); 181 is '
                            'not rolled back over them';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conrelid =
                              to_regclass('derek_research_model_runs')
                          AND conname =
                              'derek_research_model_runs_one_per_day') THEN
            ALTER TABLE derek_research_model_runs
                ADD CONSTRAINT derek_research_model_runs_one_per_day
                UNIQUE (run_day);
        END IF;
        -- 181's indexes ON THIS TABLE only (never one of the same name on
        -- another schema's table further down the search path)
        FOR ix IN SELECT i.indexrelid::regclass::text
                    FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                   WHERE i.indrelid = to_regclass('derek_research_model_runs')
                     AND c.relname IN (
                         'derek_research_model_runs_one_decisive_run_per_day',
                         'derek_research_model_runs_run_day_ran_at_idx')
        LOOP
            EXECUTE 'DROP INDEX ' || ix;
        END LOOP;
    END IF;
END $$;
