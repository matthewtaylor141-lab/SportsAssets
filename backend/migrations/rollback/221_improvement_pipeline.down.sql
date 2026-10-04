-- Rollback of 221 (the bounded self-improvement and collaboration pipeline).
-- Refuses while any HUMAN / ENGINEERING step or any disagreement is on the
-- ledger: a release approval, a candidate patch reference, a rollback, a
-- human review and a preserved dissent are records of people's decisions and
-- are never dropped as cleanup. Items, runner-written stages and runs alone
-- are re-derivable from the agents' own records and do not block it.
-- Touches only improve_* objects (migration 155's improvement_* tables are
-- not this migration's and are left alone).
DO $$
BEGIN
    IF to_regclass('improve_events') IS NOT NULL
       AND EXISTS (SELECT 1 FROM improve_events
                    WHERE actor_class IN ('HUMAN', 'ENGINEERING')) THEN
        RAISE EXCEPTION 'improve_events holds human / engineering steps; '
                        'rollback refused';
    END IF;
    IF to_regclass('improve_disagreements') IS NOT NULL
       AND EXISTS (SELECT 1 FROM improve_disagreements) THEN
        RAISE EXCEPTION 'improve_disagreements holds preserved disagreements; '
                        'rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS improve_runs;
DROP TABLE IF EXISTS improve_disagreements;
DROP TABLE IF EXISTS improve_events;
DROP TABLE IF EXISTS improve_items;
DROP FUNCTION IF EXISTS improve_no_truncate();
DROP FUNCTION IF EXISTS improve_runs_append_only();
DROP FUNCTION IF EXISTS improve_disagreements_guard();
DROP FUNCTION IF EXISTS improve_items_guard();
DROP FUNCTION IF EXISTS improve_events_advance();
DROP FUNCTION IF EXISTS improve_events_guard();
DROP FUNCTION IF EXISTS improve_runner_session();
DROP FUNCTION IF EXISTS improve_refs_exist(jsonb);
DROP FUNCTION IF EXISTS improve_ref_exists(jsonb);
DROP FUNCTION IF EXISTS improve_ref_target(text);
DROP FUNCTION IF EXISTS improve_stage_seq(text);
DROP FUNCTION IF EXISTS improve_no_authority(jsonb);
DROP FUNCTION IF EXISTS improve_positions_ok(jsonb);
DROP FUNCTION IF EXISTS improve_parties_distinct(text[]);
DROP FUNCTION IF EXISTS improve_ref_ok(jsonb);
DROP FUNCTION IF EXISTS improve_refs_grounded(jsonb);
DROP FUNCTION IF EXISTS improve_is_machine_actor(text);
