-- ============================================================================
-- RE-ANCHOR PRE-FLIGHT: PLAN ONLY. Nothing here is executed.
-- GENERATED from research/rn1_checkB_reanchor.sql by prefixing each statement
-- with EXPLAIN. Do not hand-edit. EXPLAIN performs full parse analysis, so a
-- bad name fails here in seconds instead of aborting the real file under
-- ON_ERROR_STOP; it also shows whether check A's nested-loop rescan is back.
-- ============================================================================

\echo '== PLAN ONLY -- 1. THE CLOCK ITSELF: what the persistence path actually recorded =='
-- The evidence behind the semantics above, measured rather than asserted, and
-- the count of rows the correction CANNOT be applied to.
EXPLAIN
SELECT count(*) AS usable_snapshot_rows,
       count(*) FILTER (WHERE detail->>'snap_age_s' IS NULL) AS MISSING_snap_age_s,
       count(*) FILTER (WHERE detail->>'snap_partial' IS NOT NULL) AS flagged_partial,
       count(*) FILTER (WHERE detail->>'snap_state' = 'fresh_complete') AS fresh_complete,
       count(*) FILTER (WHERE detail->>'snap_state' = 'fresh_partial') AS fresh_partial,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY (detail->>'snap_age_s')::float8)::numeric, 1) AS p50_snap_age_s,
       round(percentile_cont(0.9) WITHIN GROUP (
         ORDER BY (detail->>'snap_age_s')::float8)::numeric, 1) AS p90_snap_age_s,
       round(max((detail->>'snap_age_s')::float8)::numeric, 1) AS max_snap_age_s,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY (detail->>'fills_since_snap')::float8)::numeric, 1) AS p50_fills_since_snap,
       round(percentile_cont(0.9) WITHIN GROUP (
         ORDER BY (detail->>'fills_since_snap')::float8)::numeric, 1) AS p90_fills_since_snap,
       max((detail->>'fills_since_snap')::float8)::bigint AS max_fills_since_snap
  FROM mirror_shadow
 WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL;

\echo '== PLAN ONLY -- 2. BUY/SELL/HOLD MIX under each of the five state models =='

