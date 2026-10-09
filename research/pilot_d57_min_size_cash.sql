-- READ-ONLY. SMALL LIVE PILOT V1 audit, deliverables 5 and 7: the minimum-size
-- order policy (about 1 USD notional at the venue) and fail-closed CASH.
-- Every statement is a SELECT. No credential, no venue call, no write.
-- Windows are hard-coded (this surface admits no psql variables): the last
-- 2 hours, the last 24 hours and the last 7 days, by each row's own clock.
--   A  live rails in force: execmirror_control, small_live_control, the age of
--      the newest retail account snapshot (buying power present or not; no
--      balance values), lifecycle state of the one live-eligible strategy
--   B  paper decision verdicts and refusal codes, bucketed by cause class
--   C  invariant: no ENTER with non-positive EV, a stale probability or an
--      unestablished contract match (must be zero)
--   D  the entry refusal census, the profitability bind and the CASH records
--   E  the SMALL LIVE adapter (SHADOW) records: states, exclusions, live
--      quantity and live notional distribution
--   F  the ACTUAL lane (execution_intents): states, refusals, live quantity
--   G  what the canonical ENTER intents would size to at the live scale, and
--      the live-size EV after the venue cent-rounded fee (theta 0.0695)

\echo A1 execmirror_control (live rails): scale, per-order cap, switch state
SELECT enabled, stopped, flatten_on_stop, scale, max_order_usd, rounding,
       cutover_at, revision, updated_at,
       (account_fingerprint IS NOT NULL) AS fingerprint_pinned
  FROM execmirror_control;

\echo A2 small_live_control (SMALL LIVE mode and halt)
SELECT mode, halted, halted_at, halt_reason, cleared_at, updated_at FROM small_live_control;

\echo A3 newest retail account snapshot: age and whether buying power is stated (no values)
SELECT at, round(extract(epoch FROM now() - at)::numeric, 1) AS age_s,
       (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(balances) = 'array'
                                                       THEN balances ELSE '[]'::jsonb END) b
         WHERE b ? 'buyingPower') AS balances_with_buying_power,
       open_orders,
       (SELECT count(*) FROM execmirror_snapshots WHERE at > now() - interval '24 hours') AS snapshots_24h
  FROM execmirror_snapshots ORDER BY at DESC LIMIT 1;

\echo A4 lifecycle state per strategy (newest event per strategy, paper_acct_main)
SELECT DISTINCT ON (strategy) strategy, to_state, rule_id, actor, recorded_at
  FROM paper_strategy_lifecycle_events
 WHERE account_id = 'paper_acct_main'
 ORDER BY strategy, event_id DESC;

\echo B1 paper decisions by strategy and verdict (2 h, 24 h, 7 d)
SELECT strategy, verdict,
       count(*) FILTER (WHERE decided_at > now() - interval '2 hours') AS n_2h,
       count(*) FILTER (WHERE decided_at > now() - interval '24 hours') AS n_24h,
       count(*) AS n_7d, max(decided_at) AS newest
  FROM paper_decisions WHERE decided_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo B2 paper decision refusal codes, 24 h, by cause class
WITH r AS (
  SELECT strategy, coalesce(refusal, '(none)') AS refusal
    FROM paper_decisions
   WHERE decided_at > now() - interval '24 hours' AND verdict <> 'ENTER')
