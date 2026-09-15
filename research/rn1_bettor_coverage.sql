-- ============================================================================
-- THE §3/§4 DATA GATE: what BETTOR-side evidence exists, and over what window
-- (2026-09-11, read-only.)
--
-- WHY THIS RUNS FIRST. §3 asks whether BETTOR could reproduce RN1's matched
-- economics using ONLY information available at the decision timestamp, and §4
-- asks whether our fills are adversely selected. Neither can be answered from
-- his fills alone -- both need OUR orders, OUR observed book, and OUR forward
-- prices. Before writing a line of replay I need to know which of those exist,
-- over which dates, and how complete they are, because that decides which
-- questions are answerable and which must come back UNKNOWN.
--
-- THE FIELDS THAT MATTER, and what each one buys:
--
--   mirror_orders.bid_at_place / ask_at_place   the book AS WE SAW IT when we
--       decided. This is the anti-lookahead spine of §3: it is a record of the
--       information we actually had, not a reconstruction.
--   mirror_orders.ask_at_send                   the ask at the moment of send,
--       so placement latency can be separated from decision latency.
--   mirror_orders.his_fill_id                   the join from our order to the
--       specific RN1 fill that caused it. Without this the opportunity-level
--       analysis is guesswork.
--   mirror_orders.maker / taker_at_placement    OUR maker/taker status, which
--       is recorded, unlike his.
--   mirror_fill_answers                         one row per fill of his with
--       the answer we gave it. This is the OPPORTUNITY LEDGER -- the denominator
--       for §4's filled-versus-missed, including the ones we never acted on.
--   price_path (row_id, t_s, ask)               forward asks after a row, which
--       is where §4's +30s / +2m / +5m / +15m movement has to come from.
--
-- THREE MIGRATIONS ARE RECENT (059 send record, 060 fill answers, 061 cause
-- index), so those columns are expected to be sparse or absent before their
-- deploy date. Statement 2 measures that rather than assuming it, because a
-- column that is 95% NULL cannot carry a headline.
--
-- Read-only: six SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. THE WINDOW: when does each BETTOR table actually carry rows? =='
SELECT 'mirror_orders' AS tbl, count(*) AS rows,
       to_char(min(placed_at), 'YYYY-MM-DD HH24:MI') AS first_row,
       to_char(max(placed_at), 'YYYY-MM-DD HH24:MI') AS last_row,
       count(DISTINCT placed_at::date) AS distinct_days
  FROM mirror_orders
UNION ALL
SELECT 'mirror_books', count(*),
       to_char(min(opened_at), 'YYYY-MM-DD HH24:MI'),
       to_char(max(opened_at), 'YYYY-MM-DD HH24:MI'),
       count(DISTINCT opened_at::date)
  FROM mirror_books
UNION ALL
SELECT 'mirror_fill_answers', count(*),
       to_char(min(to_timestamp(at)), 'YYYY-MM-DD HH24:MI'),
       to_char(max(to_timestamp(at)), 'YYYY-MM-DD HH24:MI'),
       count(DISTINCT to_timestamp(at)::date)
  FROM mirror_fill_answers
UNION ALL
SELECT 'price_path', count(*),
       to_char(min(sampled_at), 'YYYY-MM-DD HH24:MI'),
       to_char(max(sampled_at), 'YYYY-MM-DD HH24:MI'),
       count(DISTINCT sampled_at::date)
  FROM price_path
UNION ALL
SELECT 'mirror_candidate_refusals', count(*),
       to_char(min(at), 'YYYY-MM-DD HH24:MI'),
       to_char(max(at), 'YYYY-MM-DD HH24:MI'),
       count(DISTINCT at::date)
  FROM mirror_candidate_refusals
 ORDER BY 1;


\echo '== 2. COLUMN COMPLETENESS on mirror_orders, by day =='
-- A column that is mostly NULL cannot carry a conclusion. This says from which
-- date each of the §3 fields is actually usable.
SELECT placed_at::date AS day, count(*) AS orders,
       round((100.0 * count(bid_at_place) / count(*))::numeric, 1) AS pct_bid_at_place,
       round((100.0 * count(ask_at_place) / count(*))::numeric, 1) AS pct_ask_at_place,
       round((100.0 * count(ask_at_send)  / count(*))::numeric, 1) AS pct_ask_at_send,
       round((100.0 * count(his_fill_id)  / count(*))::numeric, 1) AS pct_HIS_FILL_ID,
       round((100.0 * count(his_level)    / count(*))::numeric, 1) AS pct_his_level,
       round((100.0 * count(maker)        / count(*))::numeric, 1) AS pct_OUR_maker_flag,
       round((100.0 * count(avg_px)       / count(*))::numeric, 1) AS pct_avg_px,
       count(*) FILTER (WHERE filled > 0) AS orders_with_a_fill
  FROM mirror_orders
 WHERE placed_at >= now() - interval '30 days'
 GROUP BY 1 ORDER BY 1;


\echo '== 3. THE OPPORTUNITY LEDGER: what answers did we give his fills? =='
SELECT to_timestamp(at)::date AS day, name AS answer, count(*) AS fills,
       count(*) FILTER (WHERE order_id IS NOT NULL) AS with_an_order,
       round(avg(at - detected_at)::numeric, 2) AS mean_answer_lag_s,
       round(avg(detected_at - fill_ts)::numeric, 2) AS mean_detect_lag_s
  FROM mirror_fill_answers
 WHERE at > 0 GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC LIMIT 60;


