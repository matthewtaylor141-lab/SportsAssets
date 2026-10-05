-- Rollback of 270: refuses while either table holds a record (the refresh
-- runs and the management refusals are audit history and are never dropped
-- as cleanup). With none, drops both tables and their append-only trigger
-- function.
DO $$
DECLARE
    present boolean;
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['paper_mark_refresh_runs',
                             'paper_management_refusals'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
                INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION '% holds records; rollback refused', t;
            END IF;
        END IF;
    END LOOP;
END $$;

DROP TABLE IF EXISTS paper_management_refusals;
DROP TABLE IF EXISTS paper_mark_refresh_runs;
DROP FUNCTION IF EXISTS paper_mark_freshness_append_only();
