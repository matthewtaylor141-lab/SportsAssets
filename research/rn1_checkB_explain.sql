-- ============================================================================
-- CHECK B PRE-FLIGHT: PLAN ONLY. Nothing here is executed.
--
-- GENERATED from research/rn1_checkB_states.sql by prefixing each statement
-- with EXPLAIN. Do not hand-edit: regenerate it, so the two files cannot
-- drift and a plan proved here is the plan the real run will use.
--
-- WHY IT EXISTS. Check A cost three twenty-minute timeouts because the plan
-- was never looked at. Run 36 planned it in ten seconds and the cause was a
-- nested-loop rescan nobody had guessed -- the planner estimated a CTE at ONE
-- row when the true figure was ~356,000, and re-ran a whole sort-and-window
-- pass once per outer row. Check B's statement 2 has the same ingredients: a
-- CTE union the planner cannot estimate, feeding window aggregates.
--
-- SO THIS RUNS FIRST, and it answers two questions for a few seconds of
-- compute:
--   1  DO ALL THE NAMES RESOLVE? EXPLAIN performs full parse analysis, so a
--      misspelled column or an ambiguous reference fails HERE, cheaply,
--      instead of aborting the real file under ON_ERROR_STOP after the
--      expensive statements have already burned the timeout.
--   2  IS ANY INNER SIDE GOING TO BE RESCANNED? Read each plan for a Nested
--      Loop whose inner side is a Sort/WindowAgg with no Materialize above
--      it, and for row estimates of 1 on anything built from the canonical
--      feed CASE. Either is the check A failure returning.
--
-- The local pglast parse already proved the file is five pure SELECTs under
-- the PostgreSQL 16 grammar. A grammar parse does not resolve names against
-- the catalogue; this does.
-- ============================================================================

\echo '== PLAN ONLY -- 1. COVERAGE: designation and anchor reach over the dM population =='
-- dM here is computed from outcome_index only, so a single-leg condition
-- yields LEAST(cy,cn) = 0 for every row and drops out on its own.
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
), dm AS (
  SELECT condition_id, ts, px,
         GREATEST(LEAST(cy, cn) - LEAST(cy - y_now, cn - n_now), 0) AS d_m
    FROM (
      SELECT condition_id, ts, px,
             CASE WHEN outcome_index = 0 THEN sh ELSE 0 END AS y_now,
             CASE WHEN outcome_index = 1 THEN sh ELSE 0 END AS n_now,
             sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) OVER w AS cy,
             sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) OVER w AS cn
        FROM canon
      WINDOW w AS (PARTITION BY condition_id ORDER BY ts, id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) s
), dsg AS (
  SELECT condition_id, min(at) AS first_desig FROM (
    SELECT condition_id, opened_at AS at FROM mirror_books WHERE long_asset IS NOT NULL
    UNION ALL SELECT condition_id, at FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
    UNION ALL SELECT condition_id, at FROM mirror_shadow WHERE long_asset IS NOT NULL) q
  GROUP BY condition_id
), anc AS (
  SELECT condition_id, min(at) AS first_anchor,
         bool_or(snap_long = 0 AND snap_other = 0) AS ever_anchored_zero
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
   GROUP BY 1
)
SELECT CASE WHEN a.first_anchor IS NOT NULL AND d.ts >= a.first_anchor
              THEN 'A STATE_ANCHORED (independent snapshot at or before the event)'
            WHEN g.first_desig IS NOT NULL AND d.ts >= g.first_desig
              THEN 'B COVERAGE_SUPPORTED_UNANCHORED (causal designation, no anchor yet)'
            WHEN g.first_desig IS NOT NULL
              THEN 'C DESIGNATION EXISTS BUT IS LATER THAN THE EVENT (refused, not used)'
            ELSE 'D DESIGNATION_UNKNOWN (none ever recorded)' END AS reach,
       count(*) AS dM_events,
       count(DISTINCT d.condition_id) AS conditions,
       count(*) FILTER (WHERE a.ever_anchored_zero) AS events_in_ANCHORED_ZERO_conditions,
       round(sum(d.d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d.d_m) / NULLIF(sum(sum(d.d_m)) OVER (), 0))::numeric, 2) AS PCT_dM,
       round(sum(d.d_m * d.px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(d.d_m * d.px) / NULLIF(sum(sum(d.d_m * d.px)) OVER (), 0))::numeric, 2)
         AS PCT_matched_notional
  FROM dm d
  LEFT JOIN dsg g ON g.condition_id = d.condition_id
  LEFT JOIN anc a ON a.condition_id = d.condition_id
 WHERE d.ts >= timestamptz '2026-08-05 00:00Z' AND d.d_m > 0.000001
 GROUP BY 1 ORDER BY 1;

