-- ARE THE 52 UNSETTLED ROWS OPEN EXPOSURE, OR AN ACCOUNTING HOLE?
--
-- The exposure read returned HISTORICAL_REAL_FILLS = 52 and
-- CURRENT_UNSETTLED_FILLED_ORDERS = 52. Every fill this system has ever
-- taken is still carried as unsettled, $16,180.53 across 47 markets,
-- all BUY, the oldest placed 2026-08-04 and the newest 2026-09-10.
--
-- THAT IS TWO VERY DIFFERENT CLAIMS WEARING ONE NUMBER:
--
--   (a) the money is genuinely still on the table -- 47 sports markets
--       have not resolved in up to seven weeks, or
--   (b) the markets resolved and nothing ever wrote settled_at, so the
--       ledger reports exposure the account no longer has.
--
-- A 100% unsettled rate across a seven-week span is not what an
-- account with a working settlement writer looks like, so (b) is the
-- hypothesis. But "it looks wrong" is not a measurement: this asks the
-- markets table whether the underlying markets have closed, and asks
-- whether pnl was ever booked on rows still carried open.
--
-- Read only.
\echo == 1. DOES ANY FILLED ROW EVER GET settled_at? ==
SELECT status,
       count(*) AS rows,
       count(*) FILTER (WHERE settled_at IS NOT NULL) AS with_settled_at,
       count(*) FILTER (WHERE pnl IS NOT NULL) AS with_pnl
  FROM live_orders
 GROUP BY status
 ORDER BY rows DESC;

\echo
\echo == 2. HAVE THE UNDERLYING MARKETS RESOLVED? ==
-- If the market is closed/resolved, the position is not open exposure
-- however the ledger has it.
SELECT coalesce(m.closed::text, 'NO MARKET ROW') AS market_closed,
       count(*) AS unsettled_orders,
       round(sum(o.filled_usd)::numeric, 2) AS usd,
       min(o.placed_at)::date AS oldest,
       max(o.placed_at)::date AS newest
  FROM live_orders o
  LEFT JOIN markets m ON m.slug = o.us_market_slug
 WHERE o.status = 'filled' AND o.settled_at IS NULL
 GROUP BY 1
 ORDER BY usd DESC;

\echo
\echo == 3. AGE OF THE UNSETTLED BOOK ==
SELECT width_bucket(extract(epoch FROM (now() - placed_at)) / 86400,
                     0, 56, 8) AS week_bucket,
       min(placed_at)::date AS from_date,
       max(placed_at)::date AS to_date,
       count(*) AS orders,
       round(sum(filled_usd)::numeric, 2) AS usd
  FROM live_orders
 WHERE status = 'filled' AND settled_at IS NULL
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 4. WHICH SLEEVE PLACED THEM ==
SELECT coalesce(whale, 'NONE') AS whale,
       count(*) AS orders,
       count(DISTINCT us_market_slug) AS markets,
       round(sum(filled_usd)::numeric, 2) AS usd,
       max(placed_at)::date AS last_placed
  FROM live_orders
 WHERE status = 'filled' AND settled_at IS NULL
 GROUP BY 1
 ORDER BY usd DESC;

\echo
\echo == 5. IS ANYTHING STILL HELD AT THE VENUE PER OUR OWN MIRROR BOOKS? ==
-- mirror_books is the reconciler's view of what is open. If it says
-- nothing is open while live_orders says 47 markets are, the two
-- ledgers disagree and that disagreement is the finding.
SELECT state, count(*) AS books, max(opened_at)::date AS last_opened
  FROM mirror_books
 GROUP BY state
 ORDER BY books DESC;
