-- P0 INCIDENT, stream inc-families (line-market families: SPREAD / TOTAL /
-- TEAM TOTAL). READ-ONLY, bounded. What the translator must read off a venue
-- catalogue row, row by row, and what the venue board lists per sport.
--
-- L1  every venue line-family sportsMarketType in the current listing:
--     contracts, events, leagues, side vocabulary, intents, team fields, and
--     whether the line is a HALF-POINT (no push) or an INTEGER (push possible)
-- L2  full-game spread / total / team-total contracts by league and ET game
--     day, half-point vs integer lines
-- L3  BOTH side rows of two upcoming contracts per (full-game line type,
--     league): the exact structured fields (side_norm, intent, signed, line,
--     team fields, question) the translator maps
-- L4  the next NFL and NCAAF games: every full-game spread / total / team
--     total identifier with its line, so the public gateway listing of those
--     exact contracts can be read for their rules text
-- L5  persisted venue rules text on any valuation row of a line contract
--     (asc- / tsc- slugs): count and masked wording
-- L6  the same contract read twice (two sides) must name ONE event: events
--     whose line rows disagree on game_start or team league
\echo '== L0 · read instant =='
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et,
       (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== L1 · line-family sportsMarketTypes in the current listing (start >= now-6h, re-seen <= 90 min) =='
SELECT sports_type,
       count(*) AS rows, count(DISTINCT identifier) AS contracts,
       count(DISTINCT event_slug) AS events,
       array_to_string((array_agg(DISTINCT lower(split_part(identifier, '-', 2))))[1:8], ',') AS leagues,
       array_to_string((array_agg(DISTINCT side_norm))[1:6], ',') AS side_norm_sample,
       array_to_string((array_agg(DISTINCT intent))[1:3], ',') AS intents,
       count(*) FILTER (WHERE coalesce(team_abbr, '') <> '') AS rows_with_team,
       count(*) FILTER (WHERE line ~ '^[0-9]+\.5$') AS half_point_rows,
       count(*) FILTER (WHERE line ~ '^[0-9]+$') AS integer_rows,
       count(*) FILTER (WHERE coalesce(line, '') = '') AS no_line_rows,
       count(*) FILTER (WHERE coalesce(signed, '') <> '') AS signed_rows,
       min(identifier) AS example
  FROM us_premap
 WHERE game_start >= now() - interval '6 hours'
   AND updated_at >= now() - interval '90 minutes'
   AND (sports_type ~ '(spread|handicap|total)')
   AND sports_type !~ '_player_'
 GROUP BY 1 ORDER BY 3 DESC LIMIT 120;

\echo '== L2 · full-game line families by league x ET game day: contracts, events, half-point vs integer =='
SELECT sports_type, lower(split_part(identifier, '-', 2)) AS league,
       (game_start AT TIME ZONE 'America/New_York')::date AS et_day,
       count(DISTINCT identifier) AS contracts, count(DISTINCT event_slug) AS events,
       count(DISTINCT identifier) FILTER (WHERE line ~ '^[0-9]+\.5$') AS half_point_contracts,
       count(DISTINCT identifier) FILTER (WHERE line ~ '^[0-9]+$') AS integer_contracts,
       count(DISTINCT identifier) FILTER (WHERE NOT (coalesce(line, '') ~ '^[0-9]+(\.5)?$')) AS other_line_contracts
  FROM us_premap
 WHERE game_start >= now() - interval '6 hours'
   AND updated_at >= now() - interval '90 minutes'
   AND (sports_type ~ '_full_game_(spread|total)$' OR sports_type ~ '_team_points_full_game_total$'
        OR sports_type ~ '_team_total_(goals|runs|points)$' OR sports_type ~ '^tennis_.*(handicap|total|spread)')
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT 200;

\echo '== L3 · both side rows of two upcoming contracts per (full-game line type, league) =='
WITH pick AS (
    SELECT sports_type, lower(split_part(identifier, '-', 2)) AS league, identifier,
           row_number() OVER (PARTITION BY sports_type, lower(split_part(identifier, '-', 2))
                              ORDER BY game_start, identifier) AS rn
      FROM (SELECT DISTINCT sports_type, identifier, game_start FROM us_premap
             WHERE game_start >= now() AND game_start < now() + interval '72 hours'
               AND updated_at >= now() - interval '90 minutes'
               AND (sports_type ~ '_full_game_(spread|total)$'
                    OR sports_type ~ '_team_points_full_game_total$'
                    OR sports_type ~ '_team_total_(goals|runs|points)$'
                    OR sports_type ~ '^tennis_.*(handicap|total|spread)')) d
)
SELECT p.sports_type, p.league, u.identifier, u.market_slug, u.kind, u.side_norm, u.intent,
       u.signed, u.line, u.team_abbr, u.team_name, u.team_safe_name, u.team_id, u.team_league,
       u.event_slug, u.event_title, left(u.question, 160) AS question,
       to_char(u.game_start AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') AS game_start
  FROM pick p JOIN us_premap u ON u.identifier = p.identifier
 WHERE p.rn <= 2
 ORDER BY p.sports_type, p.league, u.identifier, u.intent LIMIT 160;

\echo '== L4 · the next NFL and NCAAF games: full-game spread / total / team-total identifiers and lines =='
WITH ev AS (
    SELECT event_slug, min(game_start) AS gs FROM us_premap
     WHERE game_start >= now() AND updated_at >= now() - interval '90 minutes'
       AND lower(split_part(identifier, '-', 2)) IN ('nfl', 'cfb')
       AND sports_type IN ('football_team_full_game_spread', 'football_team_full_game_total',
                           'football_team_points_full_game_total')
     GROUP BY 1 ORDER BY 2 LIMIT 3
)
SELECT u.sports_type, u.identifier, u.side_norm, u.intent, u.signed, u.line, u.team_abbr,
       to_char(u.game_start AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') AS game_start
  FROM us_premap u JOIN ev ON ev.event_slug = u.event_slug
 WHERE u.sports_type IN ('football_team_full_game_spread', 'football_team_full_game_total',
                         'football_team_points_full_game_total')
   AND u.updated_at >= now() - interval '90 minutes'
 ORDER BY u.event_slug, u.sports_type, u.identifier, u.intent LIMIT 260;

\echo '== L5 · persisted venue rules text on valuation rows of line contracts (asc-/tsc-), last 14 days =='
SELECT split_part(us_market_slug, '-', 1) AS kind, sport_family, market,
       count(*) AS rows, count(DISTINCT us_market_slug) AS contracts,
       count(*) FILTER (WHERE settlement_comparison->>'venue_rules_text' IS NOT NULL) AS with_rules_text
  FROM external_valuations
 WHERE decided_at > now() - interval '14 days'
   AND us_market_slug ~ '^(asc|tsc)-'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo '== L5b · the distinct wordings among them (team names and numbers masked) =='
SELECT split_part(us_market_slug, '-', 1) AS kind, split_part(us_market_slug, '-', 2) AS league,
       count(*) AS rows,
       left(regexp_replace(settlement_comparison->>'venue_rules_text', '[0-9]+(\.[0-9]+)?', '#', 'g'), 700) AS wording
  FROM external_valuations
 WHERE decided_at > now() - interval '14 days'
   AND us_market_slug ~ '^(asc|tsc)-'
   AND settlement_comparison->>'venue_rules_text' IS NOT NULL
 GROUP BY 1, 2, 4 ORDER BY 3 DESC LIMIT 20;

\echo '== L6 · line contracts whose rows disagree on start or league (the translator must refuse these) =='
SELECT sports_type, count(*) AS contracts
  FROM (SELECT identifier, max(sports_type) AS sports_type
          FROM us_premap
         WHERE game_start >= now() - interval '6 hours'
           AND updated_at >= now() - interval '90 minutes'
           AND (sports_type ~ '_full_game_(spread|total)$' OR sports_type ~ '_team_points_full_game_total$'
                OR sports_type ~ '_team_total_(goals|runs|points)$')
         GROUP BY identifier
        HAVING count(DISTINCT game_start) > 1 OR count(DISTINCT coalesce(team_league, '')) > 1
            OR count(*) <> 2) x
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
