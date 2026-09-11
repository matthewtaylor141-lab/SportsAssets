-- ============================================================================
-- SECTION 3, REQUIREMENT 4: is copy_probe missingness random?
-- (2026-09-11, read-only.)
--
-- TWO FACTS READ FROM THE PROBE WRITER, not inferred, because they decide what
-- this audit is even looking for. backend/sportsassets/copy_probe.py:
--
--   1. THE LADDER IS ASK-SIDE ONLY. `asks = sorted(... d.get("asks") ...)`,
--      `fill_from_asks(asks, ...)`, `compute_book_metrics(asks, his_price, ...)`.
--      There is NO bid ladder in copy_probes, and no bid ladder anywhere else in
--      retained data -- mirror_shadow and mirror_orders carry a top-of-book bid
--      with no size. So for any required action that is a SELL, depth-based fill
--      coverage is NOT COMPUTABLE from what we kept. That is a hard limit and it
--      will be reported as UNKNOWN rather than filled in from the ask side.
--
--   2. MISSINGNESS IS NON-RANDOM BY CONSTRUCTION. probe_trade() returns early
--      unless side == 'BUY', and returns early when latency_s > MAX_REACTION_S
--      (120). So every SELL of his, and every fill we detected more than two
--      minutes late, is missing a probe BY DESIGN. The question this file
--      answers is not whether missingness is random -- the code says it is not --
--      but HOW MUCH of the flow that leaves out and WHICH flow.
--
-- A THIRD FACT, about the clock, which cuts the other way from the usual
-- lookahead worry. `probe_at` is stamped BEFORE the semaphore is acquired, and
-- the book GET happens after the wait. So the snapshot is taken LATER than
-- probe_at by an unrecorded amount: probe_at is a LOWER BOUND on the snapshot
-- time, and `probe_snapshot_at` does not exist as a stored field. Using
-- probe_at as the snapshot moment therefore risks crediting us with a book we
-- could not yet have seen. Statement 4 measures the queue wait indirectly so
-- the size of that gap is at least bounded.
--
-- Read-only: four SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. DAILY COVERAGE: CANONICAL RN1 BUY fills vs fills with a probe =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.sport, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.* FROM base b JOIN env e
    ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.side = b.side
   AND e.canon_feed = b.feed
)
SELECT canon.ts::date AS day,
       count(*) AS canonical_BUY_fills,
       round(sum(canon.sh * canon.px)::numeric, 0) AS canonical_BUY_notional,
       count(p.id) AS fills_with_a_probe,
       round(sum(canon.sh * canon.px) FILTER (WHERE p.id IS NOT NULL)::numeric, 0)
         AS notional_with_a_probe,
       round((100.0 * count(p.id) / count(*))::numeric, 2) AS coverage_pct_by_fill,
       round((100.0 * sum(canon.sh * canon.px) FILTER (WHERE p.id IS NOT NULL)
              / NULLIF(sum(canon.sh * canon.px), 0))::numeric, 2) AS coverage_pct_by_notional,
       count(*) FILTER (WHERE p.id IS NOT NULL AND p.book_ok) AS probe_book_readable
  FROM canon LEFT JOIN copy_probes p ON p.trade_id = canon.id
 GROUP BY 1 ORDER BY 1;


\echo '== 2. MISSINGNESS STRATIFIED: who gets a probe and who does not =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.sport, t.market_slug,
         t.size::float8 AS sh, t.price::float8 AS px, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM base GROUP BY 1, 2, 3, 4
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed,
         max(n)    FILTER (WHERE feed = 'venue') AS n_venue,
         max(vwap) FILTER (WHERE feed = 'venue') AS v,
         max(vwap) FILTER (WHERE feed = 'cash')  AS c
    FROM f GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, e.n_venue, e.v, e.c,
         CASE WHEN e.v IS NULL OR e.c IS NULL THEN 'unknown'
              WHEN e.n_venue > 1 THEN 'proven_maker'
              WHEN abs((e.c - e.v) - 0.05 * e.v * (1 - e.v)) < 5e-5 THEN 'proven_taker'
              ELSE 'unknown' END AS ex_post,
         CASE WHEN abs(COALESCE(e.c, b.px) - round(COALESCE(e.c, b.px)::numeric, 2)::float8) < 1e-6
                THEN 'dt_probable_maker'
              WHEN 1.1025 - 0.2 * COALESCE(e.c, b.px) >= 0
                   AND abs(((1.05 - sqrt(1.1025 - 0.2 * COALESCE(e.c, b.px))) / 0.1)
                           - round((((1.05 - sqrt(1.1025 - 0.2 * COALESCE(e.c, b.px))) / 0.1))::numeric, 2)::float8)
                       < 1e-6
                THEN 'dt_probable_taker'
              ELSE 'dt_unknown' END AS at_decision
    FROM base b JOIN env e
      ON e.tx_hash = b.tx_hash AND e.asset = b.asset AND e.side = b.side
     AND e.canon_feed = b.feed
), j AS (
  SELECT canon.*, (p.id IS NOT NULL) AS has_probe
    FROM canon LEFT JOIN copy_probes p ON p.trade_id = canon.id
)
SELECT 'source'          AS dimension, source            AS stratum,
       count(*) AS fills, count(*) FILTER (WHERE has_probe) AS with_probe,
       round((100.0 * count(*) FILTER (WHERE has_probe) / count(*))::numeric, 2) AS coverage_pct,
       round(sum(sh * px)::numeric, 0) AS notional
  FROM j GROUP BY 1, 2
