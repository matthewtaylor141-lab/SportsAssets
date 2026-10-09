-- READ-ONLY. RC6.2 lane p-evcontrols (REWORK stage). SELECT / EXPLAIN only.
-- What completion.evidence.read_twin (DIGITAL_TWIN) costs per order, so its
-- 800-order bound can become a safety stop well above any plausible
-- population instead of a cliff. TWIN_SQL is reproduced EXACTLY as
-- backend/sportsassets/completion/evidence.py runs it (b3f1b0cd), with $1 and
-- $2 written in:
--   production today: $1 = 1791384986 (2026-10-07T14:56:26Z), $2 = 801
--   the whole history (the measurable sample of the same predicate):
--     $1 = 0, $2 = 20001
--  T1  the twin population today, and the same predicate over all history
--  T2  per-order shape: the IOC window (expires - eligible), the recorded
--      books inside it, fills per order
--  T3  the payload read_twin receives: bytes of `j` per order
--  T4  EXPLAIN ANALYZE of TWIN_SQL as production runs it today
--  T5  EXPLAIN ANALYZE of TWIN_SQL over the whole history (cold, then warm)
--  T6  EXPLAIN ANALYZE of one keyset page of 500 over the whole history
--      (the paged read: ORDER BY eligible_at, order_id)

\echo == T0 read instant
SELECT now() AS read_at, extract(epoch FROM now()) AS read_epoch;

\echo == T1 twin predicate: fresh (after 1791384986) and all history
SELECT count(*) FILTER (WHERE o.eligible_at > to_timestamp(1791384986))
         AS fresh_population,
       count(*) AS all_history_population,
       min(o.eligible_at) AS oldest, max(o.eligible_at) AS newest,
       count(DISTINCT o.us_market_slug) AS markets
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE');

\echo == T1b all history by UTC day x role
SELECT (o.eligible_at AT TIME ZONE 'UTC')::date AS day, o.role,
       count(*) AS n
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == T2 per-order shape over all history: IOC window seconds, books in window, fills
WITH o AS (
  SELECT o.order_id, o.us_market_slug, o.eligible_at, o.expires_at,
         extract(epoch FROM o.expires_at - o.eligible_at) AS window_s,
         (SELECT count(*) FROM paper_book_observations b
           WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
             AND b.observed_at >= o.eligible_at
             AND b.observed_at <= o.expires_at) AS books_in_window,
         (SELECT count(*) FROM paper_fills pf
           WHERE pf.order_id = o.order_id) AS fills
    FROM paper_orders o
   WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
     AND o.role IN ('ENTRY', 'EXIT', 'REDUCE'))
SELECT count(*) AS orders,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY window_s) AS window_s_p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY window_s) AS window_s_p95,
       max(window_s) AS window_s_max,
       avg(books_in_window) AS books_avg,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY books_in_window)
         AS books_p95,
       max(books_in_window) AS books_max,
       sum(books_in_window) AS books_total,
       avg(fills) AS fills_avg, max(fills) AS fills_max
  FROM o;

\echo == T3 payload: bytes of j per order (TWIN_SQL over all history)
WITH t AS (
SELECT json_build_object(
  'order_id', o.order_id, 'role', o.role, 'tif', o.time_in_force,
  'direction', o.direction, 'side', o.holding_side, 'slug', o.us_market_slug,
  'state', o.state, 'qty', o.qty, 'limit', o.limit_price,
  'created', extract(epoch FROM o.created_at),
  'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at),
  'terminal', extract(epoch FROM o.terminal_at),
  'fills', (SELECT json_agg(json_build_object('at', extract(epoch FROM pf.filled_at),
                   'qty', pf.qty, 'price', pf.price) ORDER BY pf.filled_at)
              FROM paper_fills pf WHERE pf.order_id = o.order_id),
  'books', (SELECT json_agg(json_build_object('at', extract(epoch FROM b.observed_at),
                   'bids', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.bids)='array'
                                       THEN b.bids ELSE '[]' END) l),
                   'asks', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.offers)='array'
                                       THEN b.offers ELSE '[]' END) l))
                   ORDER BY b.observed_at)
              FROM paper_book_observations b
             WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
               AND ((b.observed_at >= o.eligible_at
                     AND b.observed_at <= o.expires_at)
                    OR b.obs_id = (
                      SELECT p.obs_id FROM paper_book_observations p
                       WHERE p.error IS NULL
                         AND p.us_market_slug = o.us_market_slug
                         AND p.observed_at <= o.eligible_at
                       ORDER BY p.observed_at DESC LIMIT 1))))::text AS j
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp(0)
 ORDER BY o.eligible_at
 LIMIT 20001)
