-- ############################################################################
-- HISTORICAL / NON-RERUNNABLE -- references nonexistent markets.us_market_slug
--
-- Statements 2 and 3 read `mk.us_market_slug` from the `markets` table. NO
-- MIGRATION DEFINES OR ADDS THAT COLUMN: migration 001 creates `markets` with
-- condition_id, title, slug, event_slug, event_title, sport, tags, closed,
-- resolved, resolved_prices, resolved_at, updated_at, and no later migration
-- alters it. Dispatching this file will abort on statement 2 under
-- ON_ERROR_STOP=1.
--
-- Found on 2026-09-11 by the base-table column layer added to
-- research/check_sql.py. Left in place as a record of what was asked, NOT as an
-- executable query: living under research/ is not evidence that a file runs.
-- Anything needed from it must be rewritten against columns that exist, and the
-- US slug for a condition read from a surface that actually carries one.
-- ############################################################################
-- ============================================================================
-- WHAT CAN THE MECHANISM DECOMPOSITION ACTUALLY BE BUILT FROM?
-- (2026-09-11, read-only.)
--
-- The owner's mechanism decomposition asks for fourteen dimensions. Several of
-- them need data that may simply not be retained, and the honest order is to
-- find that out BEFORE writing a query that silently substitutes a proxy.
-- Specifically at risk:
--
--   time-to-event      needs a game start time. research/SCHEMA_RECOMMENDATIONS
--                      already records that `markets` has NO market-open
--                      timestamp. A `game_start` column does exist on
--                      us_premap ("market.gameStartTime, as the venue states
--                      it"), but that is the PMUS premap, reachable only
--                      through markets.us_market_slug -- i.e. ONLY for
--                      conditions that mapped to a US market. That is itself a
--                      selection, and a strong one, so its coverage has to be
--                      measured, not assumed.
--   pregame vs live    same dependency. Without game_start it is not derivable
--                      at all, and a resolution-time proxy is NOT the same
--                      thing (it includes settlement lag).
--   disposition of     needs SELL rows and settlement payouts to be present
--   unmatched          and joinable per condition.
--   realized economics needs markets.resolved_prices.
--
-- Nothing here decomposes anything. Six cheap statements that say what exists,
-- and with what coverage over RN1's own conditions, so the decomposition can
-- be designed against the data that is actually there and can state plainly
-- which of the fourteen dimensions are unavailable rather than proxying them
-- without saying so.
--
-- Read-only: six SELECTs.
-- ============================================================================


\echo '== 1. WHICH TABLES/COLUMNS EXIST AT ALL (the at-risk ones, by name) =='
SELECT table_name, column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND ( (table_name = 'markets'  AND column_name IN
            ('condition_id','sport','event_slug','event_title','slug','title',
             'resolved','resolved_prices','resolved_at','closed','venue',
             'us_market_slug','updated_at'))
      OR (table_name = 'us_premap')
      OR (table_name = 'trades' AND column_name IN
            ('id','ts','detected_at','source','side','size','price','asset',
             'condition_id','outcome_index','market_slug','event_slug','sport'))
       )
 ORDER BY table_name, column_name;


\echo '== 2. RN1 CONDITION UNIVERSE in the window, and what metadata reaches it =='
WITH base AS (
  SELECT t.condition_id, t.side, t.outcome_index, t.size::float8 AS sh,
         t.price::float8 AS px, t.ts, t.source
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
     AND t.ts <  timestamptz '2026-09-11 12:00Z'
), conds AS (
  SELECT DISTINCT condition_id FROM base
)
SELECT count(*) AS rn1_conditions,
       count(mk.condition_id) AS have_markets_row,
       count(*) FILTER (WHERE mk.sport IS NOT NULL AND mk.sport <> 'unclassified')
         AS have_sport,
       count(*) FILTER (WHERE mk.event_slug IS NOT NULL) AS have_event_slug,
       count(*) FILTER (WHERE mk.resolved) AS resolved,
       count(*) FILTER (WHERE mk.resolved_prices IS NOT NULL) AS have_resolved_prices,
       count(*) FILTER (WHERE mk.resolved_at IS NOT NULL) AS have_resolved_at,
       count(*) FILTER (WHERE mk.us_market_slug IS NOT NULL) AS have_us_slug
  FROM conds c LEFT JOIN markets mk ON mk.condition_id = c.condition_id;


\echo '== 3. IS A GAME START TIME REACHABLE? (time-to-event / pregame-vs-live) =='
-- If have_game_start is a small fraction of rn1_conditions then time-to-event
-- and pregame/live are available only on a mapped, selected subset and must be
-- reported that way or not at all.
WITH base AS (
  SELECT DISTINCT t.condition_id
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
     AND t.ts <  timestamptz '2026-09-11 12:00Z'
)
SELECT count(*) AS rn1_conditions,
       count(mk.us_market_slug) AS have_us_slug,
       count(up.game_start) AS have_game_start,
       round((100.0 * count(up.game_start) / NULLIF(count(*), 0))::numeric, 2)
         AS pct_with_game_start,
       min(up.game_start) AS earliest_start,
       max(up.game_start) AS latest_start
  FROM base b
  LEFT JOIN markets mk ON mk.condition_id = b.condition_id
  LEFT JOIN us_premap up ON up.us_slug = mk.us_market_slug;


\echo '== 4. THE TWO-LEG SHAPE: does he complete pairs, and how much is left over? =='
-- The mechanism question in its smallest form. For each condition: did he buy
-- BOTH tokens, and how much inventory is left unmatched after pairing.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), w AS (
  SELECT condition_id,
         sum(sh) FILTER (WHERE side = 'BUY' AND outcome_index = 0) AS by0,
         sum(sh) FILTER (WHERE side = 'BUY' AND outcome_index = 1) AS by1,
         sum(sh) FILTER (WHERE side = 'SELL') AS sold,
         count(*) FILTER (WHERE side = 'SELL') AS sell_fills,
         min(ts) AS first_ts, max(ts) AS last_ts
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1
), s AS (
  SELECT condition_id,
         COALESCE(by0, 0) AS y, COALESCE(by1, 0) AS n,
         LEAST(COALESCE(by0, 0), COALESCE(by1, 0)) AS matched,
         abs(COALESCE(by0, 0) - COALESCE(by1, 0)) AS unmatched,
         COALESCE(sold, 0) AS sold, sell_fills,
         EXTRACT(epoch FROM (last_ts - first_ts)) / 60.0 AS span_min
    FROM w
)
SELECT CASE WHEN y > 0 AND n > 0 THEN '1 BOTH LEGS BOUGHT'
            WHEN y > 0 OR  n > 0 THEN '2 ONE LEG ONLY'
            ELSE                      '3 NEITHER (sells only)' END AS leg_shape,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct,
       round(sum(matched)::numeric, 0) AS matched_shares,
       round(sum(unmatched)::numeric, 0) AS unmatched_shares,
       round((100.0 * sum(unmatched) / NULLIF(sum(y + n), 0))::numeric, 2)
         AS pct_of_bought_left_unmatched,
       count(*) FILTER (WHERE sell_fills > 0) AS conds_with_any_sell,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY span_min)::numeric, 1)
         AS p50_span_min,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY span_min)::numeric, 1)
         AS p90_span_min
  FROM s GROUP BY 1 ORDER BY 1;


