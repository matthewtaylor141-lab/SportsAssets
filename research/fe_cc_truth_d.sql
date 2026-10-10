-- READ ONLY. Frontend root-cause audit (group frontend), part D: the open
-- agent_tasks behind the desk HANDOFF PENDING state, and whether the live
-- score display tables exist and hold anything. SELECT statements only.
\echo == D1. open agent_tasks by assignee and kind ==
SELECT assignee, kind, count(*) AS n, min(created_at) AS oldest, max(created_at) AS newest,
       max(updated_at) AS last_touched
  FROM agent_tasks WHERE status = 'OPEN'
 GROUP BY assignee, kind ORDER BY n DESC LIMIT 20;

\echo == D2. agent_tasks status distribution ==
SELECT status, count(*) AS n FROM agent_tasks GROUP BY status ORDER BY n DESC;

\echo == D3. three open Xavier tasks, newest first ==
SELECT task_id, kind, left(title, 100) AS title, created_at, left(spec::text, 220) AS spec
  FROM agent_tasks WHERE status = 'OPEN' AND assignee = 'XAVIER'
 ORDER BY created_at DESC LIMIT 3;

\echo == D4. live score display tables present ==
SELECT to_regclass('trader_display_score_health') AS health_tbl,
       to_regclass('trader_display_score_observations') AS observations_tbl,
       to_regclass('trader_display_score_bindings') AS bindings_tbl;
