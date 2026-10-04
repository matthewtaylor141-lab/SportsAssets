-- R29: why the lost-opportunity SCORES component fails (statement timeout).
-- Read only. The table sizes, the recent component outcomes, then the plan
-- of the score-candidate read exactly as the runner issues it.
\echo == sizes
SELECT 'pos_capacity' AS t, count(*) FROM pos_capacity
UNION ALL SELECT 'pos_capacity_48h', count(*) FROM pos_capacity WHERE decided_at >= now() - interval '48 hours'
UNION ALL SELECT 'pos_capacity_distinct_candidates', count(DISTINCT candidate_id) FROM pos_capacity
UNION ALL SELECT 'lol_opportunity_scores', count(*) FROM lol_opportunity_scores
UNION ALL SELECT 'us_premap', count(*) FROM us_premap
UNION ALL SELECT 'pos_snapshots', count(*) FROM pos_snapshots;
\echo == indexes on pos_capacity / lol_opportunity_scores / us_premap
SELECT tablename, indexdef FROM pg_indexes
 WHERE tablename IN ('pos_capacity','lol_opportunity_scores','us_premap') ORDER BY 1,2;
\echo == recent component outcomes
SELECT component, status, count(*), max(started_at) AS last, max(left(error,160)) AS err
  FROM lol_runs WHERE started_at >= now() - interval '6 hours' GROUP BY 1,2 ORDER BY 1,2;
\echo == plan of the score-candidate read
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT c.capacity_id, c.candidate_id, c.status,
       extract(epoch FROM c.decided_at)::float8 AS decided_at,
       d.verdict, d.label->>'competition' AS league, d.valuation_id,
       (SELECT extract(epoch FROM g.game_start)::float8
          FROM us_premap g WHERE g.market_slug = c.us_market_slug
           AND g.game_start IS NOT NULL
         ORDER BY g.updated_at DESC NULLS LAST LIMIT 1) AS event_start_at
  FROM pos_capacity_latest c
  LEFT JOIN paper_decisions d ON d.decision_id = c.candidate_id
 WHERE c.decided_at >= now() - interval '48 hours'
   AND NOT EXISTS (SELECT 1 FROM lol_opportunity_scores s
                    WHERE s.candidate_id = c.candidate_id
                      AND s.capacity_id = c.capacity_id
                      AND s.version = 'LOL_OPPORTUNITY_SCORE_V1')
 ORDER BY c.decided_at DESC LIMIT 1500;
