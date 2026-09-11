-- ============================================================================
-- CHECK B: two state machines, gated by two validity flags
-- (2026-09-11, read-only.)
--
-- THE TWO STATE MACHINES, and they are never mixed:
--
--   CONTINUOUS_TARGET_STATE   what a corrected mirror CONTINUOUSLY TRACKING the
--       signed target would request. cf state propagates in RN1 event order,
--       cf_position_after := cf_target_after. This is the execution-feasibility
--       question over 37 days.
--   ACTUAL_STATE              what a corrected decision would have requested
--       FROM THE HISTORICAL STATE THE DEFECTIVE PRODUCTION SYSTEM ACTUALLY
--       CREATED. Confined to 09-06..09-10, the only days we truly held
--       positions.
--
-- NEITHER IS THE A->D P&L COUNTERFACTUAL. Both classify a REQUESTED action.
-- Execution -- depth, fills, slippage -- comes later and is not modelled here.
-- This pass does not reduce state for missing depth or failed fills.
--
-- VALIDITY FLAG 1, INITIALIZATION. "Earliest available fill" is NOT a known
-- zero-inventory start. Absence of an earlier fill is evidence of absence only
-- INSIDE complete retained coverage. So:
--   INIT_KNOWN          the condition's first retained fill lands at or after
--                       the coverage-complete boundary. Any earlier fill would
--                       have been retained; none exists; therefore RN1 held
--                       zero immediately before it. This is a proof, not a
--                       convention.
--   INIT_LEFT_CENSORED  the first fill predates that boundary but sits inside
--                       retained data: earlier fills MAY be missing.
--   INIT_UNKNOWN        the first fill sits at the very edge of all retained
--                       data: prior inventory may predate the record entirely.
-- Zero is never assumed. Statement 1 MEASURES where coverage actually becomes
-- complete instead of asserting it, and statement 2 classifies at two candidate
-- boundaries so the choice of boundary is visible rather than buried.
--
-- VALIDITY FLAG 2, DESIGNATION. designation_at <= event_at, always. Where no
-- causal long_asset existed by the event, the action stays DESIGNATION_UNKNOWN
-- and is NEVER synthesised from a later book or refusal row.
--
-- THE HEADLINE POPULATION is therefore causal-designation AND INIT_KNOWN. The
-- broader populations are reported beside it as sensitivities, and the excluded
-- initialization-uncertain population is carried with its own event count, dM
-- shares and matched notional so nothing disappears silently.
--
-- THE GRANULARITY, from code. analytics/mirror.py:280 in target_shares:
--     tgt = int(raw) if raw >= 0 else -int(-raw)      # toward zero, whole shares
-- The target itself is whole shares, truncated toward zero, and Postgres
-- trunc() matches that exactly. So HOLD is a real venue state -- a fill too
-- small to move the integer target -- not a tolerance band.
--
-- THE ALGEBRAIC EXPECTATION IS TESTED, NOT ASSUMED. Statement 6 checks directly
-- that sign(required_change) agrees with sign(rn1_net_after - rn1_net_before)
-- except where truncation produces HOLD. Note that cf_position_before is the
-- PREVIOUS EVENT'S cf_target_after via lag() -- not recomputed from this
-- event's designation -- precisely so that a designation that CHANGED between
-- two events shows up as a disagreement instead of being silently smoothed
-- over. Any non-HOLD disagreement is a bug or a state/designation
-- discontinuity, and statement 6 splits it by whether the designation moved.
--
-- Read-only: seven SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. EVIDENCE: where does retained coverage actually become complete? =='
-- The initialization boundary must be measured. Per feed: its window, and the
-- daily fill counts across the suspected seam, so a gap is visible as a gap.
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


