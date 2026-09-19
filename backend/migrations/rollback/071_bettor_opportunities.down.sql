-- Rollback 071. Drops BETTOR's own prospective dataset.
--
-- NOTE FOR WHOEVER RUNS THIS: this is the PRIMARY product's evidence --
-- the market states BETTOR actually had, the features it actually
-- computed, and the refusals it actually made, each recorded before the
-- outcome existed. It cannot be rebuilt from anything: the book at that
-- instant is gone, and a refusal leaves no trace anywhere else. Export
-- before dropping.

BEGIN;

ALTER TABLE shadow_decisions
    DROP CONSTRAINT IF EXISTS shadow_decisions_bettor_observed;
DROP INDEX IF EXISTS shadow_decisions_opportunity_idx;
ALTER TABLE shadow_decisions
    DROP COLUMN IF EXISTS bettor_opportunity_id;

DROP TABLE IF EXISTS bettor_opportunities;

COMMIT;