SELECT count(*) AS orders, sum(length(j)) AS bytes_total,
       round(avg(length(j))) AS bytes_avg,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY length(j)) AS bytes_p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY length(j)) AS bytes_p95,
       max(length(j)) AS bytes_max
  FROM t;

\echo == T4 EXPLAIN ANALYZE TWIN_SQL as production runs it today ($1 = 1791384986, LIMIT 801)
EXPLAIN (ANALYZE, BUFFERS)
SELECT json_build_object(
  'order_id', o.order_id, 'role', o.role, 'tif', o.time_in_force,
  'direction', o.direction, 'side', o.holding_side, 'slug', o.us_market_slug,
  'state', o.state, 'qty', o.qty, 'limit', o.limit_price,
  'created', extract(epoch FROM o.created_at),
  'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at),
  'terminal', extract(epoch FROM o.terminal_at),
  'fills', (SELECT json_agg(json_build_object('at', extract(epoch FROM pf.filled_at),
                   'qty', pf.qty, 'price', pf.price) ORDER BY pf.filled_at)
              FROM paper_fills pf WHERE pf.order_id = o.order_id),
  'books', (SELECT json_agg(json_build_object('at', extract(epoch FROM b.observed_at),
                   'bids', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.bids)='array'
                                       THEN b.bids ELSE '[]' END) l),
                   'asks', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.offers)='array'
                                       THEN b.offers ELSE '[]' END) l))
                   ORDER BY b.observed_at)
              FROM paper_book_observations b
             WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
               AND ((b.observed_at >= o.eligible_at
                     AND b.observed_at <= o.expires_at)
                    OR b.obs_id = (
                      SELECT p.obs_id FROM paper_book_observations p
                       WHERE p.error IS NULL
                         AND p.us_market_slug = o.us_market_slug
                         AND p.observed_at <= o.eligible_at
                       ORDER BY p.observed_at DESC LIMIT 1))))::text AS j
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp(1791384986)
 ORDER BY o.eligible_at
 LIMIT 801;

\echo == T5a EXPLAIN ANALYZE TWIN_SQL over the whole history ($1 = 0, LIMIT 20001), first run
EXPLAIN (ANALYZE, BUFFERS)
SELECT json_build_object(
  'order_id', o.order_id, 'role', o.role, 'tif', o.time_in_force,
  'direction', o.direction, 'side', o.holding_side, 'slug', o.us_market_slug,
  'state', o.state, 'qty', o.qty, 'limit', o.limit_price,
  'created', extract(epoch FROM o.created_at),
  'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at),
  'terminal', extract(epoch FROM o.terminal_at),
  'fills', (SELECT json_agg(json_build_object('at', extract(epoch FROM pf.filled_at),
                   'qty', pf.qty, 'price', pf.price) ORDER BY pf.filled_at)
              FROM paper_fills pf WHERE pf.order_id = o.order_id),
  'books', (SELECT json_agg(json_build_object('at', extract(epoch FROM b.observed_at),
                   'bids', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.bids)='array'
                                       THEN b.bids ELSE '[]' END) l),
                   'asks', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.offers)='array'
                                       THEN b.offers ELSE '[]' END) l))
                   ORDER BY b.observed_at)
              FROM paper_book_observations b
             WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
               AND ((b.observed_at >= o.eligible_at
                     AND b.observed_at <= o.expires_at)
                    OR b.obs_id = (
                      SELECT p.obs_id FROM paper_book_observations p
                       WHERE p.error IS NULL
                         AND p.us_market_slug = o.us_market_slug
                         AND p.observed_at <= o.eligible_at
                       ORDER BY p.observed_at DESC LIMIT 1))))::text AS j
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp(0)
 ORDER BY o.eligible_at
 LIMIT 20001;

