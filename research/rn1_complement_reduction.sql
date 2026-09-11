-- ============================================================================
-- B3 v2. COMPLEMENT-REDUCTION, CHRONOLOGICALLY -- CORRECTED AND EXTENDED
-- (2026-09-11, read-only.) Owner-specified.
--
-- ---------------------------------------------------------------------------
-- RUN 68's SELF-CHECK FAILED AND THE CAUSE IS FOUND. Run 68 reported
-- sum(path dM) = 47,634,973 against endpoint M = 56,805,911, with 11,826 of
-- 26,248 conditions violating the telescoping identity. Per instruction that
-- is a RECONSTRUCTION BUG, not economic evidence, and run 68's statements 1
-- and 2 are NOT interpreted anywhere until it is cleared.
--
-- THE BUG IS IN THE CHECK'S REFERENCE VALUE, NOT IN THE WALK.
-- PostgreSQL LEAST and GREATEST IGNORE NULL arguments; the result is NULL only
-- if every argument is NULL. Run 68's endpoint reference was
--
--     LEAST(sum(sh) FILTER (WHERE outcome_index = 0),
--           sum(sh) FILTER (WHERE outcome_index = 1))
--
-- and on a SINGLE-LEG condition one FILTER sum is NULL, so LEAST returned THE
-- OTHER LEG'S FULL QUANTITY where the true M is 0. The walk computes
-- LEAST over sum(CASE ... ELSE 0), which is never NULL, and therefore returns
-- 0 correctly. So the walk was right and the yardstick was wrong. Statement 3
-- below computes the reference BOTH WAYS and prints them side by side, so this
-- is demonstrated rather than asserted.
--
-- THE SAME PATTERN REACHES PUBLISHED FIGURES, and is corrected here rather
-- than left in the record. runs 62 and 63 build
--
--     LEAST(max(CASE WHEN outcome_index = 0 THEN COALESCE(qbuy,0) END),
--           max(CASE WHEN outcome_index = 1 THEN COALESCE(qbuy,0) END)) AS mq
--
-- where max(CASE ...) over a group with no matching rows is NULL. So mq equals
-- the one-sided quantity on single-leg conditions instead of 0. What that does
-- and does not touch:
--
--   AFFECTED -- matched_qty, i.e. sum(mq): run 62's 10,492,139 bridged /
--   46,313,773 unbridged and run 63's 51,113,813 all-eligible are OVERSTATED.
--
--   NOT AFFECTED -- matched cost, matched gross P&L and every matched ROI,
--   INCLUDING 1.383% / 0.804% / 1.556%. pair_cost is written
--   CASE WHEN qy > 0 AND qn > 0 THEN ... END, so it is NULL on a single-leg
--   condition; mq * pair_cost and mq * (1 - pair_cost) are then NULL, and
--   sum() skips NULL. Those sums never saw a single-leg condition.
--
-- Statement 4 reports matched quantity under both computations so the size of
-- the correction is measured, not estimated.
--
-- ---------------------------------------------------------------------------
-- MATERIALITY RENAME, per instruction. peak_directional - end_directional is
-- now MAX_DIRECTIONAL_DRAWDOWN_TO_ENDPOINT. It is an ENDPOINT-VERSUS-PEAK
-- statistic and NOT cumulative reduction activity: a condition that reduces
-- and rebuilds repeatedly can end at its own peak and score zero on it. The
-- cumulative measure is sum(directional_reduction_qty), reported beside it.
--
-- FILL-LEVEL QUANTITIES, per instruction. With D_before = |qY_before - qN_before|
-- and X the purchased token:
--
--     X is the currently SHORTER leg:
--         directional_reduction_qty = least(fill_size, D_before)
--         flip_excess_qty           = greatest(fill_size - D_before, 0)
--     otherwise:
--         directional_reduction_qty = 0
--         flip_excess_qty           = 0
--
-- THE IDENTITY TO TEST, under the flat-start BUY-only reconstruction:
--
--     sum(directional_reduction_qty) = sum(dM) = endpoint M
--
-- All three are separately computed in statement 3. Failure is a
-- reconstruction bug and is to be treated as such, never as economic evidence.
--
-- ---------------------------------------------------------------------------
-- THE QUESTION THIS RUN EXISTS TO ANSWER. Not whether matched quantity exists
-- -- that is settled. It is HOW RN1 TRAVERSES BETWEEN DIRECTIONAL AND PAIRED
-- STATES, and in particular how often exposure flips and rebuilds. Statement 5
-- therefore stratifies NEVER_REDUCED / REDUCED_NOT_FLIPPED / FLIPPED by
-- conditions, acquisition cost, matched cost, matched gross P&L and ROI,
-- ending residual cost, settlement-based residual P&L where available, and
-- bridge status; statement 6 adds sport. Together those say whether the 1.383%
-- matched mechanism comes from CONTINUOUSLY BUILDING PAIRED INVENTORY or from
-- REPEATEDLY ACCUMULATING DIRECTIONAL EXPOSURE AND THEN NEUTRALIZING IT.
--
-- ---------------------------------------------------------------------------
-- FLAT-START IS AN ASSUMPTION, NOT A FACT, and statement 7 stops treating it
-- as one. Two reconstructions are run from the SAME window walk, because the
-- pre-window seed is a per-condition constant that simply adds to the running
-- sums:
--
--     WINDOW_FLAT_START  starts at (0, 0)
--     HISTORY_SEEDED     starts at (seed_y, seed_n), the canonical BUY
--                        quantities retained BEFORE the window opens
--
-- A TRAP TO NAME BEFORE READING THE COMPARISON. Only 56 conditions have any
-- observed pre-window canonical fill. On the other ~26,192 the seed is
-- identically zero and the two reconstructions are THE SAME ARITHMETIC, so a
-- full-population comparison would show ~100% agreement AS AN ARTIFACT OF THE
-- COMPARISON BEING VACUOUS -- exactly the error that made the SELL-based clean
-- cohort look reassuring. Statement 7 therefore reports the comparison
-- RESTRICTED to conditions where the seed is non-zero, and states the vacuous
-- share separately instead of averaging it in.
--
-- And seeding from retained history does not establish the true starting
-- balance either: our retention has its own left edge. Every condition without
-- proven pre-window inventory stays INITIAL_STATE_UNKNOWN. FULLY_IN_WINDOW
-- remains retired (research/TERMINOLOGY.md section 5).
--
-- STRUCTURAL EXCLUSION, unchanged: trades.side is CHECK (side IN
-- ('BUY','SELL')), so a complete-set conversion or early redemption has no row
-- shape. TRUE_CASH_PATH = NOT IDENTIFIABLE FROM THIS LEDGER, even if
-- acquisition-time matched margin and end-state residual economics are.
--
-- Read-only: seven SELECTs.
-- ============================================================================


