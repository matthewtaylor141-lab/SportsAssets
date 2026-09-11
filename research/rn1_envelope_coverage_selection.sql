-- ============================================================================
-- SECTION 3, ITEMS 1 AND 2: ENVELOPE-LEVEL COVERAGE, AND WHETHER COVERAGE IS
-- SELECTED ON RN1'S OWN EDGE  (2026-09-11, read-only.)
--
-- WHY ENVELOPE LEVEL. My earlier daily coverage figure hung copy_probes.trade_id
-- off the CANONICAL row and read 80-90%. That is wrong as a measure of
-- execution coverage: the probe fires from the chain listener, so for an
-- envelope both feeds saw, the canonical row is the VENUE row, whose trade_id
-- carries no probe even though the same execution was probed through its chain
-- row. Coverage has to ask "did ANY row of this economic execution get a
-- probe", which is what this file does.
--
-- THE SELL SIDE IS CLOSED. engine_fills is disqualified for neutral execution
-- inference -- its snapshot fires only when the engine formed an INTENT, over a
-- league-allowlisted universe, with a below-threshold `exploration` cohort whose
-- tag is never persisted, and a 20,000-slot queue that DROPS records under load
-- (drops correlate with market activity, so coverage thins exactly when books
-- move). So:
--
--     SELL-SIDE DEPTH: NOT IDENTIFIABLE FROM RETAINED NEUTRAL DATA
--
-- which means UNKNOWN EXECUTION ECONOMICS. It does not mean unprofitable and it
-- does not mean unfillable. Where only a top bid exists the report may carry
-- best_bid, required_sell_price, their difference and an
-- ABOVE/AT/BELOW_REQUIRED label -- and nothing else. No fill size, no
-- probability, no VWAP, no P&L.
--
-- THE CLOCK TERMS, final:
--     probe_at - detected_at                  valid same-clock
--                                             dispatch-after-detection latency
--     venue_detection_delay_from_reported_ts  a timestamp-based delay, NOT
--                                             proven absolute latency
--     cash-path sub-second absolute latency   CLOCK_UNRESOLVED
--
-- Read-only: four SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. ENVELOPE-LEVEL COVERAGE: count and capital-weighted =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  -- THE ECONOMIC ENVELOPE, and whether ANY of its rows was probed.
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed,
         bool_or(EXISTS (SELECT 1 FROM copy_probes p
                          WHERE p.trade_id = base.id AND p.book_ok
                            AND p.best_ask IS NOT NULL)) AS has_valid_probe
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, e.has_valid_probe
    FROM base b JOIN env e
      ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.side = b.side
     AND e.canon_feed = b.feed
), r AS (
  SELECT condition_id, tx_hash, asset, side, ts, sh, px, has_valid_probe,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS y,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n
    FROM canon
), dm AS (
  SELECT condition_id, tx_hash, asset, side, sh, px, has_valid_probe,
         GREATEST(LEAST(y, n)
                  - COALESCE(LEAST(lag(y) OVER w, lag(n) OVER w), 0), 0) AS d_m
    FROM r WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), agg AS (
  SELECT tx_hash, asset, side, max(has_valid_probe::int)::boolean AS has_valid_probe,
         sum(sh) AS shares, sum(sh * px) AS notional,
         sum(d_m) AS d_m, sum(d_m * px) AS matched_notional
    FROM dm GROUP BY 1, 2, 3
)
SELECT count(*) AS economic_envelopes,
       round(sum(notional)::numeric, 0) AS rn1_notional,
       round(sum(shares)::numeric, 0) AS rn1_shares,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(matched_notional)::numeric, 0) AS matched_notional,
       count(*) FILTER (WHERE has_valid_probe) AS probe_covered_envelopes,
       round((100.0 * count(*) FILTER (WHERE has_valid_probe) / count(*))::numeric, 2)
         AS pct_envelopes_covered,
       round((100.0 * sum(notional) FILTER (WHERE has_valid_probe)
              / NULLIF(sum(notional), 0))::numeric, 2) AS PCT_NOTIONAL_COVERED,
       round(sum(d_m) FILTER (WHERE has_valid_probe)::numeric, 0) AS probe_covered_dM,
       round((100.0 * sum(d_m) FILTER (WHERE has_valid_probe)
              / NULLIF(sum(d_m), 0))::numeric, 2) AS PCT_dM_COVERED,
       round((100.0 * sum(matched_notional) FILTER (WHERE has_valid_probe)
              / NULLIF(sum(matched_notional), 0))::numeric, 2) AS PCT_MATCHED_NOTIONAL_COVERED
  FROM agg;


