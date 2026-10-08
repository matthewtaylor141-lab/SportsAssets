-- Audrey "paper agent pass has not completed for an unknown time" (read only):
-- the runtime's last attempt record, the pass health row and the recent
-- pass cadence, to tell a missing "at" (refused attempt) from a stopped pass.
\echo === ingestion_state paper_session_last_pass (no payload bodies) ===
SELECT value->>'at' AS at, to_timestamp((value->>'written_at')::float8) AS written_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'refusal' AS refusal,
       left(value->>'why', 240) AS why, value->>'heartbeat_truncated' AS truncated,
       value->>'original_chars' AS original_chars, value->>'elapsed_s' AS elapsed_s,
       value->>'errors' AS errors, value->>'budget_exhausted' AS budget_exhausted
  FROM ingestion_state WHERE key = 'paper_session_last_pass';
\echo === ext_pinnacle_last_cycle ===
SELECT to_timestamp((value->>'at')::float8) AS at FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
\echo === paper_session_health ===
SELECT * FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 3;
\echo === paper ledger last commits ===
SELECT max(committed_at) AS last_commit, max(seq) AS last_seq FROM paper_ledger;
\echo === paper decisions per 10 min (last 4h) ===
SELECT date_trunc('hour', decided_at) + floor(extract(minute FROM decided_at)/10)*interval '10 min' AS bucket,
       count(*) AS decisions
  FROM paper_decisions WHERE decided_at > now() - interval '4 hours' GROUP BY 1 ORDER BY 1;
