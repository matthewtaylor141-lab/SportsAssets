-- ============================================================================
-- LTI PRE-FLIGHT: PLAN ONLY. Nothing here is executed.
-- GENERATED from research/rn1_lti.sql by prefixing each statement with
-- EXPLAIN. Do not hand-edit.
-- ============================================================================

\echo '== PLAN ONLY -- 1. LTI COVERAGE: what share of dM qualifies, and why the rest does not =='

EXPLAIN
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
         (y.prev_ev IS NOT NULL AND y.prev_long IS DISTINCT FROM y.as_of_long)
                                                            AS desig_changed_here,
         (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
                                                            AS ratio_changed_here,
         (y.prev_ev IS NULL)                                AS no_preceding_event,
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
         GREATEST(0, z.m - 1) AS min_abs_local_qty,
         CASE WHEN z.d_is_integer THEN z.m ELSE z.m + 1 END AS max_abs_local_qty,
         -- HOLD is possible exactly when the lower bound is zero, i.e. when
         -- |r*dN| < 2. At or above 2 the side is FORCED with no state at all.
         (GREATEST(0, z.m - 1) = 0) AS hold_possible,
         (abs(z.d_cont) >= 2.0) AS side_forced,
         CASE WHEN z.signed_dn > 0 THEN 'BUY '
              WHEN z.signed_dn < 0 THEN 'SELL' END AS local_side,
         -- LOCAL_FILL_EFFECT_IDENTIFIED: L(N) = trunc(r(N+dN)) - trunc(rN) at a
         -- FROZEN event-time coordinate. The marginal effect of THIS fill. No
         -- history, no cross-event continuity.
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known)
           AS lfei,
         -- TARGET_TRANSITION_IDENTIFIED: the STRONGER claim that the local
         -- effect equals the clean mirror's transition FROM ITS PRECEDING
         -- TARGET. Needs coordinate and ratio compatibility ACROSS the
         -- transition: a changed r makes
         --   trunc(r_after*N_after) - trunc(r_before*N_before)
         -- not a function of dN at all, and a changed designation remaps the
         -- signed coordinate so the two nets are not even the same quantity.
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known
          AND NOT z.desig_changed_here AND NOT z.ratio_changed_here)
           AS tti
    FROM lti z
   WHERE z.ts >= timestamptz '2026-08-05 00:00Z'
), tot AS (
  SELECT count(*)::float8 AS ev, sum(d_m) AS dm, sum(d_m * px) AS notional
    FROM q WHERE d_m > 0.000001 AND c1_designation
), lab AS (
  SELECT 1 AS blk,
         CASE WHEN lfei THEN 'A LOCAL_FILL_EFFECT_IDENTIFIED'
              WHEN NOT c6_effect_known
                THEN 'B refused: designated token is neither traded leg'
              WHEN NOT c2_ratio
                THEN 'C refused: no observed ratio (never recorded)'
              WHEN NOT c5_fill_known
                THEN 'D refused: fill quantity or outcome unusable'
              ELSE 'E refused: other' END AS label, d_m, px, condition_id
    FROM q WHERE d_m > 0.000001 AND c1_designation
  UNION ALL
  SELECT 2,
         CASE WHEN tti AND no_preceding_event
                THEN 'F TARGET_TRANSITION_IDENTIFIED (no preceding event in window)'
              WHEN tti
                THEN 'G TARGET_TRANSITION_IDENTIFIED (preceding target, same coordinate and ratio)'
              WHEN ratio_changed_here
                THEN 'H LOCAL ONLY: ratio moved -- cross-event target not a function of dN'
              WHEN desig_changed_here
                THEN 'I LOCAL ONLY: designation moved -- signed coordinate remapped'
              ELSE 'J LOCAL ONLY: other' END, d_m, px, condition_id
    FROM q WHERE d_m > 0.000001 AND c1_designation AND lfei
)
SELECT lab.label,
       count(*) AS dM_events,
       round((100.0 * count(*) / t.ev)::numeric, 2) AS PCT_OF_DESIGNATED_EVENTS,
       count(DISTINCT lab.condition_id) AS conditions,
       round(sum(lab.d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(lab.d_m) / NULLIF(t.dm, 0))::numeric, 2) AS PCT_OF_DESIGNATED_dM,
       round(sum(lab.d_m * lab.px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(lab.d_m * lab.px) / NULLIF(t.notional, 0))::numeric, 2)
         AS PCT_OF_DESIGNATED_NOTIONAL
  FROM lab CROSS JOIN tot t
 GROUP BY lab.blk, lab.label, t.ev, t.dm, t.notional
 ORDER BY lab.blk, lab.label;

