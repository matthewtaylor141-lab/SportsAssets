-- Candidate 25 readback (read-only): Profitability OS migrations 216-219,
-- its runners' first forward rows, Eddie/Scout identities, workers commit,
-- and the capital guard (no actual order, lane stopped).
\echo '== M · migrations 213-222 =='
SELECT version, applied_at FROM schema_migrations WHERE version ~ '^(213|214|215|216|217|218|219|220|221|222)' ORDER BY 1;
\echo '== W · workers boot commit =='
SELECT value->>'commit' AS commit, value->>'at' AS at FROM ingestion_state WHERE key = 'workers_boot';
\echo '== I · agent identities and heartbeats =='
SELECT i.agent_id, s.state, s.last_heartbeat_at FROM agent_identities i LEFT JOIN agent_status s USING (agent_id) ORDER BY 1;
\echo '== R1 · profitability runs (216) =='
SELECT component, status, count(*) AS n, max(finished_at) AS last_at FROM pos_runs GROUP BY 1, 2 ORDER BY 1;
\echo '== R2 · north-star observations (216) =='
SELECT metric, book, status, value, sample_n, observed_at FROM (
  SELECT DISTINCT ON (metric, book) * FROM pos_metric_observations ORDER BY metric, book, observed_at DESC) x ORDER BY 1, 2;
\echo '== R3 · learning runs (218) =='
SELECT component, status, count(*) AS n, max(finished_at) AS last_at FROM poslearn_runs GROUP BY 1, 2 ORDER BY 1;
SELECT subject_id, status, role FROM poslearn_registrations ORDER BY 1 LIMIT 30;
\echo '== R4 · twin runs + evidence ladder (219) =='
SELECT component, status, count(*) AS n, max(finished_at) AS last_at FROM twin_runs GROUP BY 1, 2 ORDER BY 1;
SELECT level, confidence, computed_at FROM twin_evidence_ladder ORDER BY computed_at DESC LIMIT 1;
\echo '== R5 · Eddie / Scout (217) =='
SELECT recommendation, count(*) AS n, max(estimated_at) AS last_at FROM eddie_execution_estimates GROUP BY 1;
SELECT count(*) AS sources FROM scout_sources; SELECT state, count(*) FROM scout_features GROUP BY 1;
\echo '== G · capital guard =='
SELECT count(*) AS violations_since_cand24 FROM execution_intents
 WHERE live_eligible AND coalesce(live_eligibility->'admission'->>'verdict','') <> 'LIVE_ADMISSIBLE' AND created_at > '2026-10-04 05:30:00+00';
SELECT state, count(*) FROM execmirror_orders WHERE execution_intent_id IS NOT NULL GROUP BY 1;
SELECT enabled, stopped, left(account_fingerprint, 12) AS fp, scale, max_order_usd FROM execmirror_control;
