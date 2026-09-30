-- down for 152. REFUSES while any agent record exists: the handoffs name who
-- owns inventory, the task history and decision index are what the owner and
-- Audrey read, and the policy versions say what was binding. None of that is
-- dropped by a rollback; it is removed deliberately or not at all.
DO $$
DECLARE
    t text;
    present boolean;
BEGIN
    FOREACH t IN ARRAY ARRAY['agent_handoff_fills', 'agent_position_handoffs',
                             'agent_decisions', 'agent_task_events',
                             'agent_tasks', 'agent_policy_versions',
                             'agent_runs', 'agent_status',
                             'agent_identities'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
                INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION 'agent records are present; 152 is not rolled '
                                'back over them';
            END IF;
        END IF;
    END LOOP;
END $$;
DROP TABLE IF EXISTS agent_handoff_fills;
DROP TABLE IF EXISTS agent_position_handoffs;
DROP TABLE IF EXISTS agent_decisions;
DROP TABLE IF EXISTS agent_task_events;
DROP FUNCTION IF EXISTS agent_task_events_append_only();
DROP TABLE IF EXISTS agent_tasks;
DROP TABLE IF EXISTS agent_policy_versions;
DROP TABLE IF EXISTS agent_runs;
DROP FUNCTION IF EXISTS agent_runs_append_only();
DROP TABLE IF EXISTS agent_status;
DROP TABLE IF EXISTS agent_identities;
