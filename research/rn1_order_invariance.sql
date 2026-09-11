-- ============================================================================
-- ORDER-INVARIANCE OF THE FLIP CLASSIFICATION, PROVED RATHER THAN SAMPLED
-- (2026-09-11, read-only.) Owner-specified.
--
-- ---------------------------------------------------------------------------
-- TWO CORRECTIONS FIRST, BOTH TO MY OWN WORK.
--
-- 1 THE FLIP CLASSIFIER WAS WRONG AT EXACT FLAT. It tested
--       sgn_before <> 0 AND sgn_after <> 0 AND sgn_before <> sgn_after
--   so a path through exactly zero was never counted: + -> 0 -> - scored NO
--   flip and fell into REDUCED_NOT_FLIPPED. The corrected definition carries
--   the LAST NONZERO sign across flat states:
--       + -> 0 -> -   flip          + -> 0 -> +   no flip
--       - -> 0 -> +   flip          - -> 0 -> -   no flip
--   Equivalently, and this is the form used below:
--       FLIPPED  <=>  the path visits some D > 0 AND some D < 0,
--   with D = qY - qN. The two forms were checked to agree in
--   research/test_order_invariance.py section 1.
--
-- 2 MY PROPOSED ORDER-SENSITIVITY PROOF WAS WRONG. I claimed that a tied
--   block whose reachable interval [L, U] = [D0 - Ntot, D0 + Ytot] spans zero
--   is order-sensitive. It is not. The owner's counterexample settles it: if
--   D0 > 0 and D1 < 0 then EVERY ordering must cross, so the block spans zero
--   and FLIPPED is invariantly TRUE. Spanning zero makes a crossing REACHABLE,
--   not OPTIONAL.
--
-- ---------------------------------------------------------------------------
-- THE RULE ACTUALLY USED, and it is proved over ALL orderings, not sampled.
--
-- A tied block's TOTAL contribution to each leg is fixed, so the boundary
-- states between blocks are the same under every ordering. Within a block,
-- any interleaving keeps D inside [L, U], and both endpoints are attained --
-- by ALL_N_THEN_ALL_Y and ALL_Y_THEN_ALL_N respectively. Hence per condition:
--
--   guaranteed_pos  some FIXED boundary state > 0   (visited under every order)
--   guaranteed_neg  some FIXED boundary state < 0
--   possible_pos    guaranteed_pos OR some block U > 0
--   possible_neg    guaranteed_neg OR some block L < 0
--
--   PROVABLY_FLIPPED            guaranteed_pos AND guaranteed_neg
--   PROVABLY_NO_FLIP            NOT (possible_pos AND possible_neg)
--   ORDER_NOT_PROVEN_INVARIANT  otherwise
--
-- THE THIRD NAME IS DELIBERATE AND IS NOT "PROVEN_ORDER_SENSITIVE". Failing to
-- prove invariance is not proving sensitivity, and the rule is knowingly
-- conservative in one place: U and L of the SAME block are NOT jointly
-- achievable -- one ordering cannot both lead with Y and lead with N -- so a
-- condition whose only route to each sign runs through a single block is
-- reported as not-proven when it may in fact be invariant. Measured on
-- exhaustive enumeration: 1,056 synthetic conditions, 1,054 exact, 2
-- conservative, and ZERO cases where a proof was claimed that enumeration
-- contradicts. The approximation runs only in the safe direction.
--
-- VERIFICATION, before this file was written, not after:
--   research/test_order_invariance.py enumerates every permutation of small
--   blocks -- 940 nonzero-boundary blocks, 0 mismatches -- and every
--   permutation combination across multi-block conditions, 0 unsafe.
--
-- ENDPOINT QUANTITIES REMAIN USABLE REGARDLESS. Run 70 measured
-- dm_difference = 0.000000 and max_abs_dm_difference = 0.000000000 under a
-- reordered walk, and the endpoint M is algebraically order-invariant. THE
-- FREEZE APPLIES ONLY TO ORDER-DEPENDENT BEHAVIOURAL CLAIMS.
--
-- Read-only: three SELECTs.
-- ============================================================================