\echo '== 5. DOES THE PRICE-BAND FINDING SURVIVE WITHIN A CONDITION? =='
-- THE FIRST AND CHEAPEST SELECTION TEST, and the one that matters most.
-- pair_edge is a CONDITION-level number. Both legs of a completed pair sit at
-- roughly complementary prices, so the <10c bucket and the >=90c bucket should
-- contain LARGELY THE SAME CONDITIONS -- and a condition contributes the SAME
-- pair_edge to both. If so, an event-weighted "edge by price band" cannot be
-- a causal price effect; it can only be a re-weighting of which conditions
-- have how many fills at which prices. This measures that overlap directly.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), inwin AS (
  SELECT * FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), band AS (
  SELECT condition_id,
         CASE WHEN px < 0.10 THEN 'band 1 <10c' WHEN px < 0.25 THEN 'band 2 10-25c'
              WHEN px < 0.50 THEN 'band 3 25-50c' WHEN px < 0.75 THEN 'band 4 50-75c'
              WHEN px < 0.90 THEN 'band 5 75-90c' ELSE 'band 6 >=90c' END AS b,
         count(*) AS fills
    FROM inwin GROUP BY 1, 2
), per_cond AS (
  SELECT condition_id, count(DISTINCT b) AS bands_touched,
         bool_or(b = 'band 1 <10c')   AS in_lo,
         bool_or(b = 'band 6 >=90c')  AS in_hi
    FROM band GROUP BY 1
)
SELECT CASE WHEN in_lo AND in_hi THEN '1 CONDITION SPANS BOTH <10c AND >=90c'
            WHEN in_lo           THEN '2 touches <10c, never >=90c'
            WHEN in_hi           THEN '3 touches >=90c, never <10c'
            ELSE                      '4 touches neither extreme' END AS span_class,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct,
       round(avg(bands_touched)::numeric, 2) AS avg_bands_touched
  FROM per_cond GROUP BY 1 ORDER BY 1;


\echo '== 6. WHAT A SELL ROW LOOKS LIKE (disposition of unmatched inventory) =='
-- Whether the "subsequent disposition" dimension is answerable at all: are
-- there SELL rows, on which leg, and how big relative to the buys.
WITH base AS (
  SELECT t.condition_id, t.side, t.outcome_index, t.size::float8 AS sh,
         t.price::float8 AS px, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
     AND t.ts <  timestamptz '2026-09-11 12:00Z'
)
SELECT side,
       count(*) AS fills,
       count(DISTINCT condition_id) AS conditions,
       round(sum(sh)::numeric, 0) AS shares,
       round(sum(sh * px)::numeric, 0) AS notional,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY px)::numeric, 4) AS p50_price,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY sh)::numeric, 1) AS p50_size
  FROM base GROUP BY 1 ORDER BY 1;
