-- READ-ONLY. WHY THE PINNACLE-DEVIG ENTRY LANE WRITES NO ENTRY_DECISION ROWS.
-- Companion to research/derek_entry_lane_investigation_2026-10-01.md.
--
-- L0 control row, heartbeat keys present (state only; no secrets)
-- L1 valuations per hour around the two boundaries (09-27 19:36Z, 09-30 02:56Z)
-- L2 any external_valuations row of ANY experiment inside the silent window
-- L3 durable per-event ledger (ext_candidate_outcomes): span and cycles per day
-- L4 first_refusal per day from the durable ledger
-- L5 latest heartbeat: headline fields, odds_freshness, outcome_join, candidate_outcomes
-- L6 latest heartbeat: mapped_candidate_ledger entries
-- L7 latest cycle's durable ledger rows (every event, every code)
-- L8 stale-on-arrival split over the durable ledger, per day
-- L9 the identity refusal: every event refused VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM, distinct
-- L10 the 8 old unlabelled ENTRY_DECISION fixtures: what the outcome join recorded
-- L11 the unjoined queue: size, never-asked vs asked, by purpose

\echo '== L0 control and heartbeat rows =='
SELECT key,
       CASE WHEN key = 'ext_pinnacle_shadow' THEN left(value::text, 40)
            ELSE left(COALESCE(value->>'state', '-'), 40) END AS state_or_value,
       CASE WHEN jsonb_typeof(value) = 'object' AND value ? 'written_at'
            THEN to_timestamp((value->>'written_at')::float8) END AS written_at,
       CASE WHEN jsonb_typeof(value) = 'object' THEN left(COALESCE(value->>'why', '-'), 160) END AS why
  FROM ingestion_state
 WHERE key LIKE 'ext_pinnacle%'
 ORDER BY key;

\echo '== L1a valuations per hour 2026-09-27 12Z .. 09-28 04Z =='
SELECT date_trunc('hour', decided_at) AS hour, record_purpose, count(*) AS n,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at BETWEEN '2026-09-27 12:00+00' AND '2026-09-28 04:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== L1b valuations per hour 2026-09-29 18Z .. 09-30 06Z =='
SELECT date_trunc('hour', decided_at) AS hour, record_purpose, count(*) AS n,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at BETWEEN '2026-09-29 18:00+00' AND '2026-09-30 06:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== L2 any valuation of any experiment, 09-27 19:37Z .. 09-30 02:55Z =='
SELECT experiment_id, record_purpose, count(*) AS n, min(decided_at), max(decided_at)
  FROM external_valuations
 WHERE decided_at > '2026-09-27 19:37:30+00' AND decided_at < '2026-09-30 02:55:00+00'
 GROUP BY 1, 2;

\echo '== L2b the last ENTRY_DECISION rows and the first CALIBRATION_ONLY rows =='
(SELECT 'last_entry' AS which, id, decided_at, us_market_slug, admissible,
        left(COALESCE(array_to_string(refusals, ','), '-'), 160) AS refusals
   FROM external_valuations
  WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND record_purpose = 'ENTRY_DECISION'
  ORDER BY decided_at DESC LIMIT 3)
UNION ALL
(SELECT 'first_calib', id, decided_at, us_market_slug, admissible,
        left(COALESCE(array_to_string(refusals, ','), '-'), 160)
   FROM external_valuations
  WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND record_purpose = 'CALIBRATION_ONLY'
  ORDER BY decided_at ASC LIMIT 3);

\echo '== L3 durable per-event ledger span and cycles per day =='
SELECT date_trunc('day', cycle_at)::date AS day, count(DISTINCT cycle_id) AS cycles,
       count(*) AS event_rows, min(cycle_at) AS first_cycle, max(cycle_at) AS last_cycle,
       count(*) FILTER (WHERE outcome = 'ADMITTED') AS admitted
  FROM ext_candidate_outcomes GROUP BY 1 ORDER BY 1;

\echo '== L4 first_refusal per day, durable ledger =='
SELECT date_trunc('day', cycle_at)::date AS day, COALESCE(first_refusal, outcome) AS first_refusal_or_outcome,
       count(*) AS event_rows, count(DISTINCT provider_event_id) AS distinct_events
  FROM ext_candidate_outcomes GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== L4b every code appearing anywhere in codes, per day (not only first) =='
