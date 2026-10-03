-- cand22 NCAAF venue naming (read-only): every cfb full-game winner contract
-- on the America/New_York day 2026-10-03 = [2026-10-03 04:00Z, 2026-10-04 04:00Z).
\echo '== N1 · cfb full-game winner events: rows per contract and side shape =='
SELECT count(*) AS events,
       count(*) FILTER (WHERE n_rows = 2 AND n_long = 1 AND n_short = 1 AND n_names = 2) AS two_way_long_short,
       count(*) FILTER (WHERE NOT (n_rows = 2 AND n_long = 1 AND n_short = 1 AND n_names = 2)) AS other_shape,
       count(*) FILTER (WHERE n_contracts <> 1) AS events_with_not_one_contract,
       min(game_start) AS first_start, max(game_start) AS last_start,
       count(*) FILTER (WHERE game_start < now()) AS started_by_read
  FROM (SELECT event_slug, min(game_start) AS game_start, count(*) AS n_rows,
               count(DISTINCT market_slug) AS n_contracts,
               count(*) FILTER (WHERE intent = 'ORDER_INTENT_BUY_LONG') AS n_long,
               count(*) FILTER (WHERE intent = 'ORDER_INTENT_BUY_SHORT') AS n_short,
               count(DISTINCT team_name) AS n_names
          FROM us_premap
         WHERE split_part(market_slug, '-', 2) = 'cfb'
           AND sports_type = 'football_team_full_game_winner'
           AND game_start >= timestamptz '2026-10-03 04:00Z'
           AND game_start <  timestamptz '2026-10-04 04:00Z'
         GROUP BY event_slug) e;

\echo '== N2 · the non-two-way shapes =='
SELECT event_slug, market_slug, intent, team_name, side_norm, team_abbr, line, game_start
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb'
   AND sports_type = 'football_team_full_game_winner'
   AND game_start >= timestamptz '2026-10-03 04:00Z'
   AND game_start <  timestamptz '2026-10-04 04:00Z'
   AND event_slug IN (
     SELECT event_slug FROM us_premap
      WHERE split_part(market_slug, '-', 2) = 'cfb'
        AND sports_type = 'football_team_full_game_winner'
      GROUP BY event_slug
     HAVING count(*) <> 2 OR count(DISTINCT team_name) <> 2)
 ORDER BY event_slug, intent LIMIT 20;

\echo '== N3 · every participant: venue team_name + side_norm (nickname) + abbr =='
SELECT to_char(game_start AT TIME ZONE 'America/New_York', 'HH24:MI') AS et,
       event_slug,
       string_agg(team_name || ' | ' || coalesce(side_norm, '?') || ' | ' || coalesce(team_abbr, '?')
                  || ' | ' || replace(intent, 'ORDER_INTENT_BUY_', ''), ' ;; ' ORDER BY intent) AS sides,
       max(updated_at) AS updated_at
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb'
   AND sports_type = 'football_team_full_game_winner'
   AND game_start >= timestamptz '2026-10-03 04:00Z'
   AND game_start <  timestamptz '2026-10-04 04:00Z'
 GROUP BY game_start, event_slug ORDER BY game_start, event_slug;

\echo '== N4 · the football board the patched collector would read (token, events) =='
SELECT split_part(market_slug, '-', 2) AS token, count(DISTINCT event_slug) AS events,
       (array_agg(DISTINCT left(event_title, 80)))[1:6] AS titles
  FROM us_premap
 WHERE sports_type LIKE 'football%'
   AND game_start > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

\echo '== N5 · recency of the cfb winner rows (venue-native reads rows re-seen within 5400 s) =='
SELECT count(*) AS rows, count(*) FILTER (WHERE updated_at >= now() - interval '5400 seconds') AS reseen_5400s,
       max(updated_at) AS newest, min(updated_at) AS oldest
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb'
   AND sports_type = 'football_team_full_game_winner'
   AND game_start > now() - interval '6 hours';
