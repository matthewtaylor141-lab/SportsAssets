-- ============================================================================
-- CHECK B, TWO STATE MACHINES: actual production state vs continuous target
-- (2026-09-11, read-only.)
--
-- THE DISTINCTION THIS FILE EXISTS TO ENFORCE. "What would corrected logic have
-- done" has two different answers and they must never be mixed:
--
--   ACTUAL_STATE_09_06_TO_09_10   bettor_pre_position is OUR REAL BOOKED
--       POSITION, rebuilt from our own filled orders. Answers the narrow
--       question: from the state production actually reached, what would the
--       corrected rule have requested? Confined to the days we truly held
--       positions, because outside them the question is not defined.
--
--   CONTINUOUS_TARGET_STATE_37D   cf_bettor_position_before is propagated
--       recursively in RN1 event order, cf_position_after := cf_target_after.
--       Answers the execution-feasibility question: what would a CONTINUOUSLY
--       OPERATING corrected mirror request over 37 days? Using
--       bettor_pre_position = 0 here would answer a COLD-START question at
--       every event and would manufacture an enormous FLAT cohort out of the
--       mere absence of a historical book.
--
-- This first pass is a TARGET-STATE counterfactual, NOT an execution
-- simulation: the state is not reduced for missing depth or failed fills.
-- Its only job is to classify the action a corrected mirror would REQUEST.
--
-- THE GRANULARITY, CONFIRMED FROM CODE rather than chosen. analytics/mirror.py
-- :280 in target_shares:
--     tgt = int(raw) if raw >= 0 else -int(-raw)      # toward zero, whole shares
-- The TARGET ITSELF is truncated toward zero to whole shares, and the order
-- path carries that through (submit_fok(slug, wire, int(qty), sell); "shares =
-- int(usd / limit)"; the ledger column is whole shares). So:
--   * the continuous model compares two INTEGERS, and HOLD is exact equality --
--     no arbitrary band is needed, and the earlier 0.5-share band is dropped;
--   * the actual model compares an integer target to a possibly fractional
--     booked position, so the executable size is trunc() of the difference and
--     HOLD is that being zero.
-- Postgres trunc() truncates toward zero, matching the Python exactly.
-- Truncation is not cosmetic: a fill too small to move the integer target
-- produces a genuine HOLD, which is a real state, not a rounding artifact.
--
-- INITIALIZATION -- option 1, the preferred one. The cumulative position is
-- built from the EARLIEST AVAILABLE RN1 FILL, with no 2026-08-05 floor, so a
-- market already open at the window start enters with its reconstructed
-- inventory rather than silently at zero. Statement 1 measures how good that
-- reconstruction is and flags the conditions where it cannot be trusted:
-- a condition whose first observed fill sits at the very edge of the data may
-- have inventory that predates our record, and is labelled INIT_BURN_IN.
-- Every later statement reports with and without that cohort.
--
-- DESIGNATION IS TIME-CAUSAL. Every event takes the designation that existed
-- AT OR BEFORE it -- a LATERAL picking the latest row with at <= event_ts
-- across mirror_books and mirror_candidate_refusals. No later book or refusal
-- row may retrospectively designate an earlier event. Where no causal
-- designation exists the action is DESIGNATION_UNKNOWN, never borrowed from a
-- later mapping. designation_at, designation_age and designation_source are
-- carried on every row.
--
-- Read-only: six SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. INITIALIZATION EVIDENCE: can prior RN1 inventory be reconstructed? =='
WITH f AS (
  SELECT t.condition_id, t.ts, t.size::float8 AS sh
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), g AS (
  SELECT min(ts) AS data_start, max(ts) AS data_end FROM f
), percond AS (
  SELECT condition_id, min(ts) AS first_fill, max(ts) AS last_fill,
         count(*) AS fills
    FROM f GROUP BY 1
)
SELECT to_char((SELECT data_start FROM g), 'YYYY-MM-DD HH24:MI') AS earliest_rn1_fill,
       to_char((SELECT data_end FROM g), 'YYYY-MM-DD HH24:MI') AS latest_rn1_fill,
       count(*) AS conditions_all_time,
       count(*) FILTER (WHERE last_fill >= timestamptz '2026-08-05 00:00Z')
         AS conditions_active_in_window,
       count(*) FILTER (WHERE last_fill >= timestamptz '2026-08-05 00:00Z'
                          AND first_fill <  timestamptz '2026-08-05 00:00Z')
         AS ALREADY_OPEN_AT_WINDOW_START,
       count(*) FILTER (WHERE last_fill >= timestamptz '2026-08-05 00:00Z'
                          AND first_fill <= (SELECT data_start FROM g) + interval '24 hours')
         AS INIT_BURN_IN_inventory_may_predate_the_data,
       round((100.0 * count(*) FILTER (WHERE last_fill >= timestamptz '2026-08-05 00:00Z'
                                         AND first_fill <= (SELECT data_start FROM g) + interval '24 hours')
              / NULLIF(count(*) FILTER (WHERE last_fill >= timestamptz '2026-08-05 00:00Z'), 0))::numeric, 2)
         AS pct_burn_in
  FROM percond;


\echo '== 2. ACTUAL_STATE_09_06_TO_09_10: corrected rule from the real book =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS at,
         COALESCE(ratio, 0.10) AS ratio, 'A book' AS src
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10, 'B refusal'
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND EXISTS (SELECT 1 FROM desig_rows d WHERE d.condition_id = t.condition_id)
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2 FROM canon GROUP BY 1
), cum AS (
  -- NO WINDOW FLOOR: the running position starts at his earliest available
  -- fill, so a market already open enters with reconstructed inventory.
  SELECT c.id, c.condition_id, c.ts, c.asset, c.sh, c.px, l.a1, l.a2,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.slug, d.ratio, d.at AS designation_at, d.src,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.slug, dr.ratio, dr.at, dr.src
        FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-09-06 00:00Z'
     AND c.ts <  timestamptz '2026-09-11 00:00Z'
), n AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END AS net_after,
         COALESCE((SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
                     FROM mirror_orders o
                    WHERE o.us_market_slug = e.slug AND o.filled > 0
                      AND o.done_at IS NOT NULL AND o.done_at <= e.ts), 0) AS bpp
    FROM ev e
), a AS (
  SELECT n.*,
         trunc(n.ratio * n.net_after) AS target_after_int,
         trunc(trunc(n.ratio * n.net_after) - n.bpp) AS executable_change
    FROM n
)
SELECT CASE WHEN long_asset IS NULL THEN '0 DESIGNATION_UNKNOWN (no causal designation)'
            WHEN executable_change > 0 THEN '1 BUY'
            WHEN executable_change < 0 THEN '2 SELL'
            ELSE '3 HOLD (target move under one whole share)' END AS required_action,
       CASE WHEN d_m > 0.000001 THEN 'B dM COMPLETION' ELSE 'A ENTRY / OPENING' END AS event_class,
       count(*) AS events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round(avg(bpp)::numeric, 1) AS mean_actual_pre_position,
       round(avg(target_after_int)::numeric, 1) AS mean_target_after
  FROM a GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 3. CONTINUOUS_TARGET_STATE_37D: cf state propagated in event order =='