\echo '== 1. PER-FILL CLASSIFICATION and quantities (WINDOW_FLAT_START) =='
WITH base AS MATERIALIZED (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), buys AS MATERIALIZED (
  SELECT condition_id, id, ts, outcome_index, sh, px FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn
    FROM buys b
), st AS MATERIALIZED (
  SELECT w.*,
         w.cy - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS cy0,
         w.cn - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS cn0
    FROM walk w
), q AS MATERIALIZED (
  SELECT st.*,
         abs(st.cy0 - st.cn0) AS d_before,
         LEAST(st.cy, st.cn) - LEAST(st.cy0, st.cn0) AS dm,
         CASE WHEN st.outcome_index = 0 THEN st.cy0 ELSE st.cn0 END AS qx0,
         CASE WHEN st.outcome_index = 0 THEN st.cn0 ELSE st.cy0 END AS qo0
    FROM st
), f AS MATERIALIZED (
  SELECT q.*,
         CASE WHEN q.qx0 < q.qo0 THEN least(q.sh, q.d_before) ELSE 0 END
           AS directional_reduction_qty,
         CASE WHEN q.qx0 < q.qo0 THEN greatest(q.sh - q.d_before, 0) ELSE 0 END
           AS flip_excess_qty
    FROM q
)
SELECT CASE
         WHEN dm < -1e-9 THEN '0 NEGATIVE dM (assumption violated; investigate)'
         WHEN qx0 >= qo0
           THEN '3 DIRECTIONAL_ADD (X already the longer or equal leg)'
         WHEN flip_excess_qty > 1e-9
           THEN '2 DIRECTIONAL_FLIP (crossed through flat onto X)'
         ELSE '1 PAIR_FORMATION (whole fill reduces the other leg into matched)'
       END AS fill_class,
       count(*) AS fills,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_fills,
       count(DISTINCT condition_id) AS conditions_touched,
       round(sum(sh)::numeric, 0) AS shares,
       round(sum(sh * px)::numeric, 0) AS notional_usd,
       round((100.0 * sum(sh * px) / sum(sum(sh * px)) OVER ())::numeric, 3)
         AS pct_notional,
       round(sum(directional_reduction_qty)::numeric, 0) AS directional_reduction_qty,
       round(sum(flip_excess_qty)::numeric, 0) AS flip_excess_qty,
       round(sum(dm)::numeric, 0) AS dm_shares,
       round(sum(directional_reduction_qty * px)::numeric, 0) AS reduction_usd,
       round(sum(flip_excess_qty * px)::numeric, 0) AS flip_excess_usd
  FROM f GROUP BY 1 ORDER BY 1;


