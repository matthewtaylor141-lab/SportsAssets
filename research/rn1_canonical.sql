-- ============================================================================
-- THE CANONICAL RN1 FILL SET: construction, conservation, timing, sample
-- (2026-09-11, read-only.)
--
-- A CORRECTION THAT COMES FIRST, because it changes the whole model. Every
-- class analysis so far grouped by the raw `source` column and looked only at
-- `chain` and `poll`. The venue-labelling census shows FOUR sources on his
-- rows, and the two I was ignoring are not small:
--
--     backfill  512,329 rows (54.1%)      poll  248,970 (26.3%)
--     chain     183,890 rows (19.4%)      s1      1,453 ( 0.2%)
--
-- Read from the ingestion code, those four are TWO economic feeds, not four:
--
--   VENUE FEED  = poll + backfill
--     history.py imports `parse_data_api_trade` from poller.py, so a backfill
--     row is the SAME Data API row as a poll row, collected later. Price is
--     `float(raw["price"])` verbatim -- the venue's stated execution price,
--     BEFORE the fee. Authoritative, not derived.
--
--   CASH FEED   = chain + s1
--     s1_emitter builds its rows from `rec_prices(rec)[0]`, which is
--     `round(rec["usdc_units"] / rec["size_units"], 6)` -- byte-identical to
--     the chain decoder, and the emitter abstains outright when a chain row
--     already exists. Price is USDC per share actually moved, AFTER the fee.
--     DERIVED (a division), authoritative for cash, not for the venue price.
--
-- WHY THAT MATTERS FOR DEDUPLICATION. `dedupe_key` is UNIQUE and carries the
-- price at 6 dp. Two collectors reporting the SAME fill with the SAME price
-- therefore produce ONE row -- they cannot both be stored. So:
--
--   * WITHIN a family, multiple rows in one envelope are genuinely distinct
--     executions. The row count is trustworthy. D1's collapse destroys them.
--   * ACROSS families, the same execution IS stored twice, because the venue
--     price and the cash price differ by the fee and so the keys differ.
--     Summing the families double-counts. RAW is wrong here.
--
-- Both of those are now measured, not asserted: the fee-normalised class D
-- test found 17,680 of 20,855 cross-family envelopes SHARE a fill once the
-- wedge is undone, shares conserve in 20,832 of them, and the union reads
-- 58,755,892 shares against a richest-source 29,394,645 -- a factor of
-- 1.9988. That is the double count, seen directly.
--
-- THE RULE, therefore:
--   1. Envelope = (tx_hash, asset, side).
--   2. The VENUE family gives the fill decomposition where it saw the
--      envelope; otherwise the CASH family does. Never the sum.
--   3. venue_price from the venue family; where only cash saw it, the venue
--      price is INVERTED through the wedge and labelled `cash_inverted`.
--   4. effective_cash_price from the cash family; where only the venue family
--      saw it, the fee is `modelled` and labelled as such.
--   5. Nothing silently picks the more precise number. Every economic field
--      carries its own source label.
--
-- THE TIMING RULE, preserved exactly as the owner set it: historical replay
-- may use only information available by the relevant DETECTION timestamp,
-- never exchange_ts as knowledge time. Four clocks are carried separately and
-- §3 must read `first_any_detection_at`, never `reconstruction_available_at`.
--
-- Read-only: six SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 0. THE TWO FEEDS, and whether the dedupe key really separates them =='
-- If the key separates families, then within a family no two rows of the same
-- envelope can share (size, price, ts) -- and any that do would be a genuine
-- multi-fill at one price, not a duplicate.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed,
         t.size::float8 AS sh, t.price::float8 AS px, t.ts, t.detected_at
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
)
SELECT feed, source, count(*) AS rows,
       count(DISTINCT (tx_hash, asset, side)) AS envelopes,
       round((count(*)::numeric / NULLIF(count(DISTINCT (tx_hash, asset, side)), 0)), 3)
         AS rows_per_envelope,
       round(sum(sh)::numeric, 0) AS shares,
       round(min(px)::numeric, 6) AS px_min, round(max(px)::numeric, 6) AS px_max,
       to_char(min(ts), 'YYYY-MM-DD') AS first_fill,
       to_char(max(ts), 'YYYY-MM-DD') AS last_fill
  FROM t GROUP BY 1, 2 ORDER BY 1, 3 DESC;