SELECT date_trunc('day', cycle_at)::date AS day, c.code, count(*) AS event_rows
  FROM ext_candidate_outcomes, jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(codes) = 'array' THEN codes ELSE '[]'::jsonb END) AS c(code)
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== L5 latest heartbeat headline =='
SELECT to_timestamp((value->>'written_at')::float8) AS written_at,
       value->>'state' AS state, value->>'evaluated' AS evaluated, value->>'written' AS written,
       value->>'markets_considered' AS markets_considered,
       value->>'valuations_recorded_inadmissible_for_calibration' AS calib_recorded,
       left((value->'refusals')::text, 900) AS refusals
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== L5b latest heartbeat odds_freshness =='
SELECT left(jsonb_pretty(value->'odds_freshness'), 3000) AS odds_freshness
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== L5c latest heartbeat candidate_outcomes =='
SELECT left(jsonb_pretty(value->'candidate_outcomes'), 3000) AS candidate_outcomes
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== L5d latest heartbeat outcome_join and calibration_only digests =='
SELECT left(jsonb_pretty(value->'outcome_join'), 2500) AS outcome_join,
       left(jsonb_pretty(value->'calibration_only'), 1500) AS calibration_only,
       left(jsonb_pretty(value->'market_subscription'), 2000) AS market_subscription
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== L5e latest heartbeat step timing and selection =='
SELECT left((value->'step_timing_s')::text, 800) AS step_timing_s,
       left((value->'sports_selection')::text, 1200) AS sports_selection,
       left((value->'venue_errors')::text, 1500) AS venue_errors,
       value->'heartbeat_trimmed' AS trimmed
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== L6 latest heartbeat mapped_candidate_ledger =='
SELECT e->>'first_refusal' AS first_refusal, e->>'stage' AS stage,
       e->>'global_slug' AS global_slug, e->>'us_market_slug' AS us_slug,
       e->>'priced_outcome' AS priced_outcome, e->>'age_s' AS age_s,
       e->>'provider_lag_s' AS provider_lag_s, e->>'our_processing_s' AS our_processing_s,
       left(COALESCE(e->>'why', ''), 300) AS why
  FROM ingestion_state, jsonb_array_elements(
         CASE WHEN jsonb_typeof(value->'mapped_candidate_ledger') = 'array'
              THEN value->'mapped_candidate_ledger' ELSE '[]'::jsonb END) e
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== L6b one full mapped_candidate_ledger entry per first_refusal (raw, shortened) =='
SELECT DISTINCT ON (e->>'first_refusal') e->>'first_refusal' AS first_refusal, left(e::text, 2500) AS entry
  FROM ingestion_state, jsonb_array_elements(
         CASE WHEN jsonb_typeof(value->'mapped_candidate_ledger') = 'array'
              THEN value->'mapped_candidate_ledger' ELSE '[]'::jsonb END) e
 WHERE key = 'ext_pinnacle_last_cycle'
 ORDER BY e->>'first_refusal';

\echo '== L7 latest durable cycle: every event =='
SELECT queue_position AS q, sport_key, home, away, commence_time, outcome, first_refusal,
       global_slug, us_market_slug, mapped_by, global_refusal_replaced,
       provider_lag_s, our_processing_s, quote_age_s, left(codes::text, 200) AS codes
  FROM ext_candidate_outcomes
 WHERE cycle_id = (SELECT cycle_id FROM ext_candidate_outcomes ORDER BY cycle_at DESC LIMIT 1)
 ORDER BY sport_key, queue_position;

\echo '== L8 stale-on-arrival split, durable ledger, per day =='
SELECT date_trunc('day', cycle_at)::date AS day,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale_on_arrival,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL' AND provider_lag_s > 30) AS provider_lag_alone_over_30,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL' AND provider_lag_s <= 30) AS ours_pushed_it_over,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL' AND provider_lag_s IS NULL) AS lag_unmeasured,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS p50_provider_lag_all,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS p90_provider_lag_all,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY our_processing_s)::numeric, 1) AS p50_ours_all,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY our_processing_s)::numeric, 1) AS p90_ours_all,
       count(provider_lag_s) AS lag_samples
  FROM ext_candidate_outcomes GROUP BY 1 ORDER BY 1;

\echo '== L8b stale-on-arrival events, latest 30 =='
SELECT cycle_at, queue_position AS q, home, away, provider_lag_s, our_processing_s, quote_age_s, us_market_slug
  FROM ext_candidate_outcomes
 WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 ORDER BY cycle_at DESC, queue_position LIMIT 30;

\echo '== L9 identity refusal: distinct events refused VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM =='
SELECT home, away, commence_time, global_slug, us_market_slug, mapped_by,
       count(*) AS cycles, min(cycle_at), max(cycle_at), left(max(codes::text), 300) AS codes
  FROM ext_candidate_outcomes
 WHERE codes::text LIKE '%VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM%'
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY max(cycle_at) DESC LIMIT 40;

