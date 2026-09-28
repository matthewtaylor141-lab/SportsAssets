-- WHY 20 EPL EVENTS MAP TO 0 OF 217 SOCCER VENUE CONTRACTS. Read-only, bounded.
--
-- THE LIVE CYCLE, not the historical cohort. `ext_pinnacle_last_cycle` on build
-- c3d0cfc reports evaluated 0 / written 0 with
--   soccer_epl: provider_events 20, with_pinnacle_h2h 20,
--               venue_markets_open_and_fresh 217,
--               mapped_to_a_venue_contract 0
-- and its own note says a zero-evaluated cycle establishes NOTHING about edge:
-- it is an input-path fact.
--
-- COLUMN NAMES ARE READ FROM THE WRITER, NOT GUESSED. My first attempt used
-- `event_key` and aborted the whole file on ON_ERROR_STOP; the writer at
-- premap.py:6276 names `event_keys`. Statement 0 now dumps the column list so
-- the next reader never has to guess either.

\echo == 0 · THE COLUMNS us_premap ACTUALLY HAS ==
SELECT column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'us_premap'
 ORDER BY ordinal_position;

\echo == 1 · EVERY SLUG FAMILY IN us_premap, BY VOLUME ==
SELECT split_part(market_slug, '-', 2) AS family,
       count(*)                        AS premap_rows,
       count(DISTINCT event_slug)       AS events,
       min(market_slug)                 AS example
  FROM us_premap
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 25;

\echo == 2 · THE KIND AND INTENT MIX, WHICH THE TYPE FILTER READS ==
SELECT split_part(market_slug, '-', 2) AS family,
       coalesce(kind, 'null')           AS kind,
       coalesce(intent, 'null')         AS intent,
       count(*)                         AS n
  FROM us_premap
 GROUP BY 1, 2, 3
 ORDER BY 4 DESC
 LIMIT 25;

\echo == 3 · ONE SOCCER EVENT IN FULL, WITH ITS KEYS ==
SELECT event_slug, market_slug, kind, side_norm, intent,
       left(array_to_string(event_keys, ' | '), 200) AS keys
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) NOT IN
       ('mlb','nfl','nba','nhl','ncaaf','ncaab','wnba','cfb')
 ORDER BY event_slug, market_slug
 LIMIT 14;

\echo == 4 · THE PROVIDER SIDE FOR THE SAME SPORTS ==
SELECT sport_family, market,
       left(coalesce(us_market_slug, 'NOT_MAPPED'), 40) AS us_slug,
       count(*)                                          AS n,
       max(observed_at)::timestamptz(0)                   AS newest
  FROM external_valuations
 WHERE sport_family <> 'baseball'
 GROUP BY 1, 2, 3
 ORDER BY 4 DESC
 LIMIT 15;

\echo == 5 · AND WHETHER ANY SOCCER EVENT HAS A KICKOFF INSTANT ==
SELECT count(*)                                       AS premap_events,
       count(*) FILTER (WHERE has_start)               AS with_kickoff
  FROM (SELECT DISTINCT p.event_slug,
               EXISTS (SELECT 1 FROM market_starts m
                        WHERE m.market_slug = p.market_slug) AS has_start
          FROM us_premap p
         WHERE split_part(p.market_slug, '-', 2) NOT IN
               ('mlb','nfl','nba','nhl','ncaaf','ncaab','wnba','cfb')) t;