\echo '== 1. CROSS_LEG_TIMESTAMP_TIE_PRESENT: the population, not the lower bound =='
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
), grp AS MATERIALIZED (
  SELECT condition_id, ts, count(*) AS n,
         count(DISTINCT outcome_index) AS legs
    FROM buys GROUP BY 1, 2
), flag AS MATERIALIZED (
  SELECT condition_id,
         bool_or(n > 1 AND legs = 2) AS has_cross_leg_tie,
         count(*) FILTER (WHERE n > 1 AND legs = 2) AS n_cross_leg_blocks
    FROM grp GROUP BY 1
), cond AS MATERIALIZED (
  SELECT condition_id,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 0), 0) AS qy,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 1), 0) AS qn,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 0), 0) AS cy,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 1), 0) AS cn
    FROM buys GROUP BY 1
)
SELECT CASE WHEN f.has_cross_leg_tie
            THEN '1 CROSS_LEG_TIMESTAMP_TIE_PRESENT'
            ELSE '2 no cross-leg tie (path order fully determined)' END AS tie_population,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_conditions,
       round(sum(c.cy + c.cn)::numeric, 0) AS acquisition_cost,
       round((100.0 * sum(c.cy + c.cn) / sum(sum(c.cy + c.cn)) OVER ())::numeric, 3)
         AS pct_acq_cost,
       round(sum(COALESCE(LEAST(c.qy, c.qn)
                 * (CASE WHEN c.qy > 0 AND c.qn > 0
                         THEN c.cy / c.qy + c.cn / c.qn END), 0))::numeric, 0)
         AS matched_cost,
       round((100.0 * sum(COALESCE(LEAST(c.qy, c.qn)
                 * (CASE WHEN c.qy > 0 AND c.qn > 0
                         THEN c.cy / c.qy + c.cn / c.qn END), 0))
              / sum(sum(COALESCE(LEAST(c.qy, c.qn)
                 * (CASE WHEN c.qy > 0 AND c.qn > 0
                         THEN c.cy / c.qy + c.cn / c.qn END), 0))) OVER ())::numeric, 3)
         AS pct_matched_cost,
       sum(f.n_cross_leg_blocks) AS cross_leg_blocks
  FROM flag f JOIN cond c ON c.condition_id = f.condition_id
 GROUP BY 1 ORDER BY 1;


\echo '== 2. THE PROOF: provably no-flip / provably flipped / not proven invariant =='
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
), blk AS MATERIALIZED (
  -- one row per (condition, timestamp) block; totals are order-independent
  SELECT condition_id, ts,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) AS ytot,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) AS ntot,
         sum(sh * px) AS notional,
         count(*) AS n, count(DISTINCT outcome_index) AS legs
    FROM buys GROUP BY 1, 2
), st AS MATERIALIZED (
  SELECT b.*,
         -- D after this block, and D before it: both fixed under any ordering
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING) AS d_after,
         sum(b.ytot - b.ntot) OVER (PARTITION BY b.condition_id ORDER BY b.ts
                                    ROWS UNBOUNDED PRECEDING)
           - (b.ytot - b.ntot) AS d_before
    FROM blk b
), r AS MATERIALIZED (
  SELECT st.*,
         st.d_before + st.ytot AS u_reach,   -- attained by ALL_Y_THEN_ALL_N
         st.d_before - st.ntot AS l_reach    -- attained by ALL_N_THEN_ALL_Y
    FROM st
), agg AS MATERIALIZED (
  SELECT condition_id,
         bool_or(d_after >  1e-9) OR bool_or(d_before >  1e-9) AS guaranteed_pos,
         bool_or(d_after < -1e-9) OR bool_or(d_before < -1e-9) AS guaranteed_neg,
         bool_or(u_reach >  1e-9) AS reach_pos,
         bool_or(l_reach < -1e-9) AS reach_neg,
         bool_or(n > 1 AND legs = 2) AS has_cross_leg_tie,
         count(*) FILTER (WHERE n > 1 AND legs = 2
                            AND (abs(d_before) <= 1e-9 OR abs(d_after) <= 1e-9))
           AS zero_boundary_blocks,
         sum(notional) AS acq_cost
    FROM r GROUP BY 1
), lg AS MATERIALIZED (
  SELECT condition_id,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 0), 0) AS qy,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 1), 0) AS qn,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 0), 0) AS cy,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 1), 0) AS cn
    FROM buys GROUP BY 1
), k AS MATERIALIZED (
  SELECT a.*, l.qy, l.qn,
         COALESCE(LEAST(l.qy, l.qn)
                  * (CASE WHEN l.qy > 0 AND l.qn > 0
                          THEN l.cy / l.qy + l.cn / l.qn END), 0) AS matched_cost,
         (a.guaranteed_pos OR a.reach_pos) AS possible_pos,
         (a.guaranteed_neg OR a.reach_neg) AS possible_neg
    FROM agg a JOIN lg l ON l.condition_id = a.condition_id
)
SELECT CASE
         WHEN guaranteed_pos AND guaranteed_neg
           THEN '1 PROVABLY_FLIPPED (every admissible ordering flips)'
         WHEN NOT (possible_pos AND possible_neg)
           THEN '2 PROVABLY_NO_FLIP (no admissible ordering flips)'
         ELSE '3 ORDER_NOT_PROVEN_INVARIANT (a flip is reachable but not forced)'
       END AS order_class,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round((100.0 * sum(acq_cost) / sum(sum(acq_cost)) OVER ())::numeric, 3)
         AS pct_acq_cost,
       round(sum(matched_cost)::numeric, 0) AS matched_cost,
       round((100.0 * sum(matched_cost) / sum(sum(matched_cost)) OVER ())::numeric, 3)
         AS pct_matched_cost,
       count(*) FILTER (WHERE has_cross_leg_tie) AS with_cross_leg_tie,
       count(*) FILTER (WHERE zero_boundary_blocks > 0)
         AS with_a_zero_boundary_cross_leg_block,
       round(sum(acq_cost) FILTER (WHERE zero_boundary_blocks > 0)::numeric, 0)
         AS acq_cost_zero_boundary
  FROM k GROUP BY 1 ORDER BY 1;