\echo '== PLAN ONLY -- 2. SIDE AND EXACT STATE-FREE QUANTITY RANGE, per population =='

EXPLAIN
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
         (y.prev_ev IS NOT NULL AND y.prev_long IS DISTINCT FROM y.as_of_long)
                                                            AS desig_changed_here,
         (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
                                                            AS ratio_changed_here,
         (y.prev_ev IS NULL)                                AS no_preceding_event,
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
         GREATEST(0, z.m - 1) AS min_abs_local_qty,
         CASE WHEN z.d_is_integer THEN z.m ELSE z.m + 1 END AS max_abs_local_qty,
         -- HOLD is possible exactly when the lower bound is zero, i.e. when
         -- |r*dN| < 2. At or above 2 the side is FORCED with no state at all.
         (GREATEST(0, z.m - 1) = 0) AS hold_possible,
         (abs(z.d_cont) >= 2.0) AS side_forced,
         CASE WHEN z.signed_dn > 0 THEN 'BUY '
              WHEN z.signed_dn < 0 THEN 'SELL' END AS local_side,
         -- LOCAL_FILL_EFFECT_IDENTIFIED: L(N) = trunc(r(N+dN)) - trunc(rN) at a
         -- FROZEN event-time coordinate. The marginal effect of THIS fill. No
         -- history, no cross-event continuity.
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known)
           AS lfei,
         -- TARGET_TRANSITION_IDENTIFIED: the STRONGER claim that the local
         -- effect equals the clean mirror's transition FROM ITS PRECEDING
         -- TARGET. Needs coordinate and ratio compatibility ACROSS the
         -- transition: a changed r makes
         --   trunc(r_after*N_after) - trunc(r_before*N_before)
         -- not a function of dN at all, and a changed designation remaps the
         -- signed coordinate so the two nets are not even the same quantity.
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known
          AND NOT z.desig_changed_here AND NOT z.ratio_changed_here)
           AS tti
    FROM lti z
   WHERE z.ts >= timestamptz '2026-08-05 00:00Z'
), pop AS (
  SELECT '1 LOCAL_FILL_EFFECT_IDENTIFIED' AS population, * FROM q
   WHERE d_m > 0.000001 AND lfei
  UNION ALL
  SELECT '2 TARGET_TRANSITION_IDENTIFIED', * FROM q
   WHERE d_m > 0.000001 AND tti
)
SELECT population, local_side,
       count(*) AS dM_events,
       count(DISTINCT condition_id) AS conditions,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round((100.0 * count(*) FILTER (WHERE side_forced) / NULLIF(count(*), 0))::numeric, 2)
         AS PCT_SIDE_FORCED_d_GE_2,
       round((100.0 * count(*) FILTER (WHERE hold_possible) / NULLIF(count(*), 0))::numeric, 2)
         AS pct_hold_possible,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ideal_continuous_qty)::numeric, 2)
         AS p50_d_shares,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY ideal_continuous_qty)::numeric, 2)
         AS p90_d_shares,
       round(sum(min_abs_local_qty)::numeric, 0) AS SUM_MIN_ABS_LOCAL_QTY,
       round(sum(max_abs_local_qty)::numeric, 0) AS SUM_MAX_ABS_LOCAL_QTY
  FROM pop WHERE local_side IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== PLAN ONLY -- 3. VALIDATION: the modelled state may never oppose the local sign =='
