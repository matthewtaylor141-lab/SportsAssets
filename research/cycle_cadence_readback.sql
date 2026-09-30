-- READ-ONLY. HOW OFTEN THE SCHEDULED CYCLE ACTUALLY RUNS, measured from what
-- it writes (one attempt-ledger pass per cycle; entry-lane valuations), and
-- the latest heartbeat's timings and servicing digests.

\echo '== M1 · cycle cadence from the attempt ledger: one pass per cycle (24 h) =='
WITH p AS (
  SELECT pass_id, min(attempted_at) AS started
    FROM bettor_pair_observation_attempts
   WHERE attempted_at > now() - interval '24 hours'
   GROUP BY pass_id),
d AS (
  SELECT started, extract(epoch FROM started - lag(started) OVER (ORDER BY started)) AS gap_s
    FROM p)
SELECT count(*) AS passes, round(min(gap_s)) AS min_gap_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric) AS median_gap_s,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY gap_s))::numeric) AS p90_gap_s,
       round(max(gap_s)) AS max_gap_s
  FROM d WHERE gap_s IS NOT NULL;

\echo '== M2 · cycle cadence from entry-lane valuations: distinct decision instants per cycle (24 h) =='
WITH v AS (
  SELECT date_trunc('minute', decided_at) AS m FROM external_valuations
   WHERE decided_at > now() - interval '24 hours' GROUP BY 1),
c AS (
  SELECT m, extract(epoch FROM m - lag(m) OVER (ORDER BY m)) AS gap_s FROM v)
SELECT count(*) AS minutes_with_valuations,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric) AS median_gap_s,
       round(max(gap_s)) AS max_gap_s,
       count(*) FILTER (WHERE gap_s > 600) AS gaps_over_10_min
  FROM c WHERE gap_s IS NOT NULL;

\echo '== M3 · the latest heartbeat: step timings and servicing digests, if recorded =='
SELECT to_timestamp((value->>'at')::float8) AS written_at,
       value->'writer'->>'build' AS build,
       value->>'state' AS state,
       (value->'pair_observation'->>'elapsed_s') AS observation_elapsed_s,
       value->'timings' AS timings,
       value->'funded_servicing' IS NOT NULL AS has_funded_servicing,
       left(coalesce((value->'funded_servicing')::text, ''), 300) AS funded_servicing,
       left(coalesce((value->'xavier')::text, (value->'xavier_review')::text, ''), 300) AS xavier
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
