-- 200 · ACTUAL-LANE ADMISSION GUARD (independent audit of ebe54a8, 2026-10-03).
--
-- An execution intent could be written live_eligible = true from its strategy
-- and policy version alone, while its decision record said book currency
-- NOT_ESTABLISHED and settlement compatibility UNKNOWN. The code now derives
-- live eligibility from the decision-time admission (actual_admission.py);
-- this migration makes the database refuse the defect independently:
--
--   execution_intents_admission_ck
--       live_eligible = true only when live_eligibility.admission.verdict is
--       LIVE_ADMISSIBLE. NOT VALID: it binds every new or updated row; rows
--       written before it are audit history and are kept as written.
--   execmirror_orders_intent_admitted (trigger)
--       an actual order row that names an execution intent can be created
--       only for an intent that is live_eligible -- the claim itself is
--       refused for any other intent, whatever process attempts it.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'execution_intents_admission_ck') THEN
        ALTER TABLE execution_intents
            ADD CONSTRAINT execution_intents_admission_ck CHECK (
                NOT live_eligible
                OR (live_eligibility -> 'admission' ->> 'verdict')
                   = 'LIVE_ADMISSIBLE') NOT VALID;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION execmirror_orders_intent_admitted() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.execution_intent_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM execution_intents
             WHERE intent_id = NEW.execution_intent_id AND live_eligible) THEN
        RAISE EXCEPTION 'actual order for execution intent % refused: the intent is not live-admitted',
            NEW.execution_intent_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS execmirror_orders_intent_admitted ON execmirror_orders;
CREATE TRIGGER execmirror_orders_intent_admitted
    BEFORE INSERT ON execmirror_orders
    FOR EACH ROW EXECUTE FUNCTION execmirror_orders_intent_admitted();
