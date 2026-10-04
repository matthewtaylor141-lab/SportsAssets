-- R30A RUNTIME STREAM, fourth read (READ ONLY). Server-side statement times
-- of the statements whose client-side 2-3 s budgets expired (pg_stat_statements,
-- installed per the third read), and the status vocabulary the loop-health
-- reader must judge: every service_heartbeats row and the ingestion_state
-- heartbeat keys it reads.

\echo '== J1 · pg_stat_statements: reactive attempt writes, the research worker tick statements, loop heartbeats (server time) =='
SELECT left(regexp_replace(query, '\s+', ' ', 'g'), 140) AS query, calls,
       round(mean_exec_time::numeric, 2) AS mean_ms,
       round(max_exec_time::numeric, 1) AS max_ms,
       round(stddev_exec_time::numeric, 2) AS sd_ms
  FROM pg_stat_statements
 WHERE query ILIKE '%pinnapi_reactive_attempts%'
    OR query ILIKE '%to_regclass(%agent_tasks%'
    OR query ILIKE '%FROM agent_tasks WHERE kind%'
    OR query ILIKE '%service_heartbeats%'
    OR query ILIKE '%pg_try_advisory_lock%'
 ORDER BY max_exec_time DESC
 LIMIT 25;

\echo '== J2 · pg_stat_statements: the statements with the largest single execution (server time) =='
SELECT left(regexp_replace(query, '\s+', ' ', 'g'), 140) AS query, calls,
       round(mean_exec_time::numeric, 1) AS mean_ms,
       round(max_exec_time::numeric, 1) AS max_ms
  FROM pg_stat_statements
 ORDER BY max_exec_time DESC
 LIMIT 15;

\echo '== J3 · pg_stat_statements reset time =='
SELECT stats_reset FROM pg_stat_statements_info;

\echo '== J4 · service_heartbeats: every row (status vocabulary per writer) =='
SELECT service, status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 0) AS age_s,
       left(detail::text, 160) AS detail
  FROM service_heartbeats ORDER BY service;

\echo '== J5 · ingestion_state heartbeat keys the loop-health reader judges =='
SELECT key, value->>'state' AS state, value->>'status' AS status,
       coalesce(value->>'at', value->>'beat_at') AS at,
       left(value::text, 160) AS value
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_servicing',
               'pinnapi_feed_last', 'rn1x_model_last_cycle', 'premap_last',
               'roster_auto_last', 'agent.capabilities.heartbeat:paper_acct_main',
               'pinnapi_feed', 'workers_boot')
 ORDER BY key;
