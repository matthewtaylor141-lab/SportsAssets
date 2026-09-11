-- ============================================================================
-- B3. COMPLEMENT-REDUCTION CONTAMINATION, RECONSTRUCTED CHRONOLOGICALLY
-- (2026-09-11, read-only.) Owner-specified. Supersedes step B's SELL test.
--
-- WHY THE EARLIER TEST WAS VACUOUS. Step B filtered a "clean" cohort on
-- sell_qty = 0, i.e. on RN1_SOURCE_FILL_SIDE = 'SELL'. On his source venue
-- that quantity CANNOT BE NON-ZERO: exposure is reduced by BUYING THE
-- COMPLEMENTARY TOKEN, so a reduction books a BUY on the other token and never
-- a SELL row. A filter testing a condition that cannot fail retains ~100% of
-- the population and has measured nothing. The 99.707% retention rate is
-- evidence THE FILTER IS NOT BINDING ON THE MECHANISM THAT MATTERS, not
-- evidence the mechanism is absent. Both conclusions drawn from it are
-- withdrawn (research/TERMINOLOGY.md section 2).
--
-- Terminology is locked. RN1_SOURCE_FILL_SIDE is his source-venue fill
-- vocabulary (BUY only, in this window). BETTOR_REQUIRED_ACTION is our PMUS
-- target action under one-signed-position accounting (BUY/SELL/HOLD), and the
-- 32,847 SELL-required events of run 59 belong there and nowhere else. No bare
-- "SELL" appears below.
--
-- ---------------------------------------------------------------------------
-- THE PER-FILL DECOMPOSITION, from inventory state IMMEDIATELY BEFORE the fill.
--
-- For a BUY of token X of size s, with qX and qOther the running BUY
-- quantities before the fill:
--
--     M_before = min(qY, qN)                 M_after = min(qY', qN')
--     dM       = M_after - M_before
--     matched_component         = dM
--     new_directional_component = s - dM
--
-- and directional_before = |qY - qN|, carried on whichever side is longer.
--
-- The arithmetic yields a COMPLETE TRICHOTOMY, not a heuristic:
--
--   X is the LONGER or EQUAL side  ->  M_after = M_before, dM = 0.
--       The whole fill adds directional exposure.        DIRECTIONAL_ADD
--
--   X is the SHORTER side and s <= directional_before  ->  dM = s.
--       The whole fill converts the other side's directional exposure into
--       matched inventory.                               PAIR_FORMATION
--
--   X is the SHORTER side and s >  directional_before  ->  dM = directional_
--       before, and the excess s - dM opens NEW exposure on X: the position
--       crossed through flat and changed sides.          DIRECTIONAL_FLIP
--
-- Every canonical BUY falls in exactly one. This is the class-3 split already
-- established, now evaluated at every fill along the path instead of once at
-- the endpoint.
--
-- ---------------------------------------------------------------------------
-- WHY THE PATH AND NOT THE ENDPOINT. M = min(qY, qN) computed once per
-- condition COLLAPSES THE PATH TO ITS ENDPOINT. Two conditions with identical
-- end-state M and residual can have completely different histories: one built
-- a position monotonically, the other built directional exposure, traded it
-- away through the complement, and rebuilt it. The second has round-trip
-- directional economics the endpoint cannot see, and the acquisition-time
-- matched margin is not the whole story for it.
--
-- So statement 2 asks the owner's question directly -- was the end-of-window
-- residual ever REDUCED, FLIPPED, or REBUILT -- with these condition classes:
--
--   NEVER_REDUCED        end directional exposure == peak; it only ever grew
--   REDUCED_NOT_FLIPPED  end < peak, but the long side never changed
--   FLIPPED              the long side changed at least once
--
-- and REBUILT reported across all of them: directional exposure touched zero
-- and ended non-zero.
--
-- MATERIALITY IS THE POINT, NOT THE COUNTS. peak_directional - end_directional
-- is the exposure the endpoint view never sees. Reported in shares and in
-- dollars at the reducing fills' own prices.
--
-- ---------------------------------------------------------------------------
-- SELF-CHECK. Along each condition's path, sum(dM) must equal the endpoint
-- M = min(qY, qN) exactly -- the telescoping identity
-- d_m(t) = max(min(cy_t,cn_t) - min(cy_{t-1},cn_{t-1}), 0) established in the
-- BUY work. Statement 3 measures the discrepancy rather than assuming it. A
-- non-zero result invalidates the walk, not the endpoint.
--
-- NOTE ON dM's SIGN. In this BUY-only window min(qY,qN) is non-decreasing, so
-- dM >= 0 always and the max(...,0) of the original definition never binds.
-- The walk does NOT clamp, precisely so that a negative dM would show up in
-- statement 1 as a violated assumption instead of being silently floored.
--
-- ---------------------------------------------------------------------------
-- WINDOW EDGES. PREWINDOW_INVENTORY_PROVEN only where in-window evidence
-- proves prior inventory. Everything else is PREWINDOW_INVENTORY_NOT_PROVEN.
-- FULLY_IN_WINDOW is retired: observing no pre-window fill is absence of
-- evidence, not proof of no earlier inventory, and the walk below starts from
-- an ASSUMED FLAT BOOK at the window's left edge. Where that assumption is
-- false the early path is wrong, which is exactly why the label may not claim
-- more than it knows.
--
-- STRUCTURAL EXCLUSION, unchanged and not weakened by anything here:
-- trades.side is CHECK (side IN ('BUY','SELL')), so a complete-set conversion
-- back to collateral or an early redemption HAS NO ROW SHAPE.
--     TRUE_CASH_PATH = NOT IDENTIFIABLE FROM THIS LEDGER
-- even if acquisition-time matched margin and end-state residual economics
-- are identifiable. Unquantifiable, never zero, never small.
--
-- Read-only: three SELECTs.
-- ============================================================================


