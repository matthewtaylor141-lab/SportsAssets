-- THE 52 ARE A RESIDUAL, NOT A PORTFOLIO. FOLLOW THEM AS FAR AS THE DATA GOES.
--
-- The previous read changed the question. live_orders is not 52 rows of open
-- exposure beside a settled history -- it is 166,585 rows in eight states, of
-- which 5,271 SETTLED (425,812.30 acquired, settled_at and pnl on every row)
-- and 457 CASHED_OUT (40,113.00, settled_at on every row). The 52 in status
-- filled have settled_at on NONE and pnl on 11. They are the rows whose
-- disposition was never written back.
--
-- BEFORE QUERYING, TWO TABLES RULED OUT BY THEIR SCHEMA, NOT BY A ZERO COUNT:
--
--   bettor_state_settlements (088) is keyed to observation_id REFERENCES
--   bettor_state_observations, and carries settlement_outcome /
--   settlement_ts / settlement_semantics_status. It is the BETTOR research
--   corpus settling its own observations. It has no us_market_slug and no
--   link to live_orders, so it cannot settle a fill.
--
--   positions (001) and api_positions (002) are both keyed to whale_id
--   REFERENCES whales. They are the TRACKED WHALE positions -- the accounts
--   we copy -- not our own holdings. Reading our exposure out of them would
--   be reading someone else book.
--
-- So the candidate list from the table-name sweep shrinks on inspection, and
-- the shape of the answer is already visible: apart from live_orders itself
-- and mirror_books, this database may hold NO record of our own venue
-- positions. Sections 1 and 2 test that rather than assert it.
--
-- Read only. Nothing is reconciled, closed or adjusted.
--
-- NOTE ON \echo: psql treats an apostrophe as an unterminated quoted string
-- and prints an error before continuing. The previous file hit that on the
-- word VENUE-apostrophe-S. No apostrophes below.
\echo == 1. WHAT EACH CANDIDATE TABLE IS ACTUALLY KEYED TO ==
SELECT c.relname AS table_name,
       (SELECT count(*) FROM information_schema.columns ic
         WHERE ic.table_name = c.relname
           AND ic.column_name = 'whale_id')        AS keyed_to_whale,
       (SELECT count(*) FROM information_schema.columns ic
         WHERE ic.table_name = c.relname
           AND ic.column_name = 'us_market_slug')  AS has_us_slug,
       (SELECT count(*) FROM information_schema.columns ic
         WHERE ic.table_name = c.relname
           AND ic.column_name = 'observation_id')  AS keyed_to_observation
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
   AND c.relname IN ('api_positions', 'positions', 'shadow_positions',
                     'shadow_position_events', 'mirror_registered_positions',
                     'bettor_experimental_positions',
                     'bettor_experimental_position_events',
                     'bettor_state_settlements', 'mirror_books', 'live_orders')
 ORDER BY 1;

\echo
\echo == 2. ROW COUNTS, SO AN EMPTY TABLE IS NOT MISTAKEN FOR A MISSING ONE ==
SELECT 'positions' AS tbl, count(*) AS rows FROM positions
UNION ALL SELECT 'api_positions', count(*) FROM api_positions
UNION ALL SELECT 'shadow_positions', count(*) FROM shadow_positions
UNION ALL SELECT 'mirror_registered_positions', count(*)
            FROM mirror_registered_positions
UNION ALL SELECT 'bettor_experimental_positions', count(*)
            FROM bettor_experimental_positions
UNION ALL SELECT 'bettor_state_settlements', count(*)
            FROM bettor_state_settlements
ORDER BY 1;

\echo
\echo == 3. DID THE 52 EVER BECOME A SETTLED OR CASHED_OUT ROW ELSEWHERE ==
-- One market can carry several live_orders rows. If a slug from the 52 also
-- appears under settled or cashed_out, the position WAS closed and only this
-- row was left behind: bookkeeping residue, not exposure. If it appears
-- nowhere else, the acquisition has no recorded disposition at all, which is
-- a different and worse finding. Both are reported, neither is assumed.
SELECT f.id,
       f.us_market_slug,
       round(f.filled_usd::numeric, 2) AS our_acquisition,
       count(x.id) FILTER (WHERE x.status = 'settled')    AS also_settled,
       count(x.id) FILTER (WHERE x.status = 'cashed_out') AS also_cashed_out,
       count(x.id) FILTER (WHERE x.status = 'exiting')    AS also_exiting,
       count(x.id) FILTER (WHERE x.status = 'merged')     AS also_merged,
       round(sum(x.pnl) FILTER (
             WHERE x.status IN ('settled', 'cashed_out'))::numeric, 2)
           AS closed_pnl_same_slug,
       max(x.settled_at) AS last_settlement_on_slug
  FROM live_orders f
  LEFT JOIN live_orders x
         ON x.us_market_slug = f.us_market_slug
        AND x.id <> f.id
 WHERE f.status = 'filled'
 GROUP BY 1, 2, 3
 ORDER BY 3 DESC;

