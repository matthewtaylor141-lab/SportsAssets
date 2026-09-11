-- ============================================================================
-- CHECK B: two state machines, initialized by an independent STATE ANCHOR
-- (2026-09-11, read-only.)
--
-- THE TWO STATE MACHINES, never mixed:
--   CONTINUOUS_TARGET_STATE   what a corrected mirror CONTINUOUSLY TRACKING the
--       signed target would request. cf state propagates in RN1 event order,
--       cf_position_after := cf_target_after.
--   ACTUAL_STATE              what a corrected decision would have requested
--       FROM THE HISTORICAL STATE THE DEFECTIVE PRODUCTION SYSTEM ACTUALLY
--       CREATED. Confined to 09-06..09-10.
-- NEITHER IS THE A->D P&L COUNTERFACTUAL. Both classify a REQUESTED action;
-- execution -- depth, fills, slippage -- comes later and is not modelled here.
--
-- INITIALIZATION IS AN ANCHOR, NOT A COMPLETENESS PROOF. The previous version
-- treated snap_long == his_long as evidence that every earlier fill had been
-- captured. That inference is WRONG: OMITTED OFFSETTING FILLS LEAVE THE SAME
-- NET INVENTORY, so agreement cannot certify fill history. What an independent
-- snapshot DOES establish is the only thing actually needed -- RN1's inventory
-- AT t_anchor:
--     rn1_long_state(t_anchor)  = snap_long
--     rn1_other_state(t_anchor) = snap_other
-- and the replay runs FORWARD from there. Nothing before the anchor is
-- reconstructed or certified from it. The anchor does NOT need to be zero: an
-- independently observed nonzero position is a perfectly valid starting state.
--
--   STATE_ANCHORED                 an independent snapshot exists at or before
--                                  the event and initializes the replay
--   ANCHORED_ZERO                  a STATE_ANCHORED special case where the
--                                  observed snapshot is flat -- noted, never
--                                  required
--   COVERAGE_SUPPORTED_UNANCHORED  no anchor; first retained fill is after the
--                                  calendar-complete boundary. Weaker, and NOT
--                                  promoted to known
--   LEFT_CENSORED                  exposure may predate complete coverage
--   UNKNOWN                        insufficient evidence
--
-- WHERE ANCHORS COME FROM. mirror_shadow is append-only (id BIGSERIAL, at
-- DEFAULT now()) and carries per row condition_id, long_asset, other_asset,
-- his_long/his_other -- mi.net_positions(OUR fills), which prove nothing about
-- themselves -- and snap_long/snap_other from _snapshot(), a genuine
-- independent read. It is therefore a TIME SERIES of independent snapshots,
-- which is what makes forward replay possible; mirror_books keeps the same pair
-- but only as of its last tick, which would anchor at the END of the window.
--
-- STALENESS IS BOUNDED, NOT ASSUMED AWAY. A snapshot is not reused indefinitely
-- across unknown intervening activity: every event carries
-- anchor_age = event_ts - anchor_ts, the distribution is reported in statement
-- 4, and statement 5 cuts a population at a stated freshness cap beside the
-- uncapped one.
--
-- SNAP VERSUS FILLS IS A VALIDATION DIAGNOSTIC, NOT THE ANCHOR CRITERION.
-- Statement 3 reports snap_long - his_long and snap_other - his_other by exact
-- / small / material mismatch, to say how reliable the cumulative fill
-- reconstruction is. Even exact agreement is reported as STATE RECONCILIATION
-- PASSED, never as fill history proven complete.
--
-- THERE IS NO MARKET-OPEN METADATA. Statement 2a proves from information_schema
-- that `markets` has no creation/open/start column -- updated_at is our own
-- upsert clock -- so "the condition did not exist before capture was complete"
-- cannot be tested from stored data. The anchor replaces that route entirely.
--
-- DESIGNATION IS TIME-CAUSAL: designation_at <= event_at, by LATERAL over
-- mirror_books, mirror_candidate_refusals AND mirror_shadow (whose rows are
-- timestamped designations too). Where none exists the action stays
-- DESIGNATION_UNKNOWN, never synthesised from a later row.
--
-- GRANULARITY, from code. analytics/mirror.py:280 target_shares:
--     tgt = int(raw) if raw >= 0 else -int(-raw)      # toward zero, whole shares
-- Postgres trunc() matches exactly, so HOLD is a real venue state, not a band.
--
-- Read-only: eight SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. FEED COVERAGE: where does retained capture become calendar-complete? =='
SELECT t.source,
       to_char(min(t.ts), 'YYYY-MM-DD HH24:MI') AS first_ts,
       to_char(max(t.ts), 'YYYY-MM-DD HH24:MI') AS last_ts,
       count(*) AS fills,
       count(DISTINCT t.ts::date) AS distinct_days,
       (max(t.ts)::date - min(t.ts)::date) + 1 AS span_days,
       ((max(t.ts)::date - min(t.ts)::date) + 1) - count(DISTINCT t.ts::date)
         AS MISSING_DAYS_INSIDE_ITS_OWN_WINDOW
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
   AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
 GROUP BY 1 ORDER BY 2;


