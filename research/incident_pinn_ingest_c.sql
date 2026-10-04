-- P0 INCIDENT (2026-10-04) segment PINNAPI COVERAGE + RAW INGESTION + FRESHNESS, second read.
-- Read-only, bounded (24 h windows, LIMITs). Splits the collector ledger into the
-- SCHEDULED cycles (more than one provider event in the cycle) and the REACTIVE
-- WS-triggered cycles (exactly one event), then decomposes NO_PINNACLE_ON_EVENT by
-- fixture horizon and QUOTE_STALE_ON_ARRIVAL by whose clock made it stale.
\echo '== C0 read instant =='
SELECT now() AS read_at;

\echo '== C1 scheduled vs reactive cycles, last 24 h =='
WITH c AS (
  SELECT cycle_id, count(*) AS n, count(DISTINCT sport_key) AS k
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours'
   GROUP BY 1)
SELECT CASE WHEN n > 1 OR k > 1 THEN 'SCHEDULED' ELSE 'REACTIVE_OR_SINGLE' END AS kind,
       count(*) AS cycles, sum(n) AS event_rows
  FROM c GROUP BY 1;

\echo '== C2 scheduled cycles: NO_PINNACLE_ON_EVENT and every other first refusal by fixture horizon, last 24 h =='
WITH c AS (
  SELECT cycle_id FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours'
   GROUP BY 1 HAVING count(*) > 1 OR count(DISTINCT sport_key) > 1),
r AS (
  SELECT o.*, CASE WHEN o.commence_time ~ '^\d{4}-\d\d-\d\dT\d\d:\d\d'
                   THEN o.commence_time::timestamptz END AS ko
    FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
   WHERE o.cycle_at > now() - interval '24 hours')
SELECT sport_key,
       CASE WHEN ko IS NULL THEN 'h?_unparsed'
            WHEN ko < cycle_at - interval '4 hours' THEN 'h0_started_over_4h_ago'
            WHEN ko <= cycle_at THEN 'h1_in_play_0_4h'
            WHEN ko <= cycle_at + interval '6 hours' THEN 'h2_pregame_0_6h'
            WHEN ko <= cycle_at + interval '24 hours' THEN 'h3_pregame_6_24h'
            WHEN ko <= cycle_at + interval '72 hours' THEN 'h4_pregame_1_3d'
            WHEN ko <= cycle_at + interval '7 days' THEN 'h5_pregame_3_7d'
            ELSE 'h6_pregame_over_7d' END AS horizon,
       coalesce(first_refusal, '(' || outcome || ')') AS first_refusal,
       count(*) AS rows, count(DISTINCT provider_event_id) AS events
  FROM r GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC LIMIT 200;

\echo '== C3 QUOTE_STALE_ON_ARRIVAL: provider-stale vs made stale by our own processing, scheduled vs reactive, last 24 h =='
WITH c AS (
  SELECT cycle_id, (count(*) > 1 OR count(DISTINCT sport_key) > 1) AS scheduled
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours'
   GROUP BY 1)
SELECT o.sport_key, CASE WHEN c.scheduled THEN 'SCHEDULED' ELSE 'REACTIVE' END AS kind,
       count(*) AS stale_rows,
       count(*) FILTER (WHERE o.provider_lag_s > 30) AS provider_lag_alone_over_30,
       count(*) FILTER (WHERE o.provider_lag_s <= 30) AS fresh_on_receipt_made_stale_by_us,
       count(*) FILTER (WHERE o.provider_lag_s IS NULL) AS lag_unmeasured,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY o.queue_position)::numeric, 0) AS queue_pos_p50,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY o.our_processing_s)::numeric, 1) AS ours_p50
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.cycle_at > now() - interval '24 hours' AND o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo '== C4 our processing delay by queue position (scheduled cycles, priced events), last 24 h =='
WITH c AS (
  SELECT cycle_id FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours'
   GROUP BY 1 HAVING count(*) > 1 OR count(DISTINCT sport_key) > 1)