SELECT CASE
         WHEN refusal ~ '(NET_EV|EV_NOT_POSITIVE|BELOW_MIN_GROSS_EDGE|FEES_CONSUME|_EV_|CAPACITY_FRONTIER|EV_PER_CAPITAL|NOT_ABSOLUTELY_POSITIVE|FORWARD_ECONOMICS|NO_QTY|EXECUTABLE)' THEN 'EV'
         WHEN refusal ~ '(STALE|NOT_CURRENT|AGE|FRESH|TIME_MISSING|DEADLINE|EXPIRED|COOLDOWN)' THEN 'FRESHNESS'
         WHEN refusal ~ '(SETTLEMENT|GRADING_PERIOD|OUTCOME_MATCH|COMPLETED_GAME_TERMS|MONEYLINE|NOT_FULL_GAME|VENUE_RULES)' THEN 'SETTLEMENT'
         WHEN refusal ~ '(CROSS_STRATEGY|SAME_CONTRACT|LIFECYCLE|STOPPING|CORRELATED|CHURN|TURNOVER|CONCENTRATION|CASH|CAP|CAPITAL|REGIME)' THEN 'RISK_OR_CAPITAL'
         WHEN refusal ~ '(UNREADABLE|ABSENT|MISSING|UNAVAILABLE|NOT_ESTABLISHED|IDENTITY|INCOMPLETE|INVALID|NO_BOOK|NO_PINNACLE)' THEN 'MISSING_OR_UNPROVEN_INPUT'
         ELSE 'OTHER' END AS cause_class,
       strategy, refusal, count(*) AS n
  FROM r GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;

\echo B3 cause class totals, 24 h and 2 h
WITH r AS (
  SELECT decided_at, coalesce(refusal, '(none)') AS refusal
    FROM paper_decisions
   WHERE decided_at > now() - interval '24 hours' AND verdict <> 'ENTER')
SELECT CASE
         WHEN refusal ~ '(NET_EV|EV_NOT_POSITIVE|BELOW_MIN_GROSS_EDGE|FEES_CONSUME|_EV_|CAPACITY_FRONTIER|EV_PER_CAPITAL|NOT_ABSOLUTELY_POSITIVE|FORWARD_ECONOMICS|NO_QTY|EXECUTABLE)' THEN 'EV'
         WHEN refusal ~ '(STALE|NOT_CURRENT|AGE|FRESH|TIME_MISSING|DEADLINE|EXPIRED|COOLDOWN)' THEN 'FRESHNESS'
         WHEN refusal ~ '(SETTLEMENT|GRADING_PERIOD|OUTCOME_MATCH|COMPLETED_GAME_TERMS|MONEYLINE|NOT_FULL_GAME|VENUE_RULES)' THEN 'SETTLEMENT'
         WHEN refusal ~ '(CROSS_STRATEGY|SAME_CONTRACT|LIFECYCLE|STOPPING|CORRELATED|CHURN|TURNOVER|CONCENTRATION|CASH|CAP|CAPITAL|REGIME)' THEN 'RISK_OR_CAPITAL'
         WHEN refusal ~ '(UNREADABLE|ABSENT|MISSING|UNAVAILABLE|NOT_ESTABLISHED|IDENTITY|INCOMPLETE|INVALID|NO_BOOK|NO_PINNACLE)' THEN 'MISSING_OR_UNPROVEN_INPUT'
         ELSE 'OTHER' END AS cause_class,
       count(*) AS n_24h,
       count(*) FILTER (WHERE decided_at > now() - interval '2 hours') AS n_2h
  FROM r GROUP BY 1 ORDER BY 2 DESC;

\echo C1 INVARIANT: ENTER decisions in 7 d with non-positive EV, stale probability, or a refusal recorded (each must be 0)
SELECT count(*) AS enters_7d,
       count(*) FILTER (WHERE (economics->'acquisition'->>'expected_net_profit_usd') IS NULL) AS enter_ev_absent,
       count(*) FILTER (WHERE (economics->'acquisition'->>'expected_net_profit_usd')::numeric <= 0) AS enter_ev_not_positive,
       count(*) FILTER (WHERE (pinnacle->>'age_s') IS NOT NULL AND (pinnacle->>'limit_s') IS NOT NULL
                          AND (pinnacle->>'age_s')::numeric > (pinnacle->>'limit_s')::numeric) AS enter_probability_beyond_limit,
       count(*) FILTER (WHERE coalesce(cardinality(refusals), 0) > 0) AS enter_with_refusals,
       count(*) FILTER (WHERE (economics->'capital_eligibility'->>'capital_eligible') IS DISTINCT FROM 'true') AS enter_without_capital_eligibility,
       min((economics->'acquisition'->>'expected_net_profit_usd')::numeric) AS min_enter_ev_usd,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (economics->'acquisition'->>'expected_net_profit_usd')::numeric) AS p50_enter_ev_usd
  FROM paper_decisions
 WHERE verdict = 'ENTER' AND decided_at > now() - interval '7 days';