\echo '== 1. THE CANONICAL CENSUS: class and reconstruction confidence =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed,
         t.size::float8 AS sh, t.price::float8 AS px, t.detected_at
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap, min(detected_at) AS first_seen
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(n)      FILTER (WHERE feed = 'cash')  AS n_cash,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash,
         max(vwap)   FILTER (WHERE feed = 'venue') AS px_venue,
         max(vwap)   FILTER (WHERE feed = 'cash')  AS px_cash
    FROM f GROUP BY 1, 2, 3
)
SELECT CASE
         WHEN n_venue IS NULL THEN 'CASH ONLY   (no venue price read)'
         WHEN n_cash  IS NULL THEN 'VENUE ONLY  (no cash observed)'
         WHEN abs(sh_venue - sh_cash) >= 0.01 THEN 'BOTH, shares DISAGREE'
         WHEN n_venue = n_cash THEN 'BOTH, same fill count'
         WHEN n_cash < n_venue THEN 'BOTH, cash aggregates the legs'
         ELSE 'BOTH, venue coarser than cash'
       END AS class,
       CASE
         WHEN n_venue IS NOT NULL AND n_cash IS NOT NULL
              AND abs(sh_venue - sh_cash) < 0.01 THEN 'HIGH'
         WHEN n_venue IS NULL OR n_cash IS NULL   THEN 'MEDIUM'
         ELSE 'LOW'
       END AS reconstruction_confidence,
       count(*) AS envelopes,
       sum(COALESCE(n_venue, n_cash)) AS canonical_fills,
       sum(COALESCE(n_venue, 0) + COALESCE(n_cash, 0)) AS raw_rows,
       count(*) AS d1_rows,
       round(sum(COALESCE(sh_venue, sh_cash))::numeric, 0) AS canonical_shares,
       round(sum(COALESCE(sh_venue, 0) + COALESCE(sh_cash, 0))::numeric, 0) AS raw_shares,
       round(sum(COALESCE(sh_venue, sh_cash)
                 * COALESCE(px_venue, px_cash))::numeric, 0) AS canonical_dollars
  FROM g GROUP BY 1, 2 ORDER BY 3 DESC;


\echo '== 2. CONSERVATION: canonical vs RAW vs D1, by envelope shape =='
-- RAW sums both families and so double-counts every envelope both saw.
-- D1 collapses each envelope to one row and so destroys every genuine
-- multi-fill. CANONICAL takes the venue family's decomposition. The test:
-- canonical must equal the venue reading wherever the venue family saw it.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed,
         t.size::float8 AS sh
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(n)      FILTER (WHERE feed = 'cash')  AS n_cash,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash
    FROM f GROUP BY 1, 2, 3
)
SELECT CASE WHEN n_venue IS NOT NULL AND n_cash IS NOT NULL THEN 'both feeds'
            WHEN n_venue IS NOT NULL THEN 'venue only' ELSE 'cash only' END AS shape,
       count(*) AS envelopes,
       sum(COALESCE(n_venue, 0) + COALESCE(n_cash, 0)) AS RAW_fills,
       sum(COALESCE(n_venue, n_cash))                  AS CANONICAL_fills,
       count(*)                                        AS D1_fills,
       round(sum(COALESCE(sh_venue, 0) + COALESCE(sh_cash, 0))::numeric, 0) AS RAW_shares,
       round(sum(COALESCE(sh_venue, sh_cash))::numeric, 0)                  AS CANONICAL_shares,
       round((100.0 * sum(COALESCE(sh_venue, sh_cash))
              / NULLIF(sum(COALESCE(sh_venue, 0) + COALESCE(sh_cash, 0)), 0))::numeric, 2)
         AS canonical_pct_of_raw,
       count(*) FILTER (WHERE n_venue IS NOT NULL AND n_cash IS NOT NULL
                          AND abs(sh_venue - sh_cash) >= 0.01) AS shares_DISAGREE
  FROM g GROUP BY 1 ORDER BY 2 DESC;


\echo '== 3. THE FOUR CLOCKS, and the lookahead the replay must never take =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed,
         t.ts, t.detected_at
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, min(ts) AS ts, min(detected_at) AS first_seen
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n_feeds,
         min(ts) AS exchange_ts,
         min(first_seen) AS first_any_detection_at,
         min(first_seen) FILTER (WHERE feed = 'venue') AS venue_first,
         max(first_seen) AS reconstruction_available_at,
         (array_agg(feed ORDER BY first_seen))[1] AS first_feed
    FROM f GROUP BY 1, 2, 3
)
SELECT first_feed AS which_feed_arrives_first, n_feeds, count(*) AS envelopes,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM first_any_detection_at - exchange_ts))::numeric, 1)
         AS p50_detect_lag_s,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM reconstruction_available_at
                               - first_any_detection_at))::numeric, 1) AS p50_LOOKAHEAD_s,
       round(percentile_cont(0.95) WITHIN GROUP (
         ORDER BY extract(epoch FROM reconstruction_available_at
                               - first_any_detection_at))::numeric, 1) AS p95_LOOKAHEAD_s,
       round(max(extract(epoch FROM reconstruction_available_at
                              - first_any_detection_at))::numeric, 1) AS max_LOOKAHEAD_s,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM venue_first - exchange_ts))::numeric, 1)
         AS p50_venue_price_known_s
  FROM g GROUP BY 1, 2 ORDER BY 3 DESC;


