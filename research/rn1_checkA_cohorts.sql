-- ============================================================================
-- CHECK A: condition cohorts that no threshold can manufacture
-- (2026-09-11, read-only.)
--
-- WHY THIS TOOK FOUR ATTEMPTS, and what the cause actually was. Runs 33, 34 and
-- 35 all timed out (600 s, 1200 s, 1200 s) and produced nothing. I fixed three
-- things I had REASONED to -- a missing copy_probes(trade_id) index, a
-- single-leg prefilter, a doubled window pass -- and re-ran each time without
-- measuring. Run 36 finally measured, with counts and an EXPLAIN that plans
-- without running, and the real cause was none of them:
--
--   Nested Loop  (rows=1)
--     Join Filter: (canon.condition_id = canon_1.condition_id)
--     ->  HashAggregate (rows=1)        [the per-condition price aggregate]
--     ->  GroupAggregate (rows=1)       [the per-condition dM aggregate]
--           ->  WindowAgg -> Sort -> CTE Scan on canon
--
-- The planner estimates `canon` at ONE ROW. It cannot estimate the join
-- condition `b.feed = (CASE WHEN bool_or(...) THEN 'venue' ELSE 'cash' END)`,
-- so it guesses 1; the true figure is ~356,000. On that estimate it chose a
-- NESTED LOOP between the two aggregates over canon, with no Materialize. In a
-- nested loop the inner side is re-executed once per outer row, the inner side
-- here is a SORT AND WINDOW PASS OVER THE WHOLE 356k-ROW SET, and the outer
-- side produces ~14,346 conditions.
--
-- So the sort and window were running roughly fourteen thousand times. That is
-- the twenty minutes. It was a plan-shape problem, not a volume problem, and
-- the three earlier fixes were shaving percentages off a quantity that was
-- being multiplied by 14,000.
--
-- THE FIX IS STRUCTURAL. `cond` and `coh` were both GROUP BY condition_id over
-- the same rows; they were only separate because one of them needed the
-- window-derived dM. Computing dM inside the window pass and then doing ONE
-- GROUP BY that emits both sets removes the join entirely -- no nested loop, no
-- rescan -- and leaves `canon` with a single reference so it can be inlined.
--
-- WHAT RUN 36 ALSO SETTLED, measured rather than assumed:
--   * 385,201 base rows across both feeds, 26,154 conditions, 14,346 two-leg.
--     My "~350k" was right, so scale was never the problem.
--   * the two-leg prefilter removes only 7.6% of rows, so it is dropped here:
--     the final qy > 0 AND qn > 0 already excludes those conditions and the
--     prefilter was buying almost nothing for an extra aggregate and join.
--   * no pathological condition -- the largest is 675 rows, p50 is 4.
--   * copy_probes is 963,364 rows / 588 MB, and among usable probes trade_id
--     is already unique (955,396 rows, 955,396 distinct), so the DISTINCT is
--     belt-and-braces rather than a real dedup.
--
-- THE METHOD IS UNCHANGED. My first disjoint cohort rule was "a majority of
-- this condition's dM shares sit on probe-covered fills". That is disjoint, but
-- the 50% cut is arbitrary: conditions at 49% and 51% are economically
-- indistinguishable and would land on opposite sides of a selection
-- comparison, so the rule itself could create the difference. It is demoted to
-- a sensitivity.
--
-- PRIMARY, over dM-GENERATING FILLS ONLY -- fills that actually create matched
-- inventory, not every fill in the condition:
--   COVERED_ONLY   every dM-generating fill has a fill-specific usable probe
--   MISSING_ONLY   none does
--   MIXED          at least one of each
-- Every condition appears exactly once, no threshold is involved, and MIXED is
-- reported as itself rather than forced onto a side.
--
-- THE PROBE FLAG IS PER ROW, tied to the exact fill that generated the dM. An
-- envelope-level bool_or would let a dM event inherit a book snapshot taken for
-- a DIFFERENT fill of the same transaction -- the lookahead this work exists to
-- avoid, and a defect already found and removed once.
--
-- THE MONOTONICITY TEST is the question behind the cohorts:
-- coverage_fraction_dM = covered_dM / total_dM per condition, binned, with
-- matched ROI and median pair edge per bin. A real association shows as a trend
-- across bins, not merely as a gap between two cohorts a rule carved out.
-- condition_count is reported per bin so a swing on a handful of conditions is
-- visible as sampling noise rather than read as a trend.
--
-- EVERY FIGURE IS ONE ROW PER CONDITION. The statistic being replaced attached
-- a condition-level pair edge to every fill row and weighted by that fill's dM
-- notional, so a condition with 40 fills stated its edge 40 times and one with
-- some fills probed and some not contributed to BOTH cohorts. The -1.77% /
-- -2.10% figures from that statistic are withdrawn.
--
-- Read-only: one SELECT. Nothing here writes.
-- ============================================================================


