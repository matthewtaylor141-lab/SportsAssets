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

\echo '== PLAN ONLY -- 2. COVERAGE_SUPPORTED_UNANCHORED_37D: action mix, designation source, algebra =='
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

\echo '== PLAN ONLY -- 3. ANCHORED MODELS on the 09-02+ overlap: ONCE-FORWARD vs RECONCILED vs UNANCHORED =='
-- All three models over ONE population and ONE causal designation, so any
-- difference between them is a difference of STATE and nothing else.
--
-- THE EVENT SET is every causally designated RN1 fill, at or after that
-- condition's first independent snapshot, at which ANY of the three models
-- registers matched-inventory growth (GREATEST(dm2,dm3,dm4) > 0). Using one
-- model's dM to select the events would bias the comparison toward it.
--
-- Block 0 states the reach before any mix is read: how much of the population
-- every model can classify, and how much is lost to an unknown designation, an
-- unknown ratio, or a designated token that is neither traded leg.
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
  SELECT condition_id, at, long_asset AS anchor_long, snap_long, snap_other
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), stream AS (
  -- THREE row kinds in ONE causal order. pri orders them within a timestamp:
  -- a designation (0) and a snapshot (1) both take effect BEFORE an event (2)
  -- that shares their instant, so no event is ever classified with information
  -- that did not exist at it. That is the causality rule, enforced by the sort
  -- key rather than by a filter that could be written the wrong way round.
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev,
         long_asset, ratio,
         NULL::text AS anchor_long, NULL::float8 AS snap_long,
         NULL::float8 AS snap_other, NULL::timestamptz AS anchor_ts,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM desig_rows
  UNION ALL
  SELECT condition_id, at, 1, NULL, NULL, NULL,
         anchor_long, snap_long, snap_other, at, NULL, 0::float8, NULL
    FROM snaps
  UNION ALL
  SELECT condition_id, ts, 2, id, NULL, NULL, NULL, NULL, NULL, NULL,
         outcome_index, sh, px
    FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         count(s.anchor_ts)  OVER wc AS ga,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS fy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS fn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), seeded AS (
  -- A snapshot's seed is the OFFSET that makes the replay reproduce it:
  --     seed := observed_snapshot - running_fills_at_that_instant
  -- so that state = seed + running_fills at every later row. Carrying the
  -- seed rather than the state is what lets one window pass express both
  -- anchoring policies.
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
         first_value(s.long_asset) OVER wd AS as_of_long,
         first_value(s.ratio)      OVER wr AS as_of_ratio,
         -- SNAPSHOT_RECONCILED: group on ga, which advances at EVERY snapshot,
         -- so the seed is replaced by each new independent observation.
         first_value(s.seed_y_row) OVER wa AS seed_y_rec,
         first_value(s.seed_n_row) OVER wa AS seed_n_rec,
         first_value(s.anchor_ts)  OVER wa AS anchor_ts_rec,
         -- ANCHOR_ONCE_FORWARD: group on (ga > 0), a single group covering
         -- every row from the FIRST snapshot onward. Its first row IS that
         -- first snapshot, so first_value pins the seed there and it never
         -- moves again. Later snapshots are observations only, never state.
         first_value(s.seed_y_row) OVER wo AS seed_y_once,
         first_value(s.seed_n_row) OVER wo AS seed_n_once,
         first_value(s.anchor_ts)  OVER wo AS anchor_ts_once
    FROM seeded s
  WINDOW wd AS (PARTITION BY s.condition_id, s.gd        ORDER BY s.ts, s.pri, s.ev),
         wr AS (PARTITION BY s.condition_id, s.gr        ORDER BY s.ts, s.pri, s.ev),
         wa AS (PARTITION BY s.condition_id, s.ga        ORDER BY s.ts, s.pri, s.ev),
         wo AS (PARTITION BY s.condition_id, (s.ga > 0)  ORDER BY s.ts, s.pri, s.ev)
), sta AS (
  SELECT c.*,
         CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS dy,
         CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS dn,
         c.fy                 AS y4, c.fn                 AS n4,
         c.seed_y_once + c.fy AS y2, c.seed_n_once + c.fn AS n2,
         c.seed_y_rec  + c.fy AS y3, c.seed_n_rec  + c.fn AS n3
    FROM carried c WHERE c.ev IS NOT NULL AND c.ga > 0
), q AS (
  SELECT s.*,
         GREATEST(LEAST(s.y2, s.n2) - LEAST(s.y2 - s.dy, s.n2 - s.dn), 0) AS dm2,
         GREATEST(LEAST(s.y3, s.n3) - LEAST(s.y3 - s.dy, s.n3 - s.dn), 0) AS dm3,
         GREATEST(LEAST(s.y4, s.n4) - LEAST(s.y4 - s.dy, s.n4 - s.dn), 0) AS dm4,
         CASE WHEN s.as_of_long = s.ay THEN s.y2 - s.n2
              WHEN s.as_of_long = s.an THEN s.n2 - s.y2 END AS net2,
         CASE WHEN s.as_of_long = s.ay THEN s.y3 - s.n3
              WHEN s.as_of_long = s.an THEN s.n3 - s.y3 END AS net3,
         CASE WHEN s.as_of_long = s.ay THEN s.y4 - s.n4
              WHEN s.as_of_long = s.an THEN s.n4 - s.y4 END AS net4,
         CASE WHEN s.as_of_long = s.ay THEN (s.y2 - s.dy) - (s.n2 - s.dn)
              WHEN s.as_of_long = s.an THEN (s.n2 - s.dn) - (s.y2 - s.dy) END AS pre2,
         CASE WHEN s.as_of_long = s.ay THEN (s.y3 - s.dy) - (s.n3 - s.dn)
              WHEN s.as_of_long = s.an THEN (s.n3 - s.dn) - (s.y3 - s.dy) END AS pre3,
         CASE WHEN s.as_of_long = s.ay THEN (s.y4 - s.dy) - (s.n4 - s.dn)
              WHEN s.as_of_long = s.an THEN (s.n4 - s.dn) - (s.y4 - s.dy) END AS pre4
    FROM sta s
), f AS (
  -- Each model propagates ITS OWN cf position: pos_before is that model's
  -- previous cf_target_after, never recomputed from this event.
  SELECT q.*,
         trunc(q.as_of_ratio * q.net2) AS t2,
         trunc(q.as_of_ratio * q.net3) AS t3,
         trunc(q.as_of_ratio * q.net4) AS t4,
         COALESCE(lag(trunc(q.as_of_ratio * q.net2)) OVER w,
                  trunc(q.as_of_ratio * q.pre2)) AS p2,
         COALESCE(lag(trunc(q.as_of_ratio * q.net3)) OVER w,
                  trunc(q.as_of_ratio * q.pre3)) AS p3,
         COALESCE(lag(trunc(q.as_of_ratio * q.net4)) OVER w,
                  trunc(q.as_of_ratio * q.pre4)) AS p4
    FROM q WINDOW w AS (PARTITION BY q.condition_id ORDER BY q.ts, q.ev)
), a AS (
  SELECT f.*,
         f.t2 - f.p2 AS qty2, f.t3 - f.p3 AS qty3, f.t4 - f.p4 AS qty4,
         CASE WHEN f.t2 - f.p2 > 0 THEN 'BUY '
              WHEN f.t2 - f.p2 < 0 THEN 'SELL' ELSE 'HOLD' END AS a2,
         CASE WHEN f.t3 - f.p3 > 0 THEN 'BUY '
              WHEN f.t3 - f.p3 < 0 THEN 'SELL' ELSE 'HOLD' END AS a3,
         CASE WHEN f.t4 - f.p4 > 0 THEN 'BUY '
              WHEN f.t4 - f.p4 < 0 THEN 'SELL' ELSE 'HOLD' END AS a4,
         GREATEST(f.dm2, f.dm3, f.dm4) AS dmw,
         extract(epoch FROM f.ts - f.anchor_ts_rec) / 3600.0 AS age_h,
         (f.as_of_long IS NOT NULL AND f.as_of_ratio IS NOT NULL
          AND f.net2 IS NOT NULL AND f.net3 IS NOT NULL
          AND f.net4 IS NOT NULL) AS classifiable
    FROM f
), lab AS (
  SELECT 0 AS blk, CASE WHEN classifiable
                          THEN '0 POPULATION  classifiable by ALL THREE models'
                        ELSE '0 POPULATION  NOT classifiable (designation/ratio/leg unknown)'
                   END AS label, dmw, px, condition_id FROM a
   WHERE dmw > 0.000001
  UNION ALL
  SELECT 1, '1 ANCHOR_ONCE_FORWARD                      ' || a2, dmw, px, condition_id
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 2, '2 SNAPSHOT_RECONCILED                      ' || a3, dmw, px, condition_id
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 3, '3 UNANCHORED (same 09-02+ overlap)         ' || a4, dmw, px, condition_id
    FROM a WHERE dmw > 0.000001 AND classifiable
)
SELECT label,
       count(*) AS dM_events,
       count(DISTINCT condition_id) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY blk))::numeric, 2)
         AS PCT_EVENTS_IN_BLOCK,
       round(sum(dmw)::numeric, 0) AS dM_shares,
       round((100.0 * sum(dmw) / NULLIF(sum(sum(dmw)) OVER (PARTITION BY blk), 0))::numeric, 2)
         AS PCT_dM_IN_BLOCK,
       round(sum(dmw * px)::numeric, 0) AS matched_notional
  FROM lab GROUP BY blk, label ORDER BY blk, label;

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