\echo '== 2. PER-CONDITION PATH SUMMARY by class (WINDOW_FLAT_START) =='
WITH base AS MATERIALIZED (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), buys AS MATERIALIZED (
  SELECT condition_id, id, ts, outcome_index, sh, px FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         count(*)     OVER (PARTITION BY b.condition_id) AS nf
    FROM buys b
), st AS MATERIALIZED (
  SELECT w.*,
         w.cy - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS cy0,
         w.cn - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS cn0,
         abs(w.cy - w.cn) AS d_after,
         sign(w.cy - w.cn) AS sgn_after
    FROM walk w
), f AS MATERIALIZED (
  SELECT st.*,
         abs(st.cy0 - st.cn0) AS d_before,
         sign(st.cy0 - st.cn0) AS sgn_before,
         CASE WHEN (CASE WHEN st.outcome_index = 0 THEN st.cy0 ELSE st.cn0 END)
                 < (CASE WHEN st.outcome_index = 0 THEN st.cn0 ELSE st.cy0 END)
              THEN least(st.sh, abs(st.cy0 - st.cn0)) ELSE 0 END AS red_qty,
         CASE WHEN (CASE WHEN st.outcome_index = 0 THEN st.cy0 ELSE st.cn0 END)
                 < (CASE WHEN st.outcome_index = 0 THEN st.cn0 ELSE st.cy0 END)
              THEN greatest(st.sh - abs(st.cy0 - st.cn0), 0) ELSE 0 END AS flip_qty
    FROM st
), agg AS MATERIALIZED (
  SELECT condition_id,
         max(nf) AS n_fills,
         sum(sh * px) AS acq_cost,
         count(*) FILTER (WHERE red_qty > 1e-9) AS n_reduction_fills,
         sum(red_qty) AS sum_reduction_qty,
         count(*) FILTER (WHERE flip_qty > 1e-9) AS n_flips,
         sum(flip_qty) AS sum_flip_excess_qty,
         count(*) FILTER (WHERE d_before <= 1e-9 AND d_after > 1e-9 AND rn > 1)
           AS n_rebuild_episodes,
         max(d_after) AS peak_directional,
         max(d_after) FILTER (WHERE rn = nf) AS end_directional,
         count(*) FILTER (WHERE sgn_before <> 0 AND sgn_after <> 0
                            AND sgn_before <> sgn_after) AS n_side_changes
    FROM f GROUP BY 1
)
SELECT CASE
         WHEN n_side_changes > 0 THEN '3 FLIPPED (the long side changed at least once)'
         WHEN peak_directional - end_directional > 1e-9
           THEN '2 REDUCED_NOT_FLIPPED (end below peak, side never changed)'
         ELSE '1 NEVER_REDUCED (directional exposure only ever grew)'
       END AS path_class,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round((100.0 * sum(acq_cost) / sum(sum(acq_cost)) OVER ())::numeric, 3)
         AS pct_acq_cost,
       sum(n_reduction_fills) AS n_reduction_fills,
       round(sum(sum_reduction_qty)::numeric, 0) AS sum_directional_reduction_qty,
       sum(n_flips) AS n_flip_fills,
       round(sum(sum_flip_excess_qty)::numeric, 0) AS sum_flip_excess_qty,
       sum(n_rebuild_episodes) AS n_rebuild_episodes,
       count(*) FILTER (WHERE n_rebuild_episodes > 0) AS conditions_with_a_rebuild,
       round(sum(peak_directional)::numeric, 0) AS peak_directional_qty,
       round(sum(end_directional)::numeric, 0) AS ending_directional_qty,
       round(sum(peak_directional - end_directional)::numeric, 0)
         AS max_directional_drawdown_to_endpoint,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY n_fills)::numeric, 1) AS p50_fills,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY n_fills)::numeric, 1) AS p90_fills
  FROM agg GROUP BY 1 ORDER BY 1;