\echo '== L9b NO_VENUE_CONTRACT_FOR_EVENT and NO_PINNACLE_ON_EVENT: distinct events, latest day =='
SELECT first_refusal, home, away, commence_time, count(*) AS cycles, left(max(codes::text), 200) AS codes
  FROM ext_candidate_outcomes
 WHERE first_refusal IN ('NO_VENUE_CONTRACT_FOR_EVENT', 'NO_PINNACLE_ON_EVENT')
   AND cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC LIMIT 80;

\echo '== L9c venue-native catalogue: baseball winner events in the next/last day, both participants =='
SELECT event_slug, min(game_start) AS game_start,
       string_agg(DISTINCT team_name, ' | ') AS team_names,
       string_agg(DISTINCT intent, ',') AS intents, count(*) AS rows, max(updated_at) AS reseen
  FROM us_premap
 WHERE sports_type = 'baseball_team_full_game_winner'
   AND game_start > now() - interval '1 day' AND game_start < now() + interval '2 days'
 GROUP BY event_slug ORDER BY min(game_start) LIMIT 60;

\echo '== L9d provider names of identity-refused events vs venue team names (same day) =='
WITH r AS (
  SELECT DISTINCT home, away, commence_time
    FROM ext_candidate_outcomes
   WHERE codes::text LIKE '%VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM%'
     AND cycle_at > now() - interval '3 days')
SELECT r.home, r.away, r.commence_time, p.event_slug, p.game_start,
       string_agg(DISTINCT p.team_name, ' | ') AS venue_team_names
  FROM r LEFT JOIN us_premap p
    ON p.sports_type = 'baseball_team_full_game_winner'
   AND abs(extract(epoch FROM p.game_start) - extract(epoch FROM r.commence_time::timestamptz)) < 6 * 3600
   AND (p.team_name ILIKE '%' || split_part(r.home, ' ', array_length(string_to_array(r.home, ' '), 1)) || '%'
        OR p.team_name ILIKE '%' || split_part(r.away, ' ', array_length(string_to_array(r.away, ' '), 1)) || '%')
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 3, 1, 5 LIMIT 60;

\echo '== L10 the old unlabelled ENTRY_DECISION fixtures: what the join recorded =='
SELECT us_market_slug, count(*) AS rows, bool_or(outcome_known) AS any_known,
       string_agg(DISTINCT COALESCE(outcome_basis, '-'), ',') AS bases,
       string_agg(DISTINCT COALESCE(outcome_side_map, '-'), ',') AS side_maps,
       string_agg(DISTINCT COALESCE(buy_intent, '-') || '/' || COALESCE(ladder_side, '-'), ',') AS intent_side,
       string_agg(DISTINCT left(COALESCE(settlement_read, '-'), 60), ' | ') AS settlement_reads,
       min(settlement_read_at) AS first_asked, max(settlement_read_at) AS last_asked,
       min(decided_at) AS decided_from, max(decided_at) AS decided_to,
       bool_or(probability IS NULL) AS any_no_pinnacle
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND record_purpose = 'ENTRY_DECISION'
   AND NOT outcome_known
   AND substring(us_market_slug from '([0-9]{4}-[0-9]{2}-[0-9]{2})')::date < (now() AT TIME ZONE 'UTC')::date - 1
 GROUP BY 1 ORDER BY 1;

\echo '== L10b same, rows with a null slug (the join skips them) =='
SELECT record_purpose, count(*) AS rows, count(*) FILTER (WHERE NOT outcome_known) AS unknown
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND us_market_slug IS NULL
 GROUP BY 1;

\echo '== L11 the unjoined queue by purpose: never asked vs asked, oldest ask =='
SELECT record_purpose,
       count(*) AS unjoined,
       count(*) FILTER (WHERE settlement_read_at IS NULL) AS never_asked,
       count(*) FILTER (WHERE settlement_read_at IS NOT NULL) AS asked,
       min(settlement_read_at) AS oldest_ask, max(settlement_read_at) AS newest_ask,
       count(*) FILTER (WHERE decided_at < now() - interval '2 hours') AS eligible_by_age
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND outcome_known = FALSE AND outcome_basis IS NULL AND us_market_slug IS NOT NULL
 GROUP BY 1;

\echo '== L11b settlement reads per hour, last 48 h (is the join running?) =='
SELECT date_trunc('hour', settlement_read_at) AS hour, count(*) AS rows_asked,
       count(*) FILTER (WHERE outcome_known) AS resolved
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND settlement_read_at > now() - interval '48 hours'
 GROUP BY 1 ORDER BY 1;
