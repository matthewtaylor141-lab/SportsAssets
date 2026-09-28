-- WHY 20 EPL EVENTS MAP TO 0 OF 217 SOCCER VENUE CONTRACTS. Read-only, bounded.
--
-- THE LIVE CYCLE, not the historical cohort. `ext_pinnacle_last_cycle` on build
-- c3d0cfc reports evaluated 0 / written 0 with
--   soccer_epl: provider_events 20, with_pinnacle_h2h 20,
--               venue_markets_open_and_fresh 217, mapped_to_a_venue_contract 0
-- and its own note says a zero-evaluated cycle establishes NOTHING about edge:
-- it is an input-path fact. This asks the input path which step loses them.
--
-- `premap.resolve` has six distinct ways to return nothing, and they need
-- completely different fixes: no keys built, no key intersection, unknown market
-- type, a type-prefix filter that emptied the pool, no side match, a side with
-- no intent. These statements separate the first four, which are the ones
-- visible from the tables alone.

\echo == 1 · HOW MUCH SOCCER THE VENUE PREMAP ACTUALLY HOLDS ==
SELECT count(*)                              AS premap_rows,
       count(DISTINCT event_slug)             AS events,
       count(DISTINCT market_slug)            AS contracts,
       min(length(event_key))                 AS min_key_len,
       max(length(event_key))                 AS max_key_len
  FROM us_premap
 WHERE market_slug LIKE '%soccer%' OR market_slug LIKE 'aec-epl%'
    OR event_slug LIKE '%epl%'    OR event_slug LIKE '%soccer%';

\echo == 2 · EVERY DISTINCT SLUG FAMILY PREFIX IN us_premap ==
SELECT split_part(market_slug, '-', 2) AS family,
       count(*)                        AS rows,
       count(DISTINCT event_slug)      AS events,
       min(market_slug)                AS example
  FROM us_premap
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 25;

\echo == 3 · THE SHAPE OF THE KEYS ON ONE SOCCER EVENT ==
SELECT event_slug, market_slug, left(event_key, 80) AS event_key,
       market_type, outcome_title, buy_intent
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) IN ('epl', 'soccer', 'ucl', 'seri', 'lali')
 ORDER BY event_slug, market_slug
 LIMIT 18;

\echo == 4 · WHAT THE PROVIDER SIDE LOOKS LIKE FOR THE SAME SPORT ==
SELECT left(coalesce(us_market_slug, '(null)'), 44) AS us_slug,
       left(coalesce(mapped_outcome, '(null)'), 26) AS mapped_outcome,
       sport_family, market,
       max(observed_at)::timestamptz(0)             AS newest,
       count(*)                                     AS n
  FROM external_valuations
 WHERE sport_family <> 'baseball'
 GROUP BY 1, 2, 3, 4
 ORDER BY 5 DESC NULLS LAST
 LIMIT 15;

\echo == 5 · WHETHER MARKET_STARTS IS POPULATED FOR SOCCER -- the C7 kickoff keys ==
SELECT split_part(market_slug, '-', 2)                   AS family,
       count(*)                                           AS rows,
       count(*) FILTER (WHERE starts_at IS NOT NULL)       AS with_start,
       max(starts_at)::timestamptz(0)                      AS newest_start
  FROM market_starts
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 12;
