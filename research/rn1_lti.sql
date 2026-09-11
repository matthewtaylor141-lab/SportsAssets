-- ============================================================================
-- LOCAL_TRANSITION_IDENTIFIED: the side, and bounds on the size, WITHOUT state
-- (2026-09-11, read-only.)
--
-- THE THEOREM, and why this beats the scan-anchor work. For a designation and
-- copy ratio r > 0 both in force at the fill:
--     target_before   = trunc(r * N_before)
--     target_after    = trunc(r * (N_before + dN))
--     required_change = target_after - target_before
--
-- 1 THE SIDE IS STATE-FREE. trunc toward zero is monotone non-decreasing on
--   the reals -- for both operands >= 0 it is floor, for both <= 0 it is ceil,
--   and for z1 < 0 <= z2, trunc(z1) = ceil(z1) <= 0 <= floor(z2) = trunc(z2).
--   So with r > 0:
--       dN > 0  =>  required_change >= 0  =>  BUY or HOLD, NEVER SELL
--       dN < 0  =>  required_change <= 0  =>  SELL or HOLD, NEVER BUY
--   N_before never appears. No inventory reconstruction is needed.
--
-- 2 THE SIZE IS BOUNDED WITHOUT STATE. With e(z) = z - trunc(z) in (-1, 1),
--       required_change = d + e(x) - e(x+d),  d = r*dN, x = r*N_before
--   so |required_change - r*dN| < 2 for every unknown starting state.
--
-- 3 THE EXACT INTEGER RANGE is tighter than that interval. Enumerating the
--   three cases -- both operands >= 0 (floor(x+d)-floor(x)), both <= 0 (the
--   ceil form), and the zero crossing (floor(d-a) + floor(a), the only case
--   that can lose a unit) -- gives, with m = floor(|r*dN|):
--       |required_change| in [max(0, m-1), m+1]   (|r*dN| not an integer)
--       |required_change| in [max(0, m-1), m  ]   (|r*dN| an integer)
--       sign(required_change) = sign(dN), or 0
--   Verified by enumeration: d=0.5 -> {0,1}; d=2.5 -> {1,2,3}; d=2 -> {1,2}.
--
-- 4 THE EXECUTION CONSEQUENCE. min|required_change| > 0 exactly when m >= 2,
--   i.e. |r*dN| >= 2. AT OR ABOVE TWO SHARES OF CONTINUOUS MOVE THE SIDE IS
--   FORCED AND A NON-ZERO ORDER IS REQUIRED, with no state and no assumption.
--   HOLD ambiguity exists only below that threshold, and is reported.
--
-- ALREADY TESTED. Check B's algebra block compared sign(required_change)
-- against sign(N_after - N_before) across the whole designated 37-day
-- population and returned 97.65% AGREES, 0.42% truncation-HOLD, 0.40%
-- DESIGNATION_CHANGED, 0.18% RATIO_CHANGED and ZERO UNEXPLAINED. Every
-- exception is either the truncation case the theorem permits or a violation
-- of conditions 3/4. Statement 3 re-tests it directly against the bounds.
--
-- A NOTE ON CONDITIONS 3 AND 4. They are NOT required for identification: the
-- local formulation never references the previous event's target, so a
-- designation or ratio that changed BETWEEN events cannot break it. That was
-- an artifact of Check B's cross-event lag(). They are required only to make
-- the comparison against the modeled state meaningful. Both populations are
-- therefore reported -- LTI_STRICT (all six conditions, the validation set)
-- and LTI_LOCAL (1, 2, 5, 6 -- everything the theorem actually needs).
--
-- THIS IS STATE-FREE AND USES NO SCAN. The scan finding stands separately and
-- is NOT used here: SCAN_ANCHORED_STATE cannot establish exact historical
-- state because 100% of usable scans are partial (run 43: fresh_complete = 0,
-- flagged_partial = 84,679), pagination is non-atomic offset paging with no
-- cursor, a condition's two legs may be sampled at different moments, scan
-- timing is not reconstructible, and the anchored population is prefix-
-- selected. None of that touches the theorem above.
--
-- DENOMINATOR, preserved: the ~46/53 BUY/SELL aggregate applies ONLY to the
-- causally designated population. 71.42% of dM shares remain
-- DESIGNATION_UNKNOWN and are not represented by it.
--
-- Read-only: three SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. LTI COVERAGE: what share of dM qualifies, and why the rest does not =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), desig_rows AS (
  SELECT condition_id, opened_at AS at, long_asset, ratio FROM mirror_books
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8 FROM mirror_candidate_refusals
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio FROM mirror_shadow
   WHERE long_asset IS NOT NULL
), stream AS (
  -- NO SNAPSHOTS. Local identification is STATE-FREE: it needs the designation
  -- and the ratio in force AT THE FILL, and nothing about RN1's inventory.
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, long_asset, ratio,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, outcome_index, sh, px FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), carried AS (
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), l0 AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         -- CONDITION 6: the fill's signed effect on RN1 net must be unambiguous.
         -- The fill's token is decided by its outcome_index; it is the LONG leg
         -- iff the as-of designation names that outcome's asset. If the
         -- designated token is neither traded leg the effect is undefined.
         CASE WHEN e.as_of_long IS NULL THEN NULL
              WHEN e.oi = 0 AND e.as_of_long = e.ay THEN  e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.an THEN  e.sh
              WHEN e.oi = 0 AND e.as_of_long = e.an THEN -e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.ay THEN -e.sh
              END AS signed_dn,
         -- the MODELED absolute state, for validation only. Never used to
         -- identify the side or the bounds.
         CASE WHEN e.as_of_long = e.ay THEN e.cy - e.cn
              WHEN e.as_of_long = e.an THEN e.cn - e.cy END AS net_after,
         CASE WHEN e.as_of_long = e.ay THEN e.py - e.pn
              WHEN e.as_of_long = e.an THEN e.pn - e.py END AS net_before
    FROM evr e
), l1 AS (
  SELECT x.*,
         lag(x.as_of_long)  OVER w AS prev_long,
         lag(x.as_of_ratio) OVER w AS prev_ratio,
         lag(x.ev)          OVER w AS prev_ev,
         trunc(x.as_of_ratio * x.net_after) AS tgt_after,
         COALESCE(lag(trunc(x.as_of_ratio * x.net_after)) OVER w,
                  trunc(x.as_of_ratio * x.net_before)) AS pos_before
    FROM l0 x WINDOW w AS (PARTITION BY x.condition_id ORDER BY x.ts, x.ev)
), lti AS (
  SELECT y.*,
         y.as_of_ratio * y.signed_dn AS d_cont,
         abs(y.as_of_ratio * y.signed_dn) AS ideal_continuous_qty,
         floor(abs(y.as_of_ratio * y.signed_dn)) AS m,
         (abs(abs(y.as_of_ratio * y.signed_dn)
              - round((abs(y.as_of_ratio * y.signed_dn))::numeric)::float8) < 1e-9)
           AS d_is_integer,
         y.tgt_after - y.pos_before AS req_modeled,
         -- the six conditions, each named so the refusals are countable
         (y.as_of_long  IS NOT NULL)                                AS c1_designation,
         (y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0)           AS c2_ratio,
         (y.prev_ev IS NULL OR y.prev_long IS NOT DISTINCT FROM y.as_of_long)
                                                                     AS c3_desig_stable,
         (y.prev_ev IS NULL OR y.prev_ratio IS NOT DISTINCT FROM y.as_of_ratio)
                                                                     AS c4_ratio_stable,
         (y.sh IS NOT NULL AND y.sh > 0 AND y.oi IN (0, 1))          AS c5_fill_known,
         (y.signed_dn IS NOT NULL)                                   AS c6_effect_known
    FROM l1 y
), q AS (
  SELECT z.*,
         -- THE EXACT INTEGER RANGE. m = floor(|r*dN|); the zero-crossing case
         -- is the only one that can lose a unit, so the magnitude lies in
         -- [max(0, m-1), m+1], and in [max(0, m-1), m] when |r*dN| is an
         -- integer. sign is sign(dN) or zero. Verified by enumeration:
         -- d=0.5 -> {0,1}; d=2.5 -> {1,2,3}; d=2 -> {1,2}.
         GREATEST(0, z.m - 1) AS min_abs_qty,
         CASE WHEN z.d_is_integer THEN z.m ELSE z.m + 1 END AS max_abs_qty,
         -- HOLD is possible exactly when the lower bound is zero, i.e. when
         -- |r*dN| < 2. At or above 2 the side is FORCED with no state at all.
         (GREATEST(0, z.m - 1) = 0) AS hold_possible,
         CASE WHEN z.signed_dn > 0 THEN 'BUY '
              WHEN z.signed_dn < 0 THEN 'SELL' END AS required_side_identified,
         (z.c1_designation AND z.c2_ratio AND z.c3_desig_stable
          AND z.c4_ratio_stable AND z.c5_fill_known AND z.c6_effect_known)
           AS lti_strict,
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known)
           AS lti_local
    FROM lti z
   WHERE z.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT CASE WHEN lti_strict THEN '1 LOCAL_TRANSITION_IDENTIFIED (all six conditions)'
            WHEN lti_local  THEN '2 LTI_LOCAL only (identified; designation or ratio moved between events)'
            WHEN NOT c1_designation THEN '3 refused: no causal designation at the event'
            WHEN NOT c2_ratio       THEN '4 refused: no observed ratio (never recorded)'
            WHEN NOT c6_effect_known THEN '5 refused: designated token is neither traded leg'
            WHEN NOT c5_fill_known  THEN '6 refused: fill quantity or outcome unusable'
            ELSE                         '7 refused: other' END AS classification,
       count(*) AS dM_events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS PCT_dM_EVENTS,
       count(DISTINCT condition_id) AS conditions,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM_SHARES,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(d_m * px) / NULLIF(sum(sum(d_m * px)) OVER (), 0))::numeric, 2)
         AS PCT_MATCHED_NOTIONAL
  FROM q WHERE d_m > 0.000001
 GROUP BY 1 ORDER BY 1;


