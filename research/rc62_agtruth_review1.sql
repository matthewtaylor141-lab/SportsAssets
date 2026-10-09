\echo R1 capability runtime loop health row
SELECT loop_name, process, starts, successes, errors, last_start_at, last_success_at, last_error_at, last_error, commit_sha, updated_at, left(detail::text, 400) AS detail FROM runtime_loop_health WHERE loop_name LIKE 'agents.capab%';
\echo R2 capability heartbeat and control
SELECT key, value FROM ingestion_state WHERE key LIKE 'agent.capabilities%';
\echo R3 claim path statements in pg_stat_statements
SELECT calls, round(mean_exec_time::numeric,2) AS mean_ms, round(max_exec_time::numeric,2) AS max_ms, rows, left(query, 160) AS q FROM pg_stat_statements WHERE (query ILIKE '%FROM agent_tasks WHERE task_id=ANY%' OR query ILIKE '%FROM ingestion_state WHERE key=$1 FOR UPDATE%' OR query ILIKE '%status=$4 AND (spec->>$5)::double precision>$6%' OR query ILIKE '%INSERT INTO ingestion_state(key,value) VALUES($1,$2::jsonb) ON CONFLICT DO NOTHING%') ORDER BY calls DESC LIMIT 12;
\echo R4 capability task events last 48h by hour and kind
SELECT date_trunc('hour', at) AS hr, kind, count(*) FROM agent_task_events WHERE task_id LIKE 'capwork:%' AND at > now() - interval '48 hours' GROUP BY 1,2 ORDER BY 1 DESC, 2 LIMIT 60;
\echo R5 open capability tasks by status
SELECT status, count(*) FROM agent_tasks WHERE kind = 'AGENT_CAPABILITY_REVIEW_V1' AND status NOT IN ('CLOSED_NO_CHANGE','REJECTED','CANCELLED') GROUP BY 1;
\echo R6 Derek agent_status now
SELECT agent_id, state, activity, left(waiting_on::text, 300) AS waiting_on, last_heartbeat_at, now() AS db_now FROM agent_status WHERE agent_id = 'DEREK';
\echo R7 Derek paper decisions last hour
SELECT count(*) AS n, max(decided_at) AS newest, max(recorded_at) AS newest_recorded FROM paper_decisions WHERE strategy = 'DEREK_ENTRY_POLICY_V2' AND decided_at > now() - interval '1 hour';
\echo R8 BOOK_TIME_IN_FUTURE candidates last 24h by venue with book age distribution in seconds
SELECT c->>'venue' AS venue, count(*) AS n, min((c->>'book_age_s')::numeric) AS min_age, percentile_cont(0.01) WITHIN GROUP (ORDER BY (c->>'book_age_s')::numeric) AS p01, percentile_cont(0.5) WITHIN GROUP (ORDER BY (c->>'book_age_s')::numeric) AS p50, max((c->>'book_age_s')::numeric) AS max_age FROM canonical_route_receipts r CROSS JOIN LATERAL jsonb_array_elements(r.candidates) c WHERE r.computed_at > now() - interval '24 hours' AND c->>'reason' = 'BOOK_TIME_IN_FUTURE' GROUP BY 1;
\echo R9 BOOK_TIME_IN_FUTURE lead buckets last 24h (seconds the book is ahead of the pass clock)
SELECT CASE WHEN -(c->>'book_age_s')::numeric <= 1 THEN 'a_le_1s' WHEN -(c->>'book_age_s')::numeric <= 5 THEN 'b_le_5s' WHEN -(c->>'book_age_s')::numeric <= 15 THEN 'c_le_15s' WHEN -(c->>'book_age_s')::numeric <= 60 THEN 'd_le_60s' ELSE 'e_gt_60s' END AS lead, count(*) FROM canonical_route_receipts r CROSS JOIN LATERAL jsonb_array_elements(r.candidates) c WHERE r.computed_at > now() - interval '24 hours' AND c->>'reason' = 'BOOK_TIME_IN_FUTURE' GROUP BY 1 ORDER BY 1;
\echo R10 receipts per minute cadence sample last hour
SELECT count(*) AS receipts, count(DISTINCT computed_at) AS distinct_passes, min(computed_at), max(computed_at) FROM canonical_route_receipts WHERE computed_at > now() - interval '1 hour';
