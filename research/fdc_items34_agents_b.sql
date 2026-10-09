-- FINAL DELIVERY CONTRACT items 3-4 readback, part B (read-only audit).
-- Archer phase errors and outcomes, agent tasks, Allie block keys on the
-- canonical intents, Derek heartbeat vs decisions, paper open positions.
-- SELECT only.

\echo B1 ARCHER runs 24 h: with a phase error, by phase error text (top 5); outcomes all time by source
SELECT count(*) AS runs_24h,
       count(*) FILTER (WHERE jsonb_typeof(summary -> 'phase_errors') = 'object' AND summary -> 'phase_errors' <> '{}'::jsonb) AS runs_with_phase_error
  FROM agent_runs WHERE agent_id = 'ARCHER' AND started_at >= now() - interval '24 hours';
SELECT left((summary -> 'phase_errors')::text, 80) AS phase_errors, count(*) AS n
  FROM agent_runs WHERE agent_id = 'ARCHER' AND started_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 5;
SELECT source, count(*) AS outcomes_all_time, max(measured_at) AS newest FROM eddie_execution_outcomes GROUP BY 1;
SELECT count(*) AS outcomes_all_time FROM eddie_execution_outcomes;

\echo B2 AGENT TASKS by assignee, kind, status: open now and created in 24 h
SELECT assignee, kind, status, count(*) AS n, count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS n_24h,
       max(updated_at) AS newest_update
  FROM agent_tasks GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25;

\echo B3 ALLIE BLOCK on the newest canonical decision intent: keys and the final allocation versus the order
SELECT intent_id, created_at, target_qty, wire_price,
       (SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(allie) k) AS allie_keys,
       left(allie::text, 400) AS allie_head
  FROM canonical_decision_intents ORDER BY created_at DESC LIMIT 1;

\echo B4 PAPER OPEN POSITIONS now by strategy (fills bought minus sold, no settlement) and newest entry fill
SELECT coalesce(o.strategy, o.label ->> 'strategy', '-') AS strategy, count(DISTINCT f.group_id) AS groups_with_fills,
       max(f.filled_at) FILTER (WHERE f.role = 'ENTRY') AS newest_entry_fill
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
 GROUP BY 1 ORDER BY 3 DESC NULLS LAST LIMIT 10;

\echo B5 DEREK heartbeat activity versus his decision records in the last hour
SELECT (SELECT activity FROM agent_status WHERE agent_id = 'DEREK') AS derek_status_activity,
       (SELECT count(*) FROM paper_decisions WHERE policy_version LIKE 'DEREK%' AND decided_at >= now() - interval '1 hour') AS derek_decisions_1h,
       (SELECT max(decided_at) FROM paper_decisions WHERE policy_version LIKE 'DEREK%') AS derek_newest_decision;