\echo '== 4. STRATIFIED VALIDATION SAMPLE: raw rows, four per class =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed,
         t.size::float8 AS sh, t.price::float8 AS px, t.ts, t.detected_at
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(n)      FILTER (WHERE feed = 'cash')  AS n_cash,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash
    FROM f GROUP BY 1, 2, 3
), c AS (
  SELECT tx_hash, asset, side,
         CASE WHEN n_venue IS NULL THEN 'A cash only'
              WHEN n_cash IS NULL THEN 'B venue only'
              WHEN abs(sh_venue - sh_cash) >= 0.01 THEN 'E shares disagree'
              WHEN n_venue = n_cash THEN 'C both, equal counts'
              ELSE 'D both, unequal counts' END AS class
    FROM g
), r AS (
  SELECT c.*, row_number() OVER (PARTITION BY class ORDER BY tx_hash) AS rn FROM c
)
SELECT r.class, left(r.tx_hash, 10) AS tx, left(r.asset, 8) AS asset, r.side,
       t.feed, t.source, round(t.sh::numeric, 2) AS shares, t.px AS price,
       to_char(t.ts, 'MM-DD HH24:MI:SS') AS exchange_ts,
       to_char(t.detected_at, 'MM-DD HH24:MI:SS') AS detected_at
  FROM r JOIN t ON t.tx_hash = r.tx_hash AND t.asset = r.asset AND t.side = r.side
 WHERE r.rn <= 4
 ORDER BY r.class, r.tx_hash, t.feed DESC, t.ts, t.px
 LIMIT 160;


\echo '== 5. THE TEN ECONOMIC FIELDS, populated, every source label carried =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed,
         t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         max(n)      FILTER (WHERE feed = 'venue') AS n_venue,
         max(n)      FILTER (WHERE feed = 'cash')  AS n_cash,
         max(shares) FILTER (WHERE feed = 'venue') AS sh_venue,
         max(shares) FILTER (WHERE feed = 'cash')  AS sh_cash,
         max(vwap)   FILTER (WHERE feed = 'venue') AS px_venue,
         max(vwap)   FILTER (WHERE feed = 'cash')  AS px_cash
    FROM f GROUP BY 1, 2, 3
), e AS (
  SELECT side, COALESCE(sh_venue, sh_cash) AS shares,
         COALESCE(px_venue,
                  CASE WHEN side = 'BUY' AND 1.1025 - 0.2 * px_cash >= 0
                       THEN (1.05 - sqrt(1.1025 - 0.2 * px_cash)) / 0.1
                       ELSE px_cash END) AS venue_price,
         CASE WHEN px_venue IS NOT NULL THEN 'venue_authoritative'
              ELSE 'cash_inverted' END AS venue_price_source,
         COALESCE(px_cash,
                  px_venue + (CASE WHEN side = 'BUY' THEN 1.0 ELSE -1.0 END)
                             * 0.05 * px_venue * (1 - px_venue)) AS effective_cash_price,
         CASE WHEN px_cash IS NULL THEN 'modelled'
              WHEN px_venue IS NULL THEN 'cash_only'
              WHEN n_cash = n_venue THEN 'observed_leg'
              ELSE 'observed_txn' END AS cash_price_source
    FROM g
)
SELECT side, venue_price_source, cash_price_source AS fee_source,
       CASE WHEN cash_price_source IN ('observed_leg', 'cash_only') THEN 'observed'
            WHEN cash_price_source = 'observed_txn' THEN 'observed at transaction'
            ELSE 'MODELLED, not measured' END AS fee_confidence,
       count(*) AS envelopes,
       round(sum(shares)::numeric, 0) AS shares,
       round(avg(venue_price)::numeric, 6) AS avg_venue_price,
       round(avg(effective_cash_price - venue_price)::numeric, 6) AS avg_fee_per_share,
       round(sum(shares * venue_price)::numeric, 0) AS gross_notional,
       round(sum(shares * (effective_cash_price - venue_price))::numeric, 0) AS fee_amount,
       round(sum(shares * effective_cash_price)::numeric, 0) AS net_cash_flow
  FROM e GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC;
