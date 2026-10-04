-- P0 INCIDENT, segment VENUE EVENT + MARKET CATALOGUE COVERAGE, second read.
-- Read-only, bounded. Follows incident_venuecat_coverage.sql (run 37232541526).
--
-- F1  the NFL / NCAAF / NHL / NBA / WNBA / MLB / UNL venue boards family by
--     family (full-game vs segment, main vs alternate lines per event)
-- F2  the PinnAPI census samples (which venue events found no feed event, and
--     what the feed carried) -- to split NO_FEED_EVENT into its real causes
-- F3  venue events the collector never asks the provider about, by token map
-- F4  provider events refused at venue-event identity, with the venue's own
--     events in the same league on the same day (naming vs absence)
-- F5  seven days of what BETTOR valued, per league per ET day
-- F6  the reactive (PinnAPI WS) attempts per competition, last 24 h
-- F7  persisted coverage funnel snapshots for the ET day (venue column)
\echo '== F0 · read instant =='
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et;

\echo '== F1a · major-league venue boards: contracts per family, current listing (start >= now-6h, re-seen <= 90 min) =='
SELECT lower(split_part(event_slug, '-', 1)) AS league, sports_type,
       count(DISTINCT event_slug) AS events, count(DISTINCT market_slug) AS contracts,
       round(count(DISTINCT market_slug)::numeric / nullif(count(DISTINCT event_slug), 0), 1) AS per_event,
       count(DISTINCT line) FILTER (WHERE coalesce(line, '') <> '') AS distinct_lines
  FROM us_premap
 WHERE game_start >= now() - interval '6 hours'
   AND updated_at >= now() - interval '90 minutes'
   AND lower(split_part(event_slug, '-', 1)) IN ('nfl', 'cfb', 'nhl', 'nba', 'wnba', 'mlb', 'unl', 'atp', 'wta')
   AND sports_type !~ '_player_'
 GROUP BY 1, 2 ORDER BY 1, 4 DESC LIMIT 220;

\echo '== F1b · the same leagues: player-prop contracts (a family PinnAPI carries only as prematch specials) =='
SELECT lower(split_part(event_slug, '-', 1)) AS league,
       count(DISTINCT event_slug) AS events, count(DISTINCT market_slug) AS prop_contracts,
       count(DISTINCT sports_type) AS prop_types
  FROM us_premap
 WHERE game_start >= now() - interval '6 hours'
   AND updated_at >= now() - interval '90 minutes'
   AND sports_type ~ '_player_'
 GROUP BY 1 ORDER BY 3 DESC LIMIT 20;

\echo '== F1c · one NFL event, every full-game spread / total / team-total contract (lines as listed) =='
WITH one AS (
    SELECT event_slug FROM us_premap
     WHERE event_slug LIKE 'nfl-%' AND game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes'
       AND sports_type = 'football_team_full_game_spread'
     GROUP BY 1 ORDER BY min(game_start) DESC LIMIT 1)
SELECT p.event_slug, p.sports_type, p.market_slug, p.line, p.signed, p.side_norm,
       p.intent, left(p.question, 90) AS question
  FROM us_premap p JOIN one USING (event_slug)
 WHERE p.sports_type IN ('football_team_full_game_spread', 'football_team_full_game_total',
                         'football_team_points_full_game_total', 'football_team_full_game_winner')
 ORDER BY p.sports_type, p.market_slug, p.side_norm LIMIT 120;

\echo '== F1d · basketball and hockey winner events by league (both start windows) =='
SELECT lower(split_part(event_slug, '-', 1)) AS league, sports_type,
       count(DISTINCT event_slug) FILTER (WHERE game_start >= now() - interval '6 hours'
                                            AND updated_at >= now() - interval '90 minutes') AS events_current,
       count(DISTINCT event_slug) FILTER (WHERE game_start >= now() - interval '24 hours'
                                            AND game_start < now() + interval '24 hours') AS events_pm24h,
       min(game_start) FILTER (WHERE game_start >= now()) AS next_start,
       (array_agg(DISTINCT left(event_title, 40)))[1:3] AS sample
  FROM us_premap
 WHERE sports_type IN ('basketball_team_full_game_winner', 'hockey_team_full_game_winner',
                       'tennis_match_winner', 'ufc_fight_winner', 'esports_match_winner',
                       'baseball_team_full_game_winner', 'darts_match_winner', 'cricket_match_winner')
   AND game_start >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 2, 4 DESC LIMIT 80;