\echo '== PLAN ONLY -- 2. CONTINUOUS_TARGET_STATE: action mix, designation source, algebra test =='
-- ONE pass over the merged stream serves all three report blocks. They were
-- three separate statements in the draft, each rebuilding the same ~620k-row
-- stream and re-sorting it; check A's lesson is that the plan shape, not the
-- volume, is what costs, and three identical window passes is three times the
-- plan. Blocks differ only in their filter, so they are a UNION ALL of labels
-- over one computation -- the same shape check A used for its cohorts.
--   block 1 ACTION   the headline mix, dM-generating events in the window
--   block 2 SOURCE   the same events split by which table named the long token
--   block 3 ALGEBRA  sign(required_change) vs sign(rn1_net_after - before),
--                    over ALL causally designated events, not only dM ones
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
  -- Leg IDENTITY, keyed on outcome_index so the two counters can never be the
  -- same token. A condition with one traded leg leaves the other NULL, which
  -- is then simply never equal to as_of_long -- no phantom matched inventory.
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), desig_rows AS (
  -- ratio is emitted ONLY by the tables that actually record one. Refusals
  -- carry a designation but no ratio, so they emit NULL and inherit the last
  -- observed ratio through a SEPARATE carry-forward below.
  SELECT condition_id, opened_at AS at, long_asset, ratio,
         'A book, map_source=' || COALESCE(map_source, '?') AS src
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, NULL::float8,
         CASE WHEN long_from = 'catalogue' THEN 'C refusal, CATALOGUE_LONG'
              ELSE 'B refusal, mapper-named' END
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, at, long_asset, ratio, 'D shadow row (_choose_long)'
    FROM mirror_shadow WHERE long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev,
         long_asset, ratio, src, NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM desig_rows
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, NULL, outcome_index, sh, px FROM canon
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
  -- TWO carry groups, deliberately. gd advances on each designation row, gr
  -- only on rows that carry a ratio, so a refusal can change the designation
  -- without resetting the ratio to an invented constant.
  SELECT r.*,
         first_value(r.long_asset) OVER wd AS as_of_long,
         first_value(r.src)        OVER wd AS as_of_src,
         first_value(r.ratio)      OVER wr AS as_of_ratio
    FROM run r
  WINDOW wd AS (PARTITION BY r.condition_id, r.gd ORDER BY r.ts, r.pri, r.ev),
         wr AS (PARTITION BY r.condition_id, r.gr ORDER BY r.ts, r.pri, r.ev)
), evr AS (
  -- Pre-fill totals are the running totals minus this row's own contribution:
  -- no lag(), no second sort.
  SELECT c.*,
         c.cy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.cn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL
), st AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         CASE WHEN e.as_of_long = e.ay THEN e.cy - e.cn
              WHEN e.as_of_long = e.an THEN e.cn - e.cy END AS net_after,
         CASE WHEN e.as_of_long = e.ay THEN e.py - e.pn
              WHEN e.as_of_long = e.an THEN e.pn - e.py END AS net_before
    FROM evr e
), f AS (
  SELECT st.*,
         trunc(st.as_of_ratio * st.net_after) AS tgt_after,
         COALESCE(lag(trunc(st.as_of_ratio * st.net_after)) OVER w,
                  trunc(st.as_of_ratio * st.net_before)) AS pos_before,
         lag(st.as_of_long)  OVER w AS prev_long,
         lag(st.as_of_ratio) OVER w AS prev_ratio,
         lag(st.ev)          OVER w AS prev_ev
    FROM st WINDOW w AS (PARTITION BY st.condition_id ORDER BY st.ts, st.ev)
), g AS (
  SELECT f.*,
         CASE WHEN f.as_of_long  IS NULL THEN '0 DESIGNATION_UNKNOWN (no causal long_asset)'
              WHEN f.net_after   IS NULL THEN '4 DESIGNATED ASSET IS NEITHER TRADED LEG'
              WHEN f.as_of_ratio IS NULL THEN '5 RATIO_UNKNOWN (designated, ratio never observed)'
              WHEN f.tgt_after - f.pos_before > 0 THEN '1 BUY'
              WHEN f.tgt_after - f.pos_before < 0 THEN '2 SELL'
              ELSE '3 HOLD (target move under one whole share)' END AS action,
         CASE WHEN f.net_after IS NULL OR f.as_of_ratio IS NULL
                THEN 'E NOT CLASSIFIABLE (no target could be formed)'
              WHEN sign(f.tgt_after - f.pos_before) = sign(f.net_after - f.net_before)
                THEN 'A AGREES'
              WHEN f.tgt_after - f.pos_before = 0
                THEN 'B HOLD from whole-share truncation (expected)'
              WHEN f.prev_ev IS NOT NULL AND f.prev_long IS DISTINCT FROM f.as_of_long
                THEN 'C NON-HOLD DISAGREEMENT: DESIGNATION_CHANGED (explained)'
              WHEN f.prev_ev IS NOT NULL AND f.prev_ratio IS DISTINCT FROM f.as_of_ratio
                THEN 'C NON-HOLD DISAGREEMENT: RATIO_CHANGED (explained)'
              ELSE 'D NON-HOLD DISAGREEMENT: UNEXPLAINED -- TREAT AS A BUG UNTIL RECONCILED'
         END AS verdict
    FROM f
), lab AS (
  SELECT 1 AS blk, '1 ACTION   ' || action AS label, d_m, px FROM g
   WHERE ts >= timestamptz '2026-08-05 00:00Z' AND d_m > 0.000001
  UNION ALL
  SELECT 2, '2 SOURCE   ' || as_of_src || '  ->  ' || action, d_m, px FROM g
   WHERE ts >= timestamptz '2026-08-05 00:00Z' AND d_m > 0.000001 AND as_of_long IS NOT NULL
  UNION ALL
  SELECT 3, '3 ALGEBRA  ' || verdict, d_m, px FROM g
   WHERE ts >= timestamptz '2026-08-05 00:00Z' AND as_of_long IS NOT NULL
)
SELECT label,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY blk))::numeric, 2)
         AS PCT_EVENTS_IN_BLOCK,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (PARTITION BY blk), 0))::numeric, 2)
         AS PCT_dM_IN_BLOCK,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM lab GROUP BY blk, label ORDER BY blk, label;

