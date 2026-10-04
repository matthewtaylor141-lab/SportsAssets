-- R30 baseline (read only): execution mirror control, execution intents,
-- paper decision flow by strategy over 24 h, open paper positions owned by
-- Xavier, review states, and agent status rows.
\echo == execmirror_control
SELECT enabled, stopped, scale, max_order_usd, cutover_at, left(coalesce(account_fingerprint,''),12) AS fp FROM execmirror_control;
\echo == execmirror_orders by state (all time)
SELECT state, count(*) FROM execmirror_orders GROUP BY 1 ORDER BY 1;
\echo == execution_intents by actual_state, last 7 days
SELECT strategy, actual_state, count(*), max(created_at) FROM execution_intents WHERE created_at > now() - interval '7 days' GROUP BY 1,2 ORDER BY 1,2;
\echo == paper decisions by strategy and verdict, last 24 h
SELECT strategy, policy_version, verdict, count(*), max(decided_at) FROM paper_decisions WHERE decided_at > now() - interval '24 hours' GROUP BY 1,2,3 ORDER BY 1,2,3;
\echo == paper entry orders by strategy, last 24 h
SELECT d.strategy, o.state, count(*) FROM paper_orders o LEFT JOIN paper_decisions d USING (decision_id) WHERE o.role='ENTRY' AND o.created_at > now() - interval '24 hours' GROUP BY 1,2 ORDER BY 1,2;
\echo == open handed-off groups and latest review state
SELECT count(*) AS handed_off FROM paper_handoffs;
SELECT recommendation_state, count(*) FROM (SELECT DISTINCT ON (group_id) group_id, recommendation_state FROM xavier_management_assessments ORDER BY group_id, assessed_at DESC) t GROUP BY 1;
\echo == agent_status
SELECT agent_id, state, left(coalesce(activity,''),80) AS activity, updated_at FROM agent_status ORDER BY 1;
\echo == eddie/allocator/karen recent output counts (24 h)
SELECT (SELECT count(*) FROM eddie_execution_estimates WHERE estimated_at > now()-interval '24 hours') AS eddie,
       (SELECT count(*) FROM intel_allocations WHERE created_at > now()-interval '24 hours') AS allie,
       (SELECT count(*) FROM karen_challenges WHERE challenged_at > now()-interval '24 hours') AS karen;
