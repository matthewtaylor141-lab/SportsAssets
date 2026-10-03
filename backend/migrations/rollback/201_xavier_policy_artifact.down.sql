-- Refuses while any artifact carries an owner approval record: an approval
-- is part of the management record and is never dropped as cleanup.
-- Otherwise drops the artifact table (the unapproved XAVIER_SMALL_LIVE_
-- MANAGEMENT_V1 row with it; agents/xavier_small_live_policy.py still
-- declares it and reports it READY_FOR_OWNER_APPROVAL, not stored).
DO $$
BEGIN
    IF to_regclass('agent_policy_artifacts') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM agent_policy_artifacts
                WHERE owner_approval_actor IS NOT NULL
                   OR status IN ('APPROVED', 'SUPERSEDED')) THEN
        RAISE EXCEPTION 'agent_policy_artifacts holds an owner approval '
                        'record; rollback refused';
    END IF;
END $$;
-- the table's guard trigger is dropped with it
DROP TABLE IF EXISTS agent_policy_artifacts;
DROP FUNCTION IF EXISTS agent_policy_artifacts_guard();