\echo '== PLAN ONLY -- 3. ANCHORED SUBSET as validation: as-of snapshot, forward replay only =='
-- Restricted FIRST to conditions that actually have an independent snapshot,
-- so the population is ~4% and the plan cannot blow up.
--
-- THE ANCHOR IS AS-OF, NOT FIXED. At every snapshot row the seed is rebased:
--     seed := snap - running_fills_at_that_row
-- and state is then always seed + running_fills. Because the seed is carried
-- forward only until the NEXT snapshot rebases it, state restarts from the
-- most recent independent observation instead of compounding one stale
-- snapshot across unknown intervening activity. Anchor age is reported.
--
-- Nothing before an anchor is reconstructed from it: events with no snapshot
-- at or before them are dropped (ga = 0), not back-filled. The snapshot is not
-- required to be zero -- a nonzero independent observation is a perfectly
-- valid starting state.
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
), snaps AS (
  SELECT condition_id, at, long_asset AS anchor_long, snap_long, snap_other, ratio
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev,
         anchor_long, snap_long, snap_other, ratio, at AS anchor_ts,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM snaps
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, NULL, NULL, NULL::timestamptz,
         outcome_index, sh, px FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.anchor_ts) OVER wc AS ga,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS fy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS fn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), seeded AS (
  SELECT r.*,
         CASE WHEN r.anchor_ts IS NULL THEN NULL
              WHEN r.anchor_long = r.ay THEN r.snap_long  - r.fy
              WHEN r.anchor_long = r.an THEN r.snap_other - r.fy END AS seed_y_row,
         CASE WHEN r.anchor_ts IS NULL THEN NULL
              WHEN r.anchor_long = r.ay THEN r.snap_other - r.fn
              WHEN r.anchor_long = r.an THEN r.snap_long  - r.fn END AS seed_n_row
    FROM run r
), carried AS (
  SELECT s.*,
         first_value(s.seed_y_row)  OVER wa AS seed_y,
         first_value(s.seed_n_row)  OVER wa AS seed_n,
         first_value(s.anchor_ts)   OVER wa AS as_of_anchor,
         first_value(s.anchor_long) OVER wa AS as_of_long,
         first_value(s.ratio)       OVER wa AS as_of_ratio
    FROM seeded s
  WINDOW wa AS (PARTITION BY s.condition_id, s.ga ORDER BY s.ts, s.pri, s.ev)
), evr AS (
  SELECT c.*,
         c.seed_y + c.fy AS sy,
         c.seed_n + c.fn AS sn,
         c.seed_y + c.fy - CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS py,
         c.seed_n + c.fn - CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS pn
    FROM carried c WHERE c.ev IS NOT NULL AND c.ga > 0
), st AS (
  SELECT e.*,
         GREATEST(LEAST(e.sy, e.sn) - LEAST(e.py, e.pn), 0) AS d_m,
         CASE WHEN e.as_of_long = e.ay THEN e.sy - e.sn
              WHEN e.as_of_long = e.an THEN e.sn - e.sy END AS net_after,
         CASE WHEN e.as_of_long = e.ay THEN e.py - e.pn
              WHEN e.as_of_long = e.an THEN e.pn - e.py END AS net_before
    FROM evr e
), f AS (
  SELECT st.*, trunc(st.as_of_ratio * st.net_after) AS tgt_after,
         COALESCE(lag(trunc(st.as_of_ratio * st.net_after)) OVER w,
                  trunc(st.as_of_ratio * st.net_before)) AS pos_before
    FROM st WINDOW w AS (PARTITION BY st.condition_id ORDER BY st.ts, st.ev)
)
SELECT CASE WHEN net_after   IS NULL THEN '4 ANCHOR LONG IS NEITHER TRADED LEG'
            WHEN as_of_ratio IS NULL THEN '5 RATIO_UNKNOWN'
            WHEN tgt_after - pos_before > 0 THEN '1 BUY'
            WHEN tgt_after - pos_before < 0 THEN '2 SELL'
            ELSE '3 HOLD (target move under one whole share)' END AS required_action,
       count(*) AS dM_events,
       count(DISTINCT condition_id) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - as_of_anchor) / 3600.0)::numeric, 2) AS p50_anchor_age_h,
       round(percentile_cont(0.9) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - as_of_anchor) / 3600.0)::numeric, 2) AS p90_anchor_age_h
  FROM f
 WHERE d_m > 0.000001 OR net_after IS NULL OR as_of_ratio IS NULL
 GROUP BY 1 ORDER BY 1;