\echo '== 1. PER-FILL CLASSIFICATION along the chronological path =='
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
  SELECT condition_id, id, ts, outcome_index, sh, px
    FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy_incl,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn_incl
    FROM buys b
), st AS MATERIALIZED (
  SELECT w.*,
         w.cy_incl - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS cy_prev,
         w.cn_incl - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS cn_prev
    FROM walk w
), cls AS MATERIALIZED (
  SELECT st.*,
         LEAST(st.cy_prev, st.cn_prev) AS m_before,
         LEAST(st.cy_incl, st.cn_incl) AS m_after,
         LEAST(st.cy_incl, st.cn_incl) - LEAST(st.cy_prev, st.cn_prev) AS dm,
         abs(st.cy_prev - st.cn_prev) AS dir_before,
         abs(st.cy_incl - st.cn_incl) AS dir_after
    FROM st
)
SELECT CASE
         WHEN dm < -1e-9 THEN '0 NEGATIVE dM (assumption violated; investigate)'
         WHEN dm <= 1e-9 THEN '3 DIRECTIONAL_ADD (X already the longer side; whole fill is new exposure)'
         WHEN sh <= dir_before + 1e-9
           THEN '1 PAIR_FORMATION (whole fill converts the other side to matched)'
         ELSE '2 DIRECTIONAL_FLIP (crossed through flat; excess opens new exposure on X)'
       END AS fill_class,
       count(*) AS fills,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_fills,
       count(DISTINCT condition_id) AS conditions_touched,
       round(sum(sh)::numeric, 0) AS shares,
       round(sum(sh * px)::numeric, 0) AS notional_usd,
       round((100.0 * sum(sh * px) / sum(sum(sh * px)) OVER ())::numeric, 3)
         AS pct_notional,
       round(sum(dm)::numeric, 0) AS matched_component_shares,
       round(sum(sh - dm)::numeric, 0) AS new_directional_component_shares,
       round(sum(dm * px)::numeric, 0) AS matched_component_usd,
       round(sum((sh - dm) * px)::numeric, 0) AS new_directional_component_usd
  FROM cls GROUP BY 1 ORDER BY 1;