\echo
\echo == 4. THE SAME, SUMMARISED, WITH THE MONEY SPLIT BY CATEGORY ==
-- Four buckets kept apart on purpose: historical acquisition cost; rows
-- whose slug shows a recorded closure; rows whose slug shows none; and the
-- part that is simply unexplained.
WITH f AS (
  SELECT o.id, o.filled_usd,
         EXISTS (SELECT 1 FROM live_orders x
                  WHERE x.us_market_slug = o.us_market_slug
                    AND x.id <> o.id
                    AND x.status IN ('settled', 'cashed_out')) AS slug_closed
    FROM live_orders o
   WHERE o.status = 'filled'
)
SELECT count(*) AS rows,
       count(*) FILTER (WHERE slug_closed)     AS slug_shows_a_closure,
       count(*) FILTER (WHERE NOT slug_closed) AS slug_shows_none,
       round(sum(filled_usd)::numeric, 2) AS acquisition_all,
       round(sum(filled_usd) FILTER (WHERE slug_closed)::numeric, 2)
           AS acquisition_where_slug_closed,
       round(sum(filled_usd) FILTER (WHERE NOT slug_closed)::numeric, 2)
           AS acquisition_where_no_closure
  FROM f;

\echo
\echo == 5. THE LEDGER WENT QUIET ON 2026-09-10. THE VENUE DID NOT. ==
-- Every live_orders status has its last row on or before 2026-09-10, yet the
-- venue reported 25 trades on 2026-09-20 and 2026-09-21 on NFL markets this
-- ledger has never seen in any status. Confirm the silence directly rather
-- than inferring it from a max().
SELECT date_trunc('day', placed_at) AS day,
       count(*) AS rows,
       count(*) FILTER (WHERE status = 'filled') AS filled
  FROM live_orders
 WHERE placed_at > now() - interval '21 days'
 GROUP BY 1
 ORDER BY 1 DESC;

\echo
\echo == 6. THE SIX MIRROR ROWS DO NOT RECONCILE AGAINST THEIR OWN FIELDS ==
-- filled_shares x fill_price misses filled_usd by three to four orders of
-- magnitude on five of six. orig_shares x fill_price reproduces it EXACTLY
-- on two and misses on four. At least one of the three fields was written by
-- a different process than the other two, so 8,911.89 is not a verified
-- acquisition cost. All three reconstructions printed side by side rather
-- than whichever one agrees.
SELECT id, us_market_slug,
       filled_shares, orig_shares, fill_price,
       round(filled_usd::numeric, 2) AS recorded_filled_usd,
       round((filled_shares * fill_price)::numeric, 2) AS filled_x_price,
       round((orig_shares * fill_price)::numeric, 2)   AS orig_x_price,
       round((filled_usd / nullif(fill_price, 0))::numeric, 2) AS implied_shares,
       round(requested_usd::numeric, 2)    AS requested_usd,
       round(requested_shares::numeric, 2) AS requested_shares
  FROM live_orders
 WHERE status = 'filled' AND order_id IS NULL
 ORDER BY filled_usd DESC;

\echo
\echo == 7. THE SAME CHECK ACROSS ALL 52, SO IT IS NOT ONLY THE SIX ==
SELECT count(*) AS filled_rows,
       count(*) FILTER (
         WHERE abs(filled_usd - filled_shares * fill_price) <= 0.02)
           AS reconciles_filled_x_price,
       count(*) FILTER (
         WHERE abs(filled_usd - orig_shares * fill_price) <= 0.02)
           AS reconciles_orig_x_price,
       count(*) FILTER (
         WHERE abs(filled_usd - filled_shares * fill_price) > 0.02
           AND abs(filled_usd - coalesce(orig_shares, -1) * fill_price) > 0.02)
           AS reconciles_neither,
       count(*) FILTER (WHERE orig_shares IS NULL) AS orig_shares_null
  FROM live_orders
 WHERE status = 'filled';

\echo
\echo == 8. AND ACROSS THE SETTLED HISTORY, AS A CONTROL ==
-- If the same inconsistency appears on the 5,271 settled rows it is a
-- ledger-wide property and says nothing special about the 52. If it appears
-- only on the 52, it is specific to rows that never closed.
SELECT status,
       count(*) AS rows,
       count(*) FILTER (
         WHERE abs(filled_usd - filled_shares * fill_price) <= 0.02)
           AS reconciles_filled_x_price,
       round((100.0 * count(*) FILTER (
              WHERE abs(filled_usd - filled_shares * fill_price) <= 0.02)
              / nullif(count(*), 0))::numeric, 2) AS pct_reconciling
  FROM live_orders
 WHERE status IN ('filled', 'settled', 'cashed_out')
   AND filled_usd > 0
 GROUP BY 1
 ORDER BY 1;