\echo '== 3. THE TRIPLE IDENTITY, and the run-68 reference bug demonstrated =='
WITH base AS MATERIALIZED (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), buys AS MATERIALIZED (
  SELECT condition_id, id, ts, outcome_index, sh, px FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn
    FROM buys b
), path AS MATERIALIZED (
  SELECT w.condition_id,
         sum(LEAST(w.cy, w.cn)
             - LEAST(w.cy - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END,
                     w.cn - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END)) AS sum_dm,
         sum(CASE WHEN (CASE WHEN w.outcome_index = 0
                             THEN w.cy - w.sh ELSE w.cn - w.sh END)
                    < (CASE WHEN w.outcome_index = 0 THEN w.cn ELSE w.cy END)
                 THEN least(w.sh,
                        abs((w.cy - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END)
                          - (w.cn - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END)))
                 ELSE 0 END) AS sum_red
    FROM walk w GROUP BY 1
), endp AS MATERIALIZED (
  SELECT condition_id,
         LEAST(sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END),
               sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)) AS m_correct,
         LEAST(sum(sh) FILTER (WHERE outcome_index = 0),
               sum(sh) FILTER (WHERE outcome_index = 1)) AS m_run68_buggy,
         count(DISTINCT outcome_index) AS n_legs
    FROM buys GROUP BY 1
)
SELECT count(*) AS conditions,
       count(*) FILTER (WHERE e.n_legs = 1) AS single_leg_conditions,
       round(sum(p.sum_red)::numeric, 3) AS sum_directional_reduction_qty,
       round(sum(p.sum_dm)::numeric, 3) AS sum_path_dm,
       round(sum(e.m_correct)::numeric, 3) AS endpoint_m_correct,
       round(sum(COALESCE(e.m_run68_buggy, 0))::numeric, 3) AS endpoint_m_run68_reference,
       round(sum(COALESCE(e.m_run68_buggy, 0) - e.m_correct)::numeric, 3)
         AS overstatement_from_least_ignoring_null,
       round(sum(p.sum_red - p.sum_dm)::numeric, 6) AS reduction_minus_dm,
       round(sum(p.sum_dm - e.m_correct)::numeric, 6) AS dm_minus_endpoint,
       round(max(abs(p.sum_dm - e.m_correct))::numeric, 9) AS max_abs_per_condition_gap,
       count(*) FILTER (WHERE abs(p.sum_dm - e.m_correct) > 1e-6)
         AS conditions_failing_identity,
       count(*) FILTER (WHERE abs(p.sum_red - p.sum_dm) > 1e-6)
         AS conditions_where_reduction_ne_dm
  FROM path p JOIN endp e ON e.condition_id = p.condition_id;


