-- R30A RUNTIME STREAM -- production evidence, READ ONLY (SELECT / EXPLAIN only).
--
-- Why it exists: the API logs of 2026-10-04 15:46-18:47Z show the PinnAPI
-- reactive audit, the PinnAPI feed heartbeat and the agent research tick all
-- failing with TimeoutError inside asyncpg Pool._acquire at the same seconds
-- (16:00:53, 17:40:58, 17:46:10, 18:21:28 ...): the API's ten-slot pool was
-- saturated. This file reads (A) what holds the database connections, (B) what
-- a failed audit leaves behind in pinnapi_reactive_attempts, (C) the loops'
-- recorded heartbeats, (D) the Opportunity Score SCORES query that hit its
-- 20 s statement timeout at 16:36:36Z, and (E) the shapes the runtime SLO
-- endpoint reads.

\echo '== A0 · connection budget =='
SELECT current_setting('max_connections') AS max_connections,
       current_setting('superuser_reserved_connections') AS reserved,
       (SELECT count(*) FROM pg_stat_activity) AS backends_all,
       (SELECT count(*) FROM pg_stat_activity
         WHERE datname = current_database()) AS backends_this_db;

\echo '== A1 · backends by client address, application and state (snapshot 1) =='
SELECT client_addr, coalesce(nullif(application_name, ''), '(none)') AS app,
       state, count(*) AS n,
       round(max(extract(epoch FROM now() - backend_start))::numeric) AS oldest_conn_s,
       round(max(extract(epoch FROM now() - state_change))::numeric, 1) AS longest_in_state_s
  FROM pg_stat_activity
 WHERE datname = current_database() AND pid <> pg_backend_pid()
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== A2 · session advisory-lock holders (the single-writer loops) =='
SELECT a.client_addr, coalesce(nullif(a.application_name, ''), '(none)') AS app,
       a.pid, a.state,
       ((l.classid::bigint << 32) | l.objid::bigint) AS advisory_key,
       to_hex((l.classid::bigint << 32) | l.objid::bigint) AS key_hex,
       round(extract(epoch FROM now() - a.backend_start)::numeric) AS conn_age_s
  FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
 WHERE l.locktype = 'advisory' AND l.granted
 ORDER BY a.client_addr, advisory_key;

\echo '== A3 · non-idle statements, longest first (snapshot 1) =='
SELECT client_addr, pid, state,
       round(extract(epoch FROM now() - query_start)::numeric, 1) AS running_s,
       wait_event_type, wait_event,
       left(regexp_replace(query, '\s+', ' ', 'g'), 140) AS query_head
  FROM pg_stat_activity
 WHERE datname = current_database() AND pid <> pg_backend_pid()
   AND state IS DISTINCT FROM 'idle'
 ORDER BY query_start NULLS LAST LIMIT 40;

SELECT pg_sleep(20) AS waited_20s;

\echo '== A4 · backends by client address and state (snapshot 2, +20 s) =='
SELECT client_addr, state, count(*) AS n
  FROM pg_stat_activity
 WHERE datname = current_database() AND pid <> pg_backend_pid()
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== A5 · non-idle statements (snapshot 2) =='
SELECT client_addr, pid, state,
       round(extract(epoch FROM now() - query_start)::numeric, 1) AS running_s,
       left(regexp_replace(query, '\s+', ' ', 'g'), 140) AS query_head
  FROM pg_stat_activity
 WHERE datname = current_database() AND pid <> pg_backend_pid()
   AND state IS DISTINCT FROM 'idle'
 ORDER BY query_start NULLS LAST LIMIT 40;

\echo '== B1 · pinnapi_reactive_attempts by state, last 24 h =='
SELECT state, count(*) AS n, min(created_at) AS first_at, max(created_at) AS last_at
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== B2 · STARTED attempts never completed (older than 60 s), last 24 h, by hour =='
SELECT date_trunc('hour', created_at) AS hour, count(*) AS orphaned_started
  FROM pinnapi_reactive_attempts
 WHERE state = 'STARTED' AND created_at > now() - interval '24 hours'
   AND updated_at < now() - interval '60 seconds'
 GROUP BY 1 ORDER BY 1;

\echo '== B3 · completed reactive evaluations: receipt -> finished latency (s), last 24 h =='
SELECT count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY lat)::numeric, 2) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY lat)::numeric, 2) AS p90_s,
       round(max(lat)::numeric, 2) AS max_s
  FROM (SELECT (detail->>'finished_at')::float8 - (detail->>'received_at')::float8 AS lat
          FROM pinnapi_reactive_attempts
         WHERE state = 'COMPLETED' AND created_at > now() - interval '24 hours'
           AND detail ? 'finished_at' AND detail ? 'received_at') q;

\echo '== B4 · one recent attempt row, detail keys (shape only) =='
SELECT state, (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(detail) k) AS detail_keys
  FROM pinnapi_reactive_attempts ORDER BY created_at DESC LIMIT 3;

\echo '== C1 · service_heartbeats =='
SELECT service, status, beat_at,
       round(extract(epoch FROM now() - beat_at)::numeric) AS age_s
  FROM service_heartbeats ORDER BY beat_at DESC;