\echo '== 2. THE THREE EXECUTION STATES, by count and capital =='
-- Every dM > 0 event needs an action. On the US venue a completion is a SELL,
-- for which no neutral depth exists; an ADD is a BUY, for which the ask ladder
-- does. The split is therefore mechanical, and it is reported rather than
-- resolved.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed,
         bool_or(EXISTS (SELECT 1 FROM copy_probes p
                          WHERE p.trade_id = base.id AND p.book_ok
                            AND p.best_ask IS NOT NULL)) AS has_valid_probe
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, e.has_valid_probe FROM base b JOIN env e
    ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.side = b.side
   AND e.canon_feed = b.feed
), r AS (
  SELECT condition_id, tx_hash, asset, ts, sh, px, has_valid_probe,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS y,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n
    FROM canon
), dm AS (
  SELECT condition_id, tx_hash, asset, sh, px, has_valid_probe,
         GREATEST(LEAST(y, n)
                  - COALESCE(LEAST(lag(y) OVER w, lag(n) OVER w), 0), 0) AS d_m
    FROM r WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
)
SELECT CASE WHEN d_m > 0.000001 AND NOT has_valid_probe
              THEN '3 SELL required, UNMEASURABLE_SELL_DEPTH'
            WHEN d_m > 0.000001
              THEN '2 SELL required, TOP_OF_BOOK_ONLY_SELL_SIDE'
            WHEN has_valid_probe
              THEN '1 BUY required, MEASURABLE_BUY_SIDE'
            ELSE '4 BUY required, no probe -- unmeasurable'
       END AS execution_state,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(sum(sh)::numeric, 0) AS shares,
       round(sum(sh * px)::numeric, 0) AS notional,
       round((100.0 * sum(sh * px) / sum(sum(sh * px)) OVER ())::numeric, 2) AS PCT_CAPITAL,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS dM_notional
  FROM dm GROUP BY 1 ORDER BY 1;


\echo '== 3. EDGE SELECTION: are covered envelopes economically different? =='
-- The question that decides whether section 3 may generalise at all. RN1's own
-- pair economics are a CONDITION-level quantity, so each envelope inherits the
-- economics of the condition it sits in, and covered and missing envelopes are
-- compared on that.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.sport, t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed,
         bool_or(EXISTS (SELECT 1 FROM copy_probes p
                          WHERE p.trade_id = base.id AND p.book_ok
                            AND p.best_ask IS NOT NULL)) AS has_valid_probe
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, e.has_valid_probe FROM base b JOIN env e
    ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.side = b.side
   AND e.canon_feed = b.feed
), cond AS (
  SELECT condition_id,
         sum(sh) FILTER (WHERE outcome_index = 0) AS qY,
         sum(sh) FILTER (WHERE outcome_index = 1) AS qN,
         sum(sh * px) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS v0,
         sum(sh * px) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS v1
    FROM canon GROUP BY 1
), r AS (
  SELECT c.condition_id, c.tx_hash, c.asset, c.ts, c.sh, c.px, c.sport,
         c.has_valid_probe,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END)
           OVER (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS y,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)
           OVER (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n
    FROM canon c
), dm AS (
  SELECT condition_id, sh, px, sport, has_valid_probe,
         GREATEST(LEAST(y, n)
                  - COALESCE(LEAST(lag(y) OVER w, lag(n) OVER w), 0), 0) AS d_m
    FROM r WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), j AS (
  SELECT dm.*, cond.qY, cond.qN, (cond.v0 + cond.v1) AS gross_pair_cost,
         (1.0 - (cond.v0 + cond.v1)) AS gross_pair_edge,
         LEAST(cond.qY, cond.qN) AS matched_sh
    FROM dm JOIN cond ON cond.condition_id = dm.condition_id
   WHERE cond.qY > 0 AND cond.qN > 0
)
SELECT CASE WHEN has_valid_probe THEN 'PROBE-COVERED' ELSE 'PROBE-MISSING' END AS cohort,
       count(*) AS events,
       round(sum(sh * px)::numeric, 0) AS notional,
       round(avg(gross_pair_cost)::numeric, 5) AS mean_gross_pair_cost,
       round(avg(gross_pair_edge)::numeric, 5) AS mean_gross_pair_edge,
       round((sum(gross_pair_edge * sh * px) / NULLIF(sum(sh * px), 0))::numeric, 5)
         AS CAPITAL_WEIGHTED_pair_edge,
       round(avg(d_m / NULLIF(sh, 0))::numeric, 4) AS mean_dM_over_fill_size,
       round((100.0 * count(*) FILTER (WHERE d_m > 0.000001 AND d_m < sh - 0.000001)
              / count(*))::numeric, 2) AS partial_match_rate_pct,
       round(avg(sh)::numeric, 0) AS mean_clip_shares,
       round(avg(px)::numeric, 4) AS mean_price,
       round(sum(d_m)::numeric, 0) AS dM_shares
  FROM j GROUP BY 1 ORDER BY 1;


