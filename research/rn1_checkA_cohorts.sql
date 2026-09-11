-- ============================================================================
-- CHECK A, REVISED: condition cohorts that no threshold can manufacture
-- (2026-09-11, read-only.)
--
-- WHAT CHANGED AND WHY. My first attempt at a disjoint cohort rule used
-- "a majority of this condition's dM shares sit on probe-covered fills". That
-- is disjoint, but the 50% cut is arbitrary: a condition at 49% and one at 51%
-- are economically indistinguishable and would be reported on opposite sides
-- of a selection comparison. The threshold could therefore create a difference
-- that is not in the data. It is demoted to a sensitivity analysis.
--
-- THE PRIMARY RULE, over dM-GENERATING FILLS ONLY -- fills that actually
-- create matched inventory, not every fill in the condition:
--
--   COVERED_ONLY   every dM-generating fill has row_has_usable_probe = TRUE
--   MISSING_ONLY   no dM-generating fill has one
--   MIXED          at least one of each
--
-- Every condition appears exactly once, no threshold is involved, and MIXED is
-- reported as itself rather than forced onto one side.
--
-- THE PROBE FLAG IS PER ROW. It is EXISTS(probe WHERE trade_id = this fill's
-- id), tied to the exact fill that generated the dM. An envelope-level
-- bool_or would let a dM event inherit a book snapshot taken for a DIFFERENT
-- fill of the same transaction, which is the lookahead this whole line of work
-- exists to avoid; that defect was already found and removed once.
--
-- AND THEN THE MONOTONICITY TEST, which is the question behind the cohorts:
-- coverage_fraction_dM = covered_dM / total_dM per condition, binned, with
-- matched ROI and median pair edge per bin. If probeability is genuinely
-- associated with better RN1 economics that should show as a trend across the
-- bins, not merely as a gap between two cohorts a rule carved out.
--
-- EVERY FIGURE IS ONE ROW PER CONDITION. The statistic being replaced attached
-- a condition-level pair edge to every fill row and weighted by that fill's dM
-- notional, so a condition with 40 fills stated its edge 40 times, and a
-- condition with some fills probed and some not contributed to BOTH cohorts.
-- The -1.77% / -2.10% figures from that statistic are withdrawn.
--
-- Read-only: three SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. PRIMARY COHORTS: all / none / some dM-generating fills probed =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), rowprobe AS (
  SELECT b.id, EXISTS (SELECT 1 FROM copy_probes p
                        WHERE p.trade_id = b.id AND p.book_ok
                          AND p.best_ask IS NOT NULL) AS covered
    FROM base b
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, r.covered FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
    JOIN rowprobe r ON r.id = b.id
), rn AS (
  SELECT condition_id, covered,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn,
         ts, tx_hash, asset
    FROM canon
), dm AS (
  SELECT condition_id, covered,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM rn WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), coh AS (
  -- COUNTED OVER dM-GENERATING FILLS ONLY. A fill that creates no matched
  -- inventory says nothing about whether the completion was observable.
  SELECT condition_id,
         count(*) FILTER (WHERE d_m > 0.000001) AS dm_fills,
         count(*) FILTER (WHERE d_m > 0.000001 AND covered) AS dm_fills_covered,
         sum(d_m) AS dm_total,
         sum(d_m) FILTER (WHERE covered) AS dm_covered
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
              ELSE                                      'C MIXED' END AS cohort
    FROM cond c JOIN coh k ON k.condition_id = c.condition_id
   WHERE c.qy > 0 AND c.qn > 0
)
SELECT cohort, count(*) AS condition_count,
       round(sum(mm)::numeric, 0) AS sum_M,
       round(sum(matched_cost)::numeric, 0) AS sum_matched_cost,
       round(sum(matched_pnl)::numeric, 0) AS sum_matched_pnl,
       round((sum(matched_pnl) / NULLIF(sum(matched_cost), 0))::numeric, 5)
         AS MATCHED_ROI,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_cost)::numeric, 5)
         AS median_pair_cost,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_edge)::numeric, 5)
         AS MEDIAN_PAIR_EDGE
  FROM m GROUP BY 1 ORDER BY 1;


