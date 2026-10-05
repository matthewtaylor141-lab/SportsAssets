-- P0 INCIDENT, NCAAF SETTLEMENT STREAM (second read), read-only. The first
-- read (incident_ncaaf_settlement_wording.sql, N6) grouped every cfb catalogue
-- type with LIMIT 30, and the money-line rows fell below the cut. This reads
-- ONLY the college money line (football_team_full_game_winner on a cfb slug):
--
-- C1  the fields the census and the de-vig league read (team_league, the
--     venue-native identifier and its prefix, kind, line) -- the same shape the
--     NFL stream's W2 read for aec-nfl rows;
-- C2  a bounded sample of production rows (both sides of the newest events),
--     the shape a test fixture copies;
-- C3  phase words on the listed games (bowl / playoff / championship), counted.
\echo '== C0 · read instant =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== C1 · cfb money-line catalogue rows (game_start in [now - 6 h, now + 10 d]) =='
SELECT sports_type, lower(coalesce(team_league, '<null>')) AS team_league,
       split_part(identifier, '-', 1) || '-' || split_part(identifier, '-', 2) AS identifier_prefix,
       (identifier = market_slug) AS identifier_is_market_slug,
       kind, (line IS NULL OR line::text = '') AS line_blank,
       count(*) AS rows, count(DISTINCT market_slug) AS contracts,
       count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb'
   AND sports_type = 'football_team_full_game_winner'
   AND game_start > now() - interval '6 hours' AND game_start < now() + interval '10 days'
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 7 DESC LIMIT 20;

\echo '== C2 · sample rows: both sides of the six soonest cfb money-line events =='
SELECT identifier, event_slug, event_title, market_slug, question, kind,
       side_norm, intent, line, team_name, team_safe_name, team_abbr,
       team_league, sports_type, game_start,
       game_start AT TIME ZONE 'America/New_York' AS start_et
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb'
   AND sports_type = 'football_team_full_game_winner'
   AND event_slug IN (SELECT event_slug FROM us_premap
                       WHERE split_part(market_slug, '-', 2) = 'cfb'
                         AND sports_type = 'football_team_full_game_winner'
                         AND game_start > now() - interval '6 hours'
                         AND game_start < now() + interval '10 days'
                       GROUP BY event_slug ORDER BY min(game_start) LIMIT 6)
 ORDER BY game_start, event_slug, side_norm LIMIT 24;

\echo '== C3 · phase words in the listed cfb money-line titles / questions =='
SELECT count(*) AS rows, count(DISTINCT market_slug) AS contracts,
       count(*) FILTER (WHERE lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) LIKE '%bowl%') AS bowl_rows,
       count(*) FILTER (WHERE lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) LIKE '%playoff%') AS playoff_rows,
       count(*) FILTER (WHERE lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) LIKE '%championship%') AS championship_rows,
       count(*) FILTER (WHERE lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) LIKE '%spring%') AS spring_rows
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb'
   AND sports_type = 'football_team_full_game_winner'
   AND game_start > now() - interval '3 days' AND game_start < now() + interval '10 days';
