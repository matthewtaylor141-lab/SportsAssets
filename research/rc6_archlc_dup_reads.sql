-- READ-ONLY. RC6 lane archer-lifecycle, second pass. The first pass
-- (rc6_archlc_dup_identities.sql) showed that the observation rows the P0
-- duplicate groups filled on are often COPIES of ONE venue read: a shared
-- read is recorded once per caller, each row with the ORIGINAL receipt
-- instant (bettor_paper_simulator.record_book, ":SHARED_READ"), so one book
-- appears as several paper_book_observations rows with one observed_at. The
-- consumed-liquidity ledger is keyed by the row (obs_id), not by the read.
-- Here the canonical identity of observed liquidity is the READ (market,
-- observed_at): every fill is placed on its read, and the reads around it
-- are measured. Every statement is a SELECT.

\echo F1 every fill of the orders of the P0 groups, on its READ (first row of the read = lowest obs_id with that market and observed_at)
WITH o AS (
  SELECT DISTINCT order_id FROM (
    SELECT order_id FROM paper_fills GROUP BY order_id, qty, price, filled_at
    HAVING count(*) > 1) x)
SELECT f.order_id, f.book_obs_id AS obs,
       to_char(b.observed_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS.MS') AS read_at,
       (SELECT min(b2.obs_id) FROM paper_book_observations b2
         WHERE b2.us_market_slug = b.us_market_slug
           AND b2.observed_at = b.observed_at) AS read_first_obs,
       (SELECT count(*) FROM paper_book_observations b2
         WHERE b2.us_market_slug = b.us_market_slug
           AND b2.observed_at = b.observed_at) AS read_rows,
       (po.queue_basis->>'placement_obs_id') AS placement_obs,
       f.wire_price, f.qty, f.evidence->'level'->>'displayed' AS displayed,
       f.evidence->>'crossing_qty' AS crossing, f.evidence->>'queue_ahead_before' AS q_before,
       f.evidence->>'queue_ahead_after' AS q_after,
       to_char(f.filled_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS.MS') AS filled_at
  FROM paper_fills f JOIN o USING (order_id)
  JOIN paper_orders po ON po.order_id = f.order_id
  JOIN paper_book_observations b ON b.obs_id = f.book_obs_id
 ORDER BY f.order_id, f.book_obs_id, f.wire_price;

\echo F2 SAME-READ CENSUS over the whole ledger: fills taken on a row that is NOT the first row of its read, by window (producer fix 2026-10-06T05:16:06Z), order type, role, and whether the first row of the read is the placement row of the order or earlier
WITH f AS (
  SELECT f.fill_id, f.order_id, f.qty, f.filled_at, f.book_obs_id, f.role,
         po.order_type, (po.queue_basis->>'placement_obs_id')::bigint AS placement_obs,
         (SELECT min(b2.obs_id) FROM paper_book_observations b2
           WHERE b2.us_market_slug = b.us_market_slug
             AND b2.observed_at = b.observed_at) AS first_obs
    FROM paper_fills f
    JOIN paper_orders po ON po.order_id = f.order_id
    JOIN paper_book_observations b ON b.obs_id = f.book_obs_id)
SELECT CASE WHEN filled_at >= timestamptz '2026-10-06 05:16:06+00'
            THEN 'AT_OR_AFTER_FIX' ELSE 'BEFORE_FIX' END AS win,
       order_type, role,
       CASE WHEN book_obs_id = first_obs THEN 'FIRST_ROW_OF_READ'
            WHEN placement_obs IS NOT NULL AND first_obs <= placement_obs
              THEN 'COPY_OF_PLACEMENT_OR_EARLIER_READ'
            ELSE 'COPY_OF_A_READ_THE_ORDER_EXAMINED' END AS row_kind,
       count(*) AS fills, round(sum(qty), 6) AS qty,
       count(DISTINCT order_id) AS orders,
       to_char(min(filled_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS first,
       to_char(max(filled_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS last
  FROM f GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo F3 the copy-row fills at or after the fix, each (bounded 300)
WITH f AS (
  SELECT f.order_id, f.role, po.order_type, f.us_market_slug, f.holding_side,
         f.direction, f.wire_price, f.qty, f.book_obs_id, f.filled_at,
         b.observed_at, (po.queue_basis->>'placement_obs_id')::bigint AS placement_obs,
         f.evidence->'level'->>'displayed' AS displayed,
         (SELECT min(b2.obs_id) FROM paper_book_observations b2
           WHERE b2.us_market_slug = b.us_market_slug
             AND b2.observed_at = b.observed_at) AS first_obs
    FROM paper_fills f
    JOIN paper_orders po ON po.order_id = f.order_id
    JOIN paper_book_observations b ON b.obs_id = f.book_obs_id
   WHERE f.filled_at >= timestamptz '2026-10-06 05:16:06+00')
SELECT order_id, role, order_type, us_market_slug, holding_side, direction,
       wire_price, qty, displayed, book_obs_id, first_obs, placement_obs,
       to_char(observed_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS.MS') AS read_at,
       to_char(filled_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS.MS') AS filled_at
  FROM f WHERE book_obs_id <> first_obs
 ORDER BY filled_at, order_id, wire_price LIMIT 300;

\echo F4 ONE READ LEVEL CONSUMED THROUGH SEVERAL ROWS (any orders): per market, consumed side, wire and read, the rows, orders and qty taken against what the read displayed, by window
WITH f AS (
  SELECT f.order_id, f.us_market_slug, f.wire_price, f.qty, f.book_obs_id,
         f.filled_at, b.observed_at,
         CASE WHEN (f.direction = 'SELL') = (f.holding_side = 'SHORT')
              THEN 'offers' ELSE 'bids' END AS side,
         (f.evidence->'level'->>'displayed')::numeric AS displayed
    FROM paper_fills f JOIN paper_book_observations b ON b.obs_id = f.book_obs_id),
lv AS (
  SELECT us_market_slug, side, wire_price, observed_at,
         count(DISTINCT book_obs_id) AS rows_n, count(DISTINCT order_id) AS orders,
         sum(qty) AS taken, max(displayed) AS displayed,
         min(filled_at) AS first_fill, max(filled_at) AS last_fill
    FROM f GROUP BY 1, 2, 3, 4 HAVING count(DISTINCT book_obs_id) > 1)
SELECT CASE WHEN last_fill >= timestamptz '2026-10-06 05:16:06+00'
            THEN 'AT_OR_AFTER_FIX' ELSE 'BEFORE_FIX' END AS win,
       orders > 1 AS cross_order, count(*) AS levels, sum(rows_n) AS rows_n,
       round(sum(taken), 6) AS taken, round(sum(greatest(taken - displayed, 0)), 6)
         AS taken_beyond_displayed,
       to_char(min(first_fill) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS first,
       to_char(max(last_fill) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS last
  FROM lv GROUP BY 1, 2 ORDER BY 1, 2;

\echo F5 the same, at or after the fix, each level (bounded 200)
WITH f AS (
  SELECT f.order_id, f.us_market_slug, f.wire_price, f.qty, f.book_obs_id,
         f.filled_at, b.observed_at,
         CASE WHEN (f.direction = 'SELL') = (f.holding_side = 'SHORT')
              THEN 'offers' ELSE 'bids' END AS side,
         (f.evidence->'level'->>'displayed')::numeric AS displayed
    FROM paper_fills f JOIN paper_book_observations b ON b.obs_id = f.book_obs_id
   WHERE f.filled_at >= timestamptz '2026-10-06 05:16:06+00')
SELECT us_market_slug, side, wire_price,
       to_char(observed_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS.MS') AS read_at,
       count(DISTINCT book_obs_id) AS rows_n, count(DISTINCT order_id) AS orders,
       string_agg(order_id || '@' || book_obs_id || ':' || qty, ' ' ORDER BY book_obs_id) AS fills,
       max(displayed) AS displayed, sum(qty) AS taken
  FROM f GROUP BY 1, 2, 3, 4 HAVING count(DISTINCT book_obs_id) > 1
 ORDER BY 4 LIMIT 200;

\echo F6 CROSS-READ REFILLS: one order, one wire level, filled on two DIFFERENT reads; the displayed size of that level on the last readable row before the later read (the seen-crossing memory of the producer since 221ce6b9), and the minimum between
WITH f AS (
  SELECT f.order_id, f.us_market_slug, f.wire_price, f.qty, f.book_obs_id,
         f.filled_at, b.observed_at,
         CASE WHEN (f.direction = 'SELL') = (f.holding_side = 'SHORT')
              THEN 'offers' ELSE 'bids' END AS side,
         (f.evidence->'level'->>'displayed')::numeric AS displayed
    FROM paper_fills f JOIN paper_book_observations b ON b.obs_id = f.book_obs_id),
r AS (
  SELECT order_id, us_market_slug, side, wire_price, observed_at,
         sum(qty) AS taken, max(displayed) AS displayed, min(filled_at) AS filled_at,
         min(book_obs_id) AS obs
    FROM f GROUP BY 1, 2, 3, 4, 5),
p AS (
  SELECT r.*, lag(observed_at) OVER w AS prev_read, lag(taken) OVER w AS prev_taken,
         lag(displayed) OVER w AS prev_displayed
    FROM r WINDOW w AS (PARTITION BY order_id, wire_price ORDER BY observed_at)),
mid AS (
  SELECT p.order_id, p.wire_price, p.observed_at, b.obs_id, b.observed_at AS mid_at,
         (SELECT max(CASE WHEN coalesce(e->'qty'->>'value', e->>'qty', e->>'size') ~ '^[0-9]*\.?[0-9]+$'
                          THEN coalesce(e->'qty'->>'value', e->>'qty', e->>'size')::numeric END)
            FROM jsonb_array_elements(CASE WHEN p.side = 'offers' THEN coalesce(b.offers, '[]'::jsonb)
                                           ELSE coalesce(b.bids, '[]'::jsonb) END) e
           WHERE coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price')
                 ~ '^[0-9]*\.?[0-9]+$'
             AND abs(coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price')::numeric
                     - p.wire_price) < 0.0000005) AS shown
    FROM p JOIN paper_book_observations b
      ON b.us_market_slug = p.us_market_slug AND b.observed_at > p.prev_read
     AND b.observed_at < p.observed_at AND coalesce(b.error, '') = ''
   WHERE p.prev_read IS NOT NULL)
SELECT p.order_id, p.side, p.wire_price,
       to_char(p.prev_read AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS first_read,
       p.prev_displayed, p.prev_taken,
       to_char(p.observed_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS later_read,
       p.displayed, p.taken,
       (SELECT count(*) FROM mid m WHERE m.order_id = p.order_id AND m.wire_price = p.wire_price
           AND m.observed_at = p.observed_at) AS reads_between,
       (SELECT min(coalesce(m.shown, 0)) FROM mid m WHERE m.order_id = p.order_id
           AND m.wire_price = p.wire_price AND m.observed_at = p.observed_at) AS min_shown_between,
       (SELECT coalesce(m.shown, 0) FROM mid m WHERE m.order_id = p.order_id
           AND m.wire_price = p.wire_price AND m.observed_at = p.observed_at
         ORDER BY m.mid_at DESC, m.obs_id DESC LIMIT 1) AS shown_last_before,
       CASE WHEN p.filled_at >= timestamptz '2026-10-06 05:16:06+00'
            THEN 'AT_OR_AFTER_FIX' ELSE 'BEFORE_FIX' END AS win
  FROM p WHERE p.prev_read IS NOT NULL
 ORDER BY p.filled_at, p.order_id, p.wire_price;

\echo F7 how often one read is recorded as several rows, per day (copies = rows beyond the first of a read)
SELECT to_char(date_trunc('day', observed_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day,
       count(*) AS rows_n,
       count(*) - count(DISTINCT (us_market_slug, observed_at)) AS copy_rows,
       count(*) FILTER (WHERE source LIKE '%SHARED_READ') AS shared_read_rows
  FROM paper_book_observations
 WHERE observed_at > now() - interval '9 days'
 GROUP BY 1 ORDER BY 1;
