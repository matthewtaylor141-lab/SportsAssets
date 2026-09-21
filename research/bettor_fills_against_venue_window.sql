-- THE 52 FILLS, AGAINST THE ONLY VENUE ACTIVITY WE CAN SEE.
--
-- The venue read returned positions: [] and external_positions: [] with
-- configured=true and error=None. That establishes ONE thing: no holdings
-- were reported for that account in that snapshot. It does NOT establish
-- realized P&L, recovered principal, or the disposition of the historical
-- acquisition cost.
--
-- The only venue-side ACTIVITY available is recent_trades: 25 rows spanning
-- 2026-09-20T21:42:55Z .. 2026-09-21T00:45:06Z. A recent WINDOW, not the
-- full history. So the questions here are narrow and answerable:
--
--   (a) how many of the 52 fills fall inside that window at all,
--   (b) what the LEDGER itself says became of each fill (settled_at, pnl,
--       payout, and the exit statuses 045 added), and
--   (c) the six rows with no order_id, in full, so a match can be labelled
--       unique / ambiguous / unmatched -- never invented.
--
-- COLUMN NOTE, because I got this wrong once already: live_orders has no
-- size/price columns. Acquisition is filled_shares x fill_price, recorded
-- as filled_usd. requested_* is intent, filled_* is what happened.
--
-- NOTHING HERE WRITES. If the numbers disagree they stay disagreeing.
\echo == 1. WHAT THE LEDGER SAYS BECAME OF EVERY ROW ==
-- 045 widened status to include cashed_out / exiting / merged / settled.
-- If acquisitions sit in 'filled' with settled_at NULL and pnl NULL, then
-- the ledger records no disposition and the 16,180.53 is an acquisition
-- total with nothing booked against it -- a finding about OUR RECORDS, not
-- about the venue.
SELECT status,
       count(*)                                        AS rows,
       count(*) FILTER (WHERE settled_at IS NOT NULL)  AS have_settled_at,
       count(*) FILTER (WHERE pnl IS NOT NULL)         AS have_pnl,
       count(*) FILTER (WHERE payout IS NOT NULL)      AS have_payout,
       round(sum(filled_usd)::numeric, 2)              AS filled_usd,
       round(sum(pnl)::numeric, 2)                     AS pnl_sum,
       min(placed_at) AS first_placed, max(placed_at) AS last_placed
  FROM live_orders
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 2. EVERY FILLED ROW, WITH THE FIELDS A VENUE ROW COULD MATCH ON ==
-- venue rows carry (market_slug, side, qty, price, time). Those are the only
-- join keys available, because 6 of the 52 have no order_id.
SELECT id,
       whale_username AS lane_user,
       lane,
       venue,
       us_market_slug,
       side,
       filled_shares,
       fill_price,
       round(filled_usd::numeric, 2) AS filled_usd,
       status,
       order_id IS NOT NULL     AS has_order_id,
       condition_id IS NOT NULL AS has_condition_id,
       placed_at,
       settled_at,
       pnl,
       placed_at >= timestamptz '2026-09-20T21:42:55Z'
         AND placed_at <= timestamptz '2026-09-21T00:45:07Z'
           AS inside_venue_window
  FROM live_orders
 WHERE status = 'filled'
 ORDER BY placed_at DESC;

\echo
\echo == 3. HOW MANY FALL INSIDE THE VENUE'S 25-ROW WINDOW AT ALL ==
-- If this is 0, recent_trades cannot corroborate a single historical fill
-- and the window is the wrong instrument for the question. Say so rather
-- than stretching it.
SELECT count(*) AS filled_rows,
       count(*) FILTER (
         WHERE placed_at >= timestamptz '2026-09-20T21:42:55Z'
           AND placed_at <= timestamptz '2026-09-21T00:45:07Z')
           AS inside_window,
       min(placed_at) AS earliest_fill,
       max(placed_at) AS latest_fill,
       round(sum(filled_usd)::numeric, 2) AS acquisition_cost_all
  FROM live_orders
 WHERE status = 'filled';

\echo
\echo == 4. THE SLUGS THE VENUE REPORTED ACTIVITY ON, VS OUR FILLS ==
-- The 25 venue rows name 7 distinct slugs. Does the ledger carry ANY row on
-- those slugs, in any status? A slug the venue traded that we never ordered
-- is an external position by another name.
SELECT us_market_slug, status, count(*) AS rows,
       round(sum(filled_usd)::numeric, 2) AS filled_usd,
       min(placed_at) AS first_placed, max(placed_at) AS last_placed
  FROM live_orders
 WHERE us_market_slug IN (
         'aec-nfl-ind-kc-2026-09-20',
         'tsc-nfl-ind-kc-2026-09-20-1q-0pt5',
         'astatc-nfl-ind-kc-2026-09-20-ptd-p',
         'aec-nfl-was-dal-2026-09-20',
         'asc-nfl-lv-lac-2026-09-20-neg-7pt5',
         'caoc-9c8f7da6507e9416',
         'tsc-nfl-jax-den-2026-09-20-3q-0pt5')
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo
\echo == 5. THE SIX WITHOUT AN order_id, IN FULL ==
-- The mirror lane, and the most valuable of the set. An order_id join cannot
-- reach them whatever the venue returns.
SELECT id, whale_username AS lane_user, lane, venue, us_market_slug,
       condition_id, asset, side, filled_shares, fill_price, orig_shares,
       round(filled_usd::numeric, 2) AS filled_usd,
       status, placed_at, settled_at, pnl, payout
  FROM live_orders
 WHERE status = 'filled' AND order_id IS NULL
 ORDER BY placed_at DESC;

\echo
\echo == 6. WHAT OTHER TABLES CLAIM TO HOLD POSITIONS OR SETTLEMENT ==
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = 'public'
   AND (table_name ILIKE '%position%' OR table_name ILIKE '%settle%'
        OR table_name ILIKE '%book%'  OR table_name ILIKE '%ledger%'
        OR table_name ILIKE '%cash%'  OR table_name ILIKE '%pnl%')
 ORDER BY 1;

\echo
\echo == 7. mirror_books: ITS OWN TWO READINGS DISAGREEING ==
-- 047 keeps ledger_net (shares BY OUR BOOKING) beside venue_net (the last
-- venue read). Where those differ, our books and the venue already
-- disagreed before today, and the row says so itself.
SELECT state,
       count(*) AS books,
       count(*) FILTER (WHERE venue_net IS NULL) AS venue_never_read,
       count(*) FILTER (WHERE venue_net IS NOT NULL
                          AND abs(venue_net - ledger_net) > 0.5) AS disagree,
       round(sum(ledger_net)::numeric, 2) AS ledger_net_sum,
       round(sum(venue_net)::numeric, 2)  AS venue_net_sum,
       max(updated_at) AS last_touched
  FROM mirror_books
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 8. mirror_books VS live_orders, BOTH DIRECTIONS ==
SELECT 'slug in mirror_books, no filled live_order' AS direction,
       count(DISTINCT b.us_market_slug) AS slugs
  FROM mirror_books b
 WHERE NOT EXISTS (SELECT 1 FROM live_orders o
                    WHERE o.us_market_slug = b.us_market_slug
                      AND o.status = 'filled')
UNION ALL
SELECT 'slug has filled live_order, no mirror_book',
       count(DISTINCT o.us_market_slug)
  FROM live_orders o
 WHERE o.status = 'filled'
   AND o.us_market_slug IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM mirror_books b
                    WHERE b.us_market_slug = o.us_market_slug);
