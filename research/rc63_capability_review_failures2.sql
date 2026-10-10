-- RC6.3 capability lane, readback 2: which agent and source each capability
-- review outcome belongs to, the provider-failure window by hour, the partial
-- text of interrupted capability replies, the server-side cost of the persona
-- fact reads (pg_stat_statements), paper table sizes, and samples of the
-- Audrey capability question and its refusal. SELECT only.
\echo D1 capability review outcomes by assignee, source kind, kind and error, all time
SELECT t.assignee, split_part(t.spec->>'source_key',':',1) AS src, e.kind,
       coalesce(e.detail->>'error','-') AS error, count(*) AS n, max(e.at) AS last_at
FROM agent_task_events e JOIN agent_tasks t USING (task_id)
WHERE t.kind='AGENT_CAPABILITY_REVIEW_V1' AND e.kind IN ('REVIEW_INCOMPLETE','GENUINE_REVIEW')
GROUP BY 1,2,3,4 ORDER BY 1,2,3,5 DESC;

\echo D2 capability replies by hour, 2026-10-06 10:00Z to 2026-10-08 02:00Z
SELECT date_trunc('hour', a.recorded_at) AS hr, a.agent_id, a.status, coalesce(a.provider->>'mode','-') AS mode,
       left(coalesce(a.provider->>'failure','-'),40) AS failure, count(*) AS n
FROM agent_chat_messages a
WHERE a.role='ASSISTANT' AND a.request_id LIKE 'capreview:%'
  AND a.recorded_at >= timestamptz '2026-10-06 10:00+00' AND a.recorded_at < timestamptz '2026-10-08 02:00+00'
GROUP BY 1,2,3,4,5 ORDER BY 1,2,3;

\echo D3 interrupted capability replies by day: how many carried streamed partial text
SELECT date_trunc('day', recorded_at) AS day, count(*) AS n,
       count(*) FILTER (WHERE length(body) > 0) AS with_text,
       round(avg(length(body))::numeric,0) AS avg_chars, max(length(body)) AS max_chars
FROM agent_chat_messages
WHERE role='ASSISTANT' AND request_id LIKE 'capreview:%' AND status='INTERRUPTED'
GROUP BY 1 ORDER BY 1 DESC;

\echo D4 the slowest statements on average (at least 50 calls) since the stats reset
SELECT calls, round(mean_exec_time::numeric,1) AS mean_ms, round(max_exec_time::numeric,1) AS max_ms,
       round((total_exec_time/1000)::numeric,0) AS total_s, rows,
       left(regexp_replace(query,'\s+',' ','g'),170) AS q
FROM pg_stat_statements WHERE calls >= 50
ORDER BY mean_exec_time DESC LIMIT 30;

\echo D5 the persona position search over paper tables (to_jsonb text match), by statement
SELECT calls, round(mean_exec_time::numeric,1) AS mean_ms, round(max_exec_time::numeric,1) AS max_ms,
       round((total_exec_time/1000)::numeric,0) AS total_s, rows,
       left(regexp_replace(query,'\s+',' ','g'),150) AS q
FROM pg_stat_statements
WHERE query ILIKE '%to_jsonb(x)::text%'
ORDER BY mean_exec_time DESC LIMIT 40;

\echo D6 paper table sizes (estimated rows and total size)
SELECT c.relname AS tbl, c.reltuples::bigint AS est_rows, pg_size_pretty(pg_total_relation_size(c.oid)) AS total
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname=current_schema() AND c.relkind='r'
  AND (c.relname LIKE 'paper\_%' OR c.relname LIKE '%\_paper\_%' OR c.relname LIKE '%\_paper')
ORDER BY c.reltuples DESC;

\echo D7 one Audrey capability question as sent (first 420 characters) and its reply (first 300)
SELECT u.recorded_at, left(u.body,420) AS question, left(a.body,300) AS reply, a.outcome
FROM agent_chat_messages u JOIN agent_chat_messages a ON a.in_reply_to=u.message_id
WHERE u.role='USER' AND u.request_id LIKE 'capreview:%' AND u.agent_id='AUDREY'
ORDER BY u.recorded_at DESC LIMIT 2;

\echo D8 Audrey capability replies by status and outcome, all time
SELECT date_trunc('day', a.recorded_at) AS day, a.status, coalesce(a.outcome,'-') AS outcome,
       coalesce(a.provider->>'mode','-') AS mode, count(*) AS n
FROM agent_chat_messages a
WHERE a.role='ASSISTANT' AND a.request_id LIKE 'capreview:%' AND a.agent_id='AUDREY'
GROUP BY 1,2,3,4 ORDER BY 1 DESC, 5 DESC;

\echo D9 capability task context keys by source kind (what the persona search is scoped by)
SELECT split_part(spec->>'source_key',':',1) AS src,
       (spec->'context') ? 'position_id' AS has_position, (spec->'context') ? 'decision_id' AS has_decision,
       (spec->'context') ? 'recommendation_id' AS has_rec, count(*) AS n
FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1'
GROUP BY 1,2,3,4 ORDER BY 1;