SELECT sport_key, least(queue_position, 20) AS queue_pos_capped,
       count(*) AS rows,
       round(avg(our_processing_s)::numeric, 1) AS ours_avg,
       round(avg(provider_lag_s)::numeric, 1) AS lag_avg,
       count(*) FILTER (WHERE quote_age_s > 30) AS over_30
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.cycle_at > now() - interval '24 hours' AND o.our_processing_s IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 120;

\echo '== C5 the newest scheduled cycle, every event row (identity, horizon, refusal, clocks) =='
WITH c AS (
  SELECT cycle_id, max(cycle_at) AS at FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '6 hours'
   GROUP BY 1 HAVING count(DISTINCT sport_key) > 1
   ORDER BY 2 DESC LIMIT 1)
SELECT o.sport_key, o.queue_position AS q, left(o.home, 24) AS home, left(o.away, 24) AS away,
       o.commence_time, o.outcome, o.first_refusal, o.stage, o.mapped_by,
       round(o.provider_lag_s::numeric, 1) AS lag, round(o.our_processing_s::numeric, 1) AS ours,
       round(o.quote_age_s::numeric, 1) AS age, o.us_market_slug,
       left(o.codes::text, 160) AS codes
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 ORDER BY o.sport_key, o.queue_position LIMIT 120;

\echo '== C6 per provider competition, last 24 h: events that EVER carried a Pinnacle price vs NEVER =='
SELECT sport_key,
       count(*) FILTER (WHERE has_pin) AS events_with_pinnacle_at_least_once,
       count(*) FILTER (WHERE NOT has_pin) AS events_never_with_pinnacle,
       count(*) AS events
  FROM (SELECT sport_key, provider_event_id,
               bool_or(coalesce(first_refusal, '') <> 'NO_PINNACLE_ON_EVENT') AS has_pin
          FROM ext_candidate_outcomes
         WHERE cycle_at > now() - interval '24 hours'
         GROUP BY 1, 2) x
 GROUP BY 1 ORDER BY 1;

\echo '== C7 reactive attempts per hour, last 24 h (WS -> evaluation throughput) =='
SELECT date_trunc('hour', created_at) AS hour, count(*) AS attempts,
       count(*) FILTER (WHERE state = 'COMPLETED') AS completed,
       count(*) FILTER (WHERE state = 'TIMEOUT') AS timeouts,
       count(DISTINCT event_id) AS events,
       count(*) FILTER (WHERE (detail->>'held')::boolean) AS held
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1;

\echo '== C8 reactive attempts: queue wait and evaluation time (seconds), last 24 h =='
SELECT state, count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (detail->>'evaluation_started_at')::float8 - (detail->>'queued_at')::float8)::numeric, 2) AS queue_wait_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY (detail->>'evaluation_started_at')::float8 - (detail->>'queued_at')::float8)::numeric, 2) AS queue_wait_p90,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (detail->>'finished_at')::float8 - (detail->>'evaluation_started_at')::float8)::numeric, 2) AS eval_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY (detail->>'finished_at')::float8 - (detail->>'evaluation_started_at')::float8)::numeric, 2) AS eval_p90
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
   AND detail ? 'queued_at' AND detail ? 'evaluation_started_at'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== C9 external_valuations, last 24 h: Pinnacle quote stream / clocks for PinnAPI-primary rows (observed_at vs received_at vs decided_at) =='
SELECT sport_family, provider,
       count(*) AS rows,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - observed_at))::numeric, 2) AS recv_minus_obs_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - observed_at))::numeric, 2) AS recv_minus_obs_p90,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM decided_at - received_at))::numeric, 2) AS decided_minus_recv_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM decided_at - received_at))::numeric, 2) AS decided_minus_recv_p90,
       count(*) FILTER (WHERE observed_at IS NULL) AS observed_null
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC;