\echo '== 2. LTI SIDE AND STATE-FREE QUANTITY BOUNDS, with the HOLD ambiguity =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), desig_rows AS (
  SELECT condition_id, opened_at AS at, long_asset, ratio FROM mirror_books
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8 FROM mirror_candidate_refusals
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio FROM mirror_shadow
   WHERE long_asset IS NOT NULL
), stream AS (
  -- NO SNAPSHOTS. Local identification is STATE-FREE: it needs the designation
  -- and the ratio in force AT THE FILL, and nothing about RN1's inventory.
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, long_asset, ratio,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, outcome_index, sh, px FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), carried AS (
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), l0 AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         -- CONDITION 6: the fill's signed effect on RN1 net must be unambiguous.
         -- The fill's token is decided by its outcome_index; it is the LONG leg
         -- iff the as-of designation names that outcome's asset. If the
         -- designated token is neither traded leg the effect is undefined.
         CASE WHEN e.as_of_long IS NULL THEN NULL
              WHEN e.oi = 0 AND e.as_of_long = e.ay THEN  e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.an THEN  e.sh
              WHEN e.oi = 0 AND e.as_of_long = e.an THEN -e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.ay THEN -e.sh
              END AS signed_dn,
         -- the MODELED absolute state, for validation only. Never used to
         -- identify the side or the bounds.
         CASE WHEN e.as_of_long = e.ay THEN e.cy - e.cn
              WHEN e.as_of_long = e.an THEN e.cn - e.cy END AS net_after,
         CASE WHEN e.as_of_long = e.ay THEN e.py - e.pn
              WHEN e.as_of_long = e.an THEN e.pn - e.py END AS net_before
    FROM evr e
), l1 AS (
  SELECT x.*,
         lag(x.as_of_long)  OVER w AS prev_long,
         lag(x.as_of_ratio) OVER w AS prev_ratio,
         lag(x.ev)          OVER w AS prev_ev,
         trunc(x.as_of_ratio * x.net_after) AS tgt_after,
         COALESCE(lag(trunc(x.as_of_ratio * x.net_after)) OVER w,
                  trunc(x.as_of_ratio * x.net_before)) AS pos_before
    FROM l0 x WINDOW w AS (PARTITION BY x.condition_id ORDER BY x.ts, x.ev)
), lti AS (
  SELECT y.*,
         y.as_of_ratio * y.signed_dn AS d_cont,
         abs(y.as_of_ratio * y.signed_dn) AS ideal_continuous_qty,
         floor(abs(y.as_of_ratio * y.signed_dn)) AS m,
         (abs(abs(y.as_of_ratio * y.signed_dn)
              - round((abs(y.as_of_ratio * y.signed_dn))::numeric)::float8) < 1e-9)
           AS d_is_integer,
         y.tgt_after - y.pos_before AS req_modeled,
         -- the six conditions, each named so the refusals are countable
         (y.as_of_long  IS NOT NULL)                                AS c1_designation,
         (y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0)           AS c2_ratio,
         (y.prev_ev IS NULL OR y.prev_long IS NOT DISTINCT FROM y.as_of_long)
                                                                     AS c3_desig_stable,
         (y.prev_ev IS NULL OR y.prev_ratio IS NOT DISTINCT FROM y.as_of_ratio)
                                                                     AS c4_ratio_stable,
         (y.sh IS NOT NULL AND y.sh > 0 AND y.oi IN (0, 1))          AS c5_fill_known,
         (y.signed_dn IS NOT NULL)                                   AS c6_effect_known
    FROM l1 y
), q AS (
  SELECT z.*,
         -- THE EXACT INTEGER RANGE. m = floor(|r*dN|); the zero-crossing case
         -- is the only one that can lose a unit, so the magnitude lies in
         -- [max(0, m-1), m+1], and in [max(0, m-1), m] when |r*dN| is an
         -- integer. sign is sign(dN) or zero. Verified by enumeration:
         -- d=0.5 -> {0,1}; d=2.5 -> {1,2,3}; d=2 -> {1,2}.
         GREATEST(0, z.m - 1) AS min_abs_qty,
         CASE WHEN z.d_is_integer THEN z.m ELSE z.m + 1 END AS max_abs_qty,
         -- HOLD is possible exactly when the lower bound is zero, i.e. when
         -- |r*dN| < 2. At or above 2 the side is FORCED with no state at all.
         (GREATEST(0, z.m - 1) = 0) AS hold_possible,
         CASE WHEN z.signed_dn > 0 THEN 'BUY '
              WHEN z.signed_dn < 0 THEN 'SELL' END AS required_side_identified,
         (z.c1_designation AND z.c2_ratio AND z.c3_desig_stable
          AND z.c4_ratio_stable AND z.c5_fill_known AND z.c6_effect_known)
           AS lti_strict,
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known)
           AS lti_local
    FROM lti z
   WHERE z.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT CASE WHEN hold_possible
              THEN 'B ' || required_side_identified || ' -- HOLD AMBIGUOUS (|r*dN| < 2)'
              ELSE 'A ' || required_side_identified || ' -- SIDE FORCED, order required' END
         AS required_action,
       count(*) AS dM_events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       count(DISTINCT condition_id) AS conditions,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ideal_continuous_qty)::numeric, 2)
         AS p50_ideal_qty,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY ideal_continuous_qty)::numeric, 2)
         AS p90_ideal_qty,
       round(sum(min_abs_qty)::numeric, 0) AS SUM_MIN_REQUIRED_SHARES,
       round(sum(max_abs_qty)::numeric, 0) AS SUM_MAX_REQUIRED_SHARES
  FROM q
 WHERE d_m > 0.000001 AND lti_strict AND required_side_identified IS NOT NULL
 GROUP BY 1 ORDER BY 1;


