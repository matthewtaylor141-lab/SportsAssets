-- RC6.3 paper pass stall diagnosis (SELECT only): what the database is doing while the
-- in-API paper pass runs. Five samples 20 s apart (pg_sleep between them).
\echo P0 the paper pass heartbeat now
SELECT now() AS sampled_at, value->>'refusal' AS refusal, value->>'why' AS why,
       to_timestamp((value->>'written_at')::float) AS written_at
  FROM ingestion_state WHERE key = 'paper_session_last_pass';
\echo P1 sample 1: active and waiting backends older than 5 s (non-idle), with wait events
SELECT pid, application_name, backend_type, state, wait_event_type, wait_event,
       round(extract(epoch FROM now() - query_start)::numeric, 1) AS q_age_s,
       round(extract(epoch FROM now() - xact_start)::numeric, 1) AS x_age_s,
       left(regexp_replace(query, '\s+', ' ', 'g'), 420) AS query
  FROM pg_stat_activity
 WHERE pid <> pg_backend_pid() AND state <> 'idle'
   AND now() - coalesce(query_start, now()) > interval '5 seconds'
 ORDER BY query_start;
\echo P2 sample 1: blocked backends and their blockers
SELECT w.pid AS waiting_pid, pg_blocking_pids(w.pid) AS blocked_by,
       round(extract(epoch FROM now() - w.query_start)::numeric, 1) AS waiting_s,
       left(regexp_replace(w.query, '\s+', ' ', 'g'), 300) AS waiting_query,
       (SELECT left(regexp_replace(b.query, '\s+', ' ', 'g'), 300) FROM pg_stat_activity b
         WHERE b.pid = (pg_blocking_pids(w.pid))[1]) AS first_blocker_query,
       (SELECT b.state FROM pg_stat_activity b WHERE b.pid = (pg_blocking_pids(w.pid))[1]) AS blocker_state,
       (SELECT round(extract(epoch FROM now() - b.xact_start)::numeric, 1) FROM pg_stat_activity b
         WHERE b.pid = (pg_blocking_pids(w.pid))[1]) AS blocker_xact_age_s
  FROM pg_stat_activity w WHERE cardinality(pg_blocking_pids(w.pid)) > 0;
\echo P3 advisory locks held or awaited
SELECT l.pid, l.granted, l.classid, l.objid, l.objsubid, a.state,
       round(extract(epoch FROM now() - a.xact_start)::numeric, 1) AS x_age_s,
       left(regexp_replace(a.query, '\s+', ' ', 'g'), 200) AS query
  FROM pg_locks l JOIN pg_stat_activity a USING (pid) WHERE l.locktype = 'advisory' ORDER BY l.granted, l.pid;
SELECT pg_sleep(20);
\echo P1 sample 2
SELECT pid, state, wait_event_type, wait_event,
       round(extract(epoch FROM now() - query_start)::numeric, 1) AS q_age_s,
       round(extract(epoch FROM now() - xact_start)::numeric, 1) AS x_age_s,
       left(regexp_replace(query, '\s+', ' ', 'g'), 420) AS query
  FROM pg_stat_activity
 WHERE pid <> pg_backend_pid() AND state <> 'idle'
   AND now() - coalesce(query_start, now()) > interval '5 seconds'
 ORDER BY query_start;
\echo P2 sample 2
SELECT w.pid AS waiting_pid, pg_blocking_pids(w.pid) AS blocked_by,
       round(extract(epoch FROM now() - w.query_start)::numeric, 1) AS waiting_s,
       left(regexp_replace(w.query, '\s+', ' ', 'g'), 300) AS waiting_query
  FROM pg_stat_activity w WHERE cardinality(pg_blocking_pids(w.pid)) > 0;
SELECT pg_sleep(20);
\echo P1 sample 3
SELECT pid, state, wait_event_type, wait_event,
       round(extract(epoch FROM now() - query_start)::numeric, 1) AS q_age_s,
       round(extract(epoch FROM now() - xact_start)::numeric, 1) AS x_age_s,
       left(regexp_replace(query, '\s+', ' ', 'g'), 420) AS query
  FROM pg_stat_activity
 WHERE pid <> pg_backend_pid() AND state <> 'idle'
   AND now() - coalesce(query_start, now()) > interval '5 seconds'
 ORDER BY query_start;
SELECT pg_sleep(20);
\echo P1 sample 4
SELECT pid, state, wait_event_type, wait_event,
       round(extract(epoch FROM now() - query_start)::numeric, 1) AS q_age_s,
       round(extract(epoch FROM now() - xact_start)::numeric, 1) AS x_age_s,
       left(regexp_replace(query, '\s+', ' ', 'g'), 420) AS query
  FROM pg_stat_activity
 WHERE pid <> pg_backend_pid() AND state <> 'idle'
   AND now() - coalesce(query_start, now()) > interval '5 seconds'
 ORDER BY query_start;
SELECT pg_sleep(20);
\echo P1 sample 5
SELECT pid, state, wait_event_type, wait_event,
       round(extract(epoch FROM now() - query_start)::numeric, 1) AS q_age_s,
       round(extract(epoch FROM now() - xact_start)::numeric, 1) AS x_age_s,
       left(regexp_replace(query, '\s+', ' ', 'g'), 420) AS query
  FROM pg_stat_activity
 WHERE pid <> pg_backend_pid() AND state <> 'idle'
   AND now() - coalesce(query_start, now()) > interval '5 seconds'
 ORDER BY query_start;
\echo P4 statement statistics for the slowest statements touching the pass tables (if pg_stat_statements is installed)
SELECT count(*) AS has_pg_stat_statements FROM pg_extension WHERE extname = 'pg_stat_statements';
\echo P5 the paper session health row and the ingestion_state keys the pass writes
SELECT session_id, heartbeat_at, passes, errors, last_error FROM paper_session_health ORDER BY heartbeat_at DESC LIMIT 3;
SELECT key, left(value::text, 300) AS value FROM ingestion_state
 WHERE key LIKE 'paper_%' OR key LIKE 'xavier_%' OR key LIKE 'audrey_%' ORDER BY key;
