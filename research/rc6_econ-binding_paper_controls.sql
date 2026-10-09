-- RC6 lane P0-ECONOMICS (binding profitability controls), read only.
-- The last 24 h of PAPER entry decisions on every strategy, by the control
-- that refused them, and the CASH count: does each control BIND in
-- production (refuse by its own code, no order), and is CASH the outcome
-- when nothing qualifies? Also the inputs the lane's new fail-closed control
-- inputs (bind control 25) read, so the effect of deploying it is measured
-- BEFORE it is deployed: learned-model ages by kind, and how many recorded
-- evaluations carried no probability / book instant.
\echo === A. paper decisions (24 h) by strategy and verdict ===
SELECT d.strategy, d.verdict, count(*) AS decisions,
       min(d.decided_at) AS first_at, max(d.decided_at) AS last_at
  FROM paper_decisions d
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === B. refused decisions (24 h) by refusing control family and code ===
WITH r AS (
  SELECT d.strategy, coalesce(d.refusal, '<none>') AS code
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '24 hours' AND d.verdict <> 'ENTER')
SELECT CASE
         WHEN code LIKE 'CAPITAL_INELIGIBLE_%'
           OR code = 'CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE'
           THEN '1_CAPITAL_ELIGIBILITY (depth/identity/settlement/EV>0)'
         WHEN code LIKE 'CHURN_%' THEN '2_BIND_CHURN'
         WHEN code IN ('CASH_WAIT_CALIBRATED_ALL_IN_EV_NOT_POSITIVE')
           THEN '2_BIND_CALIBRATION'
         WHEN code IN ('CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE')
           THEN '2_BIND_ALL_IN_EV'
         WHEN code IN ('CASH_WAIT_CAPACITY_FRONTIER_NO_POSITIVE_MARGINAL_CONTRACT',
                       'CASH_WAIT_BOUND_SIZE_BELOW_ONE_CONTRACT')
           THEN '2_BIND_CAPACITY'
         WHEN code IN ('CASH_WAIT_EV_PER_CAPITAL_HOUR_BELOW_FLOOR',
                       'CASH_WAIT_NO_EXPECTED_HOLD_FOR_CAPITAL_HOUR')
           THEN '2_BIND_CAPITAL_HOUR'
         WHEN code IN ('CASH_WAIT_CORRELATED_FIXTURE_EXPOSURE_AT_LIMIT',
                       'CASH_WAIT_SCENARIO_CONCENTRATION_LIMIT_REACHED')
           THEN '2_BIND_CORRELATION_SCENARIO'
         WHEN code = 'CASH_WAIT_PROBABILITY_AGE_BEYOND_FRESHNESS_BOUND'
           THEN '2_BIND_FRESHNESS'
         WHEN code LIKE 'CASH_WAIT_%_NOT_CURRENT'
           OR code LIKE 'CASH_WAIT_%_AT_BIND'
           OR code LIKE 'CASH_WAIT_%_OBSERVATION_TIME_MISSING'
           OR code = 'CASH_WAIT_PROBABILITY_NOT_IN_ZERO_ONE'
           THEN '2_BIND_CONTROL_INPUTS (lane, not yet deployed)'
         WHEN code LIKE 'PROFITABILITY_BIND_%' THEN '2_BIND_UNREADABLE_OR_NO_EVIDENCE'
         WHEN code IN ('CASH_WAIT_REGIME_UNKNOWN_SHADOW_ONLY',
                       'CASH_WAIT_REGIME_FORWARD_ECONOMICS_NOT_POSITIVE',
                       'REGIME_FORWARD_ECONOMICS_UNREADABLE')
           THEN '3_AUTHORITY_REGIME'
         WHEN code = 'CASH_WAIT_FORWARD_PNL_NOT_ABSOLUTELY_POSITIVE_CI_LOW_NOT_ABOVE_ZERO'
           THEN '3_AUTHORITY_ABSOLUTE_CHAMPION'
         WHEN code LIKE 'CASH_WAIT_FORWARD_ECONOMICS_%'
           OR code = 'FORWARD_ECONOMICS_UNREADABLE'
           THEN '3_AUTHORITY_FORWARD_ECONOMICS'
         WHEN code LIKE 'STRATEGY_STOPPING_RULE%'
           THEN '3_AUTHORITY_STOPPING_RULES'
         WHEN code LIKE 'LIFECYCLE_%' OR code LIKE '%LIFECYCLE%'
           THEN '0_LIFECYCLE'
         ELSE '0_POLICY_OR_UPSTREAM_INPUT (probability, book, edge, match)'
       END AS control_family,
       code, strategy, count(*) AS refused
  FROM r GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 120;