\echo '== 2. VALIDITY FLAG 1: initialization class at two candidate boundaries =='
-- b1 = 2026-07-24, the venue poll feed's start: the first moment a continuous
--      feed exists, so absence of an earlier fill is informative from there.
-- b2 = 2026-08-10, the chain feed's start: stricter, two independent feeds.
WITH f AS (
  SELECT t.condition_id, t.ts, t.size::float8 AS sh, t.price::float8 AS px,
         t.tx_hash, t.asset, t.outcome_index
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), pc AS (
  SELECT condition_id, min(ts) AS first_fill, max(ts) AS last_fill FROM f GROUP BY 1
), gs AS (SELECT min(ts) AS data_start FROM f
), rn AS (
  SELECT f.condition_id, f.ts,
         sum(CASE WHEN f.outcome_index = 0 THEN f.sh ELSE 0 END)
           OVER (PARTITION BY f.condition_id ORDER BY f.ts, f.tx_hash, f.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN f.outcome_index = 1 THEN f.sh ELSE 0 END)
           OVER (PARTITION BY f.condition_id ORDER BY f.ts, f.tx_hash, f.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn,
         f.px, f.tx_hash, f.asset
    FROM f
), dm AS (
  SELECT condition_id, ts, px,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM rn WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), b AS (
  SELECT * FROM (VALUES
    ('b1 2026-07-24 venue poll start', timestamptz '2026-07-24 00:00Z'),
    ('b2 2026-08-10 chain start',      timestamptz '2026-08-10 00:00Z')) AS v(label, cut)
)
SELECT b.label AS coverage_complete_boundary,
       CASE WHEN pc.first_fill >= b.cut THEN 'A INIT_KNOWN (zero inventory proven)'
            WHEN pc.first_fill <= (SELECT data_start FROM gs) + interval '24 hours'
              THEN 'C INIT_UNKNOWN (at the edge of all retained data)'
            ELSE 'B INIT_LEFT_CENSORED (earlier fills may be missing)' END AS init_class,
       count(DISTINCT pc.condition_id) AS conditions,
       count(d.*) AS events_in_window,
       round(sum(d.d_m)::numeric, 0) AS dM_shares,
       round(sum(d.d_m * d.px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(d.d_m) / NULLIF(sum(sum(d.d_m)) OVER (PARTITION BY b.label), 0))::numeric, 2)
         AS pct_dM_within_boundary
  FROM b CROSS JOIN pc
  JOIN dm d ON d.condition_id = pc.condition_id AND d.ts >= timestamptz '2026-08-05 00:00Z'
 WHERE pc.last_fill >= timestamptz '2026-08-05 00:00Z'
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 3. VALIDITY FLAG 2: causal-designation coverage, and then with INIT_KNOWN =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, opened_at AS at FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, at FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset, t.outcome_index,
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
), pc AS (SELECT condition_id, min(ts) AS first_fill FROM canon GROUP BY 1
), rn AS (
  SELECT c.condition_id, c.ts, c.px, c.tx_hash, c.asset,
         sum(CASE WHEN c.outcome_index = 0 THEN c.sh ELSE 0 END)
           OVER (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN c.outcome_index = 1 THEN c.sh ELSE 0 END)
           OVER (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM canon c
), ev AS (
  SELECT r.condition_id, r.ts, r.px,
         GREATEST(LEAST(r.cy, r.cn)
                  - COALESCE(LEAST(lag(r.cy) OVER w, lag(r.cn) OVER w), 0), 0) AS d_m
    FROM rn r WINDOW w AS (PARTITION BY r.condition_id ORDER BY r.ts, r.tx_hash, r.asset)
), g AS (
  SELECT e.*, pc.first_fill,
         EXISTS (SELECT 1 FROM desig_rows d
                  WHERE d.condition_id = e.condition_id AND d.at <= e.ts) AS causal_desig,
         (pc.first_fill >= timestamptz '2026-07-24 00:00Z') AS init_known
    FROM ev e JOIN pc ON pc.condition_id = e.condition_id
   WHERE e.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT 'ALL causally classifiable' AS population,
       round((100.0 * count(*) FILTER (WHERE causal_desig) / count(*))::numeric, 2) AS pct_events_with_causal_designation,
       round((100.0 * sum(d_m) FILTER (WHERE causal_desig) / NULLIF(sum(d_m), 0))::numeric, 2) AS pct_dM_with_causal_designation,
       round((100.0 * sum(d_m * px) FILTER (WHERE causal_desig) / NULLIF(sum(d_m * px), 0))::numeric, 2) AS pct_matched_notional_with_causal,
       count(*) AS events, round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM g
UNION ALL
SELECT 'INIT_KNOWN only',
       round((100.0 * count(*) FILTER (WHERE causal_desig) / NULLIF(count(*), 0))::numeric, 2),
       round((100.0 * sum(d_m) FILTER (WHERE causal_desig) / NULLIF(sum(d_m), 0))::numeric, 2),
       round((100.0 * sum(d_m * px) FILTER (WHERE causal_desig) / NULLIF(sum(d_m * px), 0))::numeric, 2),
       count(*), round(sum(d_m)::numeric, 0), round(sum(d_m * px)::numeric, 0)
  FROM g WHERE init_known
UNION ALL
SELECT 'EXCLUDED: initialization-uncertain',
       NULL, NULL, NULL,
       count(*), round(sum(d_m)::numeric, 0), round(sum(d_m * px)::numeric, 0)
  FROM g WHERE NOT init_known;


\echo '== 4. HEADLINE: CONTINUOUS_TARGET_STATE action mix, by population =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10) AS ratio, opened_at AS at
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
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
         l.a1, l.a2, l.first_fill,
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
  -- cf_position_before is the PREVIOUS event's target, carried forward; only
  -- the first in-window event falls back to the reconstructed initialization.
  SELECT t.*, trunc(t.ratio * t.net_after) AS cf_target_after,
         COALESCE(lag(trunc(t.ratio * t.net_after)) OVER w,
                  trunc(t.ratio * t.net_before)) AS cf_position_before,
         (t.first_fill >= timestamptz '2026-07-24 00:00Z') AS init_known
    FROM t WINDOW w AS (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset)
), u AS (
  SELECT 'A HEADLINE causal designation + INIT_KNOWN' AS population, * FROM c
   WHERE long_asset IS NOT NULL AND init_known
  UNION ALL
  SELECT 'B sensitivity: causal designation, any init', * FROM c WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT 'C sensitivity: every event in window', * FROM c
)
SELECT population,
       CASE WHEN long_asset IS NULL THEN '0 DESIGNATION_UNKNOWN'
            WHEN cf_target_after - cf_position_before > 0 THEN '1 BUY'
            WHEN cf_target_after - cf_position_before < 0 THEN '2 SELL'
            ELSE '3 HOLD' END AS required_action,
       count(*) AS dM_events,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY population))::numeric, 2) AS pct_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (PARTITION BY population), 0))::numeric, 2) AS PCT_dM,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM u WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 5. ACTUAL_STATE 09-06..09-10: corrected rule from the real book =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, COALESCE(ratio, 0.10) AS ratio,
         opened_at AS at FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
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
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px, l.a1, l.a2, l.first_fill,
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
                      AND o.done_at IS NOT NULL AND o.done_at <= e.ts), 0) AS bpp,
         (e.first_fill >= timestamptz '2026-07-24 00:00Z') AS init_known
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
       count(*) FILTER (WHERE init_known) AS of_which_INIT_KNOWN,
       round(avg(bpp)::numeric, 1) AS mean_actual_pre_position
  FROM a WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 6. THE ALGEBRA TEST: sign(required_change) vs sign(net_after - net_before) =='
