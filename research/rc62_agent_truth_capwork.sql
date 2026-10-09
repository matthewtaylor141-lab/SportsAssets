\echo capwork status by assignee
SELECT assignee, status, count(*) AS n, avg(pg_column_size(outcome))::bigint AS avg_outcome_bytes, max(pg_column_size(outcome)) AS max_outcome_bytes, avg(pg_column_size(spec))::bigint AS avg_spec_bytes FROM agent_tasks WHERE kind = 'AGENT_CAPABILITY_REVIEW_V1' GROUP BY 1,2 ORDER BY 1,2;
\echo claim candidate set size
SELECT count(*) AS claimable, sum(pg_column_size(t.*))::bigint AS total_row_bytes FROM agent_tasks t WHERE kind = 'AGENT_CAPABILITY_REVIEW_V1' AND spec->>'account_id' = 'paper_acct_main' AND status IN ('OPEN','IN_PROGRESS','WAITING');
\echo agent_tasks size and indexes
SELECT pg_size_pretty(pg_total_relation_size('agent_tasks')) AS total, (SELECT count(*) FROM agent_tasks) AS rows;
SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN ('agent_tasks','agent_task_events') ORDER BY 1;
\echo latest events by kind last 6h
SELECT kind, count(*) AS n, max(at) AS newest FROM agent_task_events e WHERE at > now() - interval '6 hours' AND task_id LIKE 'capwork:%' GROUP BY 1 ORDER BY 2 DESC;
\echo reject reasons all time
SELECT kind, count(*) FROM agent_task_events WHERE task_id LIKE 'capwork:%' AND kind IN ('RETRY_BUDGET_EXHAUSTED','DEPENDENCY_FAILED','REVIEW_INCOMPLETE','GENUINE_REVIEW','CLAIMED') GROUP BY 1;
\echo control row
SELECT key, value FROM ingestion_state WHERE key LIKE 'agent.capabilities%';
\echo statements touching agent_tasks
SELECT calls, round(mean_exec_time::numeric,2) AS mean_ms, round(max_exec_time::numeric,2) AS max_ms, rows, left(query, 200) AS q FROM pg_stat_statements WHERE query ILIKE '%agent_tasks%' ORDER BY total_exec_time DESC LIMIT 15;
\echo loop health
SELECT * FROM runtime_loop_health WHERE loop LIKE 'agents.capab%' OR name LIKE 'agents.capab%' LIMIT 5;