\echo '== 2. THE MONOTONICITY TEST: economics against coverage_fraction_dM =='
-- The cohorts answer "all / none / some". This answers the question behind
-- them: does economics move WITH coverage, or is any gap an artifact of where
-- a rule drew its line? A real association should show as a trend across bins.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), rowprobe AS (
  SELECT b.id, EXISTS (SELECT 1 FROM copy_probes p
                        WHERE p.trade_id = b.id AND p.book_ok
                          AND p.best_ask IS NOT NULL) AS covered
    FROM base b
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, r.covered FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
    JOIN rowprobe r ON r.id = b.id
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
  SELECT condition_id, sum(d_m) AS dm_total,
         sum(d_m) FILTER (WHERE covered) AS dm_covered
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
         COALESCE(k.dm_covered, 0) / NULLIF(k.dm_total, 0) AS cov_frac
    FROM cond c JOIN coh k ON k.condition_id = c.condition_id
   WHERE c.qy > 0 AND c.qn > 0 AND k.dm_total > 0
)
SELECT CASE WHEN cov_frac <= 0.0      THEN '1 0%'
            WHEN cov_frac <= 0.25     THEN '2 0-25%'
            WHEN cov_frac <= 0.50     THEN '3 25-50%'
            WHEN cov_frac <= 0.75     THEN '4 50-75%'
            WHEN cov_frac <  1.0      THEN '5 75-<100%'
            ELSE                           '6 100%' END AS coverage_fraction_dM_bin,
       count(*) AS condition_count,
       round(sum(mm)::numeric, 0) AS sum_M,
       round(sum(matched_cost)::numeric, 0) AS sum_matched_cost,
       round(sum(matched_pnl)::numeric, 0) AS sum_matched_pnl,
       round((sum(matched_pnl) / NULLIF(sum(matched_cost), 0))::numeric, 5)
         AS MATCHED_ROI,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_edge)::numeric, 5)
         AS MEDIAN_PAIR_EDGE,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_cost)::numeric, 5)
         AS median_pair_cost
  FROM m GROUP BY 1 ORDER BY 1;


\echo '== 3. SENSITIVITIES ONLY: the majority rule, and the all-fills strict rule =='
-- Reported so the primary cannot be accused of being the one rule that gives
-- the answer. Neither of these is the headline. The majority rule carries the
-- arbitrary 50% cut; the all-fills strict rule counts fills that generate no
-- matched inventory and so answers a slightly different question.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), rowprobe AS (
  SELECT b.id, EXISTS (SELECT 1 FROM copy_probes p
                        WHERE p.trade_id = b.id AND p.book_ok
                          AND p.best_ask IS NOT NULL) AS covered
    FROM base b
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, r.covered FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
    JOIN rowprobe r ON r.id = b.id
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
  SELECT condition_id, sum(d_m) AS dm_total,
         sum(d_m) FILTER (WHERE covered) AS dm_covered,
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
  SELECT LEAST(c.qy, c.qn) AS mm, (c.vy + c.vn) AS pair_cost,
         (1.0 - (c.vy + c.vn)) AS pair_edge,
         LEAST(c.qy, c.qn) * (c.vy + c.vn) AS matched_cost,
         LEAST(c.qy, c.qn) * (1.0 - (c.vy + c.vn)) AS matched_pnl,
         CASE WHEN k.dm_total > 0 AND COALESCE(k.dm_covered, 0) / k.dm_total >= 0.5
                THEN 'S1 MAJORITY_OF_DM covered' ELSE 'S1 MAJORITY_OF_DM missing' END AS s1,
         CASE WHEN k.all_fills_covered THEN 'S2 all-fills strict: covered'
              WHEN NOT k.any_fill_covered THEN 'S2 all-fills strict: missing'
              ELSE 'S2 all-fills strict: mixed' END AS s2
    FROM cond c JOIN coh k ON k.condition_id = c.condition_id
   WHERE c.qy > 0 AND c.qn > 0
), u AS (
  SELECT s1 AS rule, * FROM m
  UNION ALL SELECT s2, * FROM m
)
SELECT rule, count(*) AS condition_count,
       round(sum(mm)::numeric, 0) AS sum_M,
       round(sum(matched_cost)::numeric, 0) AS sum_matched_cost,
       round(sum(matched_pnl)::numeric, 0) AS sum_matched_pnl,
       round((sum(matched_pnl) / NULLIF(sum(matched_cost), 0))::numeric, 5) AS MATCHED_ROI,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_edge)::numeric, 5)
         AS MEDIAN_PAIR_EDGE
  FROM u GROUP BY 1 ORDER BY 1;
