-- READ-ONLY. RC6 lane P0-AGENTS: the facts behind every agent's status
-- contract (latest action, status, input freshness, next cycle, refusals,
-- failures), read from the rows the agents and loops already write, so the
-- new GET /api/command/agent-status read is calibrated on production shapes
-- (which rows exist, how old, how large) and not on assumptions.
--
-- Printed: codes, counts, ages and exception CLASS names only. No error
-- text, no heartbeat detail values (key names only), no account, wallet or
-- market identifiers.

\echo A1 agent_status: one row per agent (state, activity code, ages, counters, cadence)
SELECT agent_id, state,
       left(regexp_replace(coalesce(activity, ''), '0x[0-9a-fA-F]+', '0x..', 'g'), 70) AS activity,
       round(extract(epoch FROM now() - last_heartbeat_at)) AS hb_age_s,
       round(extract(epoch FROM now() - last_run_started_at)) AS run_start_age_s,
       round(extract(epoch FROM now() - last_run_finished_at)) AS run_finish_age_s,
       runs, errors,
       split_part(coalesce(last_error, ''), ':', 1) AS last_error_class,
       cadence::text AS cadence
  FROM agent_status ORDER BY agent_id;

\echo A2 agent_runs by agent, last 24 h: runs, finished, outcomes, newest start / finish age
SELECT agent_id, count(*) AS runs_24h,
       count(finished_at) AS finished_24h,
       round(extract(epoch FROM now() - max(started_at))) AS newest_start_age_s,
       round(extract(epoch FROM now() - max(finished_at))) AS newest_finish_age_s
  FROM agent_runs WHERE started_at > now() - interval '24 hours'
 GROUP BY agent_id ORDER BY agent_id;

\echo A3 agent_runs outcomes by agent, last 1 h
SELECT agent_id, left(coalesce(outcome, '(unfinished)'), 50) AS outcome,
       count(*) AS n
  FROM agent_runs WHERE started_at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo A4 service_heartbeats: status, age, detail key names (no values)
SELECT service, status, round(extract(epoch FROM now() - beat_at)) AS age_s,
       (SELECT string_agg(k, ',' ORDER BY k)
          FROM jsonb_object_keys(CASE WHEN jsonb_typeof(detail) = 'object'
                                      THEN detail ELSE '{}'::jsonb END) k)
         AS detail_keys
  FROM service_heartbeats ORDER BY service;

\echo A5 runtime_loop_health: per loop ages and counters (error class only)
SELECT loop_name, process, cadence_s,
       round(extract(epoch FROM now() - last_start_at)) AS start_age_s,
       round(extract(epoch FROM now() - last_success_at)) AS success_age_s,
       round(extract(epoch FROM now() - last_error_at)) AS error_age_s,
       split_part(coalesce(last_error, ''), ':', 1) AS last_error_class,
       starts, successes, errors
  FROM runtime_loop_health ORDER BY process, loop_name;

\echo A6 ingestion_state heartbeat-like keys: the at/beat fields (as written) and state names
SELECT key,
       coalesce(value->>'state', value->>'status') AS state,
       left(coalesce(value->>'beat_at', value->>'at'), 32) AS at_as_written,
       (value->>'next_at') IS NOT NULL AS has_next_at,
       (SELECT string_agg(k, ',' ORDER BY k)
          FROM jsonb_object_keys(value) k) AS keys
  FROM ingestion_state
 WHERE key IN ('pinnapi_feed_last', 'ext_pinnacle_last_cycle',
               'ext_pinnacle_last_servicing', 'rn1x_model_last_cycle',
               'premap_last', 'roster_auto_last', 'workers_boot',
               'agent.capabilities.heartbeat:paper_acct_main')
   AND jsonb_typeof(value) = 'object'
 ORDER BY key;

\echo A7 row-count estimates of the tables the contract reads (planner stats)
SELECT c.relname, c.reltuples::bigint AS est_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
   AND c.relname IN ('paper_decisions', 'paper_evaluation_attempts',
                     'paper_xavier_reviews', 'xavier_management_assessments',
                     'karen_challenges', 'eddie_execution_estimates',
                     'scout_features', 'paper_audrey_findings',
                     'audrey_audit_reports', 'adriana_arb_scans',
                     'adriana_arb_opportunities', 'intel_runs', 'agent_runs',
                     'pos_runs', 'twin_runs', 'improve_runs',
                     'paper_book_observations', 'shadow_decisions',
                     'agent_status', 'service_heartbeats',
                     'runtime_loop_health', 'ingestion_state')
 ORDER BY 1;