\echo '== 2a. MARKET-OPEN METADATA: does any creation/start column exist? =='
SELECT c.table_name, c.column_name, c.data_type,
       (c.column_name ~* '(creat|open|start|begin|launch)') AS looks_like_market_open
  FROM information_schema.columns c
 WHERE c.table_schema = 'public' AND c.table_name IN ('markets', 'market_tokens')
 ORDER BY c.table_name, c.ordinal_position;


\echo '== 2b. ANCHOR SUPPLY: the independent snapshot time series =='
SELECT 'mirror_shadow (append-only series)' AS source,
       count(*) AS rows,
       count(*) FILTER (WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL)
         AS rows_with_independent_snapshot,
       count(DISTINCT condition_id) FILTER (WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL)
         AS conditions_with_an_anchor,
       count(*) FILTER (WHERE snap_long = 0 AND snap_other = 0) AS anchored_zero_rows,
       to_char(min(at) FILTER (WHERE snap_long IS NOT NULL), 'MM-DD HH24:MI') AS first_anchor,
       to_char(max(at) FILTER (WHERE snap_long IS NOT NULL), 'MM-DD HH24:MI') AS last_anchor
  FROM mirror_shadow
UNION ALL
SELECT 'mirror_books (latest value only)', count(*),
       count(*) FILTER (WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL),
       count(DISTINCT condition_id) FILTER (WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL),
       count(*) FILTER (WHERE snap_long = 0 AND snap_other = 0),
       to_char(min(updated_at) FILTER (WHERE snap_long IS NOT NULL), 'MM-DD HH24:MI'),
       to_char(max(updated_at) FILTER (WHERE snap_long IS NOT NULL), 'MM-DD HH24:MI')
  FROM mirror_books;


\echo '== 3. VALIDATION DIAGNOSTIC: state reconciliation, NOT completeness proof =='
SELECT CASE WHEN abs(snap_long - COALESCE(his_long, 0)) < 0.000001
             AND abs(snap_other - COALESCE(his_other, 0)) < 0.000001
              THEN 'A EXACT (state reconciliation passed)'
            WHEN abs(snap_long - COALESCE(his_long, 0)) <= GREATEST(1.0, 0.005 * abs(snap_long))
             AND abs(snap_other - COALESCE(his_other, 0)) <= GREATEST(1.0, 0.005 * abs(snap_other))
              THEN 'B SMALL MISMATCH (within 1 share or 0.5%)'
            ELSE 'C MATERIAL MISMATCH (reconstruction unreliable here)' END AS reconciliation,
       count(*) AS snapshot_rows,
       count(DISTINCT condition_id) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_rows,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY snap_long - COALESCE(his_long, 0))::numeric, 2) AS p50_snap_minus_fills_long,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY snap_other - COALESCE(his_other, 0))::numeric, 2) AS p50_snap_minus_fills_other,
       round(percentile_cont(0.95) WITHIN GROUP (
         ORDER BY abs(snap_long - COALESCE(his_long, 0)))::numeric, 2) AS p95_abs_gap_long
  FROM mirror_shadow
 WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL
 GROUP BY 1 ORDER BY 1;