\echo '== C2 · ingestion_state heartbeat-shaped rows (time fields only) =='
SELECT key,
       coalesce(value->>'at', value->>'beat_at', value->>'heartbeat_at') AS at_field,
       value->>'status' AS status, value->>'state' AS state,
       length(value::text) AS bytes
  FROM ingestion_state
 WHERE key ILIKE '%heartbeat%' OR key ILIKE '%_last' OR key ILIKE '%loop%'
    OR key IN ('workers_boot', 'api.loop_stalls')
 ORDER BY key;

\echo '== C3 · agent_status (the agents registry) =='
SELECT agent_id, state, last_heartbeat_at,
       round(extract(epoch FROM now() - last_heartbeat_at)::numeric) AS hb_age_s,
       last_run_finished_at, runs, errors, left(last_error, 100) AS last_error,
       cadence
  FROM agent_status ORDER BY agent_id;

\echo '== D1 · pos_capacity size (the view pos_capacity_latest is DISTINCT ON over all of it) =='
SELECT count(*) AS rows, count(DISTINCT candidate_id) AS candidates,
       min(computed_at) AS oldest, max(computed_at) AS newest,
       count(*) FILTER (WHERE decided_at >= now() - interval '48 hours') AS rows_48h,
       count(DISTINCT candidate_id) FILTER (WHERE decided_at >= now() - interval '48 hours') AS candidates_48h
  FROM pos_capacity;

\echo '== D2 · lol_runs SCORES outcomes, last 48 h =='
SELECT component, status, count(*) AS n, max(finished_at) AS last_at,
       round(avg(duration_ms)) AS avg_ms, max(duration_ms) AS max_ms
  FROM lol_runs WHERE started_at > now() - interval '48 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== D3 · lol_opportunity_scores freshness =='
SELECT count(*) AS scores, max(computed_at) AS newest,
       round(extract(epoch FROM now() - max(computed_at))::numeric) AS newest_age_s
  FROM lol_opportunity_scores;

\echo '== D4 · PLAN of the SCORES read as it runs today (score_candidates) =='
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT c.capacity_id, c.candidate_id, c.status
  FROM pos_capacity_latest c
  LEFT JOIN paper_decisions d ON d.decision_id = c.candidate_id
 WHERE c.decided_at >= now() - interval '48 hours'
   AND NOT EXISTS (SELECT 1 FROM lol_opportunity_scores s
                    WHERE s.candidate_id = c.candidate_id
                      AND s.capacity_id = c.capacity_id
                      AND s.version = 'LOL_OPPORTUNITY_SCORE_V1')
 ORDER BY c.decided_at DESC LIMIT 1500;

\echo '== D5 · PLAN of the bounded rewrite (latest row per candidate among candidates decided in the window) =='
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT c.capacity_id, c.candidate_id, c.status
  FROM (SELECT DISTINCT ON (pc.candidate_id) pc.*
          FROM pos_capacity pc
         WHERE pc.candidate_id IN (SELECT w.candidate_id FROM pos_capacity w
                                    WHERE w.decided_at >= now() - interval '48 hours')
         ORDER BY pc.candidate_id, pc.computed_at DESC) c
  LEFT JOIN paper_decisions d ON d.decision_id = c.candidate_id
 WHERE c.decided_at >= now() - interval '48 hours'
   AND NOT EXISTS (SELECT 1 FROM lol_opportunity_scores s
                    WHERE s.candidate_id = c.candidate_id
                      AND s.capacity_id = c.capacity_id
                      AND s.version = 'LOL_OPPORTUNITY_SCORE_V1')
 ORDER BY c.decided_at DESC LIMIT 1500;

\echo '== E1 · Pinnacle age at decision (paper_decisions.pinnacle age_s), last 24 h =='
SELECT count(*) AS decisions,
       count(*) FILTER (WHERE pinnacle ? 'age_s' AND jsonb_typeof(pinnacle->'age_s') = 'number') AS with_age,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY (pinnacle->>'age_s')::float8)
             FILTER (WHERE jsonb_typeof(pinnacle->'age_s') = 'number')::numeric, 2) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY (pinnacle->>'age_s')::float8)
             FILTER (WHERE jsonb_typeof(pinnacle->'age_s') = 'number')::numeric, 2) AS p90_s,
       min(pinnacle->>'limit_s') AS limit_min, max(pinnacle->>'limit_s') AS limit_max
  FROM paper_decisions WHERE decided_at > now() - interval '24 hours';

\echo '== E2 · paper_decisions write latency (recorded_at - decided_at, s), last 24 h =='
SELECT count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - decided_at))::numeric, 3) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - decided_at))::numeric, 3) AS p90_s
  FROM paper_decisions WHERE decided_at > now() - interval '24 hours';

\echo '== E3 · smalllive_reconciliations and open ACTUAL positions =='
SELECT status, count(*) AS groups, max(reconciled_at) AS newest,
       round(extract(epoch FROM now() - max(reconciled_at))::numeric) AS newest_age_s
  FROM smalllive_reconciliations GROUP BY 1;
SELECT state, count(*) AS n FROM smalllive_handoffs GROUP BY 1;

\echo '== E4 · release state (workers boot row; schema max) =='
SELECT value->>'commit_sha' AS workers_sha, value->>'at' AS boot_at
  FROM ingestion_state WHERE key = 'workers_boot';
SELECT max(version) AS schema_max FROM schema_migrations;
