-- Improvement tasks opened by paper lessons, and the stale-measure evidence
-- behind Xavier's management lessons (read-only).
\echo '== T0 · PAPER_LEARNING_TASK tasks =='
SELECT task_id, assignee, status, created_at, updated_at, left(title, 220) AS title
  FROM agent_tasks WHERE kind = 'PAPER_LEARNING_TASK' ORDER BY updated_at DESC LIMIT 20;
\echo '== T1 · their latest events =='
SELECT e.task_id, e.kind, e.actor, e.at
  FROM agent_task_events e JOIN agent_tasks t USING (task_id)
 WHERE t.kind = 'PAPER_LEARNING_TASK' ORDER BY e.at DESC LIMIT 15;
\echo '== T2 · Xavier management lessons (latest) =='
SELECT DISTINCT ON (series_key) series_key, version, learned_at, improvement_task_id,
       metrics->>'reviews' AS reviews,
       metrics->>'stale_measure_reviews' AS stale_reviews,
       metrics->>'positions_closed' AS closed,
       metrics->'counterfactual_exit_at_first_review'->>'positions_compared' AS compared,
       metrics->'counterfactual_exit_at_first_review'->>'positions_where_exit_would_have_been_better' AS better,
       metrics->'counterfactual_exit_at_first_review'->>'of_which_first_measure_stale' AS better_stale,
       metrics->'counterfactual_exit_at_first_review'->>'exit_minus_realized_usd' AS exit_minus_realized
  FROM paper_agent_lessons WHERE agent_id = 'XAVIER'
 ORDER BY series_key, version DESC;
\echo '== T3 · why reviews are stale (last 24 h, by reason) =='
SELECT coalesce(measure->>'stale_reason', measure->>'reason', '(none)') AS reason,
       (measure->>'stale')::boolean AS stale, count(*) AS reviews,
       max(reviewed_at) AS latest
  FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;
\echo '== T4 · one recent stale measure, its keys and values (truncated) =='
SELECT review_id, reviewed_at, left(measure::text, 900) AS measure
  FROM paper_xavier_reviews
 WHERE (measure->>'stale')::boolean AND reviewed_at > now() - interval '24 hours'
 ORDER BY reviewed_at DESC LIMIT 2;