-- Ratio and designation transitions are EXCLUDED, not counted as violations:
-- for those the cross-event modelled change is legitimately not L(N).
-- Anything left in an X bucket is a derivation bug.
EXPLAIN
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
         (y.prev_ev IS NOT NULL AND y.prev_long IS DISTINCT FROM y.as_of_long)
                                                            AS desig_changed_here,
         (y.prev_ev IS NOT NULL AND y.prev_ratio IS DISTINCT FROM y.as_of_ratio)
                                                            AS ratio_changed_here,
         (y.prev_ev IS NULL)                                AS no_preceding_event,
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
         GREATEST(0, z.m - 1) AS min_abs_local_qty,
         CASE WHEN z.d_is_integer THEN z.m ELSE z.m + 1 END AS max_abs_local_qty,
         -- HOLD is possible exactly when the lower bound is zero, i.e. when
         -- |r*dN| < 2. At or above 2 the side is FORCED with no state at all.
         (GREATEST(0, z.m - 1) = 0) AS hold_possible,
         (abs(z.d_cont) >= 2.0) AS side_forced,
         CASE WHEN z.signed_dn > 0 THEN 'BUY '
              WHEN z.signed_dn < 0 THEN 'SELL' END AS local_side,
         -- LOCAL_FILL_EFFECT_IDENTIFIED: L(N) = trunc(r(N+dN)) - trunc(rN) at a
         -- FROZEN event-time coordinate. The marginal effect of THIS fill. No
         -- history, no cross-event continuity.
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known)
           AS lfei,
         -- TARGET_TRANSITION_IDENTIFIED: the STRONGER claim that the local
         -- effect equals the clean mirror's transition FROM ITS PRECEDING
         -- TARGET. Needs coordinate and ratio compatibility ACROSS the
         -- transition: a changed r makes
         --   trunc(r_after*N_after) - trunc(r_before*N_before)
         -- not a function of dN at all, and a changed designation remaps the
         -- signed coordinate so the two nets are not even the same quantity.
         (z.c1_designation AND z.c2_ratio AND z.c5_fill_known AND z.c6_effect_known
          AND NOT z.desig_changed_here AND NOT z.ratio_changed_here)
           AS tti
    FROM lti z
   WHERE z.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT CASE
         WHEN req_modeled IS NULL THEN 'E no modelled state to compare'
         WHEN ratio_changed_here
           THEN 'C EXCLUDED: ratio moved here (cross-event target not a function of dN)'
         WHEN desig_changed_here
           THEN 'D EXCLUDED: designation moved here (signed coordinate remapped)'
         WHEN req_modeled = 0 AND hold_possible
           THEN 'A modelled HOLD, inside the permitted truncation ambiguity'
         WHEN req_modeled = 0 AND side_forced
           THEN 'X VIOLATION: modelled HOLD where d >= 2 forbids it'
         WHEN sign(req_modeled) <> sign(signed_dn)
           THEN 'X VIOLATION: modelled side OPPOSES the locally identified sign'
         WHEN abs(req_modeled) < min_abs_local_qty OR abs(req_modeled) > max_abs_local_qty
           THEN 'X VIOLATION: modelled quantity OUTSIDE the exact integer range'
         ELSE 'B modelled side agrees AND quantity inside the exact range'
       END AS verdict,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 4) AS pct_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(max(abs(req_modeled - d_cont))::numeric, 4) AS max_abs_dev_from_r_dN,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(req_modeled - d_cont))::numeric, 4)
         AS p50_abs_dev_from_r_dN
  FROM q WHERE lfei
 GROUP BY 1 ORDER BY 1;

