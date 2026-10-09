-- READ-ONLY. RC6 LANE G2 (LIVE GAME STATE FIXTURE ADAPTER), SECOND READ:
-- WHICH VENUE team_league CODE IS WHICH SPORT, AND HOW THE VENUE NAMES TEAMS.
--
-- rc6_lgs-adapter_premap_shape.sql (run 37880110851) showed us_premap carries
-- no home_team / away_team / league / sport column; every candidate event in
-- the next 48 h names exactly two venue team ids with one team_league and one
-- game_start. Before any venue code is read as a score league the adapter
-- needs, per code: the sport family the venue's own sportsMarketType states
-- (football_ / baseball_ / basketball_ / hockey_ / soccer_ prefix), whether
-- an event ever carries two different team_league codes (a cross-competition
-- fixture, where the team's league would not be the event's), and how the
-- venue spells team names in the leagues the score registry covers.
--
--   1 every team_league code x sport-family prefix of sports_type, all rows
--   2 events whose rows carry more than one team_league (any time)
--   3 sample team names for the codes that look like registry leagues
--   4 the markets ever held by any PAPER account, by venue team_league code
--     (the candidate population the display collector would be asked for)
--
-- Nothing here parses a title, a slug or a question: the sport family is the
-- venue's own sportsMarketType text up to its first underscore. SELECT only.

\echo == 1. team_league code x sport family of sports_type (all us_premap rows) ==
SELECT coalesce(team_league, '<null>') AS team_league,
       coalesce(split_part(sports_type, '_', 1), '<null>') AS sport_family,
       count(*) AS rows,
       count(DISTINCT event_slug) AS events,
       min(game_start) AS first_start, max(game_start) AS last_start
  FROM us_premap
 WHERE team_league IS NOT NULL
 GROUP BY 1, 2
 ORDER BY 1, rows DESC;

\echo == 2. events whose rows carry more than one team_league ==
SELECT event_slug, string_agg(DISTINCT team_league, ',') AS leagues,
       string_agg(DISTINCT split_part(sports_type, '_', 1), ',') AS families,
       count(DISTINCT team_id) AS team_ids, min(game_start) AS start_at
  FROM us_premap
 WHERE team_league IS NOT NULL
 GROUP BY event_slug
HAVING count(DISTINCT team_league) > 1
 ORDER BY start_at DESC NULLS LAST
 LIMIT 60;

\echo == 3. sample venue team names for registry-like codes ==
WITH t AS (
    SELECT team_league, team_id, min(team_name) AS team_name,
           min(team_safe_name) AS team_safe_name, min(team_abbr) AS team_abbr,
           max(game_start) AS last_start,
           row_number() OVER (PARTITION BY team_league ORDER BY max(game_start) DESC NULLS LAST, team_id) AS rk
      FROM us_premap
     WHERE team_league IN ('nfl', 'cfb', 'ncaaf', 'mlb', 'nba', 'wnba', 'nhl', 'epl', 'mls',
                           'ucl', 'uefacl', 'cl', 'ncaab', 'ncaam', 'ncaaw', 'cbb', 'wcbb',
                           'ncaams', 'ncaaws', 'ncaamb', 'ncaawb')
       AND team_id IS NOT NULL
     GROUP BY team_league, team_id
)
SELECT team_league, team_id, team_name, team_safe_name, team_abbr, last_start
  FROM t
 WHERE rk <= 12
 ORDER BY team_league, rk;

\echo == 4. markets ever held by a PAPER account, by venue team_league and sport family ==
WITH ever AS (
    SELECT DISTINCT us_market_slug FROM paper_fills
), j AS (
    SELECT e.us_market_slug, pm.event_slug, pm.team_league,
           split_part(pm.sports_type, '_', 1) AS family
      FROM ever e
      LEFT JOIN LATERAL (
        SELECT u.event_slug, u.team_league, u.sports_type FROM us_premap u
         WHERE u.identifier = e.us_market_slug AND u.market_slug = e.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
)
SELECT coalesce(team_league, CASE WHEN event_slug IS NULL THEN '<no premap row>'
                                  ELSE '<row without team_league>' END) AS team_league,
       coalesce(family, '<null>') AS sport_family,
       count(*) AS markets, count(DISTINCT event_slug) AS events
  FROM j
 GROUP BY 1, 2
 ORDER BY markets DESC
 LIMIT 120;

\echo == 5. held-ever events: do the event rows name two team ids, one league, one start ==
WITH ever AS (
    SELECT DISTINCT us_market_slug FROM paper_fills
), ev AS (
    SELECT DISTINCT pm.event_slug
      FROM ever e
      JOIN LATERAL (
        SELECT u.event_slug FROM us_premap u
         WHERE u.identifier = e.us_market_slug AND u.market_slug = e.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
     WHERE pm.event_slug IS NOT NULL
), shape AS (
    SELECT ev.event_slug,
           count(DISTINCT u.team_id) AS team_ids,
           count(DISTINCT u.team_name) AS team_names,
           count(DISTINCT u.team_league) AS leagues,
           min(u.team_league) AS league,
           count(DISTINCT u.game_start) AS starts,
           count(*) FILTER (WHERE u.team_id IS NOT NULL AND u.team_name IS NULL) AS id_without_name
      FROM ev JOIN us_premap u ON u.event_slug = ev.event_slug
     GROUP BY ev.event_slug
)
SELECT coalesce(league, '<no team_league>') AS league, count(*) AS events,
       count(*) FILTER (WHERE team_ids = 2) AS two_ids,
       count(*) FILTER (WHERE team_names = 2) AS two_names,
       count(*) FILTER (WHERE leagues = 1) AS one_league,
       count(*) FILTER (WHERE starts = 1) AS one_start,
       count(*) FILTER (WHERE team_ids = 2 AND team_names = 2 AND leagues = 1 AND starts = 1) AS all_four,
       count(*) FILTER (WHERE id_without_name > 0) AS id_without_name
  FROM shape
 GROUP BY 1
 ORDER BY events DESC
 LIMIT 120;
