-- WHY HAS NO VALUATION EVER BEEN ADMISSIBLE. Read-only.
--
-- `research/funded_state.sql` returned the fact that matters most: 1,126
-- valuations written between 2026-09-24 and 2026-09-27, 1,117 of them ELIGIBLE,
-- and `admissible` = 0 for every single one. The lane evaluates and refuses
-- everything, so the first autonomous order is blocked by an economic or
-- engineering refusal and not by an authorization.
--
-- This asks WHICH refusal, unnested, so the answer is a named step rather than a
-- count. Run through research-sql.yml.

\echo == 1 · EVERY REFUSAL, UNNESTED AND RANKED ==
SELECT r AS refusal, count(*) AS n
  FROM external_valuations v, unnest(v.refusals) AS r
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 40;

\echo == 2 · HOW MANY REFUSALS EACH VALUATION CARRIES ==
SELECT coalesce(array_length(refusals, 1), 0) AS refusal_count,
       count(*)                               AS n
  FROM external_valuations
 GROUP BY 1
 ORDER BY 1;

\echo == 3 · THE FIRST REFUSAL ON THE NEWEST ELIGIBLE ROWS ==
SELECT observed_at::timestamptz(0) AS observed,
       us_market_slug,
       round(probability::numeric, 4)                   AS prob,
       round(executable_price::numeric, 4)              AS exec_px,
       round(estimated_edge_per_contract::numeric, 4)   AS edge,
       coalesce(refusals[1], '(none)')                  AS first_refusal,
       left(coalesce(why, ''), 90)                      AS why
  FROM external_valuations
 WHERE eligibility = 'ELIGIBLE'
 ORDER BY observed_at DESC
 LIMIT 15;

\echo == 4 · SETTLEMENT COMPATIBILITY, WHICH GATES A FUNDED ACTION ==
SELECT coalesce(settlement_comparison ->> 'verdict', '(null)') AS verdict,
       coalesce(settlement_comparison ->> 'fixture_event_state', '(null)') AS event_state,
       count(*) AS n
  FROM external_valuations
 GROUP BY 1, 2
 ORDER BY 3 DESC
 LIMIT 20;

\echo == 5 · WHETHER AN EDGE WAS EVER POSITIVE AFTER COSTS ==
SELECT count(*)                                                       AS rows_priced,
       count(*) FILTER (WHERE estimated_edge_per_contract > 0)         AS positive_edge,
       round(max(estimated_edge_per_contract)::numeric, 4)             AS best_edge,
       round(avg(estimated_edge_per_contract)::numeric, 4)             AS avg_edge
  FROM external_valuations
 WHERE estimated_edge_per_contract IS NOT NULL;
