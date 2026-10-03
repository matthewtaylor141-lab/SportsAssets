-- Refuses while any execution intent exists: the intents are the audit chain
-- linking each decision to its paper and actual executions and are never
-- dropped as cleanup.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM execution_intents) THEN
        RAISE EXCEPTION 'execution_intents holds records; rollback refused';
    END IF;
END $$;
DROP INDEX IF EXISTS execmirror_orders_execution_intent_uq;
ALTER TABLE execmirror_orders DROP COLUMN IF EXISTS execution_intent_id;
DROP TABLE IF EXISTS execution_intents;