\echo '== 4. price_path: which forward horizons exist, and how densely? =='
SELECT t_s AS horizon_seconds, count(*) AS samples,
       count(DISTINCT row_id) AS rows_covered,
       count(ask) AS non_null_ask,
       to_char(min(sampled_at), 'MM-DD') AS first_day,
       to_char(max(sampled_at), 'MM-DD') AS last_day
  FROM price_path GROUP BY 1 ORDER BY 1;


\echo '== 5. OVERLAP: do our orders and his CANONICAL fills share a window? =='
WITH ours AS (
  SELECT placed_at::date AS day, count(*) AS our_orders,
         count(*) FILTER (WHERE filled > 0) AS our_filled,
         count(DISTINCT us_market_slug) AS our_markets
    FROM mirror_orders WHERE placed_at >= now() - interval '30 days'
   GROUP BY 1
), his AS (
  SELECT t.ts::date AS day, count(*) AS his_rows,
         count(DISTINCT t.condition_id) AS his_conditions,
         count(*) FILTER (WHERE t.source IN ('poll', 'backfill')) AS venue_feed_rows,
         count(*) FILTER (WHERE t.source IN ('chain', 's1'))      AS cash_feed_rows
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.ts >= now() - interval '30 days'
   GROUP BY 1
)
SELECT COALESCE(ours.day, his.day) AS day,
       his.his_rows, his.his_conditions, his.venue_feed_rows, his.cash_feed_rows,
       ours.our_orders, ours.our_filled, ours.our_markets,
       CASE WHEN ours.our_orders IS NULL THEN 'HIS FILLS ONLY -- no replay possible'
            WHEN his.cash_feed_rows IS NULL OR his.cash_feed_rows = 0
              THEN 'no cash feed -- fee unobservable'
            ELSE 'BOTH -- replayable' END AS verdict
  FROM ours FULL OUTER JOIN his ON his.day = ours.day
 ORDER BY 1;


\echo '== 6. IS MAKER/TAKER KNOWABLE AT DECISION TIME? the anti-lookahead test =='
-- At the moment the cash feed tells us a fill happened we have ONE row: total
-- shares and the cash price. We do NOT yet have the venue price, and the leg
-- decomposition arrives with the venue feed a median 275 s later. So the
-- ex-post maker/taker label is NOT available at decision time unless the cash
-- price alone discriminates. It might: a taker's cash price is p + 0.05p(1-p),
-- which lands OFF the venue's cent grid, while a maker's is a VWAP of on-grid
-- levels -- and a single-leg maker fill would be exactly on the grid. This
-- measures how often each is true, so the decision-time field is a reading
-- rather than a hope.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.ts >= now() - interval '30 days'
), f AS (
  SELECT tx_hash, asset, side, feed, count(*) AS n, sum(sh) AS shares,
         sum(sh * px) / NULLIF(sum(sh), 0) AS vwap
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         max(n)    FILTER (WHERE feed = 'venue') AS n_venue,
         max(vwap) FILTER (WHERE feed = 'venue') AS v,
         max(vwap) FILTER (WHERE feed = 'cash')  AS c
    FROM f GROUP BY 1, 2, 3
), e AS (
  SELECT CASE WHEN n_venue IS NULL THEN 'never seen in venue feed'
              WHEN n_venue = 1 THEN 'ex post TAKER (single leg)'
              ELSE 'ex post MAKER (multi leg)' END AS ex_post,
         c,
         -- the cash price itself sitting on the cent grid
         abs(c - round(c::numeric, 2)::float8) < 1e-6 AS cash_on_cent_grid,
         -- the cash price inverted through the taker wedge landing on the grid
         CASE WHEN 1.1025 - 0.2 * c >= 0
              THEN abs(((1.05 - sqrt(1.1025 - 0.2 * c)) / 0.1)
                       - round((((1.05 - sqrt(1.1025 - 0.2 * c)) / 0.1))::numeric, 2)::float8) < 1e-6
         END AS inverted_on_cent_grid
    FROM g WHERE c IS NOT NULL AND c > 0 AND c < 1
)
SELECT ex_post, count(*) AS envelopes,
       count(*) FILTER (WHERE cash_on_cent_grid AND NOT COALESCE(inverted_on_cent_grid, false))
         AS only_RAW_on_grid_says_maker,
       count(*) FILTER (WHERE COALESCE(inverted_on_cent_grid, false) AND NOT cash_on_cent_grid)
         AS only_INVERTED_on_grid_says_taker,
       count(*) FILTER (WHERE cash_on_cent_grid AND COALESCE(inverted_on_cent_grid, false))
         AS BOTH_ambiguous,
       count(*) FILTER (WHERE NOT cash_on_cent_grid
                          AND NOT COALESCE(inverted_on_cent_grid, false)) AS NEITHER_unknown,
       round((100.0 * count(*) FILTER (WHERE cash_on_cent_grid
                                         != COALESCE(inverted_on_cent_grid, false))
              / count(*))::numeric, 2) AS pct_DECIDABLE_at_decision_time
  FROM e GROUP BY 1 ORDER BY 2 DESC;
