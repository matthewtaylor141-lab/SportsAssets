-- READ-ONLY. Why did ENTER decisions in the last 24 h produce no paper ENTRY
-- order? Each ENTER decision is joined to the entry-refusal census written at
-- the ledger, and each strategy's current lifecycle state is shown with the
-- rule and evidence that set it. Every statement is a SELECT.

\echo E1 ENTER decisions last 24 h by strategy, with and without an order and with a ledger refusal
SELECT d.strategy, count(*) AS enter,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)) AS with_order,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id)) AS with_ledger_refusal
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.decided_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo E2 the ledger refusals of those ENTER decisions, by stage and reason
SELECT r.strategy, r.stage, r.refusal, count(*) AS n, max(r.refused_at) AS newest
  FROM paper_entry_refusal_census r
  JOIN paper_decisions d ON d.decision_id = r.decision_id
 WHERE d.verdict = 'ENTER' AND d.decided_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;

\echo E3 every strategy current lifecycle state, the rule that set it and when
SELECT strategy, state, from_state, rule_id, rules_version, actor, recorded_at,
       left(why, 220) AS why
  FROM paper_strategy_lifecycle_current_v ORDER BY strategy;

\echo E4 lifecycle transitions in the last 7 days
SELECT strategy, from_state, to_state, rule_id, actor, recorded_at, left(why, 160) AS why
  FROM paper_strategy_lifecycle_events
 WHERE recorded_at >= now() - interval '7 days'
 ORDER BY recorded_at DESC LIMIT 25;