\echo '== 4. EDGE SELECTION stratified -- where does the difference live? =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.sport, t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed,
         bool_or(EXISTS (SELECT 1 FROM copy_probes p
                          WHERE p.trade_id = base.id AND p.book_ok
                            AND p.best_ask IS NOT NULL)) AS has_valid_probe
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, e.has_valid_probe FROM base b JOIN env e
    ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.side = b.side
   AND e.canon_feed = b.feed
), cond AS (
  SELECT condition_id,
         sum(sh * px) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS v0,
         sum(sh * px) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS v1,
         sum(sh) FILTER (WHERE outcome_index = 0) AS qY,
         sum(sh) FILTER (WHERE outcome_index = 1) AS qN
    FROM canon GROUP BY 1
), j AS (
  SELECT c.*, (cond.v0 + cond.v1) AS gross_pair_cost
    FROM canon c JOIN cond ON cond.condition_id = c.condition_id
   WHERE cond.qY > 0 AND cond.qN > 0
)
SELECT 'sport' AS dimension, COALESCE(sport, 'null') AS stratum,
       count(*) FILTER (WHERE has_valid_probe) AS covered,
       count(*) FILTER (WHERE NOT has_valid_probe) AS missing,
       round(avg(1.0 - gross_pair_cost) FILTER (WHERE has_valid_probe)::numeric, 5)
         AS covered_pair_edge,
       round(avg(1.0 - gross_pair_cost) FILTER (WHERE NOT has_valid_probe)::numeric, 5)
         AS missing_pair_edge
  FROM j GROUP BY 1, 2
UNION ALL
SELECT 'price_band',
       CASE WHEN px < 0.2 THEN '1 under 0.20' WHEN px < 0.4 THEN '2 0.20-0.40'
            WHEN px < 0.6 THEN '3 0.40-0.60' WHEN px < 0.8 THEN '4 0.60-0.80'
            ELSE '5 0.80+' END,
       count(*) FILTER (WHERE has_valid_probe),
       count(*) FILTER (WHERE NOT has_valid_probe),
       round(avg(1.0 - gross_pair_cost) FILTER (WHERE has_valid_probe)::numeric, 5),
       round(avg(1.0 - gross_pair_cost) FILTER (WHERE NOT has_valid_probe)::numeric, 5)
  FROM j GROUP BY 1, 2
UNION ALL
SELECT 'clip_size',
       CASE WHEN sh < 50 THEN '1 under 50' WHEN sh < 250 THEN '2 50-250'
            WHEN sh < 1000 THEN '3 250-1k' WHEN sh < 5000 THEN '4 1k-5k'
            ELSE '5 5k+' END,
       count(*) FILTER (WHERE has_valid_probe),
       count(*) FILTER (WHERE NOT has_valid_probe),
       round(avg(1.0 - gross_pair_cost) FILTER (WHERE has_valid_probe)::numeric, 5),
       round(avg(1.0 - gross_pair_cost) FILTER (WHERE NOT has_valid_probe)::numeric, 5)
  FROM j GROUP BY 1, 2
 ORDER BY 1, 3 DESC;
