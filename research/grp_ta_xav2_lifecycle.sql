-- READ-ONLY. Root-cause audit, group truth-agents, part 7: the Xavier lane.
-- (a) the ENTER_WITHOUT_ORDER findings of the last 48 h, (b) the paper
-- session last pass record (why the heartbeat is old), (c) Xavier reviews by
-- day (the packet population), (d) the strategy lifecycle events and the
-- loss-budget evidence behind them. SELECT only.

\echo == 1 ENTER_WITHOUT_ORDER findings, last 48 h (detail head)
SELECT found_at, severity, subject, left(detail::text, 700) AS detail_head
  FROM paper_audrey_findings WHERE kind = 'ENTER_WITHOUT_ORDER' AND found_at > now() - interval '48 hours'
 ORDER BY found_at DESC LIMIT 6;

\echo == 2 the paper session health record (last pass and last error heads, no payloads)
SELECT session_id, heartbeat_at, passes, errors, mutation_attempts,
       left(coalesce(last_pass::text, ''), 900) AS last_pass_head, left(coalesce(last_error, ''), 300) AS last_error
  FROM paper_session_health ORDER BY heartbeat_at DESC LIMIT 1;

\echo == 3 xavier reviews and handoffs by UTC day (the packet population), last 10 days
SELECT date_trunc('day', reviewed_at)::date AS day, count(*) AS reviews, count(DISTINCT group_id) AS groups
  FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '10 days' GROUP BY 1 ORDER BY 1 DESC;
SELECT date_trunc('day', first_fill_at)::date AS day, count(*) AS handoffs, count(*) FILTER (WHERE outstanding_qty > 0) AS with_outstanding
  FROM paper_handoffs WHERE first_fill_at > now() - interval '10 days' GROUP BY 1 ORDER BY 1 DESC;

\echo == 4 strategy lifecycle events (all, newest first) with the rule and recorded instant
SELECT event_id, strategy, from_state, to_state, rule_id, actor, recorded_at
  FROM paper_strategy_lifecycle_events ORDER BY event_id DESC LIMIT 12;

\echo == 5 paper settlements per UTC day, last 10 days (positions closed by settlement rather than by a sell)
SELECT date_trunc('day', settled_at)::date AS day, count(*) AS settlement_rows, count(DISTINCT group_id) AS groups, max(settled_at) AS newest
  FROM paper_settlements WHERE settled_at > now() - interval '10 days' GROUP BY 1 ORDER BY 1 DESC;

\echo == 6 working paper orders now (a resting order is not a held position)
SELECT state, count(*) AS n, max(decided_at) AS newest FROM paper_orders
 WHERE state IN ('PENDING_SIMULATION', 'RESTING', 'PARTIALLY_FILLED') GROUP BY 1 ORDER BY 2 DESC;
