-- DOES THE VENUE LIST THE COMPETITION THE LANE ASKS FOR? Read-only, bounded.
--
-- THE LIVE CYCLE, not the historical cohort. `ext_pinnacle_last_cycle` on build
-- c3d0cfc reports evaluated 0 / written 0 with
--   soccer_epl: provider_events 20, with_pinnacle_h2h 20,
--               venue_markets_open_and_fresh 217,
--               mapped_to_a_venue_contract 0
--
-- WHAT THE FIRST PASS OF THIS FILE ESTABLISHED, AND WHAT IT DID NOT.
-- Statement 1 listed the 25 largest slug families in `us_premap` and `epl` was
-- not among them. That is NOT the same as "the venue lists no English top
-- flight": a 25-row LIMIT cannot prove an absence, and the families it did
-- return are all international or continental (unl, ebfsa, ebfwca, ebfwcb,
-- afcq, lmx, mls, ncaaws). So this pass asks the absence question directly,
-- because the two candidate defects have OPPOSITE repairs:
--
--   (a) the venue lists English top-flight fixtures and the resolver cannot
--       bridge our slug to them  -> the defect is in `premap.resolve`
--   (b) the venue lists none     -> the defect is in the lane's UNIVERSE: it
--       spends provider credits on a competition this venue does not quote,
--       exactly as the SPORTS comment already says of NBA and NHL
--
-- Guessing between them is how an alias table gets invented for a fixture that
-- does not exist, so nothing below normalises anything: it prints the venue's
-- OWN competition labels (`team_league`, `sports_type`) and its OWN question
-- text.
--
-- COLUMN NAMES ARE READ FROM THE WRITER, NOT GUESSED. Two statements have now
-- been lost to a guess: `event_key` (the writer at premap.py:6276 names
-- `event_keys`) and `market_starts.market_slug` (044_trade_marks.sql keys that
-- table by `condition_id`, and `us_premap` carries its own `game_start`
-- anyway). Statement 0 dumps both column lists so the next reader need not
-- guess either.

\echo == 0 · THE COLUMN LISTS, SO NOTHING BELOW IS A GUESS ==
SELECT table_name, string_agg(column_name, ', ' ORDER BY ordinal_position)
         AS columns
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND table_name IN ('us_premap', 'external_valuations', 'markets')
 GROUP BY table_name
 ORDER BY table_name;

\echo == 1 · EVERY COMPETITION THE VENUE ITSELF NAMES, BY VOLUME ==
SELECT coalesce(sports_type, 'null')                AS sports_type,
       coalesce(team_league, 'null')                AS team_league,
       count(*)                                     AS premap_rows,
       count(DISTINCT event_slug)                   AS events,
       min(split_part(market_slug, '-', 2))         AS slug_family_min,
       max(split_part(market_slug, '-', 2))         AS slug_family_max
  FROM us_premap
 GROUP BY 1, 2
 ORDER BY 3 DESC;

\echo == 2 · DOES ANY VENUE ROW NAME AN ENGLISH TOP-FLIGHT CLUB ==
SELECT club,
       count(*)                     AS premap_rows,
       count(DISTINCT p.event_slug) AS events,
       min(p.market_slug)           AS example_slug,
       min(left(p.event_title, 60)) AS example_title
  FROM us_premap p
  CROSS JOIN (VALUES ('arsenal'), ('chelsea'), ('liverpool'),
                     ('manchester'), ('tottenham'), ('everton'),
                     ('newcastle'), ('aston villa'), ('brighton'),
                     ('fulham'), ('wolverhampton'), ('brentford'),
                     ('crystal palace'), ('nottingham'), ('bournemouth'),
                     ('west ham'), ('leeds'), ('burnley'),
                     ('sunderland'), ('premier league')) AS c(club)
 WHERE lower(coalesce(p.event_title, '') || ' ' || coalesce(p.question, ''))
       LIKE '%' || c.club || '%'
 GROUP BY 1
 ORDER BY 2 DESC;

\echo == 3 · THE LANE OWN UNIVERSE -- open soccer rows in markets ==
SELECT coalesce(sport, 'null')                    AS sport,
       count(*)                                    AS open_unresolved,
       count(DISTINCT split_part(slug, '-', 1))    AS leading_tokens,
       min(slug)                                   AS example_slug,
       min(left(event_title, 60))                  AS example_event
  FROM markets
 WHERE coalesce(closed, false) = false
   AND coalesce(resolved, false) = false
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 20;

\echo == 4 · THE BRIDGE QUESTION -- our leading token against the venue own ==
SELECT ours                                   AS our_leading_token,
       count(*)                               AS our_open_markets,
       min(left(m.event_title, 54))            AS our_example_event,
       (SELECT count(*) FROM us_premap q
         WHERE q.market_slug LIKE '%-' || ours || '-%') AS venue_rows_same_token
  FROM (SELECT slug, event_title, sport,
               split_part(slug, '-', 1) AS ours
          FROM markets
         WHERE coalesce(closed, false) = false
           AND coalesce(resolved, false) = false
           AND sport ILIKE '%occer%') m
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 20;

\echo == 5 · WHAT THE VALUATION LANE ACTUALLY WROTE, BY SPORT ==
SELECT coalesce(sport_family, 'null') AS sport_family,
       coalesce(market, 'null')        AS market,
       count(*)                        AS n,
       count(*) FILTER (WHERE admissible) AS admissible,
       max(observed_at)::timestamptz(0) AS newest
  FROM external_valuations
 GROUP BY 1, 2
 ORDER BY 3 DESC
 LIMIT 15;

\echo == 6 · AND WHETHER THE VENUE SOCCER ROWS CARRY A KICKOFF INSTANT ==
SELECT coalesce(sports_type, 'null')                     AS sports_type,
       count(DISTINCT event_slug)                        AS events,
       count(DISTINCT event_slug) FILTER (WHERE game_start IS NOT NULL)
         AS events_with_kickoff,
       min(game_start)::timestamptz(0)                   AS earliest_kickoff,
       max(game_start)::timestamptz(0)                   AS latest_kickoff
  FROM us_premap
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 15;