\echo '== F2a · PinnAPI census: unmatched venue events and the feed sample =='
SELECT value->'coverage_census'->'unmatched_event_sample' AS unmatched_event_sample,
       value->'coverage_census'->'feed_event_sample' AS feed_event_sample,
       value->'coverage_census'->'subscribed_rows' AS subscribed_rows,
       value->'coverage_census'->'proved_clock_artifact_rows' AS proved_clock_artifact_rows,
       value->'coverage_census'->'computed_at' AS computed_at
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== F2b · soccer winner events in the current listing by league, with how the census would see them (subscribed sport 1) =='
SELECT lower(split_part(event_slug, '-', 1)) AS league,
       count(DISTINCT event_slug) AS events,
       count(DISTINCT event_slug) FILTER (WHERE game_start <= now()) AS started,
       (array_agg(DISTINCT left(event_title, 46)))[1:4] AS sample_titles
  FROM us_premap
 WHERE sports_type = 'soccer_team_full_time_winner'
   AND game_start >= now() - interval '6 hours'
   AND updated_at >= now() - interval '90 minutes'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 60;

\echo '== F4 · provider events refused at venue identity (last 24 h): the refusal codes and the provider names =='
SELECT sport_key, home, away, left(commence_time, 16) AS commence,
       first_refusal,
       (SELECT string_agg(DISTINCT x, ',') FROM jsonb_array_elements_text(codes) AS x) AS codes,
       count(*) AS rows
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '24 hours'
   AND (first_refusal IN ('NO_VENUE_CONTRACT_FOR_EVENT', 'VENUE_MAPPING_AMBIGUOUS',
                          'VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE')
        OR first_refusal LIKE 'VENUE_NATIVE%' OR first_refusal LIKE 'NO_VENUE_NATIVE%'
        OR codes::text LIKE '%VENUE_NATIVE%')
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 4 LIMIT 60;

\echo '== F4b · venue soccer/football events in the leagues the collector requested, next/last 36 h (to read against F4) =='
SELECT lower(split_part(event_slug, '-', 1)) AS league, event_slug, left(event_title, 60) AS title,
       to_char(min(game_start) AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS start_utc,
       (array_agg(DISTINCT team_name) FILTER (WHERE team_name IS NOT NULL))[1:3] AS team_names
  FROM us_premap
 WHERE lower(split_part(event_slug, '-', 1)) IN ('brb', 'unl', 'mlb', 'nfl', 'cfb')
   AND sports_type ~ '(full_time_winner|full_game_winner)$'
   AND game_start >= now() - interval '36 hours' AND game_start < now() + interval '72 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 LIMIT 120;

\echo '== F5 · seven days of what BETTOR valued, per ET day and venue league =='
SELECT (decided_at AT TIME ZONE 'America/New_York')::date AS et_day,
       lower(split_part(coalesce(us_market_slug, ''), '-', 2)) AS league,
       count(*) AS rows, count(DISTINCT us_market_slug) AS contracts,
       count(DISTINCT event_key) AS provider_events,
       count(DISTINCT market) AS markets
  FROM external_valuations
 WHERE decided_at >= now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1 DESC, 4 DESC LIMIT 120;

\echo '== F5b · seven days of the collection ledger: provider events and events with a venue contract, per ET day and competition =='
SELECT (cycle_at AT TIME ZONE 'America/New_York')::date AS et_day, sport_key,
       count(DISTINCT cycle_id) AS cycles,
       count(DISTINCT provider_event_id) AS provider_events,
       count(DISTINCT provider_event_id) FILTER (WHERE us_market_slug IS NOT NULL) AS with_contract
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1 DESC, 4 DESC LIMIT 120;

\echo '== F6 · reactive attempts table: columns, then per-competition counts (last 24 h) =='
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_name = 'pinnapi_reactive_attempts' ORDER BY ordinal_position LIMIT 40;

\echo '== F7 · persisted coverage funnel snapshots, ET today =='
SELECT league, provider_events AS prov, mapped_events AS mapped, evaluated_events AS eval,
       decided_events AS decided, entered_events AS entered, venue_catalogue_events AS venue,
       computed_at
  FROM coverage_funnel_snapshots
 WHERE tz = 'America/New_York' AND day = (now() AT TIME ZONE 'America/New_York')::date
 ORDER BY venue_catalogue_events DESC NULLS LAST, provider_events DESC NULLS LAST LIMIT 60;
