-- Rollback 068. Drops the shadow lane entirely.
--
-- NOTE FOR WHOEVER RUNS THIS: the decision ledger is prospective
-- evidence. Once discarded, what BETTOR claimed to know at each T0
-- cannot be reconstructed -- not from the outcomes, not from the scores,
-- not from anything. Export before dropping if the run mattered.

BEGIN;

DROP TABLE IF EXISTS shadow_scores;
DROP TABLE IF EXISTS shadow_position_events;
DROP TABLE IF EXISTS shadow_positions;
DROP TABLE IF EXISTS shadow_executions;
DROP TABLE IF EXISTS shadow_decisions;
DROP FUNCTION IF EXISTS shadow_append_only();

COMMIT;
