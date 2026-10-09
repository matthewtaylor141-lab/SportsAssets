-- READ-ONLY. SMALL LIVE PILOT V1, DELIVERABLE 2 (end-to-end PAPER chain), RUN 1:
-- (a) the census of the newest PAPER positions that reached an ENTRY fill, with
-- every link of the chain counted per group (discovery valuation, market plane
-- registry row, ENTER decision, profitability bind evaluations, capital
-- authority on the SUBMITTED event, ledger reservation, order events, fills,
-- Derek -> Xavier handoff, Xavier reviews / theses / assessments / management
-- orders, settlement, Audrey report and findings), and (b) the state that
-- decides whether ANY strategy can reach a PAPER ENTRY today: lifecycle,
-- kill-switch rows, decisions and refusals by strategy over 24 h, the ledger
-- refusal census and shadow counterfactuals by strategy. SELECT only.

\echo C0 NOW and RUNNING COMMIT per process
SELECT now() AS db_now;
SELECT process, commit_sha, count(*) AS loops, max(last_success_at) AS newest_success
  FROM runtime_loop_health GROUP BY 1, 2 ORDER BY 1, 2;

\echo C1 TABLE SIZE ESTIMATES (planner statistics, no scan)
SELECT relname, reltuples::bigint AS est_rows
  FROM pg_class
 WHERE relname IN ('paper_fills', 'paper_orders', 'paper_order_events', 'paper_decisions',
                   'paper_xavier_reviews', 'paper_audrey_findings', 'paper_audrey_reports',
                   'market_plane_events', 'market_plane_registry', 'external_valuations',
                   'paper_book_observations', 'paper_profitability_evaluations',
                   'xavier_management_assessments', 'paper_settlements', 'us_premap')
 ORDER BY 1;

\echo C2 NEWEST 10 PAPER FILLS (any role)
SELECT fill_id, order_id, strategy, role, direction, group_id, us_market_slug, holding_side,
       qty, price, fee_usd, filled_at, basis
  FROM paper_fills ORDER BY filled_at DESC LIMIT 10;

\echo C3 PAPER FILLS ALL TIME by strategy and role: count, groups, first and newest
SELECT strategy, role, direction, count(*) AS fills, count(DISTINCT group_id) AS groups,
       min(filled_at) AS first_fill, max(filled_at) AS newest_fill
  FROM paper_fills GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo C4 CHAIN CENSUS: the 30 newest groups by first ENTRY fill, every link counted
WITH g AS (
    SELECT group_id, min(filled_at) AS first_entry_fill, max(filled_at) AS last_entry_fill,
           min(account_id) AS account_id, min(strategy) AS strategy,
           min(us_market_slug) AS slug, min(holding_side) AS side
      FROM paper_fills WHERE role = 'ENTRY'
     GROUP BY group_id ORDER BY 2 DESC LIMIT 30),
eo AS (
    SELECT o.group_id, o.order_id, o.idempotency_key, o.decision_id
      FROM paper_orders o JOIN g ON g.group_id = o.group_id WHERE o.role = 'ENTRY'),
fl AS (
    SELECT f.group_id,
           coalesce(sum(f.qty) FILTER (WHERE f.direction = 'BUY'), 0) AS bought,
           coalesce(sum(f.qty) FILTER (WHERE f.direction = 'SELL'), 0) AS sold,
           count(*) AS fills, max(f.filled_at) AS newest_fill
      FROM paper_fills f JOIN g ON g.group_id = f.group_id GROUP BY f.group_id),
st AS (
    SELECT DISTINCT ON (s.group_id) s.group_id, s.qty, s.outcome, s.settled_at, s.version
      FROM paper_settlements s JOIN g ON g.group_id = s.group_id
     ORDER BY s.group_id, s.version DESC, s.settled_at DESC)
