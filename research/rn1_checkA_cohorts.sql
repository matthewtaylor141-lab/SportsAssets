-- ============================================================================
-- CHECK A, REVISED: condition cohorts that no threshold can manufacture
-- (2026-09-11, read-only.)
--
-- WHY THE FIRST ATTEMPT TIMED OUT, and the fix. Run 33 died on statement 1 at
-- the 600 s statement timeout, so it produced NOTHING. The cause is a schema
-- gap, not query size: copy_probes carries NO INDEX ON trade_id. Migration 005
-- declares `trade_id BIGINT REFERENCES trades (id)` and PostgreSQL does NOT
-- create an index for a foreign key, and the only indexes ever added are
-- (whale_id, probe_at DESC) and (probe_at). So the per-row
--     EXISTS (SELECT 1 FROM copy_probes p WHERE p.trade_id = b.id ...)
-- was a SEQUENTIAL SCAN OF copy_probes FOR EVERY ONE of ~350k canonical fills.
-- That is also the likeliest cause of the earlier exit=124 on the envelope
-- file. It is worth fixing in the schema, but this file must not write, so the
-- query is restructured instead: the probed trade_ids are collected ONCE into
-- a set and hash-joined, turning 350k scans into one.
--
-- AND THE WHOLE FILE IS NOW ONE STATEMENT. The previous version repeated the
-- same nine-CTE chain three times -- once per statement -- so the expensive
-- part was computed three times over. Cohorts, coverage bins and sensitivities
-- are all aggregations over the SAME per-condition table, so they are produced
-- from one pass and stacked with a label column.
--
-- WHAT CHANGED IN THE METHOD. My first disjoint cohort rule was "a majority of
-- this condition's dM shares sit on probe-covered fills". That is disjoint, but
-- the 50% cut is arbitrary: conditions at 49% and 51% are economically
-- indistinguishable and would land on opposite sides of a selection
-- comparison, so the rule itself could create the difference. It is demoted to
-- a sensitivity.
--
-- THE PRIMARY RULE, over dM-GENERATING FILLS ONLY -- fills that actually create
-- matched inventory, not every fill in the condition:
--   COVERED_ONLY   every dM-generating fill has row_has_usable_probe = TRUE
--   MISSING_ONLY   no dM-generating fill has one
--   MIXED          at least one of each
-- Every condition appears exactly once, no threshold is involved, and MIXED is
-- reported as itself rather than forced onto one side.
--
-- THE PROBE FLAG IS PER ROW, tied to the exact fill that generated the dM. An
-- envelope-level bool_or would let a dM event inherit a book snapshot taken for
-- a DIFFERENT fill of the same transaction, which is the lookahead this work
-- exists to avoid; that defect was found and removed once already.
--
-- THE MONOTONICITY TEST is the question behind the cohorts:
-- coverage_fraction_dM = covered_dM / total_dM per condition, binned, with
-- matched ROI and median pair edge per bin. A real association shows as a trend
-- across bins, not merely as a gap between two cohorts a rule carved out.
--
-- EVERY FIGURE IS ONE ROW PER CONDITION. The statistic being replaced attached
-- a condition-level pair edge to every fill row and weighted by that fill's dM
-- notional, so a condition with 40 fills stated its edge 40 times and a
-- condition with some fills probed and some not contributed to BOTH cohorts.
-- The -1.77% / -2.10% figures from that statistic are withdrawn.
--
-- Read-only: one SELECT. Nothing here writes.
-- ============================================================================


