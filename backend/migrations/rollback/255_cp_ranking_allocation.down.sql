-- ROLLBACK 255 · CONTROL PLANE RANKING AND CAPITAL ALLOCATION (stream C).
-- Refused while ANY ranking or allocation row exists: they are the record of
-- what the ranker and the allocator decided in SHADOW and are never dropped
-- as cleanup. With none, drops only 255's objects. Like 225's rollback, the
-- 217 registry function is left as declared (it skips absent tables).
DO $$
BEGIN
    IF (to_regclass('cp_opportunity_rankings') IS NOT NULL
            AND EXISTS (SELECT 1 FROM cp_opportunity_rankings))
       OR (to_regclass('cp_capital_allocations') IS NOT NULL
            AND EXISTS (SELECT 1 FROM cp_capital_allocations)) THEN
        RAISE EXCEPTION 'ROLLBACK_255_REFUSED: ranking / allocation records exist (audit chain)';
    END IF;
END $$;

DROP TABLE IF EXISTS cp_capital_allocations;
DROP TABLE IF EXISTS cp_opportunity_rankings;
DROP TABLE IF EXISTS cp_ranking_allocation_configs;
DROP FUNCTION IF EXISTS cp_ranking_allocation_append_only();
