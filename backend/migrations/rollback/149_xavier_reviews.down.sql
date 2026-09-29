-- down for 149. Refuses while any review is recorded: a review is the record
-- of what Xavier's forecasts were compared against, and dropping it would
-- erase the evidence of which models and decisions were found wanting.
DO $$
BEGIN
    IF to_regclass('bettor_xavier_reviews') IS NOT NULL
       AND EXISTS (SELECT 1 FROM bettor_xavier_reviews) THEN
        RAISE EXCEPTION 'xavier reviews are recorded; 149 is not rolled back '
                        'over them';
    END IF;
END $$;
DROP TABLE IF EXISTS bettor_xavier_reviews;
DROP FUNCTION IF EXISTS bettor_xavier_review_is_a_record();