\echo '== COHORTS, COVERAGE BINS AND SENSITIVITIES, one row per condition =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), probed AS (
  -- ONE pass over copy_probes, not one per fill. There is no index on
  -- trade_id, so a correlated EXISTS scans the whole table per row.
  SELECT DISTINCT p.trade_id
    FROM copy_probes p
   WHERE p.book_ok AND p.best_ask IS NOT NULL AND p.trade_id IS NOT NULL
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, (pr.trade_id IS NOT NULL) AS covered
    FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
    LEFT JOIN probed pr ON pr.trade_id = b.id
), rn AS (
  SELECT condition_id, covered, ts, tx_hash, asset,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM canon
), dm AS (
  SELECT condition_id, covered,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM rn WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), coh AS (
  -- dM-GENERATING FILLS ONLY for the primary rule. A fill that creates no
  -- matched inventory says nothing about whether a completion was observable.
  SELECT condition_id,
         count(*) FILTER (WHERE d_m > 0.000001) AS dm_fills,
         count(*) FILTER (WHERE d_m > 0.000001 AND covered) AS dm_fills_covered,
         sum(d_m) AS dm_total,
         COALESCE(sum(d_m) FILTER (WHERE covered), 0) AS dm_covered,
         bool_and(covered) AS all_fills_covered,
         bool_or(covered) AS any_fill_covered
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
), m AS (
  SELECT c.condition_id, LEAST(c.qy, c.qn) AS mm,
         (c.vy + c.vn) AS pair_cost, (1.0 - (c.vy + c.vn)) AS pair_edge,
         LEAST(c.qy, c.qn) * (c.vy + c.vn) AS matched_cost,
         LEAST(c.qy, c.qn) * (1.0 - (c.vy + c.vn)) AS matched_pnl,
         CASE WHEN k.dm_fills = 0 THEN 'D NO_dM_GENERATING_FILL (guard bucket)'
              WHEN k.dm_fills_covered = k.dm_fills THEN 'A COVERED_ONLY'
              WHEN k.dm_fills_covered = 0          THEN 'B MISSING_ONLY'
              ELSE                                      'C MIXED' END AS cohort,
         CASE WHEN k.dm_total IS NULL OR k.dm_total <= 0 THEN NULL
              ELSE k.dm_covered / k.dm_total END AS cov_frac,
         CASE WHEN k.dm_total > 0 AND k.dm_covered / k.dm_total >= 0.5
                THEN 'covered' ELSE 'missing' END AS s1,
         CASE WHEN k.all_fills_covered THEN 'covered'
              WHEN NOT k.any_fill_covered THEN 'missing'
              ELSE 'mixed' END AS s2
    FROM cond c JOIN coh k ON k.condition_id = c.condition_id
   WHERE c.qy > 0 AND c.qn > 0
), lab AS (
  SELECT '1 PRIMARY cohort: ' || cohort AS label, * FROM m
  UNION ALL
  SELECT '2 coverage_fraction_dM bin: ' ||
         CASE WHEN cov_frac IS NULL   THEN 'n/a (no dM)'
              WHEN cov_frac <= 0.0    THEN '1 0%'
              WHEN cov_frac <= 0.25   THEN '2 0-25%'
              WHEN cov_frac <= 0.50   THEN '3 25-50%'
              WHEN cov_frac <= 0.75   THEN '4 50-75%'
              WHEN cov_frac <  1.0    THEN '5 75-<100%'
              ELSE                         '6 100%' END, * FROM m
  UNION ALL
  SELECT '3 SENSITIVITY majority-of-dM: ' || s1, * FROM m
  UNION ALL
  SELECT '4 SENSITIVITY all-fills strict: ' || s2, * FROM m
)
SELECT label, count(*) AS condition_count,
       round(sum(mm)::numeric, 0) AS sum_M,
       round(sum(matched_cost)::numeric, 0) AS sum_matched_cost,
       round(sum(matched_pnl)::numeric, 0) AS sum_matched_pnl,
       round((sum(matched_pnl) / NULLIF(sum(matched_cost), 0))::numeric, 5) AS MATCHED_ROI,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_cost)::numeric, 5)
         AS median_pair_cost,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_edge)::numeric, 5)
         AS MEDIAN_PAIR_EDGE
  FROM lab GROUP BY 1 ORDER BY 1;