\echo '== 3. THE CORRECTED FLIP CLASSIFIER vs the one used in run 69 =='
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
), w AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE -b.sh END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS d,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         count(*)     OVER (PARTITION BY b.condition_id) AS nf
    FROM buys b
), s AS MATERIALIZED (
  SELECT w.*,
         w.d - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE -w.sh END AS d0,
         sum(w.sh * w.px) OVER (PARTITION BY w.condition_id) AS acq_cost
    FROM w
), c AS MATERIALIZED (
  SELECT condition_id, max(acq_cost) AS acq_cost,
         -- run 69's classifier: adjacent nonzero signs only, so a path through
         -- exact flat was never counted as a flip
         count(*) FILTER (WHERE sign(d0) <> 0 AND sign(d) <> 0
                            AND sign(d0) <> sign(d)) > 0 AS flipped_run69,
         -- corrected: FLIPPED <=> the path visits both signs, which is exactly
         -- carrying the last nonzero sign across flat states
         (bool_or(d > 1e-9) AND bool_or(d < -1e-9)) AS flipped_corrected,
         max(abs(d)) AS peak_directional,
         max(abs(d)) FILTER (WHERE rn = nf) AS end_directional
    FROM s GROUP BY 1
)
SELECT count(*) AS conditions,
       count(*) FILTER (WHERE flipped_run69) AS flipped_under_run69_classifier,
       count(*) FILTER (WHERE flipped_corrected) AS flipped_under_corrected,
       count(*) FILTER (WHERE flipped_corrected AND NOT flipped_run69)
         AS newly_flipped_missed_by_run69,
       round(sum(acq_cost) FILTER (WHERE flipped_corrected AND NOT flipped_run69)::numeric, 0)
         AS acq_cost_newly_flipped,
       count(*) FILTER (WHERE flipped_run69 AND NOT flipped_corrected)
         AS lost_expected_0,
       round((100.0 * count(*) FILTER (WHERE flipped_corrected)
              / count(*))::numeric, 3) AS pct_flipped_corrected,
       round((100.0 * sum(acq_cost) FILTER (WHERE flipped_corrected)
              / NULLIF(sum(acq_cost), 0))::numeric, 3) AS pct_acq_cost_flipped_corrected
  FROM c;