\echo C2 ENTER decisions by strategy, 7 d (count, newest, paper qty and limit percentiles)
SELECT strategy, count(*) AS enters, max(decided_at) AS newest,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY proposed_qty) AS p50_qty,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY limit_price) AS p50_limit,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY proposed_qty * limit_price) AS p50_paper_cost_usd,
       max(proposed_qty * limit_price) AS max_paper_cost_usd
  FROM paper_decisions
 WHERE verdict = 'ENTER' AND decided_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC;

\echo D1 entry refusal census, 24 h, by stage and refusal (top 40)
SELECT stage, refusal, count(*) AS n, max(refused_at) AS newest
  FROM paper_entry_refusal_census
 WHERE refused_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

\echo D2 profitability bind evaluations, 24 h, by stage verdict and refusal (top 30)
SELECT stage, verdict, coalesce(refusal, '(none)') AS refusal, count(*) AS n, max(evaluated_at) AS newest
  FROM paper_profitability_evaluations
 WHERE evaluated_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo D3 CASH decisions recorded by the pass, 24 h, per strategy, and the newest 5
SELECT strategy, count(*) AS cash_records_24h, max(pass_at) AS newest,
       sum(decisions_evaluated) AS decisions_evaluated_24h
  FROM paper_cash_decisions WHERE pass_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;
SELECT strategy, pass_at, decisions_evaluated, left(binding_refusals::text, 300) AS binding_refusals
  FROM paper_cash_decisions ORDER BY pass_at DESC LIMIT 5;

\echo E1 SMALL LIVE adapter records (SHADOW), 7 d: state and exclusion
SELECT intent_kind, mode, state, coalesce(exclusion, '(none)') AS exclusion, count(*) AS n,
       count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS n_24h,
       max(created_at) AS newest
  FROM canonical_intent_executions
 WHERE adapter = 'SMALL_LIVE' AND created_at > now() - interval '7 days'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;

\echo E2 SMALL LIVE adapter DECISION records, 7 d: plan state and plan exclusion behind the governance refusal
SELECT refs->>'plan_state' AS plan_state, coalesce(refs->>'plan_exclusion', '(none)') AS plan_exclusion,
       refs->>'max_order_usd' AS max_order_usd, refs->>'buying_power_current' AS buying_power_current,
       count(*) AS n
  FROM canonical_intent_executions
 WHERE adapter = 'SMALL_LIVE' AND intent_kind = 'DECISION' AND created_at > now() - interval '7 days'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC;

\echo E3 SMALL LIVE adapter DECISION records, 7 d: live quantity and live notional (wire x qty) distribution
WITH x AS (
  SELECT (requested->>'qty')::numeric AS live_qty,
         (requested->>'wire_price')::numeric AS wire,
         requested->>'order_intent' AS oi
    FROM canonical_intent_executions
   WHERE adapter = 'SMALL_LIVE' AND intent_kind = 'DECISION'
     AND created_at > now() - interval '7 days')
SELECT count(*) AS n,
       count(*) FILTER (WHERE live_qty IS NULL OR live_qty < 1) AS live_qty_below_one,
       count(*) FILTER (WHERE live_qty = 1) AS live_qty_1,
       count(*) FILTER (WHERE live_qty = 2) AS live_qty_2,
       count(*) FILTER (WHERE live_qty >= 3) AS live_qty_3_plus,
       min(live_qty * CASE WHEN oi = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire ELSE wire END) AS min_live_cost_usd,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY live_qty * CASE WHEN oi = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire ELSE wire END) AS p50_live_cost_usd,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY live_qty * CASE WHEN oi = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire ELSE wire END) AS p90_live_cost_usd,
       max(live_qty * CASE WHEN oi = 'ORDER_INTENT_BUY_SHORT' THEN 1 - wire ELSE wire END) AS max_live_cost_usd
  FROM x;