\echo '== 4. MATCHED QUANTITY: the published figure against the corrected one =='
WITH base AS MATERIALIZED (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), leg AS MATERIALIZED (
  SELECT condition_id, outcome_index,
         sum(sh)      FILTER (WHERE side = 'BUY') AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY') AS cbuy
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1, 2
), cond AS MATERIALIZED (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         LEAST(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END),
               max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END))
           AS mq_as_published,
         LEAST(COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END), 0),
               COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END), 0))
           AS mq_corrected,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END), 0) AS qy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END), 0) AS qn,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END), 0) AS cy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END), 0) AS cn
    FROM leg l GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2 AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0) AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), br1 AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM (
    SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
  ) u WHERE condition_id IS NOT NULL
), e AS MATERIALIZED (
  SELECT c.*, (br1.condition_id IS NOT NULL) AS bridged,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost
    FROM cond c
    JOIN tok t ON t.condition_id = c.condition_id
    LEFT JOIN br1 ON br1.condition_id = c.condition_id
)
SELECT CASE WHEN bridged THEN '1 BRIDGED' ELSE '2 UNBRIDGED' END AS grp,
       count(*) AS conditions,
       count(*) FILTER (WHERE qy = 0 OR qn = 0) AS single_leg_conditions,
       round(sum(mq_as_published)::numeric, 0) AS matched_qty_as_published,
       round(sum(mq_corrected)::numeric, 0) AS matched_qty_corrected,
       round(sum(mq_as_published - mq_corrected)::numeric, 0) AS overstatement_qty,
       round(sum(mq_corrected * pair_cost)::numeric, 0) AS matched_cost,
       round(sum(mq_corrected * (1.0 - pair_cost))::numeric, 0) AS matched_gross_pnl,
       round((100.0 * sum(mq_corrected * (1.0 - pair_cost))
              / NULLIF(sum(mq_corrected * pair_cost), 0))::numeric, 3) AS matched_roi_pct
  FROM e GROUP BY 1
UNION ALL
SELECT '3 ALL ELIGIBLE', count(*), count(*) FILTER (WHERE qy = 0 OR qn = 0),
       round(sum(mq_as_published)::numeric, 0),
       round(sum(mq_corrected)::numeric, 0),
       round(sum(mq_as_published - mq_corrected)::numeric, 0),
       round(sum(mq_corrected * pair_cost)::numeric, 0),
       round(sum(mq_corrected * (1.0 - pair_cost))::numeric, 0),
       round((100.0 * sum(mq_corrected * (1.0 - pair_cost))
              / NULLIF(sum(mq_corrected * pair_cost), 0))::numeric, 3)
  FROM e
 ORDER BY 1;