\echo '== PLAN ONLY -- 6. RECONSTRUCTION ERROR at each re-anchor point, by time since the previous anchor =='
-- THE DIRECT MEASUREMENT OF ACCUMULATED RECONSTRUCTION ERROR, and the reason
-- SNAPSHOT_RECONCILED exists. At every snapshot after a condition's first,
-- compare
--     predicted_state_before_reanchor = previous observed snapshot
--                                     + RN1 fills captured in between
-- against
--     observed_snapshot_state         = this independent snapshot
-- and band the discrepancy by how long the interval was. A growing error with
-- elapsed time is fill capture drifting; a flat one is a constant offset.
--
-- LONG AND OTHER ARE IN THE SNAPSHOT'S OWN FRAME. If the designated long token
-- FLIPPED between two snapshots, "long" names different tokens at each end and
-- the two are not comparable; those points get their own bucket rather than a
-- fabricated difference.
--
-- The economic version of this -- how often the error is large enough to
-- CHANGE the required action -- is statement 7's block 4, which compares
-- ANCHOR_ONCE_FORWARD against SNAPSHOT_RECONCILED at dM events in the same
-- elapsed bands. Shares are the cause; that is the cost.
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
  SELECT condition_id, at, long_asset AS anchor_long, snap_long, snap_other
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), stream AS (
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev,
         anchor_long, snap_long, snap_other, NULL::int AS oi, 0::float8 AS sh
    FROM snaps
  UNION ALL
  SELECT condition_id, ts, 1, id, NULL, NULL, NULL, outcome_index, sh FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS fy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS fn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), o AS (
  -- Snapshot rows only, with cumulative captured fills ON THAT SNAPSHOT'S OWN
  -- long and other tokens at the instant it was taken.
  SELECT condition_id, ts, anchor_long, snap_long, snap_other,
         CASE WHEN anchor_long = ay THEN fy WHEN anchor_long = an THEN fn END AS fl,
         CASE WHEN anchor_long = ay THEN fn WHEN anchor_long = an THEN fy END AS fo
    FROM run WHERE anchor_long IS NOT NULL
), g AS (
  SELECT o.*,
         lag(snap_long)  OVER w AS p_long,
         lag(snap_other) OVER w AS p_other,
         lag(fl)         OVER w AS p_fl,
         lag(fo)         OVER w AS p_fo,
         lag(ts)         OVER w AS p_ts,
         lag(anchor_long) OVER w AS p_anchor_long
    FROM o WINDOW w AS (PARTITION BY condition_id ORDER BY ts)
), e AS (
  SELECT g.*,
         (p_long  + (fl - p_fl)) - snap_long  AS err_long,
         (p_other + (fo - p_fo)) - snap_other AS err_other,
         ((p_long + (fl - p_fl)) - (p_other + (fo - p_fo)))
           - (snap_long - snap_other)         AS err_net,
         extract(epoch FROM ts - p_ts) / 3600.0 AS age_h
    FROM g WHERE p_ts IS NOT NULL
)
SELECT CASE WHEN fl IS NULL OR p_fl IS NULL
              THEN 'X SNAPSHOT LONG IS NEITHER TRADED LEG (not comparable)'
            WHEN p_anchor_long IS DISTINCT FROM anchor_long
              THEN 'Y DESIGNATED LONG FLIPPED BETWEEN ANCHORS (not comparable)'
            WHEN age_h <= 1  THEN 'A <=1h'
            WHEN age_h <= 6  THEN 'B 1-6h'
            WHEN age_h <= 24 THEN 'C 6-24h'
            ELSE                  'D >24h' END AS anchor_age_band,
       count(*) AS reanchor_points,
       count(DISTINCT condition_id) AS conditions,
       round((100.0 * count(*) FILTER (WHERE abs(err_long) < 0.5 AND abs(err_other) < 0.5)
              / NULLIF(count(*), 0))::numeric, 2) AS EXACT_STATE_MATCH_PCT,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(err_long))::numeric, 1)
         AS p50_abs_long_err,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(err_other))::numeric, 1)
         AS p50_abs_other_err,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY abs(err_long))::numeric, 1)
         AS p90_abs_long_err,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY abs(err_other))::numeric, 1)
         AS p90_abs_other_err,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY err_net)::numeric, 1)
         AS p50_SIGNED_NET_ERR,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY abs(err_net))::numeric, 1)
         AS p90_abs_net_err
  FROM e GROUP BY 1 ORDER BY 1;

