-- Rollback of 196_kalshi_smalllive.sql. REFUSES while any Kalshi live intent,
-- live fill or account reconciliation exists: those are the record of what
-- the account held and did, and they are removed deliberately or not at all.
-- No paper table is touched.
DO $$
DECLARE
    t text;
    present boolean;
BEGIN
    FOREACH t IN ARRAY ARRAY['kalshi_live_fills', 'kalshi_live_intents',
                             'kalshi_account_reconciliations'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
                INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION 'kalshi small-live records are present; 196 is '
                                'not rolled back over them';
            END IF;
        END IF;
    END LOOP;
END $$;
DROP TABLE IF EXISTS kalshi_live_events;
DROP TABLE IF EXISTS kalshi_live_fills;
DROP TABLE IF EXISTS kalshi_live_intents;
DROP TABLE IF EXISTS kalshi_smalllive_control;
DROP TABLE IF EXISTS kalshi_account_reconciliations;
