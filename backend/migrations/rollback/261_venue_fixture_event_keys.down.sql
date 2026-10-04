-- Rollback of 261. Refuses while any fixed key exists: the rows are the
-- record of which event key each venue fixture's valuations carry, and are
-- never dropped as cleanup. Touches only 261's objects.
DO $$
BEGIN
    IF to_regclass('venue_fixture_event_keys') IS NOT NULL
       AND EXISTS (SELECT 1 FROM venue_fixture_event_keys) THEN
        RAISE EXCEPTION 'venue_fixture_event_keys holds fixed keys; '
                        'rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS venue_fixture_event_keys;
DROP FUNCTION IF EXISTS venue_fixture_event_keys_append_only();