-- cf_position_before is the PREVIOUS event's target in this market, because
-- cf_position_after := cf_target_after by definition of a target-state
-- counterfactual. No intervening fills exist between two consecutive events of
-- the same market, so cf_position_before = trunc(ratio x rn1_net_before).
WITH desig_rows AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS at,
         COALESCE(ratio, 0.10) AS ratio, 'A book' AS src
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10, 'B refusal'
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND EXISTS (SELECT 1 FROM desig_rows d WHERE d.condition_id = t.condition_id)
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2,
         min(ts) AS first_fill FROM canon GROUP BY 1
), gstart AS (
  SELECT min(ts) AS data_start FROM canon
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.asset, c.sh, c.px, l.a1, l.a2, l.first_fill,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.ratio, d.at AS designation_at,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio, dr.at
        FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), a AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         trunc(e.ratio * CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
                              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END)
           AS cf_target_after,
         trunc(e.ratio * CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
                              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END)
           AS cf_position_before,
         (e.first_fill <= (SELECT data_start FROM gstart) + interval '24 hours')
           AS init_burn_in
    FROM ev e
)
SELECT CASE WHEN long_asset IS NULL THEN '0 DESIGNATION_UNKNOWN (no causal designation)'
            WHEN cf_target_after - cf_position_before > 0 THEN '1 BUY'
            WHEN cf_target_after - cf_position_before < 0 THEN '2 SELL'
            ELSE '3 HOLD (target move under one whole share)' END AS required_action,
       CASE WHEN init_burn_in THEN 'INIT_BURN_IN (prior inventory uncertain)'
            ELSE 'INIT_RECONSTRUCTED' END AS init_quality,
       count(*) AS events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round(avg(cf_position_before)::numeric, 1) AS mean_cf_position_before,
       round(avg(cf_target_after)::numeric, 1) AS mean_cf_target_after
  FROM a GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 4. dM ACTION MIX under the CONTINUOUS model, by designation class =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS at,
         COALESCE(ratio, 0.10) AS ratio,
         'book row -- branch not recorded' AS long_from
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10,
         CASE WHEN long_from = 'catalogue' THEN 'catalogue'
              WHEN long_from IS NULL OR long_from = '' THEN 'mapper -- unlabelled'
              ELSE long_from END
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND EXISTS (SELECT 1 FROM desig_rows d WHERE d.condition_id = t.condition_id)
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2 FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.asset, c.sh, c.px, l.a1, l.a2,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.ratio, d.long_from, d.at AS designation_at,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio, dr.long_from, dr.at
        FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), first_desig AS (
  -- the FIRST designation a condition ever received. The branch test asks what
  -- his position looked like when the long token was CHOSEN, so it must use
  -- that timestamp -- not each event's own as-of designation, which is by
  -- construction always at or before the event and so can never bound it.
  SELECT condition_id, min(at) AS first_at FROM desig_rows GROUP BY 1
), dpos AS (
  SELECT DISTINCT ON (c.condition_id) c.condition_id,
         c.cum1 AS c1_at_desig, c.cum2 AS c2_at_desig
    FROM cum c JOIN first_desig f ON f.condition_id = c.condition_id
   WHERE c.ts <= f.first_at
   ORDER BY c.condition_id, c.ts DESC
), a AS (
  SELECT e.*, dp.c1_at_desig, dp.c2_at_desig,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         trunc(e.ratio * CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
                              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END)
           AS cf_target_after,
         trunc(e.ratio * CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
                              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END)
           AS cf_position_before
    FROM ev e LEFT JOIN dpos dp ON dp.condition_id = e.condition_id
)
SELECT CASE WHEN long_asset IS NULL THEN '5 DESIGNATION_UNKNOWN'
            WHEN long_from = 'catalogue' THEN '3 CATALOGUE_LONG'
            WHEN c1_at_desig IS NULL THEN '4 NO_POSITION_AT_DESIGNATION'
            WHEN (CASE WHEN long_asset = a1 THEN c1_at_desig ELSE c2_at_desig END)
               < (CASE WHEN long_asset = a1 THEN c2_at_desig ELSE c1_at_desig END)
              THEN '2 INTENT_DESIGNATED_LONG (proven)'
            ELSE '1 POSITION_CONSISTENT_LONG (not proven)' END AS designation_class,
       CASE WHEN long_asset IS NULL THEN '0 DESIGNATION_UNKNOWN'
            WHEN cf_target_after - cf_position_before > 0 THEN '1 BUY'
            WHEN cf_target_after - cf_position_before < 0 THEN '2 SELL'
            ELSE '3 HOLD' END AS required_action,
       count(*) AS dM_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM
  FROM a WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 5. DESIGNATION CAUSALITY: how much is lost, and how stale is the rest =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, opened_at AS at, 'A book' AS src
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, at, 'B refusal'
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), ev AS (
  SELECT c.id, c.ts, c.sh, c.px, c.condition_id, d.at AS designation_at, d.src,
         EXISTS (SELECT 1 FROM desig_rows dr WHERE dr.condition_id = c.condition_id)
           AS has_any_designation_ever
    FROM canon c
    LEFT JOIN LATERAL (
      SELECT dr.at, dr.src FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
)
SELECT CASE WHEN designation_at IS NOT NULL THEN '1 causal designation exists'
            WHEN has_any_designation_ever
              THEN '2 designation exists but is LATER than the event (refused)'
            ELSE '3 no designation at all' END AS causality,
       COALESCE(src, 'n/a') AS designation_source,
       count(*) AS events,
       round(sum(sh * px)::numeric, 0) AS notional,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - designation_at) / 3600.0)::numeric, 2)
         AS median_designation_age_hours,
       round(percentile_cont(0.95) WITHIN GROUP (
         ORDER BY extract(epoch FROM ts - designation_at) / 3600.0)::numeric, 2)
         AS p95_designation_age_hours
  FROM ev GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 6. THE TWO MODELS SIDE BY SIDE on the SAME 09-06..09-10 events =='
