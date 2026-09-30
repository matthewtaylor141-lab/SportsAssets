-- READ-ONLY. CONSECUTIVE SERVICING PASSES, SAMPLED LIVE, PLUS THE NEW CYCLE
-- HEARTBEAT (writer build, market-data subscription digest).
--
-- The servicing task writes ONE row (ingestion_state 'ext_pinnacle_last_servicing')
-- and overwrites it every pass; there is no per-pass history table. So this reads
-- that row four times, 61 s apart (pg_sleep; read-only), and prints each pass's
-- own instant, duration, state/refusal (NOT_RUN = the execution authority was
-- busy) and what the pass serviced. `funded_servicing` null/absent means no
-- funded account is configured: the scheduler ran, and there was nothing to
-- service. No credential or balance is selected.

\echo '== S · servicing samples (four reads, 61 s apart) =='
SELECT now() AS read_at, value->>'state' AS state, value->>'refusal' AS refusal,
       to_timestamp((value->>'pass_at')::float8) AS pass_at,
       value->>'elapsed_s' AS pass_elapsed_s,
       value->'servicing_cadence'->>'passes' AS passes_so_far,
       value->'servicing_cadence'->>'skipped_busy' AS skipped_busy,
       value->'servicing_cadence'->>'errors' AS errors,
       value->'servicing_cadence'->'recent_start_gaps_s' AS start_gaps_s,
       value->'writer'->>'build' AS build,
       coalesce(value->'funded_servicing'::text, 'null') AS funded_servicing
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';
SELECT pg_sleep(61);
SELECT now() AS read_at, value->>'state' AS state, value->>'refusal' AS refusal,
       to_timestamp((value->>'pass_at')::float8) AS pass_at,
       value->>'elapsed_s' AS pass_elapsed_s,
       value->'servicing_cadence'->>'passes' AS passes_so_far,
       value->'servicing_cadence'->>'skipped_busy' AS skipped_busy,
       value->'servicing_cadence'->>'errors' AS errors,
       value->'servicing_cadence'->'recent_start_gaps_s' AS start_gaps_s,
       value->'writer'->>'build' AS build,
       coalesce(value->'funded_servicing'::text, 'null') AS funded_servicing
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';
SELECT pg_sleep(61);
SELECT now() AS read_at, value->>'state' AS state, value->>'refusal' AS refusal,
       to_timestamp((value->>'pass_at')::float8) AS pass_at,
       value->>'elapsed_s' AS pass_elapsed_s,
       value->'servicing_cadence'->>'passes' AS passes_so_far,
       value->'servicing_cadence'->>'skipped_busy' AS skipped_busy,
       value->'servicing_cadence'->>'errors' AS errors,
       value->'servicing_cadence'->'recent_start_gaps_s' AS start_gaps_s,
       value->'writer'->>'build' AS build,
       coalesce(value->'funded_servicing'::text, 'null') AS funded_servicing
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';
SELECT pg_sleep(61);
SELECT now() AS read_at, value->>'state' AS state, value->>'refusal' AS refusal,
       to_timestamp((value->>'pass_at')::float8) AS pass_at,
       value->>'elapsed_s' AS pass_elapsed_s,
       value->'servicing_cadence'->>'passes' AS passes_so_far,
       value->'servicing_cadence'->>'skipped_busy' AS skipped_busy,
       value->'servicing_cadence'->>'errors' AS errors,
       value->'servicing_cadence'->'recent_start_gaps_s' AS start_gaps_s,
       value->'writer'->>'build' AS build,
       coalesce(value->'funded_servicing'::text, 'null') AS funded_servicing
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';

\echo '== H · the cycle heartbeat: writer build, elapsed, servicing source, subscription digest =='
SELECT to_timestamp((value->>'at')::float8) AS written_at,
       value->'writer'->>'build' AS writer_build, value->>'state' AS state,
       value->>'elapsed_s' AS cycle_elapsed_s,
       value->'servicing_cadence'->>'servicer' AS servicer
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT jsonb_pretty(value->'market_subscription') AS market_subscription
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== P · pair observation pass digest from the cycle heartbeat =='
SELECT jsonb_pretty(jsonb_strip_nulls(jsonb_build_object(
         'at', value->'pair_observation'->'at',
         'elapsed_s', value->'pair_observation'->'elapsed_s',
         'observations_written', value->'pair_observation'->'observations_written',
         'observations_written_not_admitted', value->'pair_observation'->'observations_written_not_admitted',
         'candidates', value->'pair_observation'->'candidates',
         'conclusions', value->'pair_observation'->'conclusions'))) AS pair_pass
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== O · counts now, and one observation''s full cancellation record =='
SELECT admission_status, label_status, count(*) AS observations,
       count(DISTINCT fixture) AS fixtures, max(observed_at) AS last_observed
  FROM bettor_pair_observations GROUP BY 1, 2;
SELECT observation_id, fixture, primary_slug, hedge_slug,
       jsonb_pretty(cancellation_terms) AS cancellation_terms
  FROM bettor_pair_observations ORDER BY observed_at DESC LIMIT 1;
