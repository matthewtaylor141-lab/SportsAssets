-- READ-ONLY. RC6 lane P0-AGENTS: which exception Archer's 'results' phase
-- (agents.pos_workflow.attach_results) records on every pass (agent_status
-- runs 957 = errors 957 in run 37932587274). Exception CLASS names and the
-- phase keys only; no error text, no identifiers.

\echo P1 Archer runs in the last 24 h by phase-error key and exception class
SELECT k AS phase, summary -> 'phase_errors' ->> k AS exception_class,
       count(*) AS runs,
       round(extract(epoch FROM now() - max(started_at))) AS newest_age_s,
       round(extract(epoch FROM now() - min(started_at))) AS oldest_age_s
  FROM agent_runs,
       jsonb_object_keys(CASE WHEN jsonb_typeof(summary -> 'phase_errors')
                              = 'object' THEN summary -> 'phase_errors'
                              ELSE '{}'::jsonb END) k
 WHERE agent_id = 'ARCHER' AND started_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo P2 the first Archer run that recorded a results-phase error, and runs before it without one
SELECT min(started_at) FILTER (WHERE summary -> 'phase_errors' ? 'results')
         AS first_results_error_at,
       max(started_at) FILTER (WHERE NOT coalesce(summary -> 'phase_errors'
           ? 'results', false)) AS newest_clean_run_at,
       count(*) AS runs_total
  FROM agent_runs WHERE agent_id = 'ARCHER';

\echo P3 the candidate-review steps the phase walks: steps 4 and 7 with no result, by step and agent
SELECT s.seq, s.agent, s.status, count(*) AS n
  FROM pos_candidate_review_steps s
 WHERE s.seq IN (4, 7) AND s.result IS NULL
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
