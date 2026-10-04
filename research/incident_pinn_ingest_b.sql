-- P0 INCIDENT (2026-10-04) segment PINNAPI COVERAGE + RAW INGESTION + FRESHNESS.
-- Read-only, bounded by time windows and LIMITs. Tables: ext_candidate_outcomes
-- (one row per provider event per collector cycle), external_valuations,
-- pinnapi_reactive_attempts, us_premap (venue catalogue), markets.
\echo '== B0 read instant =='
SELECT now() AS read_at;

\echo '== B1 collector cycles per hour, last 24 h (cadence; CYCLE_S = 900 s => 4/h expected) =='
SELECT date_trunc('hour', cycle_at) AS hour, count(DISTINCT cycle_id) AS cycles,
       count(*) AS event_rows, count(DISTINCT sport_key) AS sport_keys
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1;

\echo '== B2 which provider competitions each cycle requested, last 48 h (budget = 4 keys) =='
WITH c AS (
  SELECT cycle_id, min(cycle_at) AS at,
         string_agg(DISTINCT sport_key, ',' ORDER BY sport_key) AS keys
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '48 hours'
   GROUP BY 1)
SELECT keys, count(*) AS cycles, min(at) AS first_at, max(at) AS last_at
  FROM c GROUP BY 1 ORDER BY 2 DESC LIMIT 40;

\echo '== B3 per provider competition, last 24 h: cycles present, events, rows by outcome =='
SELECT sport_key, family,
       count(DISTINCT cycle_id) AS cycles_present,
       count(DISTINCT provider_event_id) AS distinct_events,
       count(*) AS rows,
       count(*) FILTER (WHERE outcome = 'ADMITTED') AS admitted,
       count(*) FILTER (WHERE outcome = 'REFUSED') AS refused,
       count(*) FILTER (WHERE outcome = 'DEFERRED') AS deferred,
       count(*) FILTER (WHERE outcome = 'ALREADY_RECORDED') AS already_recorded,
       count(*) FILTER (WHERE outcome = 'UNCLASSIFIED') AS unclassified,
       max(cycle_at) AS last_seen
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 5 DESC;

\echo '== B4 per provider competition x first refusal, last 24 h (rows and distinct events) =='
SELECT sport_key, coalesce(first_refusal, '(' || outcome || ')') AS first_refusal,
       coalesce(stage, '-') AS stage, count(*) AS rows,
       count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC
 LIMIT 150;

\echo '== B5 provider lag / our processing / quote age at arrival per competition, last 24 h (seconds) =='
SELECT sport_key,
       count(provider_lag_s) AS n_lag,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS lag_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS lag_p90,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY our_processing_s)::numeric, 1) AS ours_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY our_processing_s)::numeric, 1) AS ours_p90,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY quote_age_s)::numeric, 1) AS age_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY quote_age_s)::numeric, 1) AS age_p90,
       count(*) FILTER (WHERE quote_age_s > 30) AS age_over_30,
       count(*) FILTER (WHERE provider_lag_s > 30) AS provider_lag_over_30,
       count(*) FILTER (WHERE provider_lag_s IS NULL) AS no_lag_measure
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== B6 deferred (MAX_PER_CYCLE=40) by competition and hour, last 24 h =='
SELECT date_trunc('hour', cycle_at) AS hour, sport_key, count(*) AS deferred_rows,
       count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours' AND outcome = 'DEFERRED'
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 120;

\echo '== B7 external_valuations last 24 h by sport_family x provider x purpose =='
SELECT sport_family, provider, coalesce(record_purpose, '-') AS purpose,
       count(*) AS rows, count(DISTINCT event_key) AS events,
       count(DISTINCT us_market_slug) AS contracts,
       count(*) FILTER (WHERE probability IS NOT NULL) AS with_probability,
       count(*) FILTER (WHERE admissible) AS admissible,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY age_s)::numeric, 1) AS age_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY age_s)::numeric, 1) AS age_p90,
       count(*) FILTER (WHERE age_s IS NULL) AS age_null,
       max(decided_at) AS last
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo '== B8 PinnAPI primary selection outcome per sport_family, last 24 h (reference_input.fallback_reason) =='
SELECT sport_family,
       coalesce(settlement_comparison->'reference_input'->>'provider', '(none)') AS ref_provider,
       coalesce(settlement_comparison->'reference_input'->>'fallback_reason', '(primary used)') AS fallback_reason,
       count(*) AS rows, count(DISTINCT event_key) AS events
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 60;

\echo '== B9 PinnAPI feed-read provenance when the primary was used or refused, last 24 h =='
SELECT sport_family,
       settlement_comparison->'reference_input'->>'stream' AS stream,
       count(*) AS rows,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (settlement_comparison->'reference_input'->>'quote_age_s')::float8)::numeric, 2) AS ws_age_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY (settlement_comparison->'reference_input'->>'quote_age_s')::float8)::numeric, 2) AS ws_age_p90,
       count(*) FILTER (WHERE settlement_comparison->'reference_input'->'decision_check'->>'ok' = 'false') AS decision_check_failed
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
   AND settlement_comparison->'reference_input'->>'provider' = 'pinnapi.com/raw-websocket'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo '== B10 external_valuations refusal codes per sport_family, last 24 h (unnested, top 80) =='
SELECT sport_family, r AS refusal, count(*) AS rows, count(DISTINCT event_key) AS events
  FROM external_valuations, unnest(refusals) AS r
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 80;

\echo '== B11 reactive (WS -> evaluation) attempts, last 24 h, by state and reason =='
SELECT state, coalesce(detail->>'reason', '-') AS reason, count(*) AS attempts,
       count(DISTINCT event_id) AS events, min(created_at) AS first, max(created_at) AS last
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

\echo '== B12 reactive scheduler counters on the newest attempt (cumulative since process start) =='
SELECT created_at, state, detail->'counters' AS counters, detail->'writer' AS writer
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
 ORDER BY created_at DESC LIMIT 1;

\echo '== B13 venue catalogue (us_premap) per venue league token x sport family, starting within [-6h, +48h] =='
SELECT split_part(market_slug, '-', 2) AS league_token,
       split_part(coalesce(sports_type, ''), '_', 1) AS family,
       count(DISTINCT event_slug) AS events, count(DISTINCT market_slug) AS contracts,
       count(DISTINCT event_slug) FILTER (WHERE sports_type ILIKE '%full_game_winner%'
                                          OR sports_type ILIKE '%full_time_winner%'
                                          OR sports_type ILIKE '%match_winner%') AS winner_events,
       count(DISTINCT event_slug) FILTER (WHERE game_start <= now()) AS started_events,
       min(game_start) AS first_start, max(game_start) AS last_start
  FROM us_premap
 WHERE game_start > now() - interval '6 hours' AND game_start < now() + interval '48 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 80;

\echo '== B14 venue catalogue sports_type values, [-6h, +48h] (market families the venue lists) =='
SELECT coalesce(sports_type, '(null)') AS sports_type, count(DISTINCT event_slug) AS events,
       count(DISTINCT market_slug) AS contracts
  FROM us_premap
 WHERE game_start > now() - interval '6 hours' AND game_start < now() + interval '48 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 80;

\echo '== B15 global markets table: open + fresh (2 d) rows per sport label (collector MARKETS_SQL universe) =='
SELECT sport, count(*) AS open_fresh
  FROM markets
 WHERE NOT closed AND NOT resolved AND updated_at >= now() - interval '2 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;
