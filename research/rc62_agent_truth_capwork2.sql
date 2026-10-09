\echo review incomplete errors by day
SELECT date_trunc('day', at) AS day, detail->>'error' AS error, count(*) FROM agent_task_events WHERE task_id LIKE 'capwork:%' AND kind = 'REVIEW_INCOMPLETE' GROUP BY 1,2 ORDER BY 1 DESC, 3 DESC LIMIT 40;
\echo claimed and genuine by day
SELECT date_trunc('day', at) AS day, kind, count(*), max(at) FROM agent_task_events WHERE task_id LIKE 'capwork:%' AND kind IN ('CLAIMED','GENUINE_REVIEW','DEPENDENCY_FAILED') GROUP BY 1,2 ORDER BY 1 DESC, 2 LIMIT 40;
\echo loop health columns
SELECT column_name FROM information_schema.columns WHERE table_name = 'runtime_loop_health' ORDER BY ordinal_position;
\echo loop health row
SELECT * FROM runtime_loop_health WHERE name LIKE 'agents.capab%';