\echo '== 4. ANCHOR CLASS AND FRESHNESS over the dM population =='
WITH anc AS (
  SELECT condition_id, at AS anchor_ts, snap_long, snap_other
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
), f AS (
  SELECT t.condition_id, t.ts, t.tx_hash, t.asset, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), pc AS (SELECT condition_id, min(ts) AS first_fill FROM f GROUP BY 1
), gs AS (SELECT min(ts) AS data_start FROM f
), rn AS (
  SELECT f.condition_id, f.ts, f.px, f.tx_hash, f.asset,
         sum(CASE WHEN f.outcome_index = 0 THEN f.sh ELSE 0 END)
           OVER (PARTITION BY f.condition_id ORDER BY f.ts, f.tx_hash, f.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN f.outcome_index = 1 THEN f.sh ELSE 0 END)
           OVER (PARTITION BY f.condition_id ORDER BY f.ts, f.tx_hash, f.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM f
), dm AS (
  SELECT condition_id, ts, px,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM rn WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), ev AS (
  SELECT d.condition_id, d.ts, d.px, d.d_m, pc.first_fill,
         a.anchor_ts, a.snap_long, a.snap_other
    FROM dm d JOIN pc ON pc.condition_id = d.condition_id
    LEFT JOIN LATERAL (
      SELECT anc.anchor_ts, anc.snap_long, anc.snap_other FROM anc
       WHERE anc.condition_id = d.condition_id AND anc.anchor_ts <= d.ts
       ORDER BY anc.anchor_ts DESC LIMIT 1) a ON TRUE
   WHERE d.ts >= timestamptz '2026-08-05 00:00Z' AND d.d_m > 0.000001
)
SELECT CASE WHEN anchor_ts IS NOT NULL AND snap_long = 0 AND snap_other = 0
              THEN 'A1 ANCHORED_ZERO (independent flat snapshot)'
            WHEN anchor_ts IS NOT NULL
              THEN 'A2 STATE_ANCHORED (independent nonzero snapshot)'
            WHEN first_fill >= timestamptz '2026-07-24 00:00Z'
              THEN 'B COVERAGE_SUPPORTED_UNANCHORED (weaker, not known)'
            WHEN first_fill <= (SELECT data_start FROM gs) + interval '24 hours'
              THEN 'D UNKNOWN (edge of all retained data)'
            ELSE 'C LEFT_CENSORED (exposure may predate coverage)' END AS anchor_class,
       count(*) AS dM_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(d_m * px) / NULLIF(sum(sum(d_m * px)) OVER (), 0))::numeric, 2)
         AS PCT_matched_notional,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - anchor_ts) / 3600.0)::numeric, 2) AS p50_anchor_age_h,
       round(percentile_cont(0.95) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - anchor_ts) / 3600.0)::numeric, 2) AS p95_anchor_age_h
  FROM ev GROUP BY 1 ORDER BY 1;


