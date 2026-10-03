-- Removes the database-level admission guard only; no record is touched.
DROP TRIGGER IF EXISTS execmirror_orders_intent_admitted ON execmirror_orders;
DROP FUNCTION IF EXISTS execmirror_orders_intent_admitted();
ALTER TABLE execution_intents DROP CONSTRAINT IF EXISTS execution_intents_admission_ck;