\echo '== COHORTS, COVERAGE BINS AND SENSITIVITIES, one row per condition =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), probed AS (
  -- ONE pass over copy_probes. There is no index on trade_id, so a correlated
  -- EXISTS would scan all 588 MB per fill. Measured: trade_id is already unique
  -- among usable probes, so the DISTINCT costs a hash but removes nothing.
  SELECT DISTINCT p.trade_id
    FROM copy_probes p
   WHERE p.book_ok AND p.best_ask IS NOT NULL AND p.trade_id IS NOT NULL
), env AS (
  SELECT tx_hash, asset,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2
), canon AS (
  -- REFERENCED ONCE, deliberately. Two references made PostgreSQL materialise
  -- it and then join two aggregates over it, which is what produced the
  -- nested-loop rescan.
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px,
         (pr.trade_id IS NOT NULL) AS covered
    FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.canon_feed = b.feed
    LEFT JOIN probed pr ON pr.trade_id = b.id
), win AS (
  -- ONE window level. The pre-fill running totals are the running totals minus
  -- this row's own contribution, so no lag() and no second sort is needed. The
  -- sort key is (condition_id, ts, id): `id` is a unique bigint and an equally
  -- deterministic tiebreak at equal timestamps, and dM is invariant to which
  -- deterministic tiebreak is used.
  SELECT condition_id, covered, outcome_index, sh, px,
         CASE WHEN outcome_index = 0 THEN sh ELSE 0 END AS y_now,
         CASE WHEN outcome_index = 1 THEN sh ELSE 0 END AS n_now,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) OVER w AS cy,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) OVER w AS cn
    FROM canon
  WINDOW w AS (PARTITION BY condition_id ORDER BY ts, id
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), d AS (
  SELECT condition_id, covered, outcome_index, sh, px,
         GREATEST(LEAST(cy, cn) - LEAST(cy - y_now, cn - n_now), 0) AS d_m
    FROM win
), percond AS (
  -- THE FIX: ONE GROUP BY emitting BOTH the dM aggregates and the price
  -- aggregates. Previously these were two separate GROUP BYs joined on
  -- condition_id, and that join is what the planner turned into a nested loop
  -- that re-ran the whole window pass per condition.
  SELECT condition_id,
         count(*) FILTER (WHERE d_m > 0.000001) AS dm_fills,
         count(*) FILTER (WHERE d_m > 0.000001 AND covered) AS dm_fills_covered,
         sum(d_m) AS dm_total,
         COALESCE(sum(d_m) FILTER (WHERE covered), 0) AS dm_covered,
         bool_and(covered) AS all_fills_covered,
         bool_or(covered) AS any_fill_covered,
         sum(sh) FILTER (WHERE outcome_index = 0) AS qy,
         sum(sh) FILTER (WHERE outcome_index = 1) AS qn,
         sum(sh * px) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS vy,
         sum(sh * px) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS vn
    FROM d GROUP BY 1
), m AS (
  SELECT condition_id, LEAST(qy, qn) AS mm,
         (vy + vn) AS pair_cost, (1.0 - (vy + vn)) AS pair_edge,
         LEAST(qy, qn) * (vy + vn) AS matched_cost,
         LEAST(qy, qn) * (1.0 - (vy + vn)) AS matched_pnl,
         CASE WHEN dm_fills = 0 THEN 'D NO_dM_GENERATING_FILL (guard bucket)'
              WHEN dm_fills_covered = dm_fills THEN 'A COVERED_ONLY'
              WHEN dm_fills_covered = 0        THEN 'B MISSING_ONLY'
              ELSE                                  'C MIXED' END AS cohort,
         CASE WHEN dm_total IS NULL OR dm_total <= 0 THEN NULL
              ELSE dm_covered / dm_total END AS cov_frac,
         CASE WHEN dm_total > 0 AND dm_covered / dm_total >= 0.5
                THEN 'covered' ELSE 'missing' END AS s1,
         CASE WHEN all_fills_covered THEN 'covered'
              WHEN NOT any_fill_covered THEN 'missing'
              ELSE 'mixed' END AS s2
    FROM percond
   WHERE qy > 0 AND qn > 0
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