EXPLAIN
WITH ac AS (
  SELECT DISTINCT condition_id FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN ac ON ac.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY' AND t.outcome_index IN (0, 1)
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
), snaps AS (
  SELECT condition_id, at, long_asset AS anchor_long, snap_long, snap_other,
         (detail->>'snap_age_s')::float8 AS age_s
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), snapv AS (
  -- THE SAME SCAN, PLACED THREE WAYS.
  --   variant 0  PLACEMENT_WRITE   shadow.at -- run 41's conservative placement
  --   variant 1  PLACEMENT_LATEST  at - snap_age_s -- the LATEST DEFENSIBLE
  --              placement derivable from persisted timing. It is a
  --              scan-COMPLETION-derived placement, not an observation instant.
  --   variant 2  TIMING_STRESS     at - 2*snap_age_s -- EXPLICITLY ARBITRARY.
  --              snap_age_s carries NO information about the duration of the
  --              /positions walk, so multiplying it CANNOT bound the unobserved
  --              scan. It is not a bound, not an interval endpoint, not a
  --              confidence limit and not an estimate of truth. It exists only
  --              to show whether the conclusions move at all when the anchor is
  --              displaced; nothing is inferred from its value.
  SELECT n.condition_id, v.variant,
         CASE v.variant WHEN 0 THEN n.at
                        WHEN 1 THEN n.at - make_interval(secs => n.age_s)
                        ELSE        n.at - make_interval(secs => 2.0 * n.age_s) END AS eff_at,
         n.anchor_long, n.snap_long, n.snap_other
    FROM snaps n CROSS JOIN (VALUES (0), (1), (2)) AS v(variant)
   WHERE n.age_s IS NOT NULL
), stream AS (
  -- TWO INDEPENDENT CLOCKS, deliberately not merged.
  --   designation rows keep shadow.at: _choose_long reads
  --   pos = mi.net_positions(fills) (mirror_shadow.py:946), our fills-derived
  --   position AT TICK TIME. It never reads the snapshot. Backdating it would
  --   assert a decision existed before the data it was made from.
  --   snapshot rows move to their effective time: they come from the whale-exit
  --   worker's cached page walk (whale_exits.py:1107), a different source that
  --   can be up to SNAP_MAX_AGE_S (300 s) older than the row carrying it.
  -- anchor_long is used ONLY to orient a snapshot row's own two numbers into
  -- outcome-index space, never as a designation for any event.
  SELECT condition_id, at AS ts, 0 AS pri, -1 AS variant, NULL::bigint AS ev,
         long_asset, ratio, NULL::text AS anchor_long, NULL::float8 AS snap_long,
         NULL::float8 AS snap_other, false AS is_snap,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM desig_rows
  UNION ALL
  SELECT condition_id, eff_at, 1, variant, NULL, NULL, NULL,
         anchor_long, snap_long, snap_other, true, NULL, 0::float8, NULL
    FROM snapv
  UNION ALL
  SELECT condition_id, ts, 2, -1, id, NULL, NULL, NULL, NULL, NULL, false,
         outcome_index, sh, px
    FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         count(*) FILTER (WHERE s.is_snap AND s.variant = 0) OVER wc AS ga0,
         count(*) FILTER (WHERE s.is_snap AND s.variant = 1) OVER wc AS ga1,
         count(*) FILTER (WHERE s.is_snap AND s.variant = 2) OVER wc AS ga2,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS fy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS fn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.variant, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), seeded AS (
  SELECT r.*,
         CASE WHEN r.is_snap AND r.anchor_long = r.ay THEN r.snap_long  - r.fy
              WHEN r.is_snap AND r.anchor_long = r.an THEN r.snap_other - r.fy END AS sy,
         CASE WHEN r.is_snap AND r.anchor_long = r.ay THEN r.snap_other - r.fn
              WHEN r.is_snap AND r.anchor_long = r.an THEN r.snap_long  - r.fn END AS sn
    FROM run r
), sv AS (
  SELECT s.*,
         CASE WHEN s.variant = 0 THEN s.sy END AS sy0,
         CASE WHEN s.variant = 0 THEN s.sn END AS sn0,
         CASE WHEN s.variant = 1 THEN s.sy END AS sy1,
         CASE WHEN s.variant = 1 THEN s.sn END AS sn1,
         CASE WHEN s.variant = 2 THEN s.sy END AS sy2,
         CASE WHEN s.variant = 2 THEN s.sn END AS sn2
    FROM seeded s
), carried AS (
  SELECT v.*,
         first_value(v.long_asset) OVER wd AS as_of_long,
         first_value(v.ratio)      OVER wr AS as_of_ratio,
         first_value(v.sy0) OVER w0 AS ky_old, first_value(v.sn0) OVER w0 AS kn_old,
         first_value(v.sy1) OVER w1 AS ky_cor, first_value(v.sn1) OVER w1 AS kn_cor,
         first_value(v.sy2) OVER w2 AS ky_str, first_value(v.sn2) OVER w2 AS kn_str,
         first_value(v.sy1) OVER wo AS ky_onc, first_value(v.sn1) OVER wo AS kn_onc
    FROM sv v
  WINDOW wd AS (PARTITION BY v.condition_id, v.gd ORDER BY v.ts, v.pri, v.variant, v.ev),
         wr AS (PARTITION BY v.condition_id, v.gr ORDER BY v.ts, v.pri, v.variant, v.ev),
         w0 AS (PARTITION BY v.condition_id, v.ga0 ORDER BY v.ts, v.pri, v.variant, v.ev),
         w1 AS (PARTITION BY v.condition_id, v.ga1 ORDER BY v.ts, v.pri, v.variant, v.ev),
         w2 AS (PARTITION BY v.condition_id, v.ga2 ORDER BY v.ts, v.pri, v.variant, v.ev),
         wo AS (PARTITION BY v.condition_id, (v.ga1 > 0) ORDER BY v.ts, v.pri, v.variant, v.ev)
), evr AS (
  -- ga0 > 0 keeps the population IDENTICAL to run 41's anchored set, so old
  -- against corrected is a clock change and nothing else.
  SELECT c.* FROM carried c WHERE c.ev IS NOT NULL AND c.ga0 > 0
), sta AS (
  SELECT e.*,
         CASE WHEN e.oi = 0 THEN e.sh ELSE 0 END AS dy,
         CASE WHEN e.oi = 1 THEN e.sh ELSE 0 END AS dn,
         e.fy AS yU, e.fn AS nU,
         e.ky_onc + e.fy AS yO, e.kn_onc + e.fn AS nO,
         e.ky_old + e.fy AS yD, e.kn_old + e.fn AS nD,
         e.ky_cor + e.fy AS yC, e.kn_cor + e.fn AS nC,
         e.ky_str + e.fy AS yZ, e.kn_str + e.fn AS nZ,
         e.ev AS ev_keep
    FROM evr e
), q AS (
  SELECT s.*,
         GREATEST(LEAST(s.yU, s.nU) - LEAST(s.yU - s.dy, s.nU - s.dn), 0) AS dmU,
         CASE WHEN s.as_of_long = s.ay THEN s.yU - s.nU
              WHEN s.as_of_long = s.an THEN s.nU - s.yU END AS netU,
         CASE WHEN s.as_of_long = s.ay THEN (s.yU - s.dy) - (s.nU - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nU - s.dn) - (s.yU - s.dy) END AS preU,
         GREATEST(LEAST(s.yO, s.nO) - LEAST(s.yO - s.dy, s.nO - s.dn), 0) AS dmO,
         CASE WHEN s.as_of_long = s.ay THEN s.yO - s.nO
              WHEN s.as_of_long = s.an THEN s.nO - s.yO END AS netO,
         CASE WHEN s.as_of_long = s.ay THEN (s.yO - s.dy) - (s.nO - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nO - s.dn) - (s.yO - s.dy) END AS preO,
         GREATEST(LEAST(s.yD, s.nD) - LEAST(s.yD - s.dy, s.nD - s.dn), 0) AS dmD,
         CASE WHEN s.as_of_long = s.ay THEN s.yD - s.nD
              WHEN s.as_of_long = s.an THEN s.nD - s.yD END AS netD,
         CASE WHEN s.as_of_long = s.ay THEN (s.yD - s.dy) - (s.nD - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nD - s.dn) - (s.yD - s.dy) END AS preD,
         GREATEST(LEAST(s.yC, s.nC) - LEAST(s.yC - s.dy, s.nC - s.dn), 0) AS dmC,
         CASE WHEN s.as_of_long = s.ay THEN s.yC - s.nC
              WHEN s.as_of_long = s.an THEN s.nC - s.yC END AS netC,
         CASE WHEN s.as_of_long = s.ay THEN (s.yC - s.dy) - (s.nC - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nC - s.dn) - (s.yC - s.dy) END AS preC,
         GREATEST(LEAST(s.yZ, s.nZ) - LEAST(s.yZ - s.dy, s.nZ - s.dn), 0) AS dmZ,
         CASE WHEN s.as_of_long = s.ay THEN s.yZ - s.nZ
              WHEN s.as_of_long = s.an THEN s.nZ - s.yZ END AS netZ,
         CASE WHEN s.as_of_long = s.ay THEN (s.yZ - s.dy) - (s.nZ - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nZ - s.dn) - (s.yZ - s.dy) END AS preZ,
         1 AS one
    FROM sta s
), f AS (
  SELECT q.*,
         trunc(q.as_of_ratio * q.netU) AS tU,
         COALESCE(lag(trunc(q.as_of_ratio * q.netU)) OVER w,
                  trunc(q.as_of_ratio * q.preU)) AS pU,
         trunc(q.as_of_ratio * q.netO) AS tO,
         COALESCE(lag(trunc(q.as_of_ratio * q.netO)) OVER w,
                  trunc(q.as_of_ratio * q.preO)) AS pO,
         trunc(q.as_of_ratio * q.netD) AS tD,
         COALESCE(lag(trunc(q.as_of_ratio * q.netD)) OVER w,
                  trunc(q.as_of_ratio * q.preD)) AS pD,
         trunc(q.as_of_ratio * q.netC) AS tC,
         COALESCE(lag(trunc(q.as_of_ratio * q.netC)) OVER w,
                  trunc(q.as_of_ratio * q.preC)) AS pC,
         trunc(q.as_of_ratio * q.netZ) AS tZ,
         COALESCE(lag(trunc(q.as_of_ratio * q.netZ)) OVER w,
                  trunc(q.as_of_ratio * q.preZ)) AS pZ,
         1 AS one2
    FROM q WINDOW w AS (PARTITION BY q.condition_id ORDER BY q.ts, q.ev)
), a AS (
  SELECT f.*,
         f.tU - f.pU AS qU,
         CASE WHEN f.tU - f.pU > 0 THEN 'BUY '
              WHEN f.tU - f.pU < 0 THEN 'SELL' ELSE 'HOLD' END AS aU,
         f.tO - f.pO AS qO,
         CASE WHEN f.tO - f.pO > 0 THEN 'BUY '
              WHEN f.tO - f.pO < 0 THEN 'SELL' ELSE 'HOLD' END AS aO,
         f.tD - f.pD AS qD,
         CASE WHEN f.tD - f.pD > 0 THEN 'BUY '
              WHEN f.tD - f.pD < 0 THEN 'SELL' ELSE 'HOLD' END AS aD,
         f.tC - f.pC AS qC,
         CASE WHEN f.tC - f.pC > 0 THEN 'BUY '
              WHEN f.tC - f.pC < 0 THEN 'SELL' ELSE 'HOLD' END AS aC,
         f.tZ - f.pZ AS qZ,
         CASE WHEN f.tZ - f.pZ > 0 THEN 'BUY '
              WHEN f.tZ - f.pZ < 0 THEN 'SELL' ELSE 'HOLD' END AS aZ,
         GREATEST(f.dmU, f.dmO, f.dmD, f.dmC, f.dmZ) AS dmw,
         (f.as_of_long IS NOT NULL AND f.as_of_ratio IS NOT NULL
          AND f.netU IS NOT NULL AND f.netO IS NOT NULL AND f.netD IS NOT NULL
          AND f.netC IS NOT NULL AND f.netZ IS NOT NULL) AS classifiable
    FROM f
), lab AS (
  SELECT 1 AS blk, '1 UNANCHORED_37D_MODEL' AS model, aU AS act, dmw, px, condition_id
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 2 AS blk, '2 SCAN_ANCHOR_ONCE_FORWARD' AS model, aO AS act, dmw, px, condition_id
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 3 AS blk, '3 RECONCILED @ PLACEMENT_WRITE (shadow.at)' AS model, aD AS act, dmw, px, condition_id
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 4 AS blk, '4 RECONCILED @ PLACEMENT_LATEST (at-snap_age_s)' AS model, aC AS act, dmw, px, condition_id
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 5 AS blk, '5 RECONCILED @ TIMING_STRESS (at-2*age, ARBITRARY)' AS model, aZ AS act, dmw, px, condition_id
    FROM a WHERE dmw > 0.000001 AND classifiable
)
SELECT model, act AS required_action,
       count(*) AS dM_events,
       count(DISTINCT condition_id) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY blk))::numeric, 2)
         AS PCT_EVENTS,
       round(sum(dmw)::numeric, 0) AS dM_shares,
       round((100.0 * sum(dmw) / NULLIF(sum(sum(dmw)) OVER (PARTITION BY blk), 0))::numeric, 2)
         AS PCT_dM,
       round(sum(dmw * px)::numeric, 0) AS matched_notional
  FROM lab GROUP BY blk, model, act ORDER BY blk, model, act;