\echo '== PLAN ONLY -- 7. CROSS-MODEL AGREEMENT: does the unanchored model decide the same thing? =='
-- THE VALIDATION QUESTION IS NOT WHETHER THE SHARE COUNTS MATCH. It is whether
-- the models produce THE SAME REQUIRED ACTION and approximately the same
-- REQUIRED QUANTITY at dM events. Share counts can differ by a constant offset
-- and still decide identically; they can also agree closely and still invert a
-- decision at the truncation boundary. So actions and quantities are what is
-- reported.
--
-- x is the CANDIDATE model, y the REFERENCE it is judged against:
--   block 1  ANCHOR_ONCE_FORWARD judged against SNAPSHOT_RECONCILED
--            -- the cost of accumulated reconstruction error
--   block 2  UNANCHORED judged against ANCHOR_ONCE_FORWARD
--   block 3  UNANCHORED judged against SNAPSHOT_RECONCILED
--            -- THE DECIDING BLOCK for whether the 37-day model may be used
--   block 4  block 1 again, banded by time since the previous anchor
--            -- the economic form of statement 6: how often accumulated error
--            is large enough to change BUY/SELL/HOLD at a dM event
--
-- "quantity error as % of intended order" is sum|qx - qy| / sum|qy|: the error
-- measured against the size the reference model would actually have ordered.
-- An inversion is one model saying BUY where the other says SELL -- the only
-- disagreement that trades in the wrong direction rather than the wrong size.
--
-- IF BLOCK 3 AGREES STRONGLY, the unanchored model has empirical support for
-- the earlier 37-day period. IF IT DOES NOT, action conclusions are restricted
-- to the anchored period and must be reported that way.
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
  SELECT condition_id, at, long_asset AS anchor_long, snap_long, snap_other
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), stream AS (
  -- THREE row kinds in ONE causal order. pri orders them within a timestamp:
  -- a designation (0) and a snapshot (1) both take effect BEFORE an event (2)
  -- that shares their instant, so no event is ever classified with information
  -- that did not exist at it. That is the causality rule, enforced by the sort
  -- key rather than by a filter that could be written the wrong way round.
  SELECT condition_id, at AS ts, 0 AS pri, NULL::bigint AS ev,
         long_asset, ratio,
         NULL::text AS anchor_long, NULL::float8 AS snap_long,
         NULL::float8 AS snap_other, NULL::timestamptz AS anchor_ts,
         NULL::int AS oi, 0::float8 AS sh, NULL::float8 AS px
    FROM desig_rows
  UNION ALL
  SELECT condition_id, at, 1, NULL, NULL, NULL,
         anchor_long, snap_long, snap_other, at, NULL, 0::float8, NULL
    FROM snaps
  UNION ALL
  SELECT condition_id, ts, 2, id, NULL, NULL, NULL, NULL, NULL, NULL,
         outcome_index, sh, px
    FROM canon
), run AS (
  SELECT s.*, l.ay, l.an,
         count(s.long_asset) OVER wc AS gd,
         count(s.ratio)      OVER wc AS gr,
         count(s.anchor_ts)  OVER wc AS ga,
         sum(CASE WHEN s.oi = 0 THEN s.sh ELSE 0 END) OVER wc AS fy,
         sum(CASE WHEN s.oi = 1 THEN s.sh ELSE 0 END) OVER wc AS fn
    FROM stream s JOIN legs l ON l.condition_id = s.condition_id
  WINDOW wc AS (PARTITION BY s.condition_id ORDER BY s.ts, s.pri, s.ev
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), seeded AS (
  -- A snapshot's seed is the OFFSET that makes the replay reproduce it:
  --     seed := observed_snapshot - running_fills_at_that_instant
  -- so that state = seed + running_fills at every later row. Carrying the
  -- seed rather than the state is what lets one window pass express both
  -- anchoring policies.
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
         first_value(s.long_asset) OVER wd AS as_of_long,
         first_value(s.ratio)      OVER wr AS as_of_ratio,
         -- SNAPSHOT_RECONCILED: group on ga, which advances at EVERY snapshot,
         -- so the seed is replaced by each new independent observation.
         first_value(s.seed_y_row) OVER wa AS seed_y_rec,
         first_value(s.seed_n_row) OVER wa AS seed_n_rec,
         first_value(s.anchor_ts)  OVER wa AS anchor_ts_rec,
         -- ANCHOR_ONCE_FORWARD: group on (ga > 0), a single group covering
         -- every row from the FIRST snapshot onward. Its first row IS that
         -- first snapshot, so first_value pins the seed there and it never
         -- moves again. Later snapshots are observations only, never state.
         first_value(s.seed_y_row) OVER wo AS seed_y_once,
         first_value(s.seed_n_row) OVER wo AS seed_n_once,
         first_value(s.anchor_ts)  OVER wo AS anchor_ts_once
    FROM seeded s
  WINDOW wd AS (PARTITION BY s.condition_id, s.gd        ORDER BY s.ts, s.pri, s.ev),
         wr AS (PARTITION BY s.condition_id, s.gr        ORDER BY s.ts, s.pri, s.ev),
         wa AS (PARTITION BY s.condition_id, s.ga        ORDER BY s.ts, s.pri, s.ev),
         wo AS (PARTITION BY s.condition_id, (s.ga > 0)  ORDER BY s.ts, s.pri, s.ev)
), sta AS (
  SELECT c.*,
         CASE WHEN c.oi = 0 THEN c.sh ELSE 0 END AS dy,
         CASE WHEN c.oi = 1 THEN c.sh ELSE 0 END AS dn,
         c.fy                 AS y4, c.fn                 AS n4,
         c.seed_y_once + c.fy AS y2, c.seed_n_once + c.fn AS n2,
         c.seed_y_rec  + c.fy AS y3, c.seed_n_rec  + c.fn AS n3
    FROM carried c WHERE c.ev IS NOT NULL AND c.ga > 0
), q AS (
  SELECT s.*,
         GREATEST(LEAST(s.y2, s.n2) - LEAST(s.y2 - s.dy, s.n2 - s.dn), 0) AS dm2,
         GREATEST(LEAST(s.y3, s.n3) - LEAST(s.y3 - s.dy, s.n3 - s.dn), 0) AS dm3,
         GREATEST(LEAST(s.y4, s.n4) - LEAST(s.y4 - s.dy, s.n4 - s.dn), 0) AS dm4,
         CASE WHEN s.as_of_long = s.ay THEN s.y2 - s.n2
              WHEN s.as_of_long = s.an THEN s.n2 - s.y2 END AS net2,
         CASE WHEN s.as_of_long = s.ay THEN s.y3 - s.n3
              WHEN s.as_of_long = s.an THEN s.n3 - s.y3 END AS net3,
         CASE WHEN s.as_of_long = s.ay THEN s.y4 - s.n4
              WHEN s.as_of_long = s.an THEN s.n4 - s.y4 END AS net4,
         CASE WHEN s.as_of_long = s.ay THEN (s.y2 - s.dy) - (s.n2 - s.dn)
              WHEN s.as_of_long = s.an THEN (s.n2 - s.dn) - (s.y2 - s.dy) END AS pre2,
         CASE WHEN s.as_of_long = s.ay THEN (s.y3 - s.dy) - (s.n3 - s.dn)
              WHEN s.as_of_long = s.an THEN (s.n3 - s.dn) - (s.y3 - s.dy) END AS pre3,
         CASE WHEN s.as_of_long = s.ay THEN (s.y4 - s.dy) - (s.n4 - s.dn)
              WHEN s.as_of_long = s.an THEN (s.n4 - s.dn) - (s.y4 - s.dy) END AS pre4
    FROM sta s
), f AS (
  -- Each model propagates ITS OWN cf position: pos_before is that model's
  -- previous cf_target_after, never recomputed from this event.
  SELECT q.*,
         trunc(q.as_of_ratio * q.net2) AS t2,
         trunc(q.as_of_ratio * q.net3) AS t3,
         trunc(q.as_of_ratio * q.net4) AS t4,
         COALESCE(lag(trunc(q.as_of_ratio * q.net2)) OVER w,
                  trunc(q.as_of_ratio * q.pre2)) AS p2,
         COALESCE(lag(trunc(q.as_of_ratio * q.net3)) OVER w,
                  trunc(q.as_of_ratio * q.pre3)) AS p3,
         COALESCE(lag(trunc(q.as_of_ratio * q.net4)) OVER w,
                  trunc(q.as_of_ratio * q.pre4)) AS p4
    FROM q WINDOW w AS (PARTITION BY q.condition_id ORDER BY q.ts, q.ev)
), a AS (
  SELECT f.*,
         f.t2 - f.p2 AS qty2, f.t3 - f.p3 AS qty3, f.t4 - f.p4 AS qty4,
         CASE WHEN f.t2 - f.p2 > 0 THEN 'BUY '
              WHEN f.t2 - f.p2 < 0 THEN 'SELL' ELSE 'HOLD' END AS a2,
         CASE WHEN f.t3 - f.p3 > 0 THEN 'BUY '
              WHEN f.t3 - f.p3 < 0 THEN 'SELL' ELSE 'HOLD' END AS a3,
         CASE WHEN f.t4 - f.p4 > 0 THEN 'BUY '
              WHEN f.t4 - f.p4 < 0 THEN 'SELL' ELSE 'HOLD' END AS a4,
         GREATEST(f.dm2, f.dm3, f.dm4) AS dmw,
         extract(epoch FROM f.ts - f.anchor_ts_rec) / 3600.0 AS age_h,
         (f.as_of_long IS NOT NULL AND f.as_of_ratio IS NOT NULL
          AND f.net2 IS NOT NULL AND f.net3 IS NOT NULL
          AND f.net4 IS NOT NULL) AS classifiable
    FROM f
), pair AS (
  SELECT 1 AS blk, '1 ANCHOR_ONCE_FORWARD  vs  SNAPSHOT_RECONCILED' AS comparison,
         'all' AS anchor_age_band, a2 AS x, a3 AS y, qty2 AS qx, qty3 AS qy, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 2, '2 UNANCHORED_37D_MODEL  vs  ANCHOR_ONCE_FORWARD',
         'all', a4, a2, qty4, qty2, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 3, '3 UNANCHORED_37D_MODEL  vs  SNAPSHOT_RECONCILED',
         'all', a4, a3, qty4, qty3, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
  UNION ALL
  SELECT 4, '4 ANCHOR_ONCE  vs  RECONCILED, by anchor age',
         CASE WHEN age_h IS NULL  THEN 'z unknown'
              WHEN age_h <= 1     THEN 'A <=1h'
              WHEN age_h <= 6     THEN 'B 1-6h'
              WHEN age_h <= 24    THEN 'C 6-24h'
              ELSE                     'D >24h' END,
         a2, a3, qty2, qty3, dmw
    FROM a WHERE dmw > 0.000001 AND classifiable
)
SELECT comparison, anchor_age_band,
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
         AS inversion_dM_shares
  FROM pair GROUP BY blk, comparison, anchor_age_band
 ORDER BY blk, comparison, anchor_age_band;

