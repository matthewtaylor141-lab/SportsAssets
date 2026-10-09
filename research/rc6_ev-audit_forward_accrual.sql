-- READ-ONLY. RC6 lane ev-audit: WHY THE FORWARD EV CONTROLS ACCRUE SLOWLY.
-- DIGITAL_TWIN needs >= 100 fresh terminal IOC/FOK paper orders after
-- 2026-10-07T14:56:26Z (epoch 1791384986); CAPACITY reads
-- paper_profitability_evaluations over 14 days; MULTIPLE_TESTING /
-- SAMPLE_INTEGRITY read red_team_holdout_registry. SELECT only.

\echo == 1 paper_orders per UTC day since 2026-10-01 by role / tif / state
SELECT date_trunc('day', created_at)::date d, role, time_in_force tif, state,
       coalesce(strategy, '-') strategy, count(*) n
  FROM paper_orders
 WHERE created_at >= '2026-10-01'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5;

\echo == 2 fresh terminal IOC/FOK orders after the diagnosis window (the twin population)
SELECT role, time_in_force tif, state, terminal_reason, count(*) n,
       min(eligible_at) first_eligible, max(eligible_at) last_eligible
  FROM paper_orders
 WHERE time_in_force IN ('IOC', 'FOK') AND terminal_at IS NOT NULL
   AND role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND eligible_at > to_timestamp(1791384986)
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo == 3 every order since the window that the twin does NOT see (tif / role / terminal)
SELECT role, time_in_force tif, (terminal_at IS NOT NULL) terminal, state,
       count(*) n
  FROM paper_orders
 WHERE created_at > to_timestamp(1791384986)
   AND NOT (time_in_force IN ('IOC', 'FOK') AND terminal_at IS NOT NULL
            AND role IN ('ENTRY', 'EXIT', 'REDUCE')
            AND eligible_at > to_timestamp(1791384986))
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo == 4 paper_decisions per UTC day since 2026-10-01 by strategy / verdict
SELECT date_trunc('day', decided_at)::date d, coalesce(strategy, '-') strategy,
       verdict, count(*) n
  FROM paper_decisions
 WHERE decided_at >= '2026-10-01'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo == 5 top refusals since the diagnosis window (decisions that placed no order)
SELECT coalesce(strategy, '-') strategy, verdict, coalesce(refusal, '-') refusal,
       count(*) n
  FROM paper_decisions
 WHERE decided_at > to_timestamp(1791384986)
 GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 40;

\echo == 6 strategy lifecycle events (all)
SELECT strategy, from_state, to_state, rule_id, actor, recorded_at
  FROM paper_strategy_lifecycle_events ORDER BY recorded_at;

\echo == 7 paper_control switches
SELECT control_key, enabled, updated_by, updated_at FROM paper_control
 ORDER BY control_key;

\echo == 8 capacity inputs, 14 days: per qty bucket
SELECT CASE WHEN qty_in <= 10 THEN '001-010' WHEN qty_in <= 50 THEN '011-050'
            WHEN qty_in <= 100 THEN '051-100' WHEN qty_in <= 500 THEN '101-500'
            ELSE '501+' END bucket,
       stage, verdict, count(*) n, count(DISTINCT fixture) fixtures,
       count(DISTINCT decision_id) decisions,
       round(avg(ev_per_contract_usd)::numeric, 5) mean_ev_pc,
       round(max(ev_per_contract_usd)::numeric, 5) max_ev_pc,
       sum(CASE WHEN ev_per_contract_usd > 0 THEN 1 ELSE 0 END) n_pos_ev,
       round(avg(fill_probability)::numeric, 4) mean_fp,
       sum(CASE WHEN fill_probability >= 0.8 THEN 1 ELSE 0 END) n_fp_ge_080,
       sum(CASE WHEN fill_probability IS NULL THEN 1 ELSE 0 END) n_fp_null,
       round(sum(all_in_ev_usd)::numeric, 2) sum_net,
       min(evaluated_at) first_at, max(evaluated_at) last_at
  FROM paper_profitability_evaluations
 WHERE evaluated_at > now() - interval '14 days' AND qty_in IS NOT NULL
   AND ev_per_contract_usd IS NOT NULL
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo == 9 capacity inputs: evaluations per decision (duplication) and per day
SELECT date_trunc('day', evaluated_at)::date d, count(*) n,
       count(DISTINCT decision_id) decisions,
       count(DISTINCT coalesce(decision_id, '') || '|' || coalesce(stage, '')) dec_stage,
       count(DISTINCT fixture) fixtures
  FROM paper_profitability_evaluations
 WHERE evaluated_at > now() - interval '14 days'
 GROUP BY 1 ORDER BY 1;

\echo == 10 holdout registry (all rows)
SELECT entry_id, study, kind, candidate_count, implementation_sha, at,
       left(detail::text, 300) detail
  FROM red_team_holdout_registry ORDER BY at;
