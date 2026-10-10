-- 367 rollback: the Kalshi SHADOW planner's own records (rc6.3
-- kalshi-shadow). Nothing reads these tables for an order, a fill, a
-- position, a control or a decision; they are SHADOW evidence only.
--
-- THE OPERATIONAL ROLLBACK IS THE KILL SWITCH, NOT THIS FILE:
-- KALSHI_SHADOW_PLANNER=off on the API stops the runner, and the previous
-- release never reads or writes these tables. So this file refuses while
-- either append-only table holds a row (evidence is not dropped); it only
-- reverses a migration that recorded nothing.
--
-- Safe to apply twice. Each emptiness check sits in its own nested IF:
-- PL/pgSQL plans a statement when it first runs it, so a check of a table
-- an earlier apply already dropped is never planned.
DO $$
BEGIN
 IF to_regclass('kalshi_shadow_intents') IS NOT NULL THEN
   IF EXISTS(SELECT 1 FROM kalshi_shadow_intents LIMIT 1) THEN
     RAISE EXCEPTION 'Refusing rollback 367: kalshi_shadow_intents contains evidence; set KALSHI_SHADOW_PLANNER=off instead';
   END IF;
 END IF;
 IF to_regclass('kalshi_shadow_account_reads') IS NOT NULL THEN
   IF EXISTS(SELECT 1 FROM kalshi_shadow_account_reads LIMIT 1) THEN
     RAISE EXCEPTION 'Refusing rollback 367: kalshi_shadow_account_reads contains evidence; set KALSHI_SHADOW_PLANNER=off instead';
   END IF;
 END IF;
END $$;
DROP TABLE IF EXISTS kalshi_shadow_intents;
DROP TABLE IF EXISTS kalshi_shadow_account_reads;
DROP FUNCTION IF EXISTS kalshi_shadow_append_only();
