-- cand24 NFL discovery (read-only), Sunday 2026-10-04.
--   ET  day = [2026-10-04 04:00Z, 2026-10-05 04:00Z)
--   UTC day = [2026-10-04 00:00Z, 2026-10-05 00:00Z)
-- Venue league token for the NFL is 'nfl' (us_premap market_slug position 2).
\echo '== N0 · read instant =='
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et;

\echo '== N1 · nfl rows by sports_type and ET day (all listed days) =='
SELECT (game_start AT TIME ZONE 'America/New_York')::date AS et_day,
       coalesce(sports_type, '(null)') AS sports_type,
       count(*) AS rows, count(DISTINCT event_slug) AS events,
       count(DISTINCT market_slug) AS contracts
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'nfl'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 60;

\echo '== N2 · nfl game-winner contracts on the ET day, every row =='
SELECT event_slug, left(event_title, 50) AS title, market_slug, kind, line,
       side_norm, intent, team_name, team_abbr, team_league,
       to_char(game_start AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') AS start_utc,
       to_char(game_start AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') AS start_et,
       updated_at
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'nfl'
   AND sports_type ILIKE '%full_game_winner%'
   AND game_start >= timestamptz '2026-10-04 00:00Z'
   AND game_start <  timestamptz '2026-10-06 06:00Z'
 ORDER BY game_start, event_slug, market_slug, side_norm;

\echo '== N2b · aec-nfl-*-2026-10-04 slugs regardless of sports_type =='
SELECT market_slug, max(sports_type) AS sports_type, count(*) AS sides,
       min(game_start) AS game_start, max(event_title) AS title
  FROM us_premap
 WHERE market_slug LIKE 'aec-nfl-%-2026-10-0%'
 GROUP BY 1 ORDER BY 4, 1;

\echo '== N3 · collector heartbeat: requested set, football board, budget =='
SELECT to_timestamp((value->>'at')::float8) AS at, value->>'state' AS state,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'sports_selection'->'rejected' AS rejected,
       value->'sports_selection'->'confirmed_by_provider' AS confirmed,
       value->'sports_selection'->'venue_football_board' AS football_board,
       value->'sports_selection'->'venue_board'->'tokens' AS soccer_board_tokens
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT k AS provider_sport, left(v::text, 600) AS funnel
  FROM ingestion_state, jsonb_each(coalesce(value->'funnel_by_provider_sport', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== N4 · PinnAPI scope and heartbeat sport ids / football cache =='
SELECT key, left(value::text, 500) AS value
  FROM ingestion_state WHERE key IN ('pinnapi_feed', 'pinnapi_feed_scope');
SELECT value->>'state' AS state,
       to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->'sport_ids' AS sport_ids, value->'scope' AS scope,
       (SELECT jsonb_object_agg(k, v) FROM jsonb_each(
          coalesce(value->'cache'->'markets_by_sport_type_phase', '{}'::jsonb)) AS e(k, v)
         WHERE k LIKE '5|%' OR k LIKE '7|%' OR k LIKE '15|%') AS football_like_markets,
       left((value->'cache')::text, 800) AS cache
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== N5 · ext_candidate_outcomes by sport_key, last 36 h =='
SELECT sport_key, family, count(*) AS rows,
       count(DISTINCT provider_event_id) AS events, max(cycle_at) AS last
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '36 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo '== N6 · any americanfootball_nfl provider event, ever (last 30 d) =='
SELECT provider_event_id, home, away, commence_time, max(cycle_at) AS last,
       max(first_refusal) AS refusal
  FROM ext_candidate_outcomes
 WHERE sport_key = 'americanfootball_nfl' AND cycle_at > now() - interval '30 days'
 GROUP BY 1, 2, 3, 4 ORDER BY 4 LIMIT 40;

\echo '== N7 · venue_fixture_metadata / fixture_metadata for nfl =='
SELECT venue, venue_fixture_key, competition, home_team, away_team,
       scheduled_kickoff, official_date, phase
  FROM venue_fixture_metadata
 WHERE sport_family ILIKE '%football%' AND scheduled_kickoff >= timestamptz '2026-10-04 00:00Z'
   AND scheduled_kickoff < timestamptz '2026-10-06 06:00Z'
 ORDER BY scheduled_kickoff LIMIT 40;

\echo '== N8 · global markets table rows that look like NFL Oct 4 =='
SELECT sport, count(*) AS n, (array_agg(slug ORDER BY slug))[1:6] AS slugs
  FROM markets
 WHERE (slug ILIKE 'nfl-%2026-10-04%' OR slug ILIKE '%-nfl-%2026-10-04%')
 GROUP BY 1;

\echo '== N9 · the boards as the collector reads them now (counts; game_start > now()-6h) =='
SELECT split_part(sports_type, '_', 1) AS family,
       split_part(market_slug, '-', 2) AS token,
       count(DISTINCT event_slug) AS events,
       count(DISTINCT event_slug) FILTER (WHERE game_start < now() + interval '24 hours') AS events_next_24h,
       count(DISTINCT event_slug) FILTER (WHERE game_start < timestamptz '2026-10-05 04:00Z') AS events_through_et_day
  FROM us_premap
 WHERE (sports_type LIKE 'soccer%' OR sports_type LIKE 'football%' OR sports_type LIKE 'baseball%')
   AND lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) NOT LIKE '%ebattles%'
   AND lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) NOT LIKE '%simulated%'
   AND game_start > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;
