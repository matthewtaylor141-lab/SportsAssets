-- READ-ONLY. RC6 lane api-responsive: what holds the API's pooled connections.
--
-- RC5 production (2026-10-08) logged heartbeat writes timing out INSIDE
-- asyncpg's pool acquire (16:32:38Z, 18:37:43Z, 18:38:17Z) and requests of
-- 17-28 s, and /healthz waits up to 2 s for a pooled SELECT 1. This reads the
-- sessions by client and state now, the statements that keep a session busy
-- longest (pg_stat_statements, normalized text only), and the session locks.

\echo P1 sessions by client address, application and state (this database)
SELECT client_addr, application_name, state, count(*) AS sessions,
       max(now() - state_change) AS longest_in_state,
       min(backend_start) AS oldest_backend
  FROM pg_stat_activity
 WHERE datname = current_database() AND pid <> pg_backend_pid()
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo P2 sessions not idle right now (oldest statement first)
SELECT client_addr, application_name, state, wait_event_type, wait_event,
       now() - xact_start AS xact_age, now() - query_start AS query_age,
       left(regexp_replace(query, '\s+', ' ', 'g'), 160) AS query
  FROM pg_stat_activity
 WHERE datname = current_database() AND pid <> pg_backend_pid()
   AND state <> 'idle'
 ORDER BY query_start NULLS LAST LIMIT 40;

\echo P3 statements by total execution time (calls >= 20), normalized text
SELECT calls, round(total_exec_time::numeric / 1000, 1) AS total_s,
       round(mean_exec_time::numeric, 1) AS mean_ms,
       round(max_exec_time::numeric, 1) AS max_ms,
       rows,
       left(regexp_replace(query, '\s+', ' ', 'g'), 150) AS query
  FROM pg_stat_statements
 WHERE calls >= 20
 ORDER BY total_exec_time DESC LIMIT 40;

\echo P4 statements by mean execution time (calls >= 5, mean >= 500 ms)
SELECT calls, round(mean_exec_time::numeric, 1) AS mean_ms,
       round(max_exec_time::numeric, 1) AS max_ms,
       round(total_exec_time::numeric / 1000, 1) AS total_s,
       left(regexp_replace(query, '\s+', ' ', 'g'), 150) AS query
  FROM pg_stat_statements
 WHERE calls >= 5 AND mean_exec_time >= 500
 ORDER BY mean_exec_time DESC LIMIT 40;

\echo P5 pg_stat_statements window
SELECT stats_reset FROM pg_stat_statements_info;