\echo '== 5. PATH CLASS stratified by the economics we already care about =='
WITH base AS MATERIALIZED (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), buys AS MATERIALIZED (
  SELECT condition_id, id, ts, outcome_index, sh, px FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         count(*)     OVER (PARTITION BY b.condition_id) AS nf
    FROM buys b
), pc AS MATERIALIZED (
  SELECT condition_id,
         CASE
           WHEN count(*) FILTER (WHERE sign(cy - CASE WHEN outcome_index = 0 THEN sh ELSE 0 END
                                            - (cn - CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)) <> 0
                              AND sign(cy - cn) <> 0
                              AND sign(cy - CASE WHEN outcome_index = 0 THEN sh ELSE 0 END
                                       - (cn - CASE WHEN outcome_index = 1 THEN sh ELSE 0 END))
                                  <> sign(cy - cn)) > 0
             THEN '3 FLIPPED (the long side changed at least once)'
           WHEN max(abs(cy - cn)) - max(abs(cy - cn)) FILTER (WHERE rn = nf) > 1e-9
             THEN '2 REDUCED_NOT_FLIPPED (end below peak, side never changed)'
           ELSE '1 NEVER_REDUCED (directional exposure only ever grew)'
         END AS path_class
    FROM walk GROUP BY 1
), lg AS MATERIALIZED (
  SELECT condition_id, outcome_index, sum(sh) AS qbuy, sum(sh * px) AS cbuy
    FROM buys GROUP BY 1, 2
), cond AS MATERIALIZED (
  SELECT l.condition_id,
         sum(l.cbuy) AS acq_cost,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN l.qbuy END), 0) AS qy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN l.qbuy END), 0) AS qn,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN l.cbuy END), 0) AS cy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN l.cbuy END), 0) AS cn
    FROM lg l GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2 AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0) AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), br1 AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM (
    SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
  ) u WHERE condition_id IS NOT NULL
), econ AS MATERIALIZED (
  SELECT c.condition_id, pc.path_class, c.acq_cost,
         (br1.condition_id IS NOT NULL) AS bridged,
         LEAST(c.qy, c.qn) AS mq,
         c.qy - LEAST(c.qy, c.qn) AS ry,
         c.qn - LEAST(c.qy, c.qn) AS rn_,
         CASE WHEN c.qy > 0 THEN c.cy / c.qy END AS vy,
         CASE WHEN c.qn > 0 THEN c.cn / c.qn END AS vn,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost,
         CASE WHEN mk.resolved AND mk.resolved_prices IS NOT NULL
              THEN (mk.resolved_prices->>0)::float8 END AS py,
         CASE WHEN mk.resolved AND mk.resolved_prices IS NOT NULL
              THEN (mk.resolved_prices->>1)::float8 END AS pn
    FROM cond c
    JOIN pc ON pc.condition_id = c.condition_id
    JOIN tok t ON t.condition_id = c.condition_id
    LEFT JOIN br1 ON br1.condition_id = c.condition_id
    LEFT JOIN markets mk ON mk.condition_id = c.condition_id
)
SELECT path_class,
       count(*) AS conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(mq * pair_cost)::numeric, 0) AS matched_cost,
       round(sum(mq * (1.0 - pair_cost))::numeric, 0) AS matched_gross_pnl,
       round((100.0 * sum(mq * (1.0 - pair_cost))
              / NULLIF(sum(mq * pair_cost), 0))::numeric, 3) AS matched_gross_roi_pct,
       round(sum(COALESCE(ry * vy, 0) + COALESCE(rn_ * vn, 0))::numeric, 0)
         AS ending_residual_cost,
       count(*) FILTER (WHERE py IS NOT NULL) AS conditions_settled,
       round(sum((py * ry + pn * rn_) - (ry * vy + rn_ * vn))
               FILTER (WHERE py IS NOT NULL)::numeric, 0) AS residual_pnl_settled,
       round((100.0 * sum((py * ry + pn * rn_) - (ry * vy + rn_ * vn))
                       FILTER (WHERE py IS NOT NULL)
              / NULLIF(sum(ry * vy + rn_ * vn) FILTER (WHERE py IS NOT NULL), 0)
             )::numeric, 3) AS residual_roi_settled_pct,
       round((100.0 * count(*) FILTER (WHERE bridged) / count(*))::numeric, 2) AS pct_bridged
  FROM econ GROUP BY 1 ORDER BY 1;


\echo '== 6. PATH CLASS by sport =='
WITH base AS MATERIALIZED (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), buys AS MATERIALIZED (
  SELECT condition_id, id, ts, outcome_index, sh, px FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         count(*)     OVER (PARTITION BY b.condition_id) AS nf
    FROM buys b
), pc AS MATERIALIZED (
  SELECT condition_id,
         CASE
           WHEN count(*) FILTER (WHERE sign(cy - CASE WHEN outcome_index = 0 THEN sh ELSE 0 END
                                            - (cn - CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)) <> 0
                              AND sign(cy - cn) <> 0
                              AND sign(cy - CASE WHEN outcome_index = 0 THEN sh ELSE 0 END
                                       - (cn - CASE WHEN outcome_index = 1 THEN sh ELSE 0 END))
                                  <> sign(cy - cn)) > 0
             THEN '3 FLIPPED'
           WHEN max(abs(cy - cn)) - max(abs(cy - cn)) FILTER (WHERE rn = nf) > 1e-9
             THEN '2 REDUCED_NOT_FLIPPED'
           ELSE '1 NEVER_REDUCED'
         END AS path_class,
         sum(sh * px) AS acq_cost
    FROM walk GROUP BY 1
)
SELECT COALESCE(mk.sport, 'NO MARKETS ROW') AS sport,
       count(*) AS conditions,
       round(sum(pc.acq_cost)::numeric, 0) AS acquisition_cost,
       count(*) FILTER (WHERE pc.path_class = '1 NEVER_REDUCED') AS n_never_reduced,
       count(*) FILTER (WHERE pc.path_class = '2 REDUCED_NOT_FLIPPED') AS n_reduced,
       count(*) FILTER (WHERE pc.path_class = '3 FLIPPED') AS n_flipped,
       round((100.0 * sum(pc.acq_cost) FILTER (WHERE pc.path_class = '3 FLIPPED')
              / NULLIF(sum(pc.acq_cost), 0))::numeric, 2) AS pct_acq_cost_flipped
  FROM pc LEFT JOIN markets mk ON mk.condition_id = pc.condition_id
 GROUP BY 1 ORDER BY 3 DESC;