\echo '== 3. VALIDATION: the modelled state must never contradict the state-free result =='
-- Any row in a VIOLATION bucket is a derivation bug, not a finding.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh, b.px
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), desig_rows AS (
  SELECT condition_id, opened_at AS at, long_asset, ratio FROM mirror_books
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8 FROM mirror_candidate_refusals
   WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio FROM mirror_shadow
   WHERE long_asset IS NOT NULL
), stream AS (
  -- NO SNAPSHOTS. Local identification is STATE-FREE: it needs the designation
  -- and the ratio in force AT THE FILL, and nothing about RN1's inventory.
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, long_asset, ratio,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, outcome_index, sh, px FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), carried AS (
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), l0 AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         -- CONDITION 6: the fill's signed effect on RN1 net must be unambiguous.
         -- The fill's token is decided by its outcome_index; it is the LONG leg
         -- iff the as-of designation names that outcome's asset. If the
         -- designated token is neither traded leg the effect is undefined.
         CASE WHEN e.as_of_long IS NULL THEN NULL
              WHEN e.oi = 0 AND e.as_of_long = e.ay THEN  e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.an THEN  e.sh
              WHEN e.oi = 0 AND e.as_of_long = e.an THEN -e.sh
              WHEN e.oi = 1 AND e.as_of_long = e.ay THEN -e.sh
              END AS signed_dn,
         -- the MODELED absolute state, for validation only. Never used to
         -- identify the side or the bounds.
         CASE WHEN e.as_of_long = e.ay THEN e.cy - e.cn
              WHEN e.as_of_long = e.an THEN e.cn - e.cy END AS net_after,
         CASE WHEN e.as_of_long = e.ay THEN e.py - e.pn
              WHEN e.as_of_long = e.an THEN e.pn - e.py END AS net_before
    FROM evr e
), l1 AS (
  SELECT x.*,
         lag(x.as_of_long)  OVER w AS prev_long,
         lag(x.as_of_ratio) OVER w AS prev_ratio,
         lag(x.ev)          OVER w AS prev_ev,
         trunc(x.as_of_ratio * x.net_after) AS tgt_after,
         COALESCE(lag(trunc(x.as_of_ratio * x.net_after)) OVER w,
                  trunc(x.as_of_ratio * x.net_before)) AS pos_before
    FROM l0 x WINDOW w AS (PARTITION BY x.condition_id ORDER BY x.ts, x.ev)
), lti AS (
  SELECT y.*,
         y.as_of_ratio * y.signed_dn AS d_cont,
         abs(y.as_of_ratio * y.signed_dn) AS ideal_continuous_qty,
         floor(abs(y.as_of_ratio * y.signed_dn)) AS m,
         (abs(abs(y.as_of_ratio * y.signed_dn)
              - round((abs(y.as_of_ratio * y.signed_dn))::numeric)::float8) < 1e-9)
           AS d_is_integer,
         y.tgt_after - y.pos_before AS req_modeled,
         -- the six conditions, each named so the refusals are countable
         (y.as_of_long  IS NOT NULL)                                AS c1_designation,
         (y.as_of_ratio IS NOT NULL AND y.as_of_ratio > 0)           AS c2_ratio,
         (y.prev_ev IS NULL OR y.prev_long IS NOT DISTINCT FROM y.as_of_long)
                                                                     AS c3_desig_stable,
         (y.prev_ev IS NULL OR y.prev_ratio IS NOT DISTINCT FROM y.as_of_ratio)
                                                                     AS c4_ratio_stable,
         (y.sh IS NOT NULL AND y.sh > 0 AND y.oi IN (0, 1))          AS c5_fill_known,
         (y.signed_dn IS NOT NULL)                                   AS c6_effect_known
    FROM l1 y
), q AS (
  SELECT z.*,
         -- THE EXACT INTEGER RANGE. m = floor(|r*dN|); the zero-crossing case
         -- is the only one that can lose a unit, so the magnitude lies in
         -- [max(0, m-1), m+1], and in [max(0, m-1), m] when |r*dN| is an
         -- integer. sign is sign(dN) or zero. Verified by enumeration:
         -- d=0.5 -> {0,1}; d=2.5 -> {1,2,3}; d=2 -> {1,2}.
         GREATEST(0, z.m - 1) AS min_abs_qty,
         CASE WHEN z.d_is_integer THEN z.m ELSE z.m + 1 END AS max_abs_qty,
         -- HOLD is possible exactly when the lower bound is zero, i.e. when
         -- |r*dN| < 2. At or above 2 the side is FORCED with no state at all.
         (GREATEST(0, z.m - 1) = 0) AS hold_possible,
         CASE WHEN z.signed_dn > 0 THEN 'BUY '
              WHEN z.signed_dn < 0 THEN 'SELL' END AS required_side_identified,
         (z.c1_designation AND z.c2_ratio AND z.c3_desig_stable
          AND z.c4_ratio_stable AND z.c5_fill_known AND z.c6_effect_known)
           AS lti_strict,
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known)
           AS lti_local
    FROM lti z
   WHERE z.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT CASE
         WHEN req_modeled IS NULL THEN 'D no modelled state to compare'
         WHEN req_modeled = 0 AND hold_possible
           THEN 'A modelled HOLD, inside the permitted truncation ambiguity'
         WHEN req_modeled = 0 AND NOT hold_possible
           THEN 'X VIOLATION: modelled HOLD where |r*dN| >= 2 forbids it'
         WHEN sign(req_modeled) <> sign(signed_dn)
           THEN 'X VIOLATION: modelled side OPPOSES the state-free side'
         WHEN abs(req_modeled) < min_abs_qty OR abs(req_modeled) > max_abs_qty
           THEN 'X VIOLATION: modelled quantity OUTSIDE the state-free bounds'
         ELSE 'B modelled side agrees AND quantity inside the bounds'
       END AS verdict,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 4) AS pct_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(max(abs(req_modeled - d_cont))::numeric, 4) AS max_abs_dev_from_r_dN,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(req_modeled - d_cont))::numeric, 4)
         AS p50_abs_dev_from_r_dN
  FROM q WHERE lti_strict AND signed_dn IS NOT NULL
 GROUP BY 1 ORDER BY 1;