\echo == T5b the same, second run (warm)
EXPLAIN (ANALYZE, BUFFERS)
SELECT json_build_object(
  'order_id', o.order_id, 'role', o.role, 'tif', o.time_in_force,
  'direction', o.direction, 'side', o.holding_side, 'slug', o.us_market_slug,
  'state', o.state, 'qty', o.qty, 'limit', o.limit_price,
  'created', extract(epoch FROM o.created_at),
  'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at),
  'terminal', extract(epoch FROM o.terminal_at),
  'fills', (SELECT json_agg(json_build_object('at', extract(epoch FROM pf.filled_at),
                   'qty', pf.qty, 'price', pf.price) ORDER BY pf.filled_at)
              FROM paper_fills pf WHERE pf.order_id = o.order_id),
  'books', (SELECT json_agg(json_build_object('at', extract(epoch FROM b.observed_at),
                   'bids', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.bids)='array'
                                       THEN b.bids ELSE '[]' END) l),
                   'asks', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.offers)='array'
                                       THEN b.offers ELSE '[]' END) l))
                   ORDER BY b.observed_at)
              FROM paper_book_observations b
             WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
               AND ((b.observed_at >= o.eligible_at
                     AND b.observed_at <= o.expires_at)
                    OR b.obs_id = (
                      SELECT p.obs_id FROM paper_book_observations p
                       WHERE p.error IS NULL
                         AND p.us_market_slug = o.us_market_slug
                         AND p.observed_at <= o.eligible_at
                       ORDER BY p.observed_at DESC LIMIT 1))))::text AS j
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp(0)
 ORDER BY o.eligible_at
 LIMIT 20001;

\echo == T6 EXPLAIN ANALYZE one keyset page of 500 over the whole history (ORDER BY eligible_at, order_id; after the start)
EXPLAIN (ANALYZE, BUFFERS)
SELECT json_build_object(
  'order_id', o.order_id, 'role', o.role, 'tif', o.time_in_force,
  'direction', o.direction, 'side', o.holding_side, 'slug', o.us_market_slug,
  'state', o.state, 'qty', o.qty, 'limit', o.limit_price,
  'created', extract(epoch FROM o.created_at),
  'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at),
  'terminal', extract(epoch FROM o.terminal_at),
  'fills', (SELECT json_agg(json_build_object('at', extract(epoch FROM pf.filled_at),
                   'qty', pf.qty, 'price', pf.price) ORDER BY pf.filled_at)
              FROM paper_fills pf WHERE pf.order_id = o.order_id),
  'books', (SELECT json_agg(json_build_object('at', extract(epoch FROM b.observed_at),
                   'bids', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.bids)='array'
                                       THEN b.bids ELSE '[]' END) l),
                   'asks', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.offers)='array'
                                       THEN b.offers ELSE '[]' END) l))
                   ORDER BY b.observed_at)
              FROM paper_book_observations b
             WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
               AND ((b.observed_at >= o.eligible_at
                     AND b.observed_at <= o.expires_at)
                    OR b.obs_id = (
                      SELECT p.obs_id FROM paper_book_observations p
                       WHERE p.error IS NULL
                         AND p.us_market_slug = o.us_market_slug
                         AND p.observed_at <= o.eligible_at
                       ORDER BY p.observed_at DESC LIMIT 1))))::text AS j,
  o.eligible_at AS k_at, o.order_id AS k_id
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp(0)
   AND (o.eligible_at, o.order_id) > (to_timestamp(0), '')
 ORDER BY o.eligible_at, o.order_id
 LIMIT 501;