\echo '== 5. HEADLINE: CONTINUOUS action mix, anchored forward replay vs broader =='
-- The anchored population replays FORWARD from the independent snapshot:
-- inventory at t_anchor IS snap_long/snap_other, and only fills after t_anchor
-- are applied on top. Nothing before the anchor is reconstructed from it.
WITH desig_rows AS (
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10) AS ratio, opened_at AS at
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10), at
    FROM mirror_shadow WHERE long_asset IS NOT NULL
), ea AS (
  -- ONE anchor per condition: the latest at or before the window start, so the
  -- whole analysed interval replays forward from a single observed state.
  SELECT DISTINCT ON (condition_id) condition_id, at AS anchor_ts,
         snap_long, snap_other, long_asset AS anchor_long
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
     AND at <= timestamptz '2026-08-05 00:00Z'
   ORDER BY condition_id, at DESC
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2, min(ts) AS first_fill
    FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px,
         l.a1, l.a2, l.first_fill, ea.anchor_ts,
         CASE WHEN ea.anchor_ts IS NULL THEN 0.0
              WHEN ea.anchor_long = l.a1 THEN ea.snap_long ELSE ea.snap_other END
         + sum(CASE WHEN c.asset = l.a1
                     AND (ea.anchor_ts IS NULL OR c.ts > ea.anchor_ts) THEN c.sh ELSE 0 END) OVER w
           AS cum1,
         CASE WHEN ea.anchor_ts IS NULL THEN 0.0
              WHEN ea.anchor_long = l.a1 THEN ea.snap_other ELSE ea.snap_long END
         + sum(CASE WHEN c.asset = l.a2
                     AND (ea.anchor_ts IS NULL OR c.ts > ea.anchor_ts) THEN c.sh ELSE 0 END) OVER w
           AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
    LEFT JOIN ea ON ea.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.ratio,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), t AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END AS net_after,
         CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END AS net_before
    FROM ev e
), c AS (
  SELECT t.*, trunc(t.ratio * t.net_after) AS cf_target_after,
         COALESCE(lag(trunc(t.ratio * t.net_after)) OVER w,
                  trunc(t.ratio * t.net_before)) AS cf_position_before,
         extract(epoch FROM t.ts - t.anchor_ts) / 3600.0 AS anchor_age_h
    FROM t WINDOW w AS (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset)
), u AS (
  SELECT 'A HEADLINE STATE_ANCHORED + causal designation' AS population, * FROM c
   WHERE long_asset IS NOT NULL AND anchor_ts IS NOT NULL
  UNION ALL
  SELECT 'B STATE_ANCHORED + causal + anchor fresher than 24 h', * FROM c
   WHERE long_asset IS NOT NULL AND anchor_ts IS NOT NULL AND anchor_age_h <= 24
  UNION ALL
  SELECT 'C broader: causal designation, UNANCHORED coverage-supported', * FROM c
   WHERE long_asset IS NOT NULL AND anchor_ts IS NULL
     AND first_fill >= timestamptz '2026-07-24 00:00Z'
  UNION ALL
  SELECT 'D sensitivity: every causally designated event', * FROM c
   WHERE long_asset IS NOT NULL
)
SELECT population,
       CASE WHEN cf_target_after - cf_position_before > 0 THEN '1 BUY'
            WHEN cf_target_after - cf_position_before < 0 THEN '2 SELL'
            ELSE '3 HOLD' END AS required_action,
       count(*) AS dM_events,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY population))::numeric, 2)
         AS pct_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (PARTITION BY population), 0))::numeric, 2)
         AS PCT_dM_within_population,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM u WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 6. ACTUAL_STATE 09-06..09-10: corrected rule from the real book =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, COALESCE(ratio, 0.10) AS ratio,
         opened_at AS at FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_market_slug, COALESCE(ratio, 0.10), at
    FROM mirror_shadow WHERE long_asset IS NOT NULL
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2 FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px, l.a1, l.a2,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.ratio, d.slug,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio, dr.slug FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-09-06 00:00Z' AND c.ts < timestamptz '2026-09-11 00:00Z'
), a AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         trunc(e.ratio * CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
                              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END) AS tgt_after,
         COALESCE((SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
                     FROM mirror_orders o
                    WHERE o.us_market_slug = e.slug AND o.filled > 0
                      AND o.done_at IS NOT NULL AND o.done_at <= e.ts), 0) AS bpp
    FROM ev e
)
SELECT CASE WHEN long_asset IS NULL THEN '0 DESIGNATION_UNKNOWN'
            WHEN trunc(tgt_after - bpp) > 0 THEN '1 BUY'
            WHEN trunc(tgt_after - bpp) < 0 THEN '2 SELL'
            ELSE '3 HOLD' END AS required_action,
       CASE WHEN abs(bpp) < 1.0 THEN 'pre-position FLAT'
            WHEN bpp > 0 THEN 'pre-position LONG' ELSE 'pre-position SHORT' END AS pre_position,
       count(*) AS dM_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round(avg(bpp)::numeric, 1) AS mean_actual_pre_position
  FROM a WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 7. ALGEBRA TEST: sign(required_change) vs sign(net_after - net_before) =='
