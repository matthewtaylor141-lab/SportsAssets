-- RC6.3 agent-state REVIEW readback 1 (SELECT only).
-- Reviewer check of rc6/be-truth 01405772 (api/command_floor.py run_degraded):
-- the Archer desk now reads his newest FINISHED agent_runs rows and their
-- summary phase_errors. This reads, on production: whether archer_runner
-- writes agent_runs rows with summary phase_errors at all, what the newest
-- finished runs carry, the agent_status row, the agent_archer service beat,
-- and the cost of the lane read (LAST_RUNS_SQL, LIMIT 12).

\echo == V1 ARCHER agent_runs in the last 6 h (count, finished, errored by the lane rule, FAILED)
SELECT count(*) AS runs,
       count(*) FILTER (WHERE finished_at IS NOT NULL) AS finished,
       count(*) FILTER (WHERE finished_at IS NULL) AS unfinished,
       count(*) FILTER (WHERE outcome = 'FAILED') AS failed_outcome,
       count(*) FILTER (WHERE jsonb_typeof(summary -> 'phase_errors') = 'object'
                          AND summary -> 'phase_errors' <> '{}'::jsonb) AS with_phase_errors,
       count(*) FILTER (WHERE summary ? 'phase_errors') AS has_phase_errors_key,
       min(started_at) AS first_started, max(finished_at) AS last_finished
  FROM agent_runs
 WHERE agent_id = 'ARCHER'
   AND started_at >= now() - interval '6 hours';

\echo == V2 ARCHER newest 12 finished runs (the lane read, newest first)
SELECT left(run_id, 24) AS run_id, started_at, finished_at, outcome,
       summary -> 'phase_errors' AS phase_errors,
       extract(epoch FROM now() - finished_at)::int AS age_s
  FROM agent_runs
 WHERE agent_id = 'ARCHER' AND finished_at IS NOT NULL
 ORDER BY started_at DESC
 LIMIT 12;

\echo == V3 ARCHER agent_runs by run_id prefix (30 d)
SELECT split_part(run_id, ':', 1) AS prefix, count(*) AS n, max(started_at) AS newest
  FROM agent_runs
 WHERE agent_id = 'ARCHER' AND started_at >= now() - interval '30 days'
 GROUP BY 1 ORDER BY 2 DESC;

\echo == V4 ARCHER agent_status row
SELECT agent_id, state, activity, runs, errors, left(last_error, 120) AS last_error,
       last_heartbeat_at, last_run_started_at, last_run_finished_at, cadence
  FROM agent_status WHERE agent_id = 'ARCHER';

\echo == V5 agent_archer service beat
SELECT service, status, beat_at, detail -> 'phase_errors' AS phase_errors,
       detail -> 'status' AS run_status
  FROM service_heartbeats WHERE service = 'agent_archer';

\echo == V6 phase error mix over the last 24 h of ARCHER runs
SELECT k AS phase, v AS error, count(*) AS runs
  FROM agent_runs r,
       LATERAL jsonb_each_text(CASE WHEN jsonb_typeof(r.summary -> 'phase_errors') = 'object'
                                    THEN r.summary -> 'phase_errors' ELSE '{}'::jsonb END) AS e(k, v)
 WHERE r.agent_id = 'ARCHER' AND r.started_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo == V7 cost of the lane read (LAST_RUNS_SQL for ARCHER, cap 12)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT run_id, started_at, finished_at, outcome,
       summary -> 'phase_errors' AS phase_errors
  FROM agent_runs WHERE agent_id = 'ARCHER' AND finished_at IS NOT NULL
 ORDER BY started_at DESC LIMIT 12;