-- The only window where both are defined. Any difference here is caused purely
-- by the state machine, because the events, designations and ratio are shared.
WITH desig_rows AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, opened_at AS at,
         COALESCE(ratio, 0.10) AS ratio
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, at, 0.10
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND EXISTS (SELECT 1 FROM desig_rows d WHERE d.condition_id = t.condition_id)
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2 FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.asset, c.sh, c.px, l.a1, l.a2,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.slug, d.ratio,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.slug, dr.ratio FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-09-06 00:00Z'
     AND c.ts <  timestamptz '2026-09-11 00:00Z'
), a AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         trunc(e.ratio * CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
                              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END) AS tgt_after,
         trunc(e.ratio * CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
                              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END) AS tgt_before,
         COALESCE((SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
                     FROM mirror_orders o
                    WHERE o.us_market_slug = e.slug AND o.filled > 0
                      AND o.done_at IS NOT NULL AND o.done_at <= e.ts), 0) AS bpp
    FROM ev e
)
SELECT CASE WHEN long_asset IS NULL THEN 'DESIGNATION_UNKNOWN'
            WHEN trunc(tgt_after - bpp) > 0 THEN 'BUY'
            WHEN trunc(tgt_after - bpp) < 0 THEN 'SELL' ELSE 'HOLD' END AS ACTUAL_STATE_action,
       CASE WHEN long_asset IS NULL THEN 'DESIGNATION_UNKNOWN'
            WHEN tgt_after - tgt_before > 0 THEN 'BUY'
            WHEN tgt_after - tgt_before < 0 THEN 'SELL' ELSE 'HOLD' END AS CONTINUOUS_action,
       count(*) AS dM_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM a WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;