\echo B1 Derek: paper_decisions in the last hour by strategy / verdict / first refusal code
SELECT strategy, verdict, coalesce(refusal, '(none)') AS refusal, count(*) AS n
  FROM paper_decisions WHERE decided_at > now() - interval '1 hour'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC LIMIT 60;

\echo B2 Derek: paper_evaluation_attempts in the last hour by outcome
SELECT strategy, outcome, count(*) AS n
  FROM paper_evaluation_attempts WHERE at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 40;

\echo B3 Xavier: reviews in the last hour by recommendation / refusal
SELECT recommendation, coalesce(refusal, '(none)') AS refusal, count(*) AS n,
       round(extract(epoch FROM now() - max(reviewed_at))) AS newest_age_s
  FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;

\echo B4 Xavier: management assessments in the last hour by recommendation / evidence state
SELECT recommendation, evidence_state, count(*) AS n,
       round(extract(epoch FROM now() - max(assessed_at))) AS newest_age_s
  FROM xavier_management_assessments
 WHERE assessed_at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;

\echo B5 Archer: estimates in the last hour by recommendation / reason
SELECT recommendation, left(coalesce(recommendation_reason, '(none)'), 60)
         AS reason, count(*) AS n,
       round(extract(epoch FROM now() - max(estimated_at))) AS newest_age_s
  FROM eddie_execution_estimates
 WHERE estimated_at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;

\echo B6 Adriana: scans in the last hour (status, why, refusal totals) and the newest by_code keys
SELECT status, left(coalesce(why, '(none)'), 60) AS why, count(*) AS scans,
       sum(refusals_total) AS refusals_total,
       round(extract(epoch FROM now() - max(finished_at))) AS newest_age_s
  FROM adriana_arb_scans WHERE started_at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
SELECT k AS by_code_key, (s.by_code->>k) AS n
  FROM (SELECT by_code FROM adriana_arb_scans
         WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1) s,
       jsonb_object_keys(CASE WHEN jsonb_typeof(s.by_code) = 'object'
                              THEN s.by_code ELSE '{}'::jsonb END) k
 ORDER BY 1 LIMIT 40;

\echo B7 Karen: challenges in the last hour by target / detector / state
SELECT target_agent, detector, state, count(*) AS n
  FROM karen_challenges WHERE challenged_at > now() - interval '1 hour'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo B8 Audrey: findings in the last hour by kind / severity; newest audit report age
SELECT kind, severity, count(*) AS n
  FROM paper_audrey_findings WHERE found_at > now() - interval '1 hour'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;
SELECT round(extract(epoch FROM now() - max(computed_at))) AS newest_report_age_s
  FROM audrey_audit_reports;

\echo B9 Scout: features by state; newest proposal / state change age
SELECT state, count(*) AS n,
       round(extract(epoch FROM now() - max(greatest(proposed_at,
             coalesce(state_set_at, proposed_at))))) AS newest_age_s
  FROM scout_features GROUP BY 1 ORDER BY 2 DESC;

\echo B10 run tables (intel / pos / twin / improve): last 24 h by component / status
SELECT 'intel_runs' AS t, component, status, count(*) AS n,
       round(extract(epoch FROM now() - max(started_at))) AS newest_age_s
  FROM intel_runs WHERE started_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
UNION ALL
SELECT 'pos_runs', component, status, count(*),
       round(extract(epoch FROM now() - max(started_at)))
  FROM pos_runs WHERE started_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
UNION ALL
SELECT 'twin_runs', component, status, count(*),
       round(extract(epoch FROM now() - max(started_at)))
  FROM twin_runs WHERE started_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
UNION ALL
SELECT 'improve_runs', '-', status, count(*),
       round(extract(epoch FROM now() - max(started_at)))
  FROM improve_runs WHERE started_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo C1 the planner on the contract's windowed reads (EXPLAIN only, nothing executed)
EXPLAIN SELECT verdict, refusal, count(*) FROM paper_decisions
 WHERE decided_at > now() - interval '1 hour' GROUP BY 1, 2;
EXPLAIN SELECT max(challenged_at) FROM karen_challenges;
EXPLAIN SELECT recommendation, count(*) FROM xavier_management_assessments
 WHERE assessed_at > now() - interval '1 hour' GROUP BY 1;
EXPLAIN SELECT agent_id, max(started_at) FROM agent_runs
 WHERE started_at > now() - interval '1 hour' GROUP BY 1;