\echo '== PLAN ONLY -- 4. ACTUAL_STATE 09-06..09-10: corrected rule from the real book =='
-- Our booked position is an AS-OF CARRY-FORWARD over the same stream, not a
-- correlated subquery: mirror_orders has no index on us_market_slug, so the
-- per-event lookup would have scanned the table once per event.
--
-- Our fills sort with pri = 0, so a fill completing at exactly an event's
-- timestamp counts as ALREADY BOOKED at that event. That is the conservative
-- reading and it matters only on exact-millisecond collisions.
EXPLAIN
WITH bk AS (
  SELECT DISTINCT ON (condition_id) condition_id, us_market_slug AS slug,
         long_asset, ratio
    FROM mirror_books WHERE long_asset IS NOT NULL AND us_market_slug IS NOT NULL
   ORDER BY condition_id, opened_at
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN bk ON bk.condition_id = t.condition_id
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
), ours AS (
  SELECT bk.condition_id, o.done_at AS ts,
         CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END AS signed_sh
    FROM mirror_orders o JOIN bk ON bk.slug = o.us_market_slug
   WHERE o.filled > 0 AND o.done_at IS NOT NULL
), stream AS (
  SELECT condition_id, ts, 0 AS pri, NULL::bigint AS ev, signed_sh,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px FROM ours
  UNION ALL
  SELECT condition_id, ts, 1, id, 0::float8, outcome_index, sh, px FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         sum(s.signed_sh) OVER wc AS bpp,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS cy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS cn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), evr AS (
  SELECT r.*, bk.long_asset, bk.ratio,
         r.cy - CASE WHEN r.oi = 0 THEN r.sh ELSE 0 END AS py,
         r.cn - CASE WHEN r.oi = 1 THEN r.sh ELSE 0 END AS pn
    FROM run r JOIN bk ON bk.condition_id = r.condition_id
   WHERE r.ev IS NOT NULL
     AND r.ts >= timestamptz '2026-09-06 00:00Z'
     AND r.ts <  timestamptz '2026-09-11 00:00Z'
), q AS (
  SELECT e.*,
         GREATEST(LEAST(e.cy, e.cn) - LEAST(e.py, e.pn), 0) AS d_m,
         trunc(e.ratio * CASE WHEN e.long_asset = e.ay THEN e.cy - e.cn
                              WHEN e.long_asset = e.an THEN e.cn - e.cy END) AS tgt_after
    FROM evr e
)
SELECT CASE WHEN tgt_after IS NULL
              THEN '4 BOOK LONG IS NEITHER TRADED LEG, OR THE BOOK RECORDS NO RATIO'
            WHEN trunc(tgt_after - bpp) > 0 THEN '1 BUY'
            WHEN trunc(tgt_after - bpp) < 0 THEN '2 SELL'
            ELSE '3 HOLD (target move under one whole share)' END AS required_action,
       CASE WHEN abs(bpp) < 1.0 THEN 'pre-position FLAT'
            WHEN bpp > 0 THEN 'pre-position LONG'
            ELSE 'pre-position SHORT' END AS bettor_pre_position,
       count(*) AS dM_events,
       count(DISTINCT condition_id) AS conditions,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round(avg(bpp)::numeric, 1) AS mean_actual_pre_position
  FROM q
 WHERE d_m > 0.000001 OR tgt_after IS NULL
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== PLAN ONLY -- 5. STATE RECONCILIATION: independent snapshot vs fills-derived inventory =='
-- A VALIDATION DIAGNOSTIC, NOT THE ANCHOR CRITERION AND NOT A COMPLETENESS
-- PROOF. Even an exact match only shows that our cumulative fill
-- reconstruction lands on the same net the venue reports; omitted offsetting
-- fills leave that net unchanged. Report this as STATE RECONCILIATION PASSED.
-- It must never be restated as fill history proven complete.
--
-- fills_derived here is the UNANCHORED reconstruction -- cumulative RN1 fills
-- from his earliest retained fill up to the snapshot instant -- which is
-- exactly the quantity statement 2 relies on. So this measures the reliability
-- of statement 2's state, which is the reason to run it.
EXPLAIN
WITH ac AS (
  SELECT DISTINCT condition_id FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
    JOIN ac ON ac.condition_id = t.condition_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY' AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.condition_id, b.ts, b.id, b.outcome_index, b.sh
    FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.cf = b.feed
), legs AS (
  SELECT condition_id,
         max(asset) FILTER (WHERE outcome_index = 0) AS ay,
         max(asset) FILTER (WHERE outcome_index = 1) AS an
    FROM base GROUP BY 1
), snaps AS (
  SELECT id AS sid, condition_id, at, long_asset AS anchor_long, snap_long, snap_other
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev, sid,
         anchor_long, snap_long, snap_other, NULL::int AS oi, 0::float8 AS sh
    FROM snaps
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL::bigint, NULL, NULL, NULL, outcome_index, sh
    FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS fy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS fn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), r AS (
  SELECT condition_id, ts, snap_long, snap_other,
         CASE WHEN anchor_long = ay THEN fy WHEN anchor_long = an THEN fn END AS fills_long,
         CASE WHEN anchor_long = ay THEN fn WHEN anchor_long = an THEN fy END AS fills_other
    FROM run WHERE sid IS NOT NULL
)
SELECT CASE WHEN fills_long IS NULL
              THEN 'E SNAPSHOT LONG IS NEITHER TRADED LEG (not comparable)'
            WHEN abs(snap_long - fills_long) < 0.5
             AND abs(snap_other - fills_other) < 0.5
              THEN 'A EXACT MATCH -- state reconciliation passed (NOT a completeness proof)'
            WHEN abs(snap_long  - fills_long)  <= GREATEST(10.0, 0.05 * abs(snap_long))
             AND abs(snap_other - fills_other) <= GREATEST(10.0, 0.05 * abs(snap_other))
              THEN 'B SMALL MISMATCH (within 10 shares or 5% on both legs)'
            ELSE 'C MATERIAL MISMATCH' END AS reconciliation,
       count(*) AS snapshots,
       count(DISTINCT condition_id) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_snapshots,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY snap_long - fills_long)::numeric, 1)
         AS p50_long_diff,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY snap_other - fills_other)::numeric, 1)
         AS p50_other_diff,
       round(max(abs(snap_long - fills_long))::numeric, 1) AS max_abs_long_diff,
       round(max(abs(snap_other - fills_other))::numeric, 1) AS max_abs_other_diff
  FROM r GROUP BY 1 ORDER BY 1;