SELECT g.group_id, g.strategy, g.slug, g.side, g.first_entry_fill,
       (SELECT count(*) FROM eo WHERE eo.group_id = g.group_id) AS entry_orders,
       (SELECT count(*) FROM eo JOIN paper_decisions d ON d.decision_id = eo.decision_id
         WHERE eo.group_id = g.group_id AND d.verdict = 'ENTER') AS enter_decisions,
       (SELECT count(*) FROM eo JOIN paper_decisions d ON d.decision_id = eo.decision_id
          JOIN external_valuations v ON v.id = d.valuation_id
         WHERE eo.group_id = g.group_id) AS valuations,
       EXISTS (SELECT 1 FROM market_plane_registry r WHERE r.contract_id = g.slug) AS plane_registry,
       EXISTS (SELECT 1 FROM us_premap p WHERE p.market_slug = g.slug) AS premap,
       (SELECT count(*) FROM paper_profitability_evaluations e
          JOIN eo ON eo.idempotency_key = e.order_key WHERE eo.group_id = g.group_id) AS bind_evals,
       (SELECT count(*) FROM paper_order_events ev JOIN eo ON eo.order_id = ev.order_id
         WHERE eo.group_id = g.group_id AND ev.kind = 'SUBMITTED'
           AND ev.detail ? 'capital_authority') AS submitted_w_authority,
       (SELECT count(*) FROM paper_order_events ev JOIN eo ON eo.order_id = ev.order_id
         WHERE eo.group_id = g.group_id) AS entry_order_events,
       fl.fills, fl.bought, fl.sold,
       EXISTS (SELECT 1 FROM paper_handoffs h WHERE h.group_id = g.group_id) AS handoff,
       (SELECT count(*) FROM paper_xavier_reviews x WHERE x.group_id = g.group_id) AS xavier_reviews,
       (SELECT count(*) FROM xavier_entry_theses t WHERE t.group_id = g.group_id) AS theses,
       (SELECT count(*) FROM xavier_management_assessments a WHERE a.group_id = g.group_id) AS assessments,
       (SELECT count(*) FROM paper_orders o WHERE o.group_id = g.group_id AND o.role <> 'ENTRY') AS mgmt_orders,
       (SELECT string_agg(DISTINCT o.role || ':' || o.state, ',') FROM paper_orders o
         WHERE o.group_id = g.group_id AND o.role <> 'ENTRY') AS mgmt_roles_states,
       (SELECT count(*) FROM paper_exit_intents i WHERE i.group_id = g.group_id) AS exit_intents,
       st.outcome AS settled_outcome, st.settled_at,
       fl.bought - fl.sold - coalesce(st.qty, 0) AS open_qty_now,
       EXISTS (SELECT 1 FROM paper_audrey_reports ar WHERE ar.account_id = g.account_id
                  AND ar.report_day = (g.first_entry_fill AT TIME ZONE 'America/New_York')::date)
           AS audrey_report_fill_day,
       (SELECT count(*) FROM paper_audrey_findings af WHERE af.account_id = g.account_id
           AND (af.subject = g.group_id
                OR af.subject = 'paperpos:' || g.account_id || ':' || g.group_id || ':' || g.slug || ':' || g.side
                OR af.subject IN (SELECT o.order_id FROM paper_orders o WHERE o.group_id = g.group_id)))
           AS audrey_findings_on_group
  FROM g LEFT JOIN fl ON fl.group_id = g.group_id LEFT JOIN st ON st.group_id = g.group_id
 ORDER BY g.first_entry_fill DESC;

\echo C5 NEWEST GROUP PER STRATEGY with a settlement, and newest closed by sale (bought = sold)
WITH g AS (
    SELECT f.strategy, f.group_id, min(f.filled_at) FILTER (WHERE f.role = 'ENTRY') AS first_entry_fill,
           coalesce(sum(f.qty) FILTER (WHERE f.direction = 'BUY'), 0) AS bought,
           coalesce(sum(f.qty) FILTER (WHERE f.direction = 'SELL'), 0) AS sold
      FROM paper_fills f GROUP BY 1, 2)
