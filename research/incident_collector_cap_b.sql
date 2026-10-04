-- P0 INCIDENT (2026-10-04) segment COLLECTOR SPORT CAP, second read (read-only).
-- (1) The metered key's cumulative usage on the collector heartbeat, read again
--     so the per-cycle delta can be computed against incident_collector_cap_a
--     (heartbeat at 2026-10-04 20:28:22Z: used 440385, remaining 14559615).
-- (2) The entry lane's measured duration per scheduled cycle (recorded_at of the
--     cycle's outcome rows minus cycle_at = servicing + entry lane), 7 d, which
--     is the cycle-time headroom any per-cycle evaluation bound has to respect.
\echo '== B0 read instant =='
SELECT now() AS read_at;

\echo '== B1 collector heartbeat: credits, requested, timing, provider events per sport =='
SELECT to_timestamp((value->>'at')::float8) AS at, value->>'elapsed_s' AS elapsed_s,
       value->'credits' AS credits,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'step_timing_s' AS step_timing_s,
       (SELECT jsonb_object_agg(f.key, f.value->'provider_events')
          FROM jsonb_each(CASE WHEN jsonb_typeof(value->'funnel_by_provider_sport') = 'object'
                               THEN value->'funnel_by_provider_sport' ELSE '{}'::jsonb END) AS f)
         AS provider_events_by_sport,
       value->'odds_freshness'->'odds_refetches' AS odds_refetches
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== B2 entry-lane duration per scheduled cycle (max recorded_at - cycle_at), per day =='
WITH cyc AS (
  SELECT cycle_id, min(cycle_at) AS cycle_at,
         extract(epoch FROM max(recorded_at) - min(cycle_at)) AS lane_s,
         count(*) AS rows
    FROM ext_candidate_outcomes
   WHERE cycle_at >= now() - interval '7 days'
   GROUP BY cycle_id HAVING count(*) >= 2)
SELECT date_trunc('day', cycle_at) AS day, count(*) AS cycles,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY lane_s)::numeric, 1) AS lane_p50_s,
       round(percentile_disc(0.9) WITHIN GROUP (ORDER BY lane_s)::numeric, 1) AS lane_p90_s,
       round(max(lane_s)::numeric, 1) AS lane_max_s,
       round(avg(rows), 1) AS avg_events
  FROM cyc GROUP BY 1 ORDER BY 1;

\echo '== B3 reactive (WS) one-event cycles: duration of the outcome write after cycle start, 24 h =='
WITH one AS (
  SELECT cycle_id, extract(epoch FROM max(recorded_at) - min(cycle_at)) AS s
    FROM ext_candidate_outcomes
   WHERE cycle_at >= now() - interval '24 hours'
   GROUP BY cycle_id HAVING count(*) = 1)
SELECT count(*) AS reactive_cycles,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY s)::numeric, 2) AS p50_s,
       round(percentile_disc(0.9) WITHIN GROUP (ORDER BY s)::numeric, 2) AS p90_s
  FROM one;
