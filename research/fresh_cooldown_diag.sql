-- Held-mark reads vs the venue cooldown gate (read-only diagnosis).
SELECT key, value::text AS v FROM ingestion_state
 WHERE key LIKE 'venue_cooldown%' ORDER BY key;

SELECT key, (value->'venue_rate_controls')::text AS rate_controls,
       value->>'finished_at' AS finished_at
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_cycle_standby');

SELECT run_id, trigger, started_at, finished_at, held_markets, due, harvested,
       read_attempted, read_ok, read_failed, skipped_budget, error
  FROM paper_mark_refresh_runs ORDER BY run_id DESC LIMIT 40;

SELECT date_trunc('minute', observed_at) - (extract(minute FROM observed_at)::int % 10) * interval '1 minute' AS bucket,
       read_basis, left(coalesce(error, 'OK'), 70) AS outcome, count(*)
  FROM paper_book_observations
 WHERE observed_at > now() - interval '6 hours'
 GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC LIMIT 300;

SELECT date_trunc('hour', observed_at) AS hr,
       count(*) FILTER (WHERE error IS NULL) AS ok,
       count(*) FILTER (WHERE error LIKE '%COOLDOWN%') AS cooldown,
       count(*) FILTER (WHERE error IS NOT NULL AND error NOT LIKE '%COOLDOWN%') AS other_err
  FROM paper_book_observations
 WHERE observed_at > now() - interval '30 hours'
 GROUP BY 1 ORDER BY 1 DESC;
