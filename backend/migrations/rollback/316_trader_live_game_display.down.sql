-- 316 rollback: the Trader live-game DISPLAY sidecar (BETTOR Live Game State
-- V1). Nothing reads these tables for a price, a probability, an order, a
-- protection packet or a settlement; the Trader read model only attaches
-- positions[].game / score_feed from them while BETTOR_DISPLAY_SCORES is on.
--
-- THE OPERATIONAL ROLLBACK IS THE FLAG, NOT THIS FILE. The package's own
-- checklist: disable BETTOR_DISPLAY_SCORES on the API and the collector, and
-- do NOT drop evidence tables or rewrite observations. So this file refuses
-- while the append-only ledgers (bindings, observations) hold any row, as
-- 312 and 313 do for theirs; it only reverses a migration that recorded
-- nothing. latest / health are rebuildable views of those ledgers.
--
-- Safe to apply twice. Each emptiness check sits in its own nested IF:
-- PL/pgSQL plans a statement when it first runs it, so a check of a table
-- an earlier apply already dropped is never planned.
DO $$
BEGIN
 IF to_regclass('trader_display_score_bindings') IS NOT NULL THEN
   IF EXISTS(SELECT 1 FROM trader_display_score_bindings LIMIT 1) THEN
     RAISE EXCEPTION 'Refusing rollback 316: trader_display_score_bindings contains evidence; disable BETTOR_DISPLAY_SCORES instead';
   END IF;
 END IF;
 IF to_regclass('trader_display_score_observations') IS NOT NULL THEN
   IF EXISTS(SELECT 1 FROM trader_display_score_observations LIMIT 1) THEN
     RAISE EXCEPTION 'Refusing rollback 316: trader_display_score_observations contains evidence; disable BETTOR_DISPLAY_SCORES instead';
   END IF;
 END IF;
END $$;
DROP TABLE IF EXISTS trader_display_score_latest;
DROP TABLE IF EXISTS trader_display_score_health;
DROP TABLE IF EXISTS trader_display_score_observations;
DROP TABLE IF EXISTS trader_display_score_bindings;
DROP FUNCTION IF EXISTS trader_display_score_append_only();
