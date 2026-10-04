-- Rollback of 222 (Xavier's recommendation valuation, state and requeue
-- triggers). Refuses while any assessment carries a recorded valuation or
-- any paper review was recorded under one of the four new triggers: those
-- are the only record of what each recommendation stood on and why each
-- review ran, and are never dropped or rewritten as cleanup. With none
-- recorded: the two nullable columns and the three constraints are removed
-- and paper_xavier_reviews gets migration 172's original trigger CHECK back.
DO $$
BEGIN
    IF to_regclass('xavier_management_assessments') IS NOT NULL
       AND EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'xavier_management_assessments'
                      AND column_name = 'valuation')
       AND EXISTS (SELECT 1 FROM xavier_management_assessments
                    WHERE valuation IS NOT NULL) THEN
        RAISE EXCEPTION 'xavier_management_assessments holds recorded '
                        'valuations; rollback refused';
    END IF;
    IF to_regclass('paper_xavier_reviews') IS NOT NULL
       AND EXISTS (SELECT 1 FROM paper_xavier_reviews
                    WHERE trigger IN ('ORDER_EVENT', 'VALUATION_CHANGE',
                                      'GAME_STATE_CHANGE',
                                      'FRESHNESS_EXPIRY')) THEN
        RAISE EXCEPTION 'paper_xavier_reviews holds reviews under the 222 '
                        'triggers; rollback refused';
    END IF;
END $$;
ALTER TABLE IF EXISTS xavier_management_assessments
    DROP CONSTRAINT IF EXISTS xavier_assessments_no_stale_action_ck,
    DROP CONSTRAINT IF EXISTS xavier_assessments_valuation_ck,
    DROP CONSTRAINT IF EXISTS xavier_assessments_rec_state_ck,
    DROP COLUMN IF EXISTS recommendation_state,
    DROP COLUMN IF EXISTS valuation;
DO $$
BEGIN
    IF to_regclass('paper_xavier_reviews') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = 'paper_xavier_reviews_trigger_ck')
    THEN
        ALTER TABLE paper_xavier_reviews
            ADD CONSTRAINT paper_xavier_reviews_trigger_ck CHECK (trigger IN (
                'FIRST_FILL', 'FILL_EVENT', 'MARKET_EVENT',
                'SCHEDULED_BACKSTOP')) NOT VALID;
    END IF;
END $$;
ALTER TABLE IF EXISTS paper_xavier_reviews
    DROP CONSTRAINT IF EXISTS paper_xavier_reviews_trigger_v2_ck;