\echo === C. bind evaluations (24 h) by stage, verdict and refusal (the controls inside the bind and the authority) ===
SELECT e.stage, e.strategy, e.verdict, coalesce(e.refusal, '<ENTER>') AS refusal,
       count(*) AS evaluations,
       round(avg(e.all_in_ev_usd)::numeric, 4) AS mean_all_in_ev_usd,
       round(avg(e.ev_per_contract_usd)::numeric, 5) AS mean_ev_per_contract
  FROM paper_profitability_evaluations e
 WHERE e.evaluated_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 5 DESC LIMIT 120;
\echo === D. paper ENTRY orders written (24 h) by strategy and state; and the record each carries ===
SELECT o.strategy, o.state, count(*) AS entry_orders,
       count(*) FILTER (WHERE NOT EXISTS (
         SELECT 1 FROM paper_profitability_evaluations e
          WHERE e.order_key = o.idempotency_key AND e.stage = 'LEDGER'
            AND e.verdict = 'ENTER')) AS without_ledger_enter_evaluation
  FROM paper_orders o
 WHERE o.role = 'ENTRY' AND o.direction = 'BUY'
   AND o.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;
SELECT count(*) AS ledger_enter_evaluations_24h,
       count(*) FILTER (WHERE e.p_used IS NOT NULL AND (
         SELECT count(*) FROM paper_counterfactual_variants v
          WHERE v.eval_id = e.eval_id) < 6) AS priced_without_full_variants
  FROM paper_profitability_evaluations e
 WHERE e.stage = 'LEDGER' AND e.verdict = 'ENTER'
   AND e.evaluated_at > now() - interval '24 hours';
\echo === E. the explicit CASH decisions (24 h) by strategy ===
SELECT c.strategy, count(*) AS cash_passes,
       sum(c.decisions_evaluated) AS decisions_evaluated,
       sum(c.entries) AS entries, max(c.pass_at) AS last_cash_pass
  FROM paper_cash_decisions c
 WHERE c.pass_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1;
\echo === F. entry-refusal census (24 h) by stage and refusal ===
SELECT n.stage, n.refusal, count(*) AS refused,
       count(DISTINCT n.us_market_slug) AS contracts,
       round(max(n.total_executable_ev_usd)::numeric, 4) AS best_refused_ev_usd
  FROM paper_entry_refusal_census n
 WHERE n.refused_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 80;
\echo === G. shadow counterfactuals (24 h) by the missing capital authority, and settled outcomes ===
SELECT s.strategy, s.capital_refusal, count(*) AS shadows,
       count(o.shadow_id) AS settled,
       count(o.shadow_id) FILTER (WHERE o.filled_qty > 0
                                    AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND')) AS settled_filled,
       round(coalesce(sum(o.counterfactual_pnl_usd), 0)::numeric, 4) AS counterfactual_pnl_usd_not_realized
  FROM paper_shadow_counterfactuals s
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE s.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;
\echo === H. learned model ages now, by account and kind (bind control 25 needs each <= 3600 s) ===
SELECT m.account_id, m.kind, count(*) AS fits_24h,
       max(m.fitted_at) AS last_fit,
       round(extract(epoch FROM now() - max(m.fitted_at))::numeric, 1) AS age_s
  FROM paper_profitability_models m
 WHERE m.fitted_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;
SELECT m.account_id, m.kind, max(m.fitted_at) AS last_fit_ever
  FROM paper_profitability_models m
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === I. recorded bind evaluations (24 h) that carried no probability / book instant, by stage and strategy ===
SELECT e.stage, e.strategy, count(*) AS evaluations,
       count(*) FILTER (WHERE e.detail -> 'freshness' IS NULL) AS no_freshness_block,
       count(*) FILTER (WHERE e.detail -> 'freshness' ->> 'p_age_s' IS NULL
                          AND e.detail -> 'freshness' IS NOT NULL) AS no_probability_instant,
       count(*) FILTER (WHERE e.detail -> 'freshness' ->> 'book_age_s' IS NULL
                          AND e.detail -> 'freshness' IS NOT NULL) AS no_book_instant,
       count(*) FILTER (WHERE e.fixture IS NULL) AS no_fixture
  FROM paper_profitability_evaluations e
 WHERE e.evaluated_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === J. totals (24 h): decisions, ENTER decisions, entry orders, CASH passes ===
SELECT (SELECT count(*) FROM paper_decisions
         WHERE decided_at > now() - interval '24 hours') AS decisions,
       (SELECT count(*) FROM paper_decisions
         WHERE decided_at > now() - interval '24 hours'
           AND verdict = 'ENTER') AS enter_decisions,
       (SELECT count(*) FROM paper_orders
         WHERE role = 'ENTRY' AND direction = 'BUY'
           AND decided_at > now() - interval '24 hours') AS entry_orders,
       (SELECT count(*) FROM paper_cash_decisions
         WHERE pass_at > now() - interval '24 hours') AS cash_passes,
       now() AS read_at;
