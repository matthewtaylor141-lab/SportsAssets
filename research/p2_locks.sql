-- READ-ONLY. Advisory locks and long-running sessions (a dead process's
-- connection holding the paper lock would time every pass out).
SELECT l.objid, l.classid, l.granted, a.pid, a.application_name, a.client_addr,
       a.backend_start, a.state, a.state_change, now()-a.query_start q_age, left(a.query,160) q
  FROM pg_locks l JOIN pg_stat_activity a USING (pid)
 WHERE l.locktype='advisory' ORDER BY a.backend_start;
SELECT pid, application_name, client_addr, backend_start, state, wait_event_type, wait_event,
       now()-xact_start xact_age, now()-query_start q_age, left(query,200) q
  FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
   AND (state<>'idle' OR xact_start IS NOT NULL) ORDER BY xact_start NULLS LAST LIMIT 40;
SELECT count(*), min(backend_start), max(backend_start), application_name, client_addr
  FROM pg_stat_activity WHERE datname=current_database() GROUP BY 4,5 ORDER BY 1 DESC;
