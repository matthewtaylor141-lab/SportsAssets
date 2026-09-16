-- ============================================================================
-- WHY IS CHECK A TOO EXPENSIVE? Measure it instead of guessing again.
-- (2026-09-11, read-only.)
--
-- Check A has now timed out three times: 600 s (run 33), 1200 s (run 34), and
-- 1200 s again (run 35) AFTER three structural fixes -- single-leg conditions
-- dropped before the windows, one window pass instead of two, and a bigint
-- sort key instead of two hex strings. Each of those was a real cost, and the
-- query is still not finishing. A fourth blind attempt is not the move.
--
-- So this file measures rather than assumes. It is cheap by construction:
-- counts over indexed predicates, and an EXPLAIN that PLANS the heavy query
-- WITHOUT RUNNING IT. Nothing here executes the expensive plan, so it should
-- return in seconds; if even this is slow, that itself is the finding.
--
-- The three things worth knowing, in order:
--   1  how big the population actually is. Every estimate so far has been my
--      inference from an earlier run's output ("~350k canonical fills"), never
--      a measured number. If it is an order of magnitude larger, the design is
--      wrong rather than the tuning.
--   2  how big copy_probes is, since the probe set is built by scanning it.
--   3  what the planner intends to do -- which node it expects to be
--      expensive, whether it expects a disk sort, and whether its row
--      estimates are anywhere near reality.
--
-- Read-only: three statements, none of which runs the heavy query.
-- ============================================================================


\echo '== 1. SCALE: how many rows and conditions does check A actually touch? =='
WITH raw AS (
  SELECT t.id, t.condition_id, t.outcome_index
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), per_cond AS (
  SELECT condition_id, count(*) AS n,
         count(*) FILTER (WHERE outcome_index = 0) AS n_yes,
         count(*) FILTER (WHERE outcome_index = 1) AS n_no
    FROM raw GROUP BY 1
)
SELECT (SELECT count(*) FROM raw) AS base_rows_both_feeds,
       count(*) AS conditions,
       count(*) FILTER (WHERE n_yes > 0 AND n_no > 0) AS TWO_LEG_CONDITIONS,
       COALESCE(sum(n) FILTER (WHERE n_yes > 0 AND n_no > 0), 0) AS ROWS_IN_TWO_LEG_CONDITIONS,
       round((100.0 * COALESCE(sum(n) FILTER (WHERE n_yes > 0 AND n_no > 0), 0)
              / NULLIF((SELECT count(*) FROM raw), 0))::numeric, 2) AS pct_rows_surviving_prefilter,
       max(n) AS biggest_condition_rows,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY n)::numeric, 1) AS p50_rows_per_condition,
       round(percentile_cont(0.999) WITHIN GROUP (ORDER BY n)::numeric, 1) AS p999_rows_per_condition
  FROM per_cond;


\echo '== 2. THE PROBE TABLE: how much is one scan of copy_probes? =='
SELECT count(*) AS copy_probes_rows,
       count(*) FILTER (WHERE book_ok AND best_ask IS NOT NULL) AS usable_probe_rows,
       count(DISTINCT trade_id) FILTER (WHERE book_ok AND best_ask IS NOT NULL)
         AS distinct_probed_trade_ids,
       pg_size_pretty(pg_total_relation_size('copy_probes')) AS total_size,
       pg_size_pretty(pg_relation_size('copy_probes')) AS heap_size
  FROM copy_probes;


\echo '== 3. THE PLAN, not the run: EXPLAIN only, nothing executed =='
EXPLAIN
WITH raw AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), two_leg AS (
  SELECT condition_id FROM raw
   GROUP BY 1 HAVING count(*) FILTER (WHERE outcome_index = 0) > 0
                AND count(*) FILTER (WHERE outcome_index = 1) > 0
), base AS (
  SELECT r.* FROM raw r JOIN two_leg tl ON tl.condition_id = r.condition_id
), probed AS (
  SELECT DISTINCT p.trade_id FROM copy_probes p
   WHERE p.book_ok AND p.best_ask IS NOT NULL AND p.trade_id IS NOT NULL
), env AS (
  SELECT tx_hash, asset,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.*, (pr.trade_id IS NOT NULL) AS covered
    FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.canon_feed = b.feed
    LEFT JOIN probed pr ON pr.trade_id = b.id
), dm AS (
  SELECT condition_id, covered,
         GREATEST(LEAST(cy, cn) - LEAST(cy - y_now, cn - n_now), 0) AS d_m
    FROM (
      SELECT condition_id, covered,
             CASE WHEN outcome_index = 0 THEN sh ELSE 0 END AS y_now,
             CASE WHEN outcome_index = 1 THEN sh ELSE 0 END AS n_now,
             sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) OVER w AS cy,
             sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) OVER w AS cn
        FROM canon
      WINDOW w AS (PARTITION BY condition_id ORDER BY ts, id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) s
), coh AS (
  SELECT condition_id,
         count(*) FILTER (WHERE d_m > 0.000001) AS dm_fills,
         count(*) FILTER (WHERE d_m > 0.000001 AND covered) AS dm_fills_covered,
         sum(d_m) AS dm_total,
         COALESCE(sum(d_m) FILTER (WHERE covered), 0) AS dm_covered
    FROM dm GROUP BY 1
), cond AS (
  SELECT condition_id,
         sum(sh) FILTER (WHERE outcome_index = 0) AS qy,
         sum(sh) FILTER (WHERE outcome_index = 1) AS qn,
         sum(sh * px) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS vy,
         sum(sh * px) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS vn
    FROM canon GROUP BY 1
)
SELECT c.condition_id, LEAST(c.qy, c.qn) AS mm, (c.vy + c.vn) AS pair_cost,
       k.dm_fills, k.dm_fills_covered, k.dm_total, k.dm_covered
  FROM cond c JOIN coh k ON k.condition_id = c.condition_id
 WHERE c.qy > 0 AND c.qn > 0;
