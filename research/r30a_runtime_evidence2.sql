-- R30A RUNTIME STREAM, second read (READ ONLY): when and how the Opportunity
-- Score SCORES component failed, the planner statistics of the tables its read
-- joins, and what the reactive scheduler's orphaned STARTED attempts were.

\echo '== F1 · lol_runs, every component row, last 48 h =='
SELECT run_id, component, status, started_at, duration_ms, left(error, 120) AS error,
       summary->>'scored' AS scored, summary->>'candidates' AS candidates
  FROM lol_runs WHERE started_at > now() - interval '48 hours'
 ORDER BY started_at, component;

\echo '== F2 · planner statistics of the tables the SCORES read joins =='
SELECT relname, n_live_tup, n_dead_tup, n_mod_since_analyze, n_ins_since_vacuum,
       last_analyze, last_autoanalyze, last_autovacuum, autoanalyze_count
  FROM pg_stat_user_tables
 WHERE relname IN ('pos_capacity', 'lol_opportunity_scores', 'paper_decisions',
                   'us_premap', 'pinnapi_reactive_attempts')
 ORDER BY relname;

\echo '== F3 · lol_opportunity_scores rows written per hour (the anti-join side grows during the day) =='
SELECT date_trunc('hour', computed_at) AS hour, count(*) AS n
  FROM lol_opportunity_scores GROUP BY 1 ORDER BY 1;

\echo '== F4 · pos_capacity rows written per hour =='
SELECT date_trunc('hour', computed_at) AS hour, count(*) AS n
  FROM pos_capacity GROUP BY 1 ORDER BY 1;

\echo '== F5 · the orphaned STARTED reactive attempts (held?, requested?, queue wait) =='
SELECT attempt_id, created_at, updated_at,
       detail->>'held' AS held, detail->>'requested' AS requested,
       round(((detail->>'evaluation_started_at')::float8 - (detail->>'queued_at')::float8)::numeric, 2) AS queue_wait_s,
       detail->'writer'->>'build' AS build
  FROM pinnapi_reactive_attempts
 WHERE state = 'STARTED' AND created_at > now() - interval '24 hours'
   AND updated_at < now() - interval '60 seconds'
 ORDER BY created_at;

\echo '== F6 · TIMEOUT reactive attempts by hour, last 24 h =='
SELECT date_trunc('hour', created_at) AS hour, count(*) AS n,
       count(*) FILTER (WHERE detail->>'held' = 'true') AS held
  FROM pinnapi_reactive_attempts
 WHERE state = 'TIMEOUT' AND created_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1;

\echo '== F7 · the profitability cycles (pos runs) duration, last 24 h =='
SELECT component, status, count(*) AS n, round(avg(extract(epoch FROM finished_at - started_at))::numeric, 1) AS avg_s,
       round(max(extract(epoch FROM finished_at - started_at))::numeric, 1) AS max_s
  FROM lol_runs WHERE started_at > now() - interval '24 hours' GROUP BY 1, 2 ORDER BY 1, 2;
