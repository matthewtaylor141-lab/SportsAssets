-- READ-ONLY. The production canary's inputs: SMALL LIVE control, the
-- effective cutover, the workers boot marker, and the decision-only journal
-- boots / decisions per boot (the cursor check needs two boots with
-- DECISION rows). Every statement is a SELECT.
SELECT mode, halted, halted_at, halt_reason FROM small_live_control WHERE id = 1;
SELECT cutover_id, release_sha, recorded_at, small_live_mode, capital_activated
  FROM live_parity_effective_cutover;
SELECT key, left(value::text, 600) FROM ingestion_state WHERE key = 'workers_boot';
SELECT boot_id, count(*) AS rows, count(*) FILTER (WHERE kind = 'DECISION') AS decisions,
       min(written_at) AS first_at, max(written_at) AS last_at
  FROM bettor_live_journal
 WHERE lane = 'polymarket-us/institutional/decision-only'
 GROUP BY 1 ORDER BY max(written_at) DESC LIMIT 6;
SELECT key, left(value::text, 300) FROM ingestion_state WHERE key = 'bettor_live_observation';
