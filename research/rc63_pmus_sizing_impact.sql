\echo rc63 pmus-sizing: the ACTUAL BUY size of every execution intent of the last 14 days at 1:1000 under three rules
\echo HALF_EVEN = base scale_qty (nearest whole contract), FLOOR_1 = the lane after rc6.3 (round down to one contract),
\echo EXACT_001 = the market increment 0.01 that fractional markets document (round down), not sendable by the lane today
\echo rows are intents; raw = paper_target_qty / 1000; a rule sends when its size is at least its own unit

WITH i AS (
  SELECT intent_id, strategy, live_eligible, order_intent,
         paper_target_qty / 1000.0 AS raw,
         CASE WHEN order_intent = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire_price
              ELSE wire_price END AS cpc
    FROM execution_intents
   WHERE created_at > now() - interval '14 days'
     AND paper_target_qty > 0
), s AS (
  SELECT *,
         CASE WHEN raw - floor(raw) = 0.5
              THEN floor(raw) + (floor(raw)::bigint % 2)
              ELSE round(raw) END AS half_even,
         floor(raw) AS floor_1,
         floor(raw * 100) / 100 AS exact_001
    FROM i
)
SELECT 'ALL_INTENTS' AS population,
       count(*) AS intents,
       count(*) FILTER (WHERE live_eligible) AS live_eligible,
       round(sum(raw), 3) AS raw_contracts,
       round(sum(raw * cpc), 2) AS raw_target_usd,
       count(*) FILTER (WHERE half_even >= 1) AS half_even_sendable,
       count(*) FILTER (WHERE floor_1 >= 1) AS floor_1_sendable,
       count(*) FILTER (WHERE exact_001 >= 0.01) AS exact_001_sendable,
       count(*) FILTER (WHERE half_even > raw) AS half_even_enlarged,
       count(*) FILTER (WHERE floor_1 > raw) AS floor_1_enlarged,
       round(sum(half_even) FILTER (WHERE half_even >= 1) / nullif(sum(raw), 0), 4) AS half_even_ratio,
       round(sum(floor_1) FILTER (WHERE floor_1 >= 1) / nullif(sum(raw), 0), 4) AS floor_1_ratio,
       round(sum(exact_001) FILTER (WHERE exact_001 >= 0.01) / nullif(sum(raw), 0), 4) AS exact_001_ratio,
       round(sum(half_even * cpc) FILTER (WHERE half_even >= 1), 2) AS half_even_usd,
       round(sum(floor_1 * cpc) FILTER (WHERE floor_1 >= 1), 2) AS floor_1_usd,
       round(sum(exact_001 * cpc) FILTER (WHERE exact_001 >= 0.01), 2) AS exact_001_usd
  FROM s
UNION ALL
SELECT 'RAW_AT_LEAST_ONE_CONTRACT',
       count(*), count(*) FILTER (WHERE live_eligible),
       round(sum(raw), 3), round(sum(raw * cpc), 2),
       count(*) FILTER (WHERE half_even >= 1), count(*) FILTER (WHERE floor_1 >= 1),
       count(*) FILTER (WHERE exact_001 >= 0.01),
       count(*) FILTER (WHERE half_even > raw), count(*) FILTER (WHERE floor_1 > raw),
       round(sum(half_even) FILTER (WHERE half_even >= 1) / nullif(sum(raw), 0), 4),
       round(sum(floor_1) FILTER (WHERE floor_1 >= 1) / nullif(sum(raw), 0), 4),
       round(sum(exact_001) FILTER (WHERE exact_001 >= 0.01) / nullif(sum(raw), 0), 4),
       round(sum(half_even * cpc) FILTER (WHERE half_even >= 1), 2),
       round(sum(floor_1 * cpc) FILTER (WHERE floor_1 >= 1), 2),
       round(sum(exact_001 * cpc) FILTER (WHERE exact_001 >= 0.01), 2)
  FROM s WHERE raw >= 1;

\echo by strategy (all intents of the window)
SELECT strategy, count(*) AS intents,
       count(*) FILTER (WHERE live_eligible) AS live_eligible,
       round(min(paper_target_qty), 2) AS min_paper_qty,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY paper_target_qty)::numeric, 2) AS median_paper_qty,
       round(max(paper_target_qty), 2) AS max_paper_qty
  FROM execution_intents
 WHERE created_at > now() - interval '14 days'
 GROUP BY strategy ORDER BY 2 DESC;

\echo the ACTUAL lane record: execmirror_orders rows ever and live_qty values (expected none sent while SMALL LIVE is SHADOW)
SELECT count(*) AS rows_ever,
       count(*) FILTER (WHERE venue_order_id IS NOT NULL) AS with_venue_order_id,
       count(*) FILTER (WHERE execution_intent_id IS NOT NULL) AS from_execution_intents
  FROM execmirror_orders;
