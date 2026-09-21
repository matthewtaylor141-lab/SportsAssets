-- WHAT ARE THE 52 ROWS IN status='filled'?
--
-- CORRECTION TO MY OWN FRAMING (2026-09-21). I wrote the first version
-- of this file asking whether the 52 filled rows were "unsettled
-- because nothing wrote settled_at" -- an accounting hole. Section 1
-- answered a different question than I asked:
--
--     settled     5271 rows   5271 with settled_at   5271 with pnl
--     cashed_out   457 rows    457 with settled_at    454 with pnl
--     filled        52 rows      0 with settled_at     11 with pnl
--
-- SETTLEMENT MOVES THE STATUS. A position that settles leaves
-- status='filled' and becomes 'settled' or 'cashed_out', carrying
-- settled_at with it. So "status='filled' AND settled_at IS NULL" is
-- not a finding, it is a tautology: no row in status='filled' can have
-- settled_at, by construction. My filter added nothing and the 100%
-- rate I thought was suspicious was arithmetic.
--
-- The real reading is simpler and worse to leave unexamined:
-- status='filled' IS the open-position state. 52 open positions,
-- $16,180.53 of cost basis, is what this ledger currently claims.
--
-- WHAT IS STILL OPEN. Whether the VENUE agrees. Section 2 of the first
-- version joined markets.slug to live_orders.us_market_slug and got
-- NO MARKET ROW for all 52 -- but those are different key spaces, so
-- that told us nothing about resolution and is not repeated here.
-- condition_id is the key live_orders actually carries.
--
-- Read only.
\echo == 1. THE 52, BY SOURCE SLEEVE ==
SELECT coalesce(whale_username, 'NONE') AS sleeve,
       count(*) AS orders,
       count(DISTINCT condition_id) AS conditions,
       round(sum(filled_usd)::numeric, 2) AS cost_usd,
       count(*) FILTER (WHERE pnl IS NOT NULL) AS with_pnl,
       max(placed_at)::date AS last_placed
  FROM live_orders
 WHERE status = 'filled'
 GROUP BY 1
 ORDER BY cost_usd DESC;

\echo
\echo == 2. DO THE UNDERLYING MARKETS STILL EXIST AS OPEN? ==
-- condition_id is the key live_orders carries, so this is the join
-- that can actually answer it.
SELECT CASE WHEN m.condition_id IS NULL THEN 'NO MARKET ROW'
            WHEN m.closed THEN 'MARKET CLOSED'
            ELSE 'MARKET OPEN' END AS market_state,
       count(*) AS orders,
       round(sum(o.filled_usd)::numeric, 2) AS cost_usd,
       min(o.placed_at)::date AS oldest,
       max(o.placed_at)::date AS newest
  FROM live_orders o
  LEFT JOIN markets m ON m.condition_id = o.condition_id
 WHERE o.status = 'filled'
 GROUP BY 1
 ORDER BY cost_usd DESC;

\echo
\echo == 3. THE ELEVEN WITH pnl ALREADY BOOKED ==
-- mirror_exit's partial branch writes a row back to status='filled'
-- after booking pnl on the part it sold. These are the residuals.
SELECT count(*) AS rows,
       round(sum(filled_usd)::numeric, 2) AS cost_usd,
       round(sum(pnl)::numeric, 2) AS pnl_booked,
       min(placed_at)::date AS oldest,
       max(placed_at)::date AS newest
  FROM live_orders
 WHERE status = 'filled' AND pnl IS NOT NULL;

\echo
\echo == 4. DOES THE RECONCILER AGREE ANYTHING IS OPEN? ==
-- mirror_books is the P1 reconciler's own view. If it says nothing is
-- open while live_orders carries 52, the two ledgers disagree and that
-- disagreement is the finding.
SELECT state, count(*) AS books, max(opened_at)::date AS last_opened
  FROM mirror_books
 GROUP BY state
 ORDER BY books DESC;

\echo
\echo == 5. HAS ANYTHING BEEN PLACED SINCE THE LAST FILL? ==
SELECT status, count(*) AS orders, max(placed_at) AS last_placed
  FROM live_orders
 WHERE placed_at > timestamptz '2026-09-10 17:06:50.527288+00'
 GROUP BY status
 ORDER BY orders DESC;
