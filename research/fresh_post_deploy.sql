-- Held-mark refresh runs and freshness inputs after the e54ee95 deploy (read-only).
SELECT run_id, trigger, started_at, finished_at, held_markets, due, harvested,
       read_attempted, read_ok, read_failed, skipped_budget, error
  FROM paper_mark_refresh_runs ORDER BY run_id DESC LIMIT 25;

SELECT run_id, o.value->>'outcome' AS outcome, count(*)
  FROM paper_mark_refresh_runs r, jsonb_each(r.outcomes) AS o
 WHERE r.run_id > (SELECT max(run_id) - 6 FROM paper_mark_refresh_runs)
 GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC;

SELECT key, value->>'writer' AS writer, value->>'at' AS at, left((value->'venue_rate_controls'->'process_request_totals')::text, 300) AS totals
  FROM ingestion_state WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_cycle_standby', 'workers_boot');
