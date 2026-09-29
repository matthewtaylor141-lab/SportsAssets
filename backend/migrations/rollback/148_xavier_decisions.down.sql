-- down for 148. Refuses while any Xavier decision is recorded: those rows are
-- the pre-action record of what the position manager considered and chose,
-- and learning reads them.
DO $$
BEGIN
    IF to_regclass('bettor_xavier_decisions') IS NOT NULL
       AND EXISTS (SELECT 1 FROM bettor_xavier_decisions) THEN
        RAISE EXCEPTION 'xavier decisions are recorded; 148 is not rolled back '
                        'over them';
    END IF;
END $$;
DROP TABLE IF EXISTS bettor_xavier_decisions;
DROP FUNCTION IF EXISTS bettor_xavier_decision_is_a_record();
