-- P0 INCIDENT (2026-10-04) segment PINNAPI COVERAGE + RAW INGESTION + FRESHNESS, third read.
-- Read-only, bounded (7-day windows over indexed timestamps, LIMITs).
\echo '== D0 read instant =='
SELECT now() AS read_at;

\echo '== D1 scheduled collector cycles per UTC day and the competition set requested (7 d) =='
WITH c AS (
  SELECT cycle_id, min(cycle_at) AS at,
         string_agg(DISTINCT sport_key, ',' ORDER BY sport_key) AS keys
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '7 days'
   GROUP BY 1 HAVING count(*) > 1 OR count(DISTINCT sport_key) > 1)
SELECT date_trunc('day', at) AS day, keys, count(*) AS cycles, min(at) AS first, max(at) AS last
  FROM c GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 80;

\echo '== D2 provider events and first refusals per competition per UTC day (7 d, scheduled + reactive) =='
SELECT date_trunc('day', cycle_at) AS day, sport_key,
       count(DISTINCT provider_event_id) AS events, count(*) AS rows,
       count(*) FILTER (WHERE first_refusal = 'NO_PINNACLE_ON_EVENT') AS no_pinnacle,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale_on_arrival,
       count(*) FILTER (WHERE outcome = 'ADMITTED') AS admitted
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 120;

\echo '== D3 PinnAPI primary vs fallback per UTC day and sport family (7 d) =='
SELECT date_trunc('day', decided_at) AS day, sport_family,
       count(*) AS rows,
       count(*) FILTER (WHERE settlement_comparison->'reference_input'->>'provider' = 'pinnapi.com/raw-websocket') AS ws_primary,
       count(*) FILTER (WHERE settlement_comparison->'reference_input'->>'fallback_reason' = 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE') AS ws_age_unknown,
       count(*) FILTER (WHERE settlement_comparison->'reference_input'->>'fallback_reason' = 'FEED_QUOTE_OLDER_THAN_LIMIT') AS ws_older_than_30s,
       count(*) FILTER (WHERE settlement_comparison->'reference_input'->>'fallback_reason' = 'FEED_OWNERSHIP_NOT_HELD') AS ws_not_owner,
       count(*) FILTER (WHERE settlement_comparison->'reference_input'->>'fallback_reason' LIKE 'PINNAPI_PRIMARY_%') AS ws_primary_refused,
       count(*) FILTER (WHERE settlement_comparison->'reference_input' IS NULL) AS no_reference_record
  FROM external_valuations
 WHERE decided_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 80;

\echo '== D4 the feed owner runtime(s) seen by the reactive audit, 48 h (first/last attempt per runtime) =='
SELECT detail->'writer'->>'runtime_id' AS runtime_id, detail->'writer'->>'build' AS build,
       min(created_at) AS first_attempt, max(created_at) AS last_attempt, count(*) AS attempts
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '48 hours'
 GROUP BY 1, 2 ORDER BY 3 LIMIT 20;

\echo '== D5 venue winner events in the next 24 h (and in play from the last 4 h) by sport family and PinnAPI feed scope =='
SELECT split_part(coalesce(sports_type, ''), '_', 1) AS family,
       CASE split_part(coalesce(sports_type, ''), '_', 1)
            WHEN 'soccer' THEN 'IN_SCOPE_1' WHEN 'baseball' THEN 'IN_SCOPE_6'
            WHEN 'football' THEN 'OUT_5' WHEN 'basketball' THEN 'OUT_3'
            WHEN 'hockey' THEN 'OUT_4' WHEN 'tennis' THEN 'OUT_2'
            WHEN 'esports' THEN 'OUT_11' WHEN 'mma' THEN 'OUT_8' WHEN 'boxing' THEN 'OUT_9'
            WHEN 'rugby' THEN 'OUT_7' WHEN 'golf' THEN 'OUT_12'
            ELSE 'NO_PINNAPI_SPORT_ID' END AS feed_scope,
       count(DISTINCT event_slug) AS winner_events,
       count(DISTINCT event_slug) FILTER (WHERE game_start <= now()) AS in_play_events,
       count(DISTINCT split_part(market_slug, '-', 2)) AS leagues,
       (array_agg(DISTINCT split_part(market_slug, '-', 2)))[1:25] AS league_tokens
  FROM us_premap
 WHERE game_start > now() - interval '4 hours' AND game_start < now() + interval '24 hours'
   AND (sports_type ILIKE '%full_game_winner' OR sports_type ILIKE '%full_time_winner'
        OR sports_type ILIKE '%match_winner')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

\echo '== D6 PinnAPI feed heartbeat history is overwritten; the reactive scheduler counters per runtime at its newest attempt =='
SELECT DISTINCT ON (detail->'writer'->>'runtime_id')
       detail->'writer'->>'runtime_id' AS runtime_id, created_at, detail->'counters' AS counters
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '48 hours'
 ORDER BY detail->'writer'->>'runtime_id', created_at DESC
 LIMIT 10;