\echo '== 2. PER-CONDITION PATH CLASS: was the residual ever reduced, flipped, rebuilt? =='
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
  SELECT condition_id, id, ts, outcome_index, sh, px
    FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy_incl,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn_incl,
         row_number() OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id) AS rn,
         count(*)     OVER (PARTITION BY b.condition_id) AS n_fills
    FROM buys b
), st AS MATERIALIZED (
  SELECT w.*,
         w.cy_incl - CASE WHEN w.outcome_index = 0 THEN w.sh ELSE 0 END AS cy_prev,
         w.cn_incl - CASE WHEN w.outcome_index = 1 THEN w.sh ELSE 0 END AS cn_prev,
         abs(w.cy_incl - w.cn_incl) AS dir_after,
         sign(w.cy_incl - w.cn_incl) AS sgn_after
    FROM walk w
), cls AS MATERIALIZED (
  SELECT st.*,
         LEAST(st.cy_incl, st.cn_incl) - LEAST(st.cy_prev, st.cn_prev) AS dm,
         abs(st.cy_prev - st.cn_prev) AS dir_before,
         sign(st.cy_prev - st.cn_prev) AS sgn_before
    FROM st
), agg AS MATERIALIZED (
  SELECT condition_id,
         max(n_fills) AS n_fills,
         sum(sh * px) AS acq_cost,
         max(dir_after) AS peak_directional,
         max(dir_after) FILTER (WHERE rn = n_fills) AS end_directional,
         sum(CASE WHEN sgn_before <> 0 AND sgn_after <> 0 AND sgn_before <> sgn_after
                  THEN 1 ELSE 0 END) AS n_flips,
         sum(CASE WHEN dm > 1e-9 THEN 1 ELSE 0 END) AS n_reducing_fills,
         sum(CASE WHEN dm > 1e-9 THEN dm * px ELSE 0 END) AS reducing_notional,
         bool_or(dir_after <= 1e-9) AS touched_flat,
         max(LEAST(cy_incl, cn_incl)) FILTER (WHERE rn = n_fills) AS end_m
    FROM cls GROUP BY 1
)
SELECT CASE
         WHEN n_flips > 0 THEN '3 FLIPPED (the long side changed at least once)'
         WHEN peak_directional - end_directional > 1e-9
           THEN '2 REDUCED_NOT_FLIPPED (end below peak, side never changed)'
         ELSE '1 NEVER_REDUCED (directional exposure only ever grew)'
       END AS path_class,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round((100.0 * sum(acq_cost) / sum(sum(acq_cost)) OVER ())::numeric, 3)
         AS pct_acq_cost,
       count(*) FILTER (WHERE touched_flat AND end_directional > 1e-9)
         AS rebuilt_after_touching_flat,
       round(sum(peak_directional)::numeric, 0) AS peak_directional_shares,
       round(sum(end_directional)::numeric, 0) AS end_directional_shares,
       round(sum(peak_directional - end_directional)::numeric, 0)
         AS exposure_the_endpoint_view_never_sees_shares,
       round(sum(reducing_notional)::numeric, 0) AS reducing_fill_notional_usd,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY n_fills)::numeric, 1) AS p50_fills,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY n_fills)::numeric, 1) AS p90_fills
  FROM agg GROUP BY 1 ORDER BY 1;


\echo '== 3. SELF-CHECK: does the path telescope to the endpoint M? =='
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
  SELECT condition_id, id, ts, outcome_index, sh, px
    FROM canon
   WHERE side = 'BUY'
     AND ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), walk AS MATERIALIZED (
  SELECT b.*,
         sum(CASE WHEN b.outcome_index = 0 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cy_incl,
         sum(CASE WHEN b.outcome_index = 1 THEN b.sh ELSE 0 END)
           OVER (PARTITION BY b.condition_id ORDER BY b.ts, b.id
                 ROWS UNBOUNDED PRECEDING) AS cn_incl
    FROM buys b
), path AS MATERIALIZED (
  SELECT condition_id,
         sum(LEAST(cy_incl, cn_incl)
             - LEAST(cy_incl - CASE WHEN outcome_index = 0 THEN sh ELSE 0 END,
                     cn_incl - CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)) AS sum_dm
    FROM walk GROUP BY 1
), endp AS MATERIALIZED (
  SELECT condition_id,
         LEAST(sum(sh) FILTER (WHERE outcome_index = 0),
               sum(sh) FILTER (WHERE outcome_index = 1)) AS end_m
    FROM buys GROUP BY 1
)
SELECT count(*) AS conditions,
       round(sum(p.sum_dm)::numeric, 3) AS sum_of_path_dm_shares,
       round(sum(COALESCE(e.end_m, 0))::numeric, 3) AS endpoint_m_shares,
       round(sum(p.sum_dm - COALESCE(e.end_m, 0))::numeric, 6) AS discrepancy_shares,
       round(max(abs(p.sum_dm - COALESCE(e.end_m, 0)))::numeric, 9)
         AS max_abs_per_condition_discrepancy,
       count(*) FILTER (WHERE abs(p.sum_dm - COALESCE(e.end_m, 0)) > 1e-6)
         AS conditions_failing_the_telescoping_identity
  FROM path p JOIN endp e ON e.condition_id = p.condition_id;
