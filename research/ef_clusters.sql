-- ECONOMIC FUNNEL (owner directive section 6), STEP 6: THE CLUSTER VALUES
-- THE UNCERTAINTY BOUNDS ARE COMPUTED FROM, AND TWO DIAGNOSTICS.
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
--
-- 6.1 exports one row per (strategy, fixture) cluster of CLOSED PAPER
-- positions (realized P&L restated from bettor_paper_ledger._position_from
-- as in ef_paper_realized.sql, and the policy's own expected net at entry),
-- so the report can apply Student-t with clusters - 1 degrees of freedom and
-- a seeded cluster bootstrap to exactly these values. 6.2 exports the
-- forward Pinnacle-only candidates the bind refused, one row per fixture:
-- the EV after MEASURED costs vs after MODELLED costs vs the settled
-- counterfactual (NOT_REALIZED_PNL). 6.3 says why Derek's policy-admitted
-- forward decisions mostly never reached the bind.

\echo '== 6.1 closed PAPER positions, one row per strategy x fixture cluster'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.filled_qty AS entry_filled_qty,
           d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net
      FROM paper_orders o LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
q AS (
    SELECT
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought ELSE 0 END AS avg_cost,
           f.*, s.qty AS settled_qty, s.payout_usd, s.settled_at,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           CASE WHEN eo.proposed_qty > 0 THEN eo.exp_net * eo.entry_filled_qty / eo.proposed_qty END AS expected
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
r AS (
    SELECT q.strategy, q.fixture, q.expected, q.first_fill_at,
           (coalesce(q.sale_gross, 0) - coalesce(q.sale_fees, 0)) - q.avg_cost * coalesce(q.sold, 0)
           + CASE WHEN coalesce(q.settled_qty, 0) > 0
                  THEN coalesce(q.payout_usd, 0) - q.avg_cost * q.settled_qty ELSE 0 END AS realized,
           coalesce(q.settled_at, q.last_fill_at) AS closed_at
      FROM q WHERE q.open_qty <= 1e-9)
SELECT r.strategy, r.fixture, count(*) AS positions,
       round(sum(r.realized), 4) AS realized_usd, round(sum(r.expected), 4) AS expected_usd,
       min(r.first_fill_at) AS first_entry, max(r.closed_at) AS last_close
  FROM r GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 6.2 forward Pinnacle-only candidates refused by the bind: measured-cost EV vs modelled vs settled counterfactual, per fixture'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
fe AS (
    SELECT DISTINCT ON (x.strategy, x.us_market_slug, x.holding_side) x.*
      FROM paper_profitability_evaluations x CROSS JOIN c
     WHERE x.evaluated_at >= c.t
       AND (x.detail -> 'attribution' ->> 'filled_qty')::numeric > 0
     ORDER BY x.strategy, x.us_market_slug, x.holding_side, x.evaluated_at, x.eval_id),
t AS (
    SELECT fe.eval_id, fe.strategy, fe.fixture,
           (fe.detail -> 'attribution' ->> 'filled_qty')::numeric AS q,
           coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'probability_edge_usd')::numeric, 0)
         + coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'settlement_difference_usd')::numeric, 0)
         + coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'spread_usd')::numeric, 0)
         + coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'slippage_usd')::numeric, 0)
         + coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'fees_usd')::numeric, 0) AS after_measured,
           coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'adverse_selection_usd')::numeric, 0)
         + coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'residual_haircut_usd')::numeric, 0)
         + coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'freshness_usd')::numeric, 0)
         + coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'management_usd')::numeric, 0)
         + coalesce((fe.detail -> 'attribution' -> 'terms' ->> 'calibration_adjustment_usd')::numeric, 0) AS modelled,
           fe.residual_haircut_per_contract AS haircut_pc
      FROM fe),
h AS (
    SELECT v.eval_id, o.counterfactual_pnl_usd AS hold_pnl, v.qty AS hold_qty
      FROM paper_counterfactual_variants v
      JOIN paper_counterfactual_variant_outcomes o ON o.variant_id = v.variant_id
     WHERE v.variant = 'POLICY_SIZE'),
fx AS (
    SELECT t.strategy, t.fixture, count(*) AS cs, sum(t.q) AS q,
           sum(t.after_measured) AS after_measured, sum(t.modelled) AS modelled,
           sum(h.hold_pnl) AS cf_policy_size_pnl, count(h.eval_id) AS cs_settled
      FROM t JOIN h ON h.eval_id = t.eval_id
     GROUP BY 1, 2)
SELECT fx.strategy, fx.fixture, fx.cs, round(fx.q, 1) AS contracts,
       round(fx.after_measured, 4) AS ev_after_measured_usd,
       round(fx.after_measured + fx.modelled, 4) AS ev_all_in_usd,
       round(fx.cf_policy_size_pnl, 4) AS cf_pnl_usd
  FROM fx ORDER BY 1, 2;

\echo '== 6.3 Derek, forward, policy-admitted decisions refused by the lifecycle: what the capital-eligibility evaluation said'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
d AS (
    SELECT x.decision_id, x.fixture, x.us_market_slug || ':' || coalesce(x.holding_side, '?') AS cs,
           x.p_internal, x.p_pinnacle, x.p_blended, x.limit_price, x.proposed_qty,
           (x.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric AS policy_net,
           (x.economics ->> 'executable_price')::numeric AS vwap,
           x.economics -> 'capital_eligibility' -> 'shadow_evaluation' AS se
      FROM paper_decisions x CROSS JOIN c
     WHERE x.decided_at >= c.t AND x.strategy = 'DEREK_ENTRY_POLICY_V2'
       AND x.refusal = 'STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY')
SELECT coalesce(d.se ->> 'capital_eligible', '(none)') AS capital_eligible,
       coalesce(d.se -> 'refusals' ->> 0, '(none)') AS ce_first_refusal,
       count(*) AS decisions, count(DISTINCT d.cs) AS contract_sides,
       count(DISTINCT d.fixture) AS fixtures,
       round(avg(d.p_internal)::numeric, 4) AS mean_p_internal,
       round(avg(d.p_pinnacle)::numeric, 4) AS mean_p_pinnacle,
       round(avg(d.p_blended)::numeric, 4) AS mean_p_blended,
       round(avg(d.vwap), 4) AS mean_vwap,
       round(avg(d.p_internal - d.p_pinnacle)::numeric, 4) AS mean_internal_minus_pinnacle,
       round(sum(d.policy_net), 2) AS policy_net_usd,
       round(sum((d.se ->> 'total_executable_ev_usd')::numeric), 2) AS ce_total_executable_ev_usd
  FROM d GROUP BY 1, 2 ORDER BY decisions DESC;
