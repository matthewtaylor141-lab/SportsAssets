-- Rollback of 191: drops agent_slack_delivery.requested_by (the verified
-- management Slack user id). Refuses while any delivery names its requester:
-- that id is the actor of a research assignment made from Slack. A second
-- run, or a run after rollback 190, is a no-op.
DO $$
DECLARE
    held boolean := false;
BEGIN
    IF to_regclass('agent_slack_delivery') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agent_slack_delivery'
                  AND column_name = 'requested_by') THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM agent_slack_delivery '
                ' WHERE requested_by IS NOT NULL)' INTO held;
    END IF;
    IF held THEN
        RAISE EXCEPTION 'agent_slack_delivery.requested_by names requesters; '
                        'rollback refused';
    END IF;
    ALTER TABLE agent_slack_delivery DROP COLUMN IF EXISTS requested_by;
END $$;
