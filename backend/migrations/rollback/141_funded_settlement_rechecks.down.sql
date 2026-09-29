-- down for 141. Refuses while any re-read is recorded: a disagreement is why
-- a label was excluded and an account refused new exposure, and dropping the
-- table would silently restore both.
DO $$
BEGIN
    IF to_regclass('bettor_funded_settlement_rechecks') IS NOT NULL
       AND EXISTS (SELECT 1 FROM bettor_funded_settlement_rechecks) THEN
        RAISE EXCEPTION 'settlement re-reads are recorded; 141 is not rolled '
                        'back over them';
    END IF;
END $$;
DROP TABLE IF EXISTS bettor_funded_settlement_rechecks;
DROP FUNCTION IF EXISTS bettor_funded_recheck_is_append_only();
