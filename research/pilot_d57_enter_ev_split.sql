-- READ-ONLY. SMALL LIVE PILOT V1 audit, deliverable 7 follow-up to
-- pilot_d57_min_size_cash.sql: the ENTER invariant split by strategy, each
-- strategy's capital-eligibility record at the path that strategy writes,
-- whether an ENTER with a non-positive acquisition EV ever became a paper
-- order, and the live-size EV of the live-eligible strategy's canonical
-- intents at the best observed level (not the limit). SELECT only.

\echo K1 economics keys written on ENTER decisions per strategy, 7 d
SELECT strategy, k, count(*) AS n
  FROM (SELECT strategy, jsonb_object_keys(economics) AS k
          FROM paper_decisions
         WHERE verdict = 'ENTER' AND decided_at > now() - interval '7 days') x
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo K2 ENTER invariant per strategy, 7 d: acquisition EV and the capital-eligibility record at both paths
SELECT strategy, count(*) AS enters,
       count(*) FILTER (WHERE (economics->'acquisition'->>'expected_net_profit_usd') IS NULL) AS acq_ev_absent,
       count(*) FILTER (WHERE (economics->'acquisition'->>'expected_net_profit_usd')::numeric <= 0) AS acq_ev_not_positive,
       count(*) FILTER (WHERE economics->'capital_eligibility'->>'capital_eligible' = 'true') AS ce_top_true,
       count(*) FILTER (WHERE economics->'acquisition'->'capital_eligibility'->>'capital_eligible' = 'true') AS ce_acq_true,
       count(*) FILTER (WHERE coalesce(economics->'capital_eligibility'->>'total_executable_ev_usd',
                                       economics->'acquisition'->'capital_eligibility'->>'total_executable_ev_usd')::numeric <= 0) AS total_exec_ev_not_positive,
       count(*) FILTER (WHERE coalesce(economics->'capital_eligibility'->>'total_executable_ev_usd',
                                       economics->'acquisition'->'capital_eligibility'->>'total_executable_ev_usd') IS NULL) AS total_exec_ev_absent,
       min(decided_at) AS first_enter, max(decided_at) AS last_enter
  FROM paper_decisions
 WHERE verdict = 'ENTER' AND decided_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC;

\echo K3 ENTER with non-positive acquisition EV, 7 d: by strategy and day, with the paper order state and any ledger refusal
WITH e AS (
  SELECT decision_id, strategy, decided_at,
         (economics->'acquisition'->>'expected_net_profit_usd')::numeric AS acq_ev,
         coalesce(economics->'capital_eligibility'->>'total_executable_ev_usd',
                  economics->'acquisition'->'capital_eligibility'->>'total_executable_ev_usd')::numeric AS exec_ev,
         policy_version
    FROM paper_decisions
   WHERE verdict = 'ENTER' AND decided_at > now() - interval '7 days'
     AND (economics->'acquisition'->>'expected_net_profit_usd')::numeric <= 0)
SELECT e.strategy, e.policy_version, date_trunc('day', e.decided_at) AS day, count(*) AS n,
       count(o.order_id) AS with_paper_order,
       count(*) FILTER (WHERE o.state IN ('FILLED', 'PARTIALLY_FILLED')) AS order_filled,
       count(*) FILTER (WHERE e.exec_ev > 0) AS exec_ev_positive,
       count(*) FILTER (WHERE e.exec_ev <= 0) AS exec_ev_not_positive,
       count(c.decision_id) AS ledger_refused,
       min(e.acq_ev) AS min_acq_ev, max(e.acq_ev) AS max_acq_ev
  FROM e
  LEFT JOIN LATERAL (SELECT order_id, state FROM paper_orders o
                      WHERE o.decision_id = e.decision_id ORDER BY o.created_at LIMIT 1) o ON true
  LEFT JOIN LATERAL (SELECT decision_id FROM paper_entry_refusal_census c
                      WHERE c.decision_id = e.decision_id AND c.stage = 'LEDGER' LIMIT 1) c ON true
 GROUP BY 1, 2, 3 ORDER BY 1, 3;

\echo K4 exploration ENTER decisions in 24 h by paper order outcome (order created or ledger refusal)
SELECT d.strategy,
       count(*) AS enters_24h,
       count(o.order_id) AS with_paper_order,
       count(c.refusal) AS ledger_refusals,
       string_agg(DISTINCT c.refusal, ', ') AS ledger_refusal_codes
  FROM paper_decisions d
  LEFT JOIN LATERAL (SELECT order_id FROM paper_orders o
                      WHERE o.decision_id = d.decision_id LIMIT 1) o ON true
  LEFT JOIN LATERAL (SELECT refusal FROM paper_entry_refusal_census c
                      WHERE c.decision_id = d.decision_id AND c.stage = 'LEDGER' LIMIT 1) c ON true
 WHERE d.verdict = 'ENTER' AND d.decided_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo G2 live-eligible canonical ENTER intents, 7 d: live size at scale 1000 and live EV at the BEST observed level (cent-rounded fee, theta 0.0695)
WITH c AS (
  SELECT i.strategy, i.target_qty::numeric AS tq, i.limit_price::numeric AS lim,
         (i.probability->>'value')::numeric AS p,
         (d.book->'levels'->0->>'price')::numeric AS best,
         (d.book->'levels'->0->>'qty')::numeric AS best_qty,
         (d.economics->'acquisition'->>'expected_net_profit_usd')::numeric AS paper_ev,
         i.target_qty::numeric / 1000 AS raw
    FROM canonical_decision_intents i
    JOIN paper_decisions d ON d.decision_id = i.decision_id
   WHERE i.created_at > now() - interval '7 days'),
q AS (
  SELECT *, CASE WHEN raw - floor(raw) = 0.5
                 THEN CASE WHEN mod(floor(raw), 2) = 0 THEN floor(raw) ELSE floor(raw) + 1 END
                 ELSE round(raw) END AS lq
    FROM c),
e AS (
  SELECT *, lq * (p - best) - round(0.0695 * lq * best * (1 - best), 2) AS live_ev_best,
         lq * (p - lim) - round(0.0695 * lq * lim * (1 - lim), 2) AS live_ev_limit,
         round(0.0695 * lq * best * (1 - best), 2) AS live_fee,
         0.0695 * lq * best * (1 - best) AS live_fee_exact
    FROM q WHERE best IS NOT NULL)
SELECT strategy, count(*) AS intents, count(*) FILTER (WHERE lq >= 1) AS live_sized,
       count(*) FILTER (WHERE lq >= 1 AND best_qty >= lq) AS best_level_covers_live_qty,
       count(*) FILTER (WHERE lq >= 1 AND paper_ev > 0) AS paper_ev_positive,
       count(*) FILTER (WHERE lq >= 1 AND paper_ev > 0 AND live_ev_best <= 0) AS live_ev_best_not_positive,
       count(*) FILTER (WHERE lq >= 1 AND paper_ev > 0 AND live_ev_limit <= 0) AS live_ev_limit_not_positive,
       count(*) FILTER (WHERE lq >= 1 AND live_fee > live_fee_exact) AS fee_rounded_up,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY live_ev_best) FILTER (WHERE lq >= 1) AS p50_live_ev_best_usd,
       min(live_ev_best) FILTER (WHERE lq >= 1) AS min_live_ev_best_usd
  FROM e GROUP BY 1 ORDER BY 2 DESC;
