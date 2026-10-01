-- READ-ONLY. (S) Xavier reviews whose measure was stale: what they selected
-- and what economic action followed. (K) the policy_decision / economics
-- key sets of recent decisions, per strategy (for the Derek workboard).
\echo '== S1 · Xavier reviews by measure freshness, selection and action =='
SELECT strategy, measure->>'source' AS measure_source,
       coalesce(measure->>'stale', '?') AS stale, recommendation,
       action->>'taken' AS action_taken, count(*),
       min(reviewed_at) AS first_at, max(reviewed_at) AS last_at
  FROM paper_xavier_reviews
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 3, 4, 5;

\echo '== S2 · every stale-measure review that took an action other than NONE/KEEP =='
SELECT review_id, group_id, reviewed_at, trigger, recommendation, refusal,
       measure->>'source' AS source, measure->>'p' AS p,
       measure->>'at' AS measure_at, measure->>'best_exit_at_review' AS best_exit,
       left(action::text, 300) AS action,
       left(selection::text, 300) AS selection
  FROM paper_xavier_reviews
 WHERE measure->>'stale' = 'true'
   AND coalesce(action->>'taken', 'NONE') NOT IN ('NONE', 'KEEP_STANDING')
 ORDER BY reviewed_at;

\echo '== S3 · sales or exit orders ever submitted by Xavier =='
SELECT o.order_id, o.group_id, o.role, o.state, o.qty, o.limit_price,
       o.decided_at, o.strategy
  FROM paper_orders o
 WHERE o.role IN ('EXIT', 'REDUCE') ORDER BY o.decided_at DESC LIMIT 20;

\echo '== K1 · policy_decision keys per strategy (latest 40 decisions each) =='
SELECT strategy, verdict, k AS key, count(*)
  FROM (SELECT strategy, verdict, policy_decision FROM paper_decisions
         WHERE decided_at > now() - interval '3 hours') d,
       LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(policy_decision) =
               'object' THEN policy_decision ELSE '{}'::jsonb END) k
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== K2 · economics keys per strategy (3 h) =='
SELECT strategy, k AS key, count(*)
  FROM (SELECT strategy, economics FROM paper_decisions
         WHERE decided_at > now() - interval '3 hours') d,
       LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(economics) =
               'object' THEN economics ELSE '{}'::jsonb END) k
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== K3 · one recent refusal per strategy: shortfall / estimate =='
SELECT DISTINCT ON (strategy) strategy, decision_id, refusal,
       left((policy_decision->'shortfall')::text, 400) AS shortfall,
       left((policy_decision->'estimate')::text, 400) AS estimate,
       left((economics->'acquisition')::text, 300) AS acquisition,
       economics->>'fee_stop' AS fee_stop
  FROM paper_decisions WHERE decided_at > now() - interval '3 hours'
   AND book_obs_id IS NOT NULL
 ORDER BY strategy, decided_at DESC;
