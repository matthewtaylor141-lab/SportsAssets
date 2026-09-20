-- WHY DID THE TAPE/PREMAP JOIN RETURN ZERO?
--
-- The seven busiest 20260919 tape symbols matched no us_premap row and
-- no bettor_opportunities row. The namespaces LOOK identical in shape
-- ('aec-mlb-min-laa-2026-09-18'), so the zero needs a cause before it
-- can be called a join failure.
--
-- Two candidates, and they are completely different findings:
--   RETENTION  premap holds current/upcoming markets and the tape's
--              2026-09-18 markets have since settled and been dropped.
--              The key is right; the windows do not overlap.
--   NAMESPACE  the tape's Symbol is not the same string as
--              market_slug. The key is wrong.
--
-- READ-ONLY.

SELECT 'PREMAP_DATE_COVERAGE' AS section,
       count(*)                                              AS rows,
       count(*) FILTER (WHERE p.market_slug LIKE '%2026-09-18%') AS d0918,
       count(*) FILTER (WHERE p.market_slug LIKE '%2026-09-19%') AS d0919,
       count(*) FILTER (WHERE p.market_slug LIKE '%2026-09-20%') AS d0920,
       count(*) FILTER (WHERE p.market_slug LIKE '%2026-09-21%') AS d0921,
       min(p.updated_at)                                     AS oldest_update,
       max(p.updated_at)                                     AS newest_update
  FROM us_premap p;

-- Does premap carry the SHAPE at all -- aec-mlb rows of any date?
SELECT 'PREMAP_AEC_MLB_SAMPLE' AS section,
       p.market_slug,
       p.team_league,
       p.sports_type,
       p.updated_at
  FROM us_premap p
 WHERE p.market_slug LIKE 'aec-mlb-%'
 ORDER BY p.updated_at DESC
 LIMIT 12;

-- And what BETTOR itself observed on those dates.
SELECT 'BETTOR_DATE_COVERAGE' AS section,
       count(*)                                                AS observations,
       count(*) FILTER (WHERE o.symbol LIKE '%2026-09-18%')     AS d0918,
       count(*) FILTER (WHERE o.symbol LIKE '%2026-09-19%')     AS d0919,
       count(*) FILTER (WHERE o.symbol LIKE '%2026-09-20%')     AS d0920,
       count(*) FILTER (WHERE o.symbol LIKE 'aec-%')            AS aec_rows
  FROM bettor_opportunities o;

-- A direct shape comparison: BETTOR's own aec- slugs, newest first.
SELECT 'BETTOR_AEC_SAMPLE' AS section,
       o.symbol,
       o.observed_at
  FROM bettor_opportunities o
 WHERE o.symbol LIKE 'aec-%'
 ORDER BY o.observed_at DESC
 LIMIT 12;
