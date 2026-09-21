-- WHICH VENUE AND ACCOUNT OWN THE 52 FILLS? Read only.
--
-- This has to be settled before any reconciliation is attempted, and it
-- was not. venue_reconcile reads Polymarket US -- positions, activities
-- and resting orders through the PMUS SDK. If the fills were placed on
-- the CLOB instead, that reconciler would query an account that never
-- saw them, find nothing, and report 52 rows NOT_FOUND_AT_VENUE. The
-- result would look like a catastrophic accounting failure and would
-- actually be me asking the wrong venue.
--
-- migration 008 added live_orders.venue with DEFAULT 'polymarket-clob',
-- and the session's own audit found a second, separate submission path
-- (live_executor._submit_fok) that reaches the CLOB and never touches
-- pmus.submit_fok. Two venues, two adapters, two sets of credentials.
-- The ledger records which one each row used; nothing so far has
-- looked.
\echo == 1. THE 52 OPEN FILLS, BY VENUE ==
SELECT coalesce(venue, 'NULL') AS venue,
       count(*) AS orders,
       count(DISTINCT us_market_slug) AS markets,
       round(sum(filled_usd)::numeric, 2) AS cost_usd,
       min(placed_at)::date AS oldest,
       max(placed_at)::date AS newest
  FROM live_orders
 WHERE status = 'filled'
 GROUP BY 1
 ORDER BY cost_usd DESC;

\echo
\echo == 2. EVERY live_orders ROW EVER, BY VENUE AND STATUS ==
-- Context for the above: if one venue carries the whole history and the
-- other carries the open rows, that is itself the finding.
SELECT coalesce(venue, 'NULL') AS venue, status,
       count(*) AS orders,
       round(sum(filled_usd)::numeric, 2) AS filled_usd,
       min(placed_at)::date AS oldest,
       max(placed_at)::date AS newest
  FROM live_orders
 GROUP BY 1, 2
 ORDER BY 1, orders DESC;

\echo
\echo == 3. DOES THE SLUG SHAPE AGREE WITH THE VENUE COLUMN? ==
-- A PMUS market slug and a CLOB condition id look nothing alike. If the
-- column says one thing and the identifier says another, the column is
-- not trustworthy and neither is anything derived from it.
SELECT coalesce(venue, 'NULL') AS venue,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS has_us_slug,
       count(*) FILTER (WHERE condition_id IS NOT NULL) AS has_condition_id,
       count(*) FILTER (WHERE order_id IS NOT NULL) AS has_order_id,
       count(*) FILTER (WHERE us_market_slug IS NULL
                          AND condition_id IS NULL) AS has_neither
  FROM live_orders
 WHERE status = 'filled'
 GROUP BY 1;

\echo
\echo == 4. WHICH LANE PLACED THEM ==
SELECT coalesce(venue, 'NULL') AS venue,
       coalesce(lane, 'NULL') AS lane,
       coalesce(whale_username, 'NONE') AS sleeve,
       count(*) AS orders,
       round(sum(filled_usd)::numeric, 2) AS cost_usd
  FROM live_orders
 WHERE status = 'filled'
 GROUP BY 1, 2, 3
 ORDER BY cost_usd DESC;

\echo
\echo == 5. A SAMPLE OF THE IDENTIFIERS, SHAPE ONLY ==
-- Enough to tell a PMUS slug from a CLOB token. No amounts, no account
-- identifiers beyond what the ledger already stores.
SELECT venue,
       left(coalesce(us_market_slug, '(null)'), 40) AS us_market_slug,
       left(coalesce(condition_id, '(null)'), 20) AS condition_id_head,
       left(coalesce(order_id, '(null)'), 20) AS order_id_head,
       placed_at::date AS placed
  FROM live_orders
 WHERE status = 'filled'
 ORDER BY placed_at DESC
 LIMIT 12;
