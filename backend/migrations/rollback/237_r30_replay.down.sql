-- Rollback of 237 (the historical R30 decision replay). Refuses while any
-- replay run is recorded: the runs are research records (each names the
-- code sha, parameters and clock range that produced it) and are never
-- dropped as cleanup. Touches only 237's objects.
DO $$
BEGIN
    IF to_regclass('r30_replay_runs') IS NOT NULL
       AND EXISTS (SELECT 1 FROM r30_replay_runs) THEN
        RAISE EXCEPTION 'r30_replay_runs holds recorded replay runs; '
                        'rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS r30_replay_events;
DROP TABLE IF EXISTS r30_replay_decisions;
DROP TABLE IF EXISTS r30_replay_runs;
DROP FUNCTION IF EXISTS r30_replay_append_only();