\echo '== 7. WINDOW_FLAT_START vs HISTORY_SEEDED, on the conditions where it can differ =='
WITH base AS MATERIALIZED (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), seed AS MATERIALIZED (
  SELECT condition_id,
         sum(CASE WHEN outcome_index = 0 AND side = 'BUY' THEN sh ELSE 0 END) AS sy,
         sum(CASE WHEN outcome_index = 1 AND side = 'BUY' THEN sh ELSE 0 END) AS sn
    FROM canon WHERE ts < timestamptz '2026-08-05 00:00Z' GROUP BY 1
), buys AS MATERIALIZED (
  SELECT condition_id, id, ts, outcome_index, sh, px FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         COALESCE(s.sy, 0) AS sy, COALESCE(s.sn, 0) AS sn,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         count(*)     OVER (PARTITION BY b.condition_id) AS nf
    FROM buys b LEFT JOIN seed s ON s.condition_id = b.condition_id
), two AS MATERIALIZED (
  SELECT w.condition_id, w.rn, w.nf, w.sh, w.px, w.sy, w.sn,
         w.cy AS fy, w.cn AS fn,
         w.cy - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS fy0,
         w.cn - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS fn0,
         w.sy + w.cy AS hy, w.sn + w.cn AS hn,
         w.sy + w.cy - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS hy0,
         w.sn + w.cn - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS hn0
    FROM walk w
), cls AS MATERIALIZED (
  SELECT condition_id,
         max(sy + sn) AS seed_qty,
         CASE WHEN count(*) FILTER (WHERE sign(fy0 - fn0) <> 0 AND sign(fy - fn) <> 0
                                      AND sign(fy0 - fn0) <> sign(fy - fn)) > 0
                THEN '3 FLIPPED'
              WHEN max(abs(fy - fn)) - max(abs(fy - fn)) FILTER (WHERE rn = nf) > 1e-9
                THEN '2 REDUCED_NOT_FLIPPED'
              ELSE '1 NEVER_REDUCED' END AS class_flat,
         CASE WHEN count(*) FILTER (WHERE sign(hy0 - hn0) <> 0 AND sign(hy - hn) <> 0
                                      AND sign(hy0 - hn0) <> sign(hy - hn)) > 0
                THEN '3 FLIPPED'
              WHEN max(abs(hy - hn)) - max(abs(hy - hn)) FILTER (WHERE rn = nf) > 1e-9
                THEN '2 REDUCED_NOT_FLIPPED'
              ELSE '1 NEVER_REDUCED' END AS class_seeded,
         sum(LEAST(fy, fn) - LEAST(fy0, fn0)) AS dm_flat,
         sum(LEAST(hy, hn) - LEAST(hy0, hn0)) AS dm_seeded,
         sum(sh * px) AS acq_cost
    FROM two GROUP BY 1
)
SELECT CASE WHEN seed_qty > 1e-9
            THEN '1 SEED NON-ZERO (PREWINDOW_INVENTORY_PROVEN; the comparison can differ)'
            ELSE '2 SEED ZERO (INITIAL_STATE_UNKNOWN; the two runs are identical arithmetic)'
       END AS seeding_stratum,
       count(*) AS conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       count(*) FILTER (WHERE class_flat <> class_seeded) AS path_class_changed,
       round((100.0 * count(*) FILTER (WHERE class_flat <> class_seeded)
              / count(*))::numeric, 3) AS pct_path_class_changed,
       round(sum(dm_flat)::numeric, 0) AS sum_dm_flat_start,
       round(sum(dm_seeded)::numeric, 0) AS sum_dm_history_seeded,
       round(sum(dm_seeded - dm_flat)::numeric, 0) AS dm_difference,
       round(max(abs(dm_seeded - dm_flat))::numeric, 3) AS max_abs_per_condition_dm_diff
  FROM cls GROUP BY 1 ORDER BY 1;