-- cf_position_before is the PREVIOUS event's cf_target_after via lag(), NOT
-- recomputed from this event's designation, so a designation that CHANGED
-- between two events surfaces as a disagreement rather than being smoothed
-- away. Truncation HOLDs are expected and separated. Anything left is a bug
-- until reconciled.
WITH desig_rows AS (
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10) AS ratio, opened_at AS at
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10), at
    FROM mirror_shadow WHERE long_asset IS NOT NULL
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2 FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px, l.a1, l.a2,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.ratio,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), t AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END AS net_after,
         CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END AS net_before
    FROM ev e
), c AS (
  SELECT t.*, trunc(t.ratio * t.net_after) AS cf_target_after,
         COALESCE(lag(trunc(t.ratio * t.net_after)) OVER w,
                  trunc(t.ratio * t.net_before)) AS cf_position_before,
         lag(t.long_asset) OVER w AS prev_long_asset
    FROM t WINDOW w AS (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset)
)
SELECT CASE WHEN sign(cf_target_after - cf_position_before) = sign(net_after - net_before)
              THEN 'A AGREES'
            WHEN cf_target_after - cf_position_before = 0
              THEN 'B HOLD from whole-share truncation (expected)'
            WHEN prev_long_asset IS DISTINCT FROM long_asset
              THEN 'C NON-HOLD DISAGREEMENT: DESIGNATION_CHANGED (explained)'
            ELSE 'D NON-HOLD DISAGREEMENT: UNEXPLAINED -- TREAT AS A BUG UNTIL RECONCILED'
       END AS verdict,
       count(*) AS events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM c WHERE long_asset IS NOT NULL
 GROUP BY 1 ORDER BY 1;


\echo '== 8. ANCHORED population: dM action mix by designation class =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10) AS ratio, opened_at AS at,
         'book row -- branch not recorded' AS long_from
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, 0.10, at,
         CASE WHEN long_from = 'catalogue' THEN 'catalogue'
              WHEN long_from IS NULL OR long_from = '' THEN 'mapper -- unlabelled'
              ELSE long_from END
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10), at, 'shadow row -- not recorded'
    FROM mirror_shadow WHERE long_asset IS NOT NULL
), first_desig AS (
  SELECT condition_id, min(at) AS first_at FROM desig_rows GROUP BY 1
), anc AS (
  SELECT DISTINCT ON (condition_id) condition_id, at AS anchor_ts
    FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
     AND at <= timestamptz '2026-08-05 00:00Z'
   ORDER BY condition_id, at DESC
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2 FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px, l.a1, l.a2,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), dpos AS (
  SELECT DISTINCT ON (c.condition_id) c.condition_id, c.a1,
         c.cum1 AS c1_at_desig, c.cum2 AS c2_at_desig
    FROM cum c JOIN first_desig fd ON fd.condition_id = c.condition_id
   WHERE c.ts <= fd.first_at
   ORDER BY c.condition_id, c.ts DESC
), ev AS (
  SELECT c.*, d.long_asset, d.ratio, d.long_from, dp.c1_at_desig, dp.c2_at_desig,
         (anc.condition_id IS NOT NULL) AS anchored,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN dpos dp ON dp.condition_id = c.condition_id
    LEFT JOIN anc ON anc.condition_id = c.condition_id
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio, dr.long_from FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), t AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END AS net_after,
         CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END AS net_before
    FROM ev e
), c AS (
  SELECT t.*, trunc(t.ratio * t.net_after) AS cf_target_after,
         COALESCE(lag(trunc(t.ratio * t.net_after)) OVER w,
                  trunc(t.ratio * t.net_before)) AS cf_position_before
    FROM t WINDOW w AS (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset)
)
SELECT CASE WHEN long_from = 'catalogue' THEN '3 CATALOGUE_LONG'
            WHEN c1_at_desig IS NULL THEN '4 NO_POSITION_AT_DESIGNATION'
            WHEN (CASE WHEN long_asset = a1 THEN c1_at_desig ELSE c2_at_desig END)
               < (CASE WHEN long_asset = a1 THEN c2_at_desig ELSE c1_at_desig END)
              THEN '2 INTENT_DESIGNATED_LONG (proven)'
            ELSE '1 POSITION_CONSISTENT_LONG (not proven)' END AS designation_class,
       CASE WHEN cf_target_after - cf_position_before > 0 THEN '1 BUY'
            WHEN cf_target_after - cf_position_before < 0 THEN '2 SELL'
            ELSE '3 HOLD' END AS required_action,
       count(*) AS dM_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM
  FROM c
 WHERE d_m > 0.000001 AND long_asset IS NOT NULL AND anchored
 GROUP BY 1, 2 ORDER BY 1, 2;
