-- 368 rollback: the exact (fractional) live quantity columns.
--
-- THE OPERATIONAL ROLLBACK DOES NOT NEED THIS FILE. The previous release runs
-- on the 368 schema unchanged: both columns are nullable, have no default and
-- no constraint, and it never names them.
--
-- SCHEMA-COMPATIBLE, NOT BEHAVIOUR-COMPATIBLE ONCE A FRACTIONAL ROW EXISTS
-- (review r2). The previous release never reads live_qty_exact: it reads the
-- rounded-up integer live_qty, so a 2.41 order that the venue reports
-- CANCELLED with 2.41 filled is never promoted to FILLED (2.41 < 3), Audrey
-- raises a false LIVE_QTY_NOT_THE_ROUNDED_SCALED_QTY (|3 - 2.419| > 0.5),
-- and live_inventory int()-truncates 2.41 held to 2, leaving 0.41 that no
-- exit, close or flatten of that release would ever sell. No fractional row
-- can exist while SMALL LIVE is SHADOW (no ACTUAL order is sent). After an
-- activation, rolling the CODE back past this release while a fractional row
-- exists needs those positions flattened (or reconciled by the owner) first.
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
