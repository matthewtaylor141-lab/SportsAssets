-- Rollback of 190 (the Slack agent bridge's delivery queue and its control
-- audit). Refuses while either table holds a row: a delivery is the record
-- of what an agent said to a manager, and a control-audit row is the record
-- of who switched the bridge on or off; neither is dropped as cleanup. The
-- `agent.slack.bridge` ingestion_state switch 190 seeded goes with it.
-- Touches only 190's objects; a second run is a no-op (the guard reads a
-- table only when it exists, by EXECUTE, as rollback 249).
DO $$
DECLARE
    held boolean := false;
BEGIN
    IF to_regclass('agent_slack_delivery') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM agent_slack_delivery)' INTO held;
    END IF;
    IF NOT held AND to_regclass('agent_slack_control_audit') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM agent_slack_control_audit)'
           INTO held;
    END IF;
    IF held THEN
        RAISE EXCEPTION 'agent_slack_delivery / agent_slack_control_audit '
                        'hold records; rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS agent_slack_control_audit;
DROP INDEX IF EXISTS agent_slack_pending;
DROP TABLE IF EXISTS agent_slack_delivery;
DO $$
BEGIN
    IF to_regclass('ingestion_state') IS NOT NULL THEN
        DELETE FROM ingestion_state WHERE key = 'agent.slack.bridge';
    END IF;
END $$;
