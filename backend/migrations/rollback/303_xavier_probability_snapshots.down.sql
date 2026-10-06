-- Rollback of 303: refuses while xavier_probability_snapshots holds a record
-- (a snapshot is the persisted evidence a management review stood on: audit
-- history, never dropped as cleanup). With none, drops the table and its
-- append-only trigger function.
DO $$
DECLARE
    present boolean;
BEGIN
    IF to_regclass('xavier_probability_snapshots') IS NOT NULL THEN
        SELECT EXISTS (SELECT 1 FROM xavier_probability_snapshots)
          INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'xavier_probability_snapshots holds records; '
                            'rollback refused';
        END IF;
    END IF;
END $$;
DROP TABLE IF EXISTS xavier_probability_snapshots;
DROP FUNCTION IF EXISTS xavier_probability_snapshots_append_only();