SELECT DISTINCT ON (g.strategy, closed_by) g.strategy, closed_by, g.group_id, g.first_entry_fill, g.bought, g.sold
  FROM (SELECT g.*, CASE WHEN EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = g.group_id)
                         THEN 'SETTLEMENT' WHEN g.bought > 0 AND g.bought - g.sold <= 1e-9 THEN 'SALE'
                         ELSE 'OPEN' END AS closed_by
          FROM g WHERE g.first_entry_fill IS NOT NULL) g
 ORDER BY g.strategy, closed_by, g.first_entry_fill DESC;

\echo B1 LIFECYCLE STATE per account and strategy (current view)
SELECT account_id, strategy, from_state, state, rule_id, actor, recorded_at, left(why, 160) AS why
  FROM paper_strategy_lifecycle_current_v ORDER BY account_id, strategy;

\echo B2 KILL-SWITCH ROWS (paper_control)
SELECT control_key, enabled, updated_by, updated_at, left(why, 120) AS why
  FROM paper_control ORDER BY control_key;

\echo B3 DECISIONS by strategy: newest ever, newest ENTER ever, last 24 h by verdict
SELECT strategy, max(decided_at) AS newest_decision,
       max(decided_at) FILTER (WHERE verdict = 'ENTER') AS newest_enter
  FROM paper_decisions WHERE decided_at >= now() - interval '30 days' GROUP BY 1 ORDER BY 1;
SELECT strategy, verdict, count(*) AS n, max(decided_at) AS newest
  FROM paper_decisions WHERE decided_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo B4 DECISION REFUSALS last 24 h by strategy (top 8 per strategy)
SELECT strategy, refusal, n FROM (
  SELECT strategy, refusal, count(*) AS n,
         row_number() OVER (PARTITION BY strategy ORDER BY count(*) DESC) AS rk
    FROM paper_decisions WHERE decided_at >= now() - interval '24 hours' AND verdict = 'REFUSE'
   GROUP BY 1, 2) x WHERE rk <= 8 ORDER BY strategy, n DESC;

\echo B5 LEDGER AND DECISION REFUSAL CENSUS last 24 h by strategy, stage, refusal (top 30)
SELECT strategy, stage, refusal, count(*) AS n, max(refused_at) AS newest
  FROM paper_entry_refusal_census WHERE refused_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo B6 SHADOW COUNTERFACTUALS by strategy and capital refusal: last 24 h and forward window since 2026-10-05 04:00Z
SELECT strategy, capital_refusal,
       count(*) FILTER (WHERE decided_at >= now() - interval '24 hours') AS last_24h,
       count(*) AS since_forward, max(decided_at) AS newest
  FROM paper_shadow_counterfactuals WHERE decided_at >= timestamptz '2026-10-05 04:00:00+00'
 GROUP BY 1, 2 ORDER BY 1, 4 DESC;

\echo B7 SETTLED SHADOW OUTCOMES in the forward window by strategy (forward observations, filled only)
SELECT s.strategy, count(*) AS settled, count(*) FILTER (WHERE o.filled_qty > 0
           AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND')) AS economic_obs,
       round(coalesce(sum(o.counterfactual_pnl_usd) FILTER (WHERE o.filled_qty > 0
           AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND')), 0)::numeric, 4) AS net_pnl_usd
  FROM paper_shadow_counterfactuals s JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE s.decided_at >= timestamptz '2026-10-05 04:00:00+00'
 GROUP BY 1 ORDER BY 1;

\echo B8 PROFITABILITY BIND EVALUATIONS last 24 h by strategy, stage, verdict, refusal (top 25)
SELECT strategy, stage, verdict, coalesce(refusal, '-') AS refusal, count(*) AS n, max(evaluated_at) AS newest
  FROM paper_profitability_evaluations WHERE evaluated_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 25;

\echo B9 EVALUATION ATTEMPTS last 24 h by strategy and outcome
SELECT strategy, outcome, count(*) AS n, max(at) AS newest
  FROM paper_evaluation_attempts WHERE at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo B10 PAPER ORDERS created last 24 h by strategy, role and state
SELECT strategy, role, state, count(*) AS n, max(created_at) AS newest
  FROM paper_orders WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