\echo F1 ACTUAL lane (execution_intents), 7 d: actual state and refusal
SELECT strategy, actual_state, coalesce(actual_refusal, '(none)') AS actual_refusal, count(*) AS n,
       count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS n_24h, max(created_at) AS newest
  FROM execution_intents WHERE created_at > now() - interval '7 days'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo F2 ACTUAL lane, 7 d: live quantity written by the lane and live notional
SELECT count(*) AS n, count(live_qty) AS with_live_qty,
       count(*) FILTER (WHERE live_qty = 0) AS live_qty_0,
       count(*) FILTER (WHERE live_qty = 1) AS live_qty_1,
       count(*) FILTER (WHERE live_qty = 2) AS live_qty_2,
       count(*) FILTER (WHERE live_qty >= 3) AS live_qty_3_plus,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY live_qty * wire_price) AS p50_live_wire_notional_usd,
       max(live_qty * wire_price) AS max_live_wire_notional_usd,
       (SELECT count(*) FROM execmirror_orders WHERE venue_order_id IS NOT NULL) AS venue_orders_ever
  FROM execution_intents WHERE created_at > now() - interval '7 days';

\echo G1 canonical ENTER intents, 7 d, sized at scale 1000: live qty (half to even), live cost, cent-rounded fee, live EV
WITH c AS (
  SELECT strategy, order_intent,
         target_qty::numeric AS tq, limit_price::numeric AS px, wire_price::numeric AS wire,
         (probability->>'value')::numeric AS p,
         target_qty::numeric / 1000 AS raw
    FROM canonical_decision_intents
   WHERE created_at > now() - interval '7 days' AND target_qty IS NOT NULL
     AND limit_price IS NOT NULL AND (probability->>'value') IS NOT NULL),
q AS (
  SELECT *, CASE WHEN raw - floor(raw) = 0.5
                 THEN CASE WHEN mod(floor(raw), 2) = 0 THEN floor(raw) ELSE floor(raw) + 1 END
                 ELSE round(raw) END AS lq
    FROM c),
e AS (
  SELECT *, lq * px AS live_cost,
         round(0.0695 * lq * px * (1 - px), 2) AS live_fee_cents,
         0.0695 * lq * px * (1 - px) AS live_fee_exact,
         lq * (p - px) AS live_gross,
         tq * (p - px) - 0.0695 * tq * px * (1 - px) AS paper_net_approx
    FROM q)
SELECT strategy, count(*) AS intents,
       count(*) FILTER (WHERE lq < 1) AS live_qty_0_excluded,
       count(*) FILTER (WHERE lq >= 1 AND live_cost < 0.5) AS cost_lt_050,
       count(*) FILTER (WHERE lq >= 1 AND live_cost >= 0.5 AND live_cost < 1.0) AS cost_050_100,
       count(*) FILTER (WHERE lq >= 1 AND live_cost >= 1.0 AND live_cost <= 1.5) AS cost_100_150,
       count(*) FILTER (WHERE lq >= 1 AND live_cost > 1.5) AS cost_gt_150,
       max(live_cost) AS max_live_cost,
       count(*) FILTER (WHERE lq >= 1 AND paper_net_approx > 0 AND live_gross - live_fee_cents <= 0) AS paper_ev_pos_live_ev_not_pos,
       count(*) FILTER (WHERE lq >= 1 AND live_gross - live_fee_cents > 0) AS live_ev_pos,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY live_gross - live_fee_cents) FILTER (WHERE lq >= 1) AS p50_live_net_ev_usd,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY p - px) AS p50_edge_per_contract
  FROM e GROUP BY 1 ORDER BY 2 DESC;
