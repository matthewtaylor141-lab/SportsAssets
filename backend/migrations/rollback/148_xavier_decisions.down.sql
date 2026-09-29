-- down for 148. Refuses while any Xavier decision or execution event is
-- recorded: those rows are the pre-action record of what the position manager
-- considered and chose and what execution did with it, and learning and
-- reconciliation read them.
DO $$
BEGIN
    IF to_regclass('bettor_xavier_execution_events') IS NOT NULL
       AND EXISTS (SELECT 1 FROM bettor_xavier_execution_events) THEN
        RAISE EXCEPTION 'xavier execution events are recorded; 148 is not '
                        'rolled back over them';
    END IF;
    IF to_regclass('bettor_xavier_decisions') IS NOT NULL
       AND EXISTS (SELECT 1 FROM bettor_xavier_decisions) THEN
        RAISE EXCEPTION 'xavier decisions are recorded; 148 is not rolled back '
                        'over them';
    END IF;
END $$;
DROP TABLE IF EXISTS bettor_xavier_execution_events;
DROP FUNCTION IF EXISTS bettor_xavier_event_is_bound();
DROP TABLE IF EXISTS bettor_xavier_decisions;
DROP FUNCTION IF EXISTS bettor_xavier_decision_is_a_record();
