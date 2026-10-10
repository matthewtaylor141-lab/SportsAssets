-- 368 rollback: the exact (fractional) live quantity columns.
--
-- THE OPERATIONAL ROLLBACK DOES NOT NEED THIS FILE. The previous release runs
-- on the 368 schema unchanged: both columns are nullable, have no default and
-- no constraint, and it never names them.
--
-- Refuses while any row carries a fractional quantity: such a row records an
-- ACTUAL order the venue was sent at a size live_qty (rounded up) does not
-- state, and that evidence is never dropped as cleanup. Safe to apply twice:
-- each check sits in its own nested IF, so a table or column an earlier apply
-- already dropped is never planned.
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'execmirror_orders'
               AND column_name = 'live_qty_exact') THEN
   IF EXISTS (SELECT 1 FROM execmirror_orders WHERE live_qty_exact IS NOT NULL) THEN
     RAISE EXCEPTION 'Refusing rollback 368: execmirror_orders holds orders sent at a fractional quantity';
   END IF;
 END IF;
 IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'execution_intents'
               AND column_name = 'live_qty_exact') THEN
   IF EXISTS (SELECT 1 FROM execution_intents WHERE live_qty_exact IS NOT NULL) THEN
     RAISE EXCEPTION 'Refusing rollback 368: execution_intents holds fractional ACTUAL sizes';
   END IF;
 END IF;
END $$;
ALTER TABLE execmirror_orders DROP COLUMN IF EXISTS live_qty_exact;
ALTER TABLE execution_intents DROP COLUMN IF EXISTS live_qty_exact;