\echo '== PLAN ONLY -- 3. PAIRWISE: the two the owner asked for, the clock delta, and the stress =='
-- x is the CANDIDATE, y the REFERENCE. "qty err %" is sum|qx-qy| / sum|qy|:
-- error against the size the reference model would actually have ordered.
-- An INVERSION is one model saying BUY where the other says SELL -- the only
-- disagreement that trades the wrong DIRECTION rather than the wrong size.
EXPLAIN
WITH ac AS (
  SELECT DISTINCT condition_id FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN ac ON ac.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY' AND t.outcome_index IN (0, 1)
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
), snaps AS (
  SELECT condition_id, at, long_asset AS anchor_long, snap_long, snap_other,
         (detail->>'snap_age_s')::float8 AS age_s
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), snapv AS (
  -- THE SAME SCAN, PLACED THREE WAYS.
  --   variant 0  PLACEMENT_WRITE   shadow.at -- run 41's conservative placement
  --   variant 1  PLACEMENT_LATEST  at - snap_age_s -- the LATEST DEFENSIBLE
  --              placement derivable from persisted timing. It is a
  --              scan-COMPLETION-derived placement, not an observation instant.
  --   variant 2  TIMING_STRESS     at - 2*snap_age_s -- EXPLICITLY ARBITRARY.
  --              snap_age_s carries NO information about the duration of the
  --              /positions walk, so multiplying it CANNOT bound the unobserved
  --              scan. It is not a bound, not an interval endpoint, not a
  --              confidence limit and not an estimate of truth. It exists only
  --              to show whether the conclusions move at all when the anchor is
  --              displaced; nothing is inferred from its value.
  SELECT n.condition_id, v.variant,
         CASE v.variant WHEN 0 THEN n.at
                        WHEN 1 THEN n.at - make_interval(secs => n.age_s)
                        ELSE        n.at - make_interval(secs => 2.0 * n.age_s) END AS eff_at,
         n.anchor_long, n.snap_long, n.snap_other
    FROM snaps n CROSS JOIN (VALUES (0), (1), (2)) AS v(variant)
   WHERE n.age_s IS NOT NULL
), stream AS (
  -- TWO INDEPENDENT CLOCKS, deliberately not merged.
  --   designation rows keep shadow.at: _choose_long reads
  --   pos = mi.net_positions(fills) (mirror_shadow.py:946), our fills-derived
  --   position AT TICK TIME. It never reads the snapshot. Backdating it would
  --   assert a decision existed before the data it was made from.
  --   snapshot rows move to their effective time: they come from the whale-exit
  --   worker's cached page walk (whale_exits.py:1107), a different source that
  --   can be up to SNAP_MAX_AGE_S (300 s) older than the row carrying it.
  -- anchor_long is used ONLY to orient a snapshot row's own two numbers into
  -- outcome-index space, never as a designation for any event.
  SELECT condition_id, at AS ts, 0 AS pri, -1 AS variant, NULL::bigint AS ev,
         long_asset, ratio, NULL::text AS anchor_long, NULL::float8 AS snap_long,
         NULL::float8 AS snap_other, false AS is_snap,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM desig_rows
  UNION ALL
  SELECT condition_id, eff_at, 1, variant, NULL, NULL, NULL,
         anchor_long, snap_long, snap_other, true, NULL, 0::float8, NULL
    FROM snapv
  UNION ALL
  SELECT condition_id, ts, 2, -1, id, NULL, NULL, NULL, NULL, NULL, false,
         outcome_index, sh, px
    FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         count(*) FILTER (WHERE s.is_snap AND s.variant = 0) OVER wc AS ga0,
         count(*) FILTER (WHERE s.is_snap AND s.variant = 1) OVER wc AS ga1,
         count(*) FILTER (WHERE s.is_snap AND s.variant = 2) OVER wc AS ga2,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS fy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS fn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.variant, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), seeded AS (
  SELECT r.*,
         CASE WHEN r.is_snap AND r.anchor_long = r.ay THEN r.snap_long  - r.fy
              WHEN r.is_snap AND r.anchor_long = r.an THEN r.snap_other - r.fy END AS sy,
         CASE WHEN r.is_snap AND r.anchor_long = r.ay THEN r.snap_other - r.fn
              WHEN r.is_snap AND r.anchor_long = r.an THEN r.snap_long  - r.fn END AS sn
    FROM run r
), sv AS (
  SELECT s.*,
         CASE WHEN s.variant = 0 THEN s.sy END AS sy0,
         CASE WHEN s.variant = 0 THEN s.sn END AS sn0,
         CASE WHEN s.variant = 1 THEN s.sy END AS sy1,
         CASE WHEN s.variant = 1 THEN s.sn END AS sn1,
         CASE WHEN s.variant = 2 THEN s.sy END AS sy2,
         CASE WHEN s.variant = 2 THEN s.sn END AS sn2
    FROM seeded s
), carried AS (
  SELECT v.*,
         first_value(v.long_asset) OVER wd AS as_of_long,
         first_value(v.ratio)      OVER wr AS as_of_ratio,
         first_value(v.sy0) OVER w0 AS ky_old, first_value(v.sn0) OVER w0 AS kn_old,
         first_value(v.sy1) OVER w1 AS ky_cor, first_value(v.sn1) OVER w1 AS kn_cor,
         first_value(v.sy2) OVER w2 AS ky_str, first_value(v.sn2) OVER w2 AS kn_str,
         first_value(v.sy1) OVER wo AS ky_onc, first_value(v.sn1) OVER wo AS kn_onc
    FROM sv v
  WINDOW wd AS (PARTITION BY v.condition_id, v.gd ORDER BY v.ts, v.pri, v.variant, v.ev),
         wr AS (PARTITION BY v.condition_id, v.gr ORDER BY v.ts, v.pri, v.variant, v.ev),
         w0 AS (PARTITION BY v.condition_id, v.ga0 ORDER BY v.ts, v.pri, v.variant, v.ev),
         w1 AS (PARTITION BY v.condition_id, v.ga1 ORDER BY v.ts, v.pri, v.variant, v.ev),
         w2 AS (PARTITION BY v.condition_id, v.ga2 ORDER BY v.ts, v.pri, v.variant, v.ev),
         wo AS (PARTITION BY v.condition_id, (v.ga1 > 0) ORDER BY v.ts, v.pri, v.variant, v.ev)
), evr AS (
  -- ga0 > 0 keeps the population IDENTICAL to run 41's anchored set, so old
  -- against corrected is a clock change and nothing else.
  SELECT c.* FROM carried c WHERE c.ev IS NOT NULL AND c.ga0 > 0
), sta AS (
  SELECT e.*,
         CASE WHEN e.oi = 0 THEN e.sh ELSE 0 END AS dy,
         CASE WHEN e.oi = 1 THEN e.sh ELSE 0 END AS dn,
         e.fy AS yU, e.fn AS nU,
         e.ky_onc + e.fy AS yO, e.kn_onc + e.fn AS nO,
         e.ky_old + e.fy AS yD, e.kn_old + e.fn AS nD,
         e.ky_cor + e.fy AS yC, e.kn_cor + e.fn AS nC,
         e.ky_str + e.fy AS yZ, e.kn_str + e.fn AS nZ,
         e.ev AS ev_keep
    FROM evr e
), q AS (
  SELECT s.*,
         GREATEST(LEAST(s.yU, s.nU) - LEAST(s.yU - s.dy, s.nU - s.dn), 0) AS dmU,
         CASE WHEN s.as_of_long = s.ay THEN s.yU - s.nU
              WHEN s.as_of_long = s.an THEN s.nU - s.yU END AS netU,
         CASE WHEN s.as_of_long = s.ay THEN (s.yU - s.dy) - (s.nU - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nU - s.dn) - (s.yU - s.dy) END AS preU,
         GREATEST(LEAST(s.yO, s.nO) - LEAST(s.yO - s.dy, s.nO - s.dn), 0) AS dmO,
         CASE WHEN s.as_of_long = s.ay THEN s.yO - s.nO
              WHEN s.as_of_long = s.an THEN s.nO - s.yO END AS netO,
         CASE WHEN s.as_of_long = s.ay THEN (s.yO - s.dy) - (s.nO - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nO - s.dn) - (s.yO - s.dy) END AS preO,
         GREATEST(LEAST(s.yD, s.nD) - LEAST(s.yD - s.dy, s.nD - s.dn), 0) AS dmD,
         CASE WHEN s.as_of_long = s.ay THEN s.yD - s.nD
              WHEN s.as_of_long = s.an THEN s.nD - s.yD END AS netD,
         CASE WHEN s.as_of_long = s.ay THEN (s.yD - s.dy) - (s.nD - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nD - s.dn) - (s.yD - s.dy) END AS preD,
         GREATEST(LEAST(s.yC, s.nC) - LEAST(s.yC - s.dy, s.nC - s.dn), 0) AS dmC,
         CASE WHEN s.as_of_long = s.ay THEN s.yC - s.nC
              WHEN s.as_of_long = s.an THEN s.nC - s.yC END AS netC,
         CASE WHEN s.as_of_long = s.ay THEN (s.yC - s.dy) - (s.nC - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nC - s.dn) - (s.yC - s.dy) END AS preC,
         GREATEST(LEAST(s.yZ, s.nZ) - LEAST(s.yZ - s.dy, s.nZ - s.dn), 0) AS dmZ,
         CASE WHEN s.as_of_long = s.ay THEN s.yZ - s.nZ
              WHEN s.as_of_long = s.an THEN s.nZ - s.yZ END AS netZ,
         CASE WHEN s.as_of_long = s.ay THEN (s.yZ - s.dy) - (s.nZ - s.dn)
              WHEN s.as_of_long = s.an THEN (s.nZ - s.dn) - (s.yZ - s.dy) END AS preZ,
         1 AS one
    FROM sta s
), f AS (
  SELECT q.*,
         trunc(q.as_of_ratio * q.netU) AS tU,
         COALESCE(lag(trunc(q.as_of_ratio * q.netU)) OVER w,
                  trunc(q.as_of_ratio * q.preU)) AS pU,
         trunc(q.as_of_ratio * q.netO) AS tO,
         COALESCE(lag(trunc(q.as_of_ratio * q.netO)) OVER w,
                  trunc(q.as_of_ratio * q.preO)) AS pO,
         trunc(q.as_of_ratio * q.netD) AS tD,
         COALESCE(lag(trunc(q.as_of_ratio * q.netD)) OVER w,
                  trunc(q.as_of_ratio * q.preD)) AS pD,
         trunc(q.as_of_ratio * q.netC) AS tC,
         COALESCE(lag(trunc(q.as_of_ratio * q.netC)) OVER w,
                  trunc(q.as_of_ratio * q.preC)) AS pC,
         trunc(q.as_of_ratio * q.netZ) AS tZ,
         COALESCE(lag(trunc(q.as_of_ratio * q.netZ)) OVER w,
                  trunc(q.as_of_ratio * q.preZ)) AS pZ,
         1 AS one2
    FROM q WINDOW w AS (PARTITION BY q.condition_id ORDER BY q.ts, q.ev)
), a AS (
  SELECT f.*,
         f.tU - f.pU AS qU,
         CASE WHEN f.tU - f.pU > 0 THEN 'BUY '
              WHEN f.tU - f.pU < 0 THEN 'SELL' ELSE 'HOLD' END AS aU,
         f.tO - f.pO AS qO,
         CASE WHEN f.tO - f.pO > 0 THEN 'BUY '
              WHEN f.tO - f.pO < 0 THEN 'SELL' ELSE 'HOLD' END AS aO,
         f.tD - f.pD AS qD,
         CASE WHEN f.tD - f.pD > 0 THEN 'BUY '
              WHEN f.tD - f.pD < 0 THEN 'SELL' ELSE 'HOLD' END AS aD,
         f.tC - f.pC AS qC,
         CASE WHEN f.tC - f.pC > 0 THEN 'BUY '
              WHEN f.tC - f.pC < 0 THEN 'SELL' ELSE 'HOLD' END AS aC,
         f.tZ - f.pZ AS qZ,
         CASE WHEN f.tZ - f.pZ > 0 THEN 'BUY '
              WHEN f.tZ - f.pZ < 0 THEN 'SELL' ELSE 'HOLD' END AS aZ,
         GREATEST(f.dmU, f.dmO, f.dmD, f.dmC, f.dmZ) AS dmw,
         (f.as_of_long IS NOT NULL AND f.as_of_ratio IS NOT NULL
          AND f.netU IS NOT NULL AND f.netO IS NOT NULL AND f.netD IS NOT NULL
          AND f.netC IS NOT NULL AND f.netZ IS NOT NULL) AS classifiable
    FROM f
), pair AS (
  SELECT 1 AS blk, '1 SCAN_ANCHOR_ONCE  vs  RECONCILED@LATEST' AS comparison,
         aO AS x, aC AS y, qO AS qx, qC AS qy, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 2 AS blk, '2 UNANCHORED        vs  RECONCILED@LATEST' AS comparison,
         aU AS x, aC AS y, qU AS qx, qC AS qy, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 3 AS blk, '3 UNANCHORED        vs  SCAN_ANCHOR_ONCE' AS comparison,
         aU AS x, aO AS y, qU AS qx, qO AS qy, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 4 AS blk, '4 PLACEMENT DELTA: RECONCILED@WRITE vs RECONCILED@LATEST' AS comparison,
         aD AS x, aC AS y, qD AS qx, qC AS qy, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 5 AS blk, '5 TIMING_STRESS (arbitrary): @LATEST vs @2x' AS comparison,
         aC AS x, aZ AS y, qC AS qx, qZ AS qy, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
)
SELECT comparison,
       count(*) AS dM_events,
       round((100.0 * count(*) FILTER (WHERE x = y) / NULLIF(count(*), 0))::numeric, 2)
         AS ACTION_AGREEMENT_PCT,
       round((100.0 * sum(dmw) FILTER (WHERE x = y) / NULLIF(sum(dmw), 0))::numeric, 2)
         AS dM_WEIGHTED_AGREEMENT_PCT,
       round(avg(abs(qx - qy))::numeric, 2) AS quantity_MAE_shares,
       round((100.0 * sum(abs(qx - qy)) / NULLIF(sum(abs(qy)), 0))::numeric, 2)
         AS QTY_ERR_PCT_OF_INTENDED,
       count(*) FILTER (WHERE (x = 'BUY ' AND y = 'SELL')
                           OR (x = 'SELL' AND y = 'BUY ')) AS BUY_SELL_INVERSIONS,
       round(sum(dmw) FILTER (WHERE (x = 'BUY ' AND y = 'SELL')
                                 OR (x = 'SELL' AND y = 'BUY '))::numeric, 0)
         AS INVERSION_dM_SHARES
  FROM pair GROUP BY blk, comparison ORDER BY blk, comparison;