-- Truncation may turn a small move into HOLD; that is expected and is reported
-- separately. Any NON-HOLD disagreement is a bug or a state/designation
-- discontinuity, so the split by "did the designation change at this event"
-- names which of the two it is.
WITH desig_rows AS (
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10) AS ratio, opened_at AS at
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
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
SELECT sign(cf_target_after - cf_position_before) AS sign_required_change,
       sign(net_after - net_before) AS sign_net_change,
       CASE WHEN prev_long_asset IS NULL THEN 'first event in market'
            WHEN prev_long_asset IS DISTINCT FROM long_asset
              THEN 'DESIGNATION CHANGED at this event'
            ELSE 'designation stable' END AS designation_continuity,
       CASE WHEN sign(cf_target_after - cf_position_before) = sign(net_after - net_before)
              THEN 'AGREES'
            WHEN cf_target_after - cf_position_before = 0
              THEN 'HOLD from whole-share truncation (expected)'
            ELSE 'NON-HOLD DISAGREEMENT -- bug or discontinuity' END AS verdict,
       count(*) AS events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM c WHERE long_asset IS NOT NULL
 GROUP BY 1, 2, 3, 4 ORDER BY 4, 1, 2;


\echo '== 7. HEADLINE population: dM action mix by designation class =='
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
), first_desig AS (
  SELECT condition_id, min(at) AS first_at FROM desig_rows GROUP BY 1
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
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px, l.a1, l.a2, l.first_fill,
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
  SELECT c.*, d.long_asset, d.ratio, d.long_from,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio, dr.long_from FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), t AS (
  SELECT e.*, dp.c1_at_desig, dp.c2_at_desig,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END AS net_after,
         CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END AS net_before
    FROM ev e LEFT JOIN dpos dp ON dp.condition_id = e.condition_id
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
 WHERE d_m > 0.000001 AND long_asset IS NOT NULL
   AND first_fill >= timestamptz '2026-07-24 00:00Z'
 GROUP BY 1, 2 ORDER BY 1, 2;
