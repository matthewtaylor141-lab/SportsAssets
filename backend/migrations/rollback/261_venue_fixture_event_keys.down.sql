-- Rollback of 261. Refuses while any fixed key exists: the rows are the
-- record of which event key each venue fixture's valuations carry, and are
-- never dropped as cleanup. Touches only 261's objects. A second run is a
-- no-op: the guard reads the table only when it exists (EXECUTE, because a
-- PL/pgSQL `IF a AND b` still plans `b`, and planning a query on a dropped
-- table raises -- fix stage 2026-10-05).
DO $$
DECLARE
    held boolean := false;
BEGIN
    IF to_regclass('venue_fixture_event_keys') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM venue_fixture_event_keys)'
           INTO held;
    END IF;
    IF held THEN
        RAISE EXCEPTION 'venue_fixture_event_keys holds fixed keys; '
                        'rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS venue_fixture_event_keys;
DROP FUNCTION IF EXISTS venue_fixture_event_keys_append_only();
