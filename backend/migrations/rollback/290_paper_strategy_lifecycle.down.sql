-- Rollback of 290. REFUSES while any lifecycle event exists: a recorded
-- transition (and the evidence it fired on) is part of the audit trail and
-- is never dropped as cleanup. With none, removes the view, the table, its
-- triggers and its function.
DO $$
DECLARE
    present boolean;
BEGIN
    IF to_regclass('paper_strategy_lifecycle_events') IS NOT NULL THEN
        SELECT EXISTS (SELECT 1 FROM paper_strategy_lifecycle_events)
          INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'paper_strategy_lifecycle_events holds records; '
                            'rollback refused';
        END IF;
    END IF;
END $$;
DROP VIEW IF EXISTS paper_strategy_lifecycle_current_v;
DROP TABLE IF EXISTS paper_strategy_lifecycle_events;
DROP FUNCTION IF EXISTS paper_lifecycle_is_append_only();