UNION ALL
SELECT 'ex_post', ex_post, count(*), count(*) FILTER (WHERE has_probe),
       round((100.0 * count(*) FILTER (WHERE has_probe) / count(*))::numeric, 2),
       round(sum(sh * px)::numeric, 0) FROM j GROUP BY 1, 2
UNION ALL
SELECT 'decision_time', at_decision, count(*), count(*) FILTER (WHERE has_probe),
       round((100.0 * count(*) FILTER (WHERE has_probe) / count(*))::numeric, 2),
       round(sum(sh * px)::numeric, 0) FROM j GROUP BY 1, 2
UNION ALL
SELECT 'sport', COALESCE(sport, 'null'), count(*), count(*) FILTER (WHERE has_probe),
       round((100.0 * count(*) FILTER (WHERE has_probe) / count(*))::numeric, 2),
       round(sum(sh * px)::numeric, 0) FROM j GROUP BY 1, 2
UNION ALL
SELECT 'price_decile', lpad((width_bucket(px, 0, 1, 10))::text, 2, '0'),
       count(*), count(*) FILTER (WHERE has_probe),
       round((100.0 * count(*) FILTER (WHERE has_probe) / count(*))::numeric, 2),
       round(sum(sh * px)::numeric, 0) FROM j GROUP BY 1, 2
UNION ALL
SELECT 'clip_size',
       CASE WHEN sh <   50 THEN '1 under 50'
            WHEN sh <  250 THEN '2 50-250'
            WHEN sh < 1000 THEN '3 250-1k'
            WHEN sh < 5000 THEN '4 1k-5k'
            ELSE                '5 5k+' END,
       count(*), count(*) FILTER (WHERE has_probe),
       round((100.0 * count(*) FILTER (WHERE has_probe) / count(*))::numeric, 2),
       round(sum(sh * px)::numeric, 0) FROM j GROUP BY 1, 2
UNION ALL
SELECT 'market_type',
       CASE WHEN market_slug IS NULL THEN 'null'
            WHEN market_slug LIKE '%-total-%' OR market_slug LIKE '%over%' THEN 'total'
            WHEN market_slug ~ '[+-][0-9]' THEN 'spread'
            ELSE 'moneyline_or_other' END,
       count(*), count(*) FILTER (WHERE has_probe),
       round((100.0 * count(*) FILTER (WHERE has_probe) / count(*))::numeric, 2),
       round(sum(sh * px)::numeric, 0) FROM j GROUP BY 1, 2
 ORDER BY 1, 3 DESC;


\echo '== 3. THE 120-SECOND CUTOFF: how much flow does it structurally exclude? =='
-- probe_trade() returns early when latency_s > 120. If late detections are a
-- meaningful share of his dollars, the probe population is not his book.
WITH base AS (
  SELECT t.id, t.ts, t.detected_at, t.size::float8 AS sh, t.price::float8 AS px,
         extract(epoch FROM t.detected_at - t.ts) AS detect_lag_s,
         t.source
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT CASE WHEN source IN ('poll', 'backfill') THEN 'venue feed' ELSE 'cash feed' END AS feed,
       CASE WHEN detect_lag_s IS NULL THEN '0 no detect clock'
            WHEN detect_lag_s <= 0    THEN '1 at or before the fill'
            WHEN detect_lag_s <= 30   THEN '2 under 30 s'
            WHEN detect_lag_s <= 120  THEN '3 30-120 s  (probe still fires)'
            WHEN detect_lag_s <= 900  THEN '4 2-15 min  (PROBE SUPPRESSED)'
            ELSE                           '5 over 15 min (PROBE SUPPRESSED)'
       END AS detect_lag_band,
       count(*) AS fills,
       round(sum(sh * px)::numeric, 0) AS notional,
       round((100.0 * sum(sh * px) / sum(sum(sh * px)) OVER ())::numeric, 2) AS pct_of_dollars
  FROM base GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 4. THE UNRECORDED SNAPSHOT GAP: how long did the probe queue? =='
-- probe_at is stamped before the semaphore; the book GET runs after it. The
-- true snapshot time is unrecorded, so this bounds the gap by comparing the
-- stated reaction against the detection lag on the same fill. A large spread
-- means probe_at understates when we actually saw the book.
SELECT date_trunc('day', p.probe_at)::date AS day,
       count(*) AS probes,
       round(avg(p.reaction_s)::numeric, 2) AS mean_stated_reaction_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY p.reaction_s)::numeric, 2) AS p50,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY p.reaction_s)::numeric, 2) AS p95,
       round(max(p.reaction_s)::numeric, 2) AS max_reaction_s,
       count(*) FILTER (WHERE p.reaction_s < 0) AS NEGATIVE_reaction_clock_skew,
       round(avg(extract(epoch FROM p.probe_at - t.detected_at))::numeric, 2)
         AS mean_probe_at_minus_detected_at_s,
       round(percentile_cont(0.95) WITHIN GROUP (
         ORDER BY extract(epoch FROM p.probe_at - t.detected_at))::numeric, 2)
         AS p95_probe_after_detection_s
  FROM copy_probes p JOIN trades t ON t.id = p.trade_id
  JOIN whales w ON w.id = p.whale_id
 WHERE lower(w.username) = 'rn1' AND p.probe_at >= timestamptz '2026-08-05 00:00Z'
 GROUP BY 1 ORDER BY 1;
