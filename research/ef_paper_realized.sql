-- ECONOMIC FUNNEL (owner directive section 6), STEP 4: THE REALIZED PAPER
-- RECORD -- EVERY POSITION EVER FILLED -- PER MECHANISM AND FROZEN POLICY,
-- CLUSTERED BY FIXTURE, RECONCILED TO THE PAPER LEDGER.
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
-- HISTORICAL PAPER EVIDENCE IS IMMUTABLE: this reads paper_fills,
-- paper_settlements, paper_orders, paper_decisions and paper_ledger and
-- writes nothing. Every PAPER entry ever filled was decided BEFORE the
-- profitability bind's cutover (last ENTRY fill 2026-10-06 00:47:19Z;
-- migration 309 applied 2026-10-06 17:38:06Z), so all of it is the
-- historical cohort under the earlier policies -- shown separately from the
-- forward cohort (ef_forward_funnel.sql), never presented as prospective.
--
-- THE POSITION is bettor_paper_ledger.POSITIONS_SQL / _position_from (RC4
-- 9b94ef5c) restated in SQL: one row per (group, contract, holding side);
-- avg cost = (buy gross + buy fees) / bought; realized = sale proceeds net of
-- sale fees - avg x sold + (settlement payout - avg x settled qty); closed
-- when open qty <= 1e-9, at its settlement instant, else its last fill
-- (bettor_strategy_lifecycle.closed_at).
--
-- EVIDENCE CLASS (owner directive / PM 02:48Z): DEREK_ENTRY_POLICY_V2 is the
-- only strategy whose probability carries BETTOR's own model (the two-model
-- blend, derek_policy V2) -> PROPRIETARY_BETTOR. PINNACLE_COMPLETED_GAME_PAPER
-- decides on the de-vigged Pinnacle probability alone (paper_benchmark) and
-- is NOT proprietary, whatever its migration-223 sleeve label says
-- (INVESTMENT) -> BENCHMARK_PINNACLE_ONLY. PINNACLE_EXPLORATION_PAPER is the
-- owner's training strategy (negative EV is a research cost) -> TRAINING.
--
-- INDEPENDENT UNIT: the fixture (paper_fills.fixture, the canonical event
-- key). Every position of one strategy on one fixture is ONE cluster; the
-- portfolio view clusters across strategies. The one-sided 98% bound here
-- is the normal one (z = 2.0537); the report applies Student-t with
-- (clusters - 1) degrees of freedom to these same moments.

\echo '== 4.1 reconciliation: positions vs the PAPER ledger (cash identity)'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome,
           settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, o.qty AS order_qty,
           o.filled_qty AS entry_filled_qty, d.policy_version, d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net_at_proposed,
           coalesce(d.p_blended, d.p_pinnacle) AS p_entry
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
p AS (
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome, s.settled_at,
           coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0) AS buy_cost,
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought
                ELSE 0 END AS avg_cost,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           eo.policy_version, eo.entry_filled_qty, eo.proposed_qty,
           eo.exp_net_at_proposed, eo.p_entry
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
q AS (
    SELECT p.*,
           (coalesce(p.sale_gross, 0) - coalesce(p.sale_fees, 0)) - p.avg_cost * coalesce(p.sold, 0)
           + CASE WHEN coalesce(p.settled_qty, 0) > 0
                  THEN coalesce(p.payout_usd, 0) - p.avg_cost * p.settled_qty ELSE 0 END AS realized,
           CASE WHEN p.open_qty <= 1e-9 THEN coalesce(p.settled_at, p.last_fill_at) END AS closed_at,
           CASE WHEN p.proposed_qty > 0 AND p.exp_net_at_proposed IS NOT NULL
                THEN p.exp_net_at_proposed * p.entry_filled_qty / p.proposed_qty END AS expected_at_entry,
           CASE WHEN p.strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'PROPRIETARY_BETTOR'
                WHEN p.strategy = 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
                ELSE 'BENCHMARK_PINNACLE_ONLY' END AS evidence_class
      FROM p),
led AS (SELECT cash_after_usd, reserved_after_usd, seq, committed_at
          FROM paper_ledger WHERE account_id = 'paper_acct_main'
         ORDER BY seq DESC LIMIT 1),
st AS (SELECT starting_cash_usd FROM paper_accounts WHERE account_id = 'paper_acct_main')
SELECT count(*) AS positions,
       count(*) FILTER (WHERE q.closed_at IS NOT NULL) AS closed,
       count(*) FILTER (WHERE q.closed_at IS NULL) AS open,
       round(sum(q.realized), 6) AS realized_all_positions_usd,
       round(sum(q.avg_cost * q.open_qty) FILTER (WHERE q.open_qty > 1e-9), 6) AS open_cost_basis_usd,
       (SELECT cash_after_usd FROM led) AS ledger_cash_usd,
       (SELECT seq FROM led) AS ledger_seq,
       (SELECT committed_at FROM led) AS ledger_last_at,
       (SELECT starting_cash_usd FROM st) AS starting_cash_usd,
       round((SELECT cash_after_usd FROM led) - (SELECT starting_cash_usd FROM st)
             + coalesce(sum(q.avg_cost * q.open_qty) FILTER (WHERE q.open_qty > 1e-9), 0), 6)
           AS ledger_implied_realized_usd,
       round(sum(q.realized) - ((SELECT cash_after_usd FROM led) - (SELECT starting_cash_usd FROM st)
             + coalesce(sum(q.avg_cost * q.open_qty) FILTER (WHERE q.open_qty > 1e-9), 0)), 6)
           AS identity_residual_usd,
       count(*) FILTER (WHERE q.open_qty < -1e-9) AS oversold_positions
  FROM q;

\echo '== 4.2 ledger kinds vs fills / settlements (each leg of the cash identity)'
SELECT 'FILL (buys: gross + fees)' AS leg,
       (SELECT round(-sum(cash_delta_usd), 6) FROM paper_ledger
         WHERE account_id = 'paper_acct_main' AND kind = 'FILL') AS ledger_usd,
       (SELECT round(sum(gross_usd + fee_usd), 6) FROM paper_fills
         WHERE account_id = 'paper_acct_main' AND direction = 'BUY') AS records_usd
UNION ALL
SELECT 'SALE (sells: gross - fees)',
       (SELECT round(sum(cash_delta_usd), 6) FROM paper_ledger
         WHERE account_id = 'paper_acct_main' AND kind = 'SALE'),
       (SELECT round(sum(gross_usd - fee_usd), 6) FROM paper_fills
         WHERE account_id = 'paper_acct_main' AND direction = 'SELL')
UNION ALL
SELECT 'SETTLEMENT (payouts, latest version)',
       (SELECT round(sum(cash_delta_usd), 6) FROM paper_ledger
         WHERE account_id = 'paper_acct_main' AND kind = 'SETTLEMENT'),
       (SELECT round(sum(payout_usd), 6) FROM (
            SELECT DISTINCT ON (position_key) payout_usd FROM paper_settlements
             WHERE account_id = 'paper_acct_main'
             ORDER BY position_key, version DESC) z)
UNION ALL
SELECT 'CORRECTION', (SELECT coalesce(round(sum(cash_delta_usd), 6), 0) FROM paper_ledger
         WHERE account_id = 'paper_acct_main' AND kind = 'CORRECTION'), 0;

\echo '== 4.3 per evidence class x strategy x frozen policy: realized PAPER record (all positions)'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome,
           settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, o.qty AS order_qty,
           o.filled_qty AS entry_filled_qty, d.policy_version, d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net_at_proposed,
           coalesce(d.p_blended, d.p_pinnacle) AS p_entry
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
p AS (
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome, s.settled_at,
           coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0) AS buy_cost,
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought
                ELSE 0 END AS avg_cost,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           eo.policy_version, eo.entry_filled_qty, eo.proposed_qty,
           eo.exp_net_at_proposed, eo.p_entry
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
q AS (
    SELECT p.*,
           (coalesce(p.sale_gross, 0) - coalesce(p.sale_fees, 0)) - p.avg_cost * coalesce(p.sold, 0)
           + CASE WHEN coalesce(p.settled_qty, 0) > 0
                  THEN coalesce(p.payout_usd, 0) - p.avg_cost * p.settled_qty ELSE 0 END AS realized,
           CASE WHEN p.open_qty <= 1e-9 THEN coalesce(p.settled_at, p.last_fill_at) END AS closed_at,
           CASE WHEN p.proposed_qty > 0 AND p.exp_net_at_proposed IS NOT NULL
                THEN p.exp_net_at_proposed * p.entry_filled_qty / p.proposed_qty END AS expected_at_entry,
           CASE WHEN p.strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'PROPRIETARY_BETTOR'
                WHEN p.strategy = 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
                ELSE 'BENCHMARK_PINNACLE_ONLY' END AS evidence_class
      FROM p)
SELECT q.evidence_class, q.strategy, coalesce(q.policy_version, '(no entry decision)') AS policy_version,
       count(*) AS positions,
       count(*) FILTER (WHERE q.closed_at IS NOT NULL) AS closed,
       count(*) FILTER (WHERE q.closed_at IS NULL) AS open,
       count(DISTINCT q.fixture) AS fixtures,
       count(DISTINCT q.fixture) FILTER (WHERE q.closed_at IS NOT NULL) AS closed_fixtures,
       round(sum(q.realized), 2) AS realized_usd,
       round(sum(q.realized) FILTER (WHERE q.closed_at IS NOT NULL), 2) AS realized_closed_usd,
       round(sum(q.expected_at_entry) FILTER (WHERE q.closed_at IS NOT NULL), 2) AS expected_at_entry_closed_usd,
       round(sum(q.realized - q.expected_at_entry) FILTER (WHERE q.closed_at IS NOT NULL), 2) AS residual_closed_usd,
       count(*) FILTER (WHERE q.closed_at IS NOT NULL AND q.expected_at_entry IS NULL) AS closed_without_expected,
       round(sum(q.buy_cost), 2) AS bought_incl_fees_usd,
       round(sum(coalesce(q.buy_fees, 0) + coalesce(q.sale_fees, 0)), 2) AS fees_measured_usd,
       count(*) FILTER (WHERE q.settled_qty > 0) AS settled_positions,
       count(*) FILTER (WHERE q.closed_at IS NOT NULL AND coalesce(q.settled_qty, 0) = 0) AS closed_by_sale_only,
       count(*) FILTER (WHERE q.outcome = 'WON') AS won, count(*) FILTER (WHERE q.outcome = 'LOST') AS lost,
       count(*) FILTER (WHERE q.outcome = 'VOID_REFUND') AS void,
       min(q.first_fill_at) AS first_entry, max(q.first_fill_at) AS last_entry,
       max(q.closed_at) AS last_close
  FROM q GROUP BY ROLLUP (1, 2, 3)
 ORDER BY 1 NULLS FIRST, 2 NULLS FIRST, 3 NULLS FIRST;

\echo '== 4.4 per evidence class x strategy x frozen policy: CLOSED positions clustered by fixture (the independent unit)'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome,
           settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, o.qty AS order_qty,
           o.filled_qty AS entry_filled_qty, d.policy_version, d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net_at_proposed,
           coalesce(d.p_blended, d.p_pinnacle) AS p_entry
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
p AS (
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome, s.settled_at,
           coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0) AS buy_cost,
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought
                ELSE 0 END AS avg_cost,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           eo.policy_version, eo.entry_filled_qty, eo.proposed_qty,
           eo.exp_net_at_proposed, eo.p_entry
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
q AS (
    SELECT p.*,
           (coalesce(p.sale_gross, 0) - coalesce(p.sale_fees, 0)) - p.avg_cost * coalesce(p.sold, 0)
           + CASE WHEN coalesce(p.settled_qty, 0) > 0
                  THEN coalesce(p.payout_usd, 0) - p.avg_cost * p.settled_qty ELSE 0 END AS realized,
           CASE WHEN p.open_qty <= 1e-9 THEN coalesce(p.settled_at, p.last_fill_at) END AS closed_at,
           CASE WHEN p.proposed_qty > 0 AND p.exp_net_at_proposed IS NOT NULL
                THEN p.exp_net_at_proposed * p.entry_filled_qty / p.proposed_qty END AS expected_at_entry,
           CASE WHEN p.strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'PROPRIETARY_BETTOR'
                WHEN p.strategy = 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
                ELSE 'BENCHMARK_PINNACLE_ONLY' END AS evidence_class
      FROM p),
cl AS (
    SELECT q.evidence_class, q.strategy, coalesce(q.policy_version, '(none)') AS policy_version,
           q.fixture, count(*) AS positions, sum(q.realized) AS pnl,
           sum(q.expected_at_entry) AS exp, sum(q.buy_cost) AS cost,
           sum(q.buy_cost * extract(epoch FROM q.closed_at - q.first_fill_at) / 3600.0) AS capital_hours
      FROM q WHERE q.closed_at IS NOT NULL
     GROUP BY 1, 2, 3, 4)
SELECT cl.evidence_class, cl.strategy, cl.policy_version,
       count(*) AS clusters, sum(cl.positions) AS positions,
       round(sum(cl.pnl), 2) AS realized_usd,
       round(avg(cl.pnl), 4) AS mean_per_cluster,
       round(stddev_samp(cl.pnl), 4) AS sd_per_cluster,
       round(stddev_samp(cl.pnl) / sqrt(count(*))::numeric, 4) AS se,
       round(avg(cl.pnl) - 2.0537 * stddev_samp(cl.pnl) / sqrt(count(*))::numeric, 4) AS lb98_z,
       round(min(cl.pnl), 2) AS worst_cluster, round(max(cl.pnl), 2) AS best_cluster,
       count(*) FILTER (WHERE cl.pnl > 0) AS clusters_positive,
       round(sum(cl.exp), 2) AS expected_usd,
       round(sum(cl.pnl - coalesce(cl.exp, 0)), 2) AS residual_usd,
       round(avg(cl.pnl - coalesce(cl.exp, 0)), 4) AS mean_residual_per_cluster,
       round(sum(cl.cost), 2) AS capital_deployed_usd,
       round(sum(cl.capital_hours), 1) AS capital_hours,
       round(sum(cl.pnl) / nullif(sum(cl.capital_hours), 0), 6) AS usd_per_capital_hour
  FROM cl GROUP BY ROLLUP (1, 2, 3)
 ORDER BY 1 NULLS FIRST, 2 NULLS FIRST, 3 NULLS FIRST;

\echo '== 4.5 portfolio and per evidence class, clustered by fixture ACROSS strategies'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome,
           settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, o.qty AS order_qty,
           o.filled_qty AS entry_filled_qty, d.policy_version, d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net_at_proposed,
           coalesce(d.p_blended, d.p_pinnacle) AS p_entry
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
p AS (
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome, s.settled_at,
           coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0) AS buy_cost,
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought
                ELSE 0 END AS avg_cost,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           eo.policy_version, eo.entry_filled_qty, eo.proposed_qty,
           eo.exp_net_at_proposed, eo.p_entry
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
q AS (
    SELECT p.*,
           (coalesce(p.sale_gross, 0) - coalesce(p.sale_fees, 0)) - p.avg_cost * coalesce(p.sold, 0)
           + CASE WHEN coalesce(p.settled_qty, 0) > 0
                  THEN coalesce(p.payout_usd, 0) - p.avg_cost * p.settled_qty ELSE 0 END AS realized,
           CASE WHEN p.open_qty <= 1e-9 THEN coalesce(p.settled_at, p.last_fill_at) END AS closed_at,
           CASE WHEN p.proposed_qty > 0 AND p.exp_net_at_proposed IS NOT NULL
                THEN p.exp_net_at_proposed * p.entry_filled_qty / p.proposed_qty END AS expected_at_entry,
           CASE WHEN p.strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'PROPRIETARY_BETTOR'
                WHEN p.strategy = 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
                ELSE 'BENCHMARK_PINNACLE_ONLY' END AS evidence_class
      FROM p),
cl AS (
    SELECT q.fixture, sum(q.realized) AS pnl
      FROM q WHERE q.closed_at IS NOT NULL GROUP BY 1),
cc AS (
    SELECT q.evidence_class, q.fixture, sum(q.realized) AS pnl
      FROM q WHERE q.closed_at IS NOT NULL GROUP BY 1, 2)
SELECT 'ALL_STRATEGIES' AS scope, count(*) AS clusters, round(sum(cl.pnl), 2) AS realized_usd,
       round(avg(cl.pnl), 4) AS mean_per_cluster, round(stddev_samp(cl.pnl), 4) AS sd,
       round(avg(cl.pnl) - 2.0537 * stddev_samp(cl.pnl) / sqrt(count(*))::numeric, 4) AS lb98_z
  FROM cl
UNION ALL
SELECT 'NON_TRAINING (proprietary + benchmark)', count(*), round(sum(pnl), 2), round(avg(pnl), 4),
       round(stddev_samp(pnl), 4),
       round(avg(pnl) - 2.0537 * stddev_samp(pnl) / sqrt(count(*))::numeric, 4)
  FROM (SELECT fixture, sum(pnl) AS pnl FROM cc WHERE evidence_class <> 'TRAINING' GROUP BY 1) z;

\echo '== 4.6 drawdown and daily realized per strategy (closed positions in close order)'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome,
           settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, o.qty AS order_qty,
           o.filled_qty AS entry_filled_qty, d.policy_version, d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net_at_proposed,
           coalesce(d.p_blended, d.p_pinnacle) AS p_entry
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
p AS (
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome, s.settled_at,
           coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0) AS buy_cost,
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought
                ELSE 0 END AS avg_cost,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           eo.policy_version, eo.entry_filled_qty, eo.proposed_qty,
           eo.exp_net_at_proposed, eo.p_entry
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
q AS (
    SELECT p.*,
           (coalesce(p.sale_gross, 0) - coalesce(p.sale_fees, 0)) - p.avg_cost * coalesce(p.sold, 0)
           + CASE WHEN coalesce(p.settled_qty, 0) > 0
                  THEN coalesce(p.payout_usd, 0) - p.avg_cost * p.settled_qty ELSE 0 END AS realized,
           CASE WHEN p.open_qty <= 1e-9 THEN coalesce(p.settled_at, p.last_fill_at) END AS closed_at,
           CASE WHEN p.proposed_qty > 0 AND p.exp_net_at_proposed IS NOT NULL
                THEN p.exp_net_at_proposed * p.entry_filled_qty / p.proposed_qty END AS expected_at_entry,
           CASE WHEN p.strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'PROPRIETARY_BETTOR'
                WHEN p.strategy = 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
                ELSE 'BENCHMARK_PINNACLE_ONLY' END AS evidence_class
      FROM p),
o AS (
    SELECT q.strategy, q.closed_at, q.realized,
           sum(q.realized) OVER (PARTITION BY q.strategy ORDER BY q.closed_at, q.group_id
                                 ROWS UNBOUNDED PRECEDING) AS cum
      FROM q WHERE q.closed_at IS NOT NULL),
dd AS (
    SELECT o.*, greatest(0, max(o.cum) OVER (PARTITION BY o.strategy ORDER BY o.closed_at
                                             ROWS UNBOUNDED PRECEDING)) AS peak
      FROM o)
SELECT dd.strategy, round(max(dd.peak - dd.cum), 2) AS max_drawdown_usd,
       round(max(dd.peak), 2) AS peak_cum_usd, round(min(dd.cum), 2) AS trough_cum_usd,
       round((array_agg(dd.cum ORDER BY dd.closed_at DESC))[1], 2) AS final_cum_usd
  FROM dd GROUP BY ROLLUP (1) ORDER BY 1 NULLS FIRST;

\echo '== 4.7 daily realized per strategy (UTC close date)'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome,
           settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, o.qty AS order_qty,
           o.filled_qty AS entry_filled_qty, d.policy_version, d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net_at_proposed,
           coalesce(d.p_blended, d.p_pinnacle) AS p_entry
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
p AS (
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome, s.settled_at,
           coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0) AS buy_cost,
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought
                ELSE 0 END AS avg_cost,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           eo.policy_version, eo.entry_filled_qty, eo.proposed_qty,
           eo.exp_net_at_proposed, eo.p_entry
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
q AS (
    SELECT p.*,
           (coalesce(p.sale_gross, 0) - coalesce(p.sale_fees, 0)) - p.avg_cost * coalesce(p.sold, 0)
           + CASE WHEN coalesce(p.settled_qty, 0) > 0
                  THEN coalesce(p.payout_usd, 0) - p.avg_cost * p.settled_qty ELSE 0 END AS realized,
           CASE WHEN p.open_qty <= 1e-9 THEN coalesce(p.settled_at, p.last_fill_at) END AS closed_at,
           CASE WHEN p.proposed_qty > 0 AND p.exp_net_at_proposed IS NOT NULL
                THEN p.exp_net_at_proposed * p.entry_filled_qty / p.proposed_qty END AS expected_at_entry,
           CASE WHEN p.strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'PROPRIETARY_BETTOR'
                WHEN p.strategy = 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
                ELSE 'BENCHMARK_PINNACLE_ONLY' END AS evidence_class
      FROM p)
SELECT q.strategy, (q.closed_at AT TIME ZONE 'UTC')::date AS close_day,
       count(*) AS positions, count(DISTINCT q.fixture) AS fixtures,
       round(sum(q.realized), 2) AS realized_usd
  FROM q WHERE q.closed_at IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 4.8 measured execution: entry fill rate, partial fills, churn (re-entry on the same contract-side)'
WITH eo AS (
    SELECT o.strategy, o.group_id, o.us_market_slug, o.holding_side, o.qty, o.filled_qty,
           o.state, o.order_type, o.time_in_force
      FROM paper_orders o
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY' AND o.direction = 'BUY')
SELECT eo.strategy, count(*) AS entry_orders,
       count(*) FILTER (WHERE eo.filled_qty > 0) AS filled_any,
       count(*) FILTER (WHERE eo.filled_qty > 0 AND eo.filled_qty < eo.qty) AS partially_filled,
       count(*) FILTER (WHERE eo.filled_qty = 0) AS unfilled,
       round(sum(eo.filled_qty) / nullif(sum(eo.qty), 0), 4) AS qty_fill_rate,
       count(DISTINCT eo.us_market_slug || eo.holding_side) FILTER (WHERE eo.filled_qty > 0) AS contract_sides_filled,
       count(*) FILTER (WHERE eo.filled_qty > 0)
         - count(DISTINCT eo.us_market_slug || eo.holding_side) FILTER (WHERE eo.filled_qty > 0) AS reentries_same_contract_side,
       string_agg(DISTINCT eo.order_type || '/' || eo.time_in_force, ', ') AS styles
  FROM eo GROUP BY 1 ORDER BY 1;

\echo '== 4.9 ECONOMIC_DUPLICATE_SUSPECT fills (same order, price, qty and instant on distinct book observations): their realized effect'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome,
           settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, o.qty AS order_qty,
           o.filled_qty AS entry_filled_qty, d.policy_version, d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net_at_proposed,
           coalesce(d.p_blended, d.p_pinnacle) AS p_entry
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
p AS (
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome, s.settled_at,
           coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0) AS buy_cost,
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought
                ELSE 0 END AS avg_cost,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           eo.policy_version, eo.entry_filled_qty, eo.proposed_qty,
           eo.exp_net_at_proposed, eo.p_entry
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
q AS (
    SELECT p.*,
           (coalesce(p.sale_gross, 0) - coalesce(p.sale_fees, 0)) - p.avg_cost * coalesce(p.sold, 0)
           + CASE WHEN coalesce(p.settled_qty, 0) > 0
                  THEN coalesce(p.payout_usd, 0) - p.avg_cost * p.settled_qty ELSE 0 END AS realized,
           CASE WHEN p.open_qty <= 1e-9 THEN coalesce(p.settled_at, p.last_fill_at) END AS closed_at,
           CASE WHEN p.proposed_qty > 0 AND p.exp_net_at_proposed IS NOT NULL
                THEN p.exp_net_at_proposed * p.entry_filled_qty / p.proposed_qty END AS expected_at_entry,
           CASE WHEN p.strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'PROPRIETARY_BETTOR'
                WHEN p.strategy = 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
                ELSE 'BENCHMARK_PINNACLE_ONLY' END AS evidence_class
      FROM p),
dup AS (
    SELECT fl.order_id, fl.group_id, fl.us_market_slug, fl.holding_side, fl.direction,
           fl.price, fl.qty, fl.filled_at, count(*) AS n, sum(fl.fee_usd) AS fees,
           max(fl.fee_usd) AS fee_one
      FROM paper_fills fl WHERE fl.account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3, 4, 5, 6, 7, 8 HAVING count(*) > 1)
SELECT q.strategy, count(*) AS suspect_groups, sum(dup.n - 1) AS extra_fills,
       round(sum((dup.n - 1) * dup.qty), 2) AS extra_qty,
       round(sum(CASE WHEN dup.direction = 'SELL'
                      THEN (dup.n - 1) * (dup.qty * (dup.price - q.avg_cost) - dup.fee_one)
                      ELSE 0 END), 2) AS extra_sell_realized_effect_usd,
       count(*) FILTER (WHERE dup.direction = 'BUY') AS buy_suspects,
       count(DISTINCT q.fixture) AS fixtures
  FROM dup JOIN q ON q.group_id = dup.group_id AND q.us_market_slug = dup.us_market_slug
                 AND q.holding_side = dup.holding_side
 GROUP BY ROLLUP (1) ORDER BY 1 NULLS FIRST;

\echo '== 4.10 open positions now (marks are not read here: unrealized is the ledger read model''s, not this file''s)'
WITH f AS (
    SELECT group_id, us_market_slug, holding_side,
           max(fixture) AS fixture, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction = 'BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction = 'BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction = 'SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction = 'SELL') AS sale_fees,
           count(*) FILTER (WHERE direction = 'SELL') AS sell_fills,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY 1, 2, 3),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome,
           settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
eo AS (
    SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, o.qty AS order_qty,
           o.filled_qty AS entry_filled_qty, d.policy_version, d.proposed_qty,
           coalesce((d.economics -> 'headline' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'acquisition' ->> 'expected_net_profit_usd')::numeric,
                    (d.economics -> 'estimate' ->> 'expected_net_profit_usd')::numeric) AS exp_net_at_proposed,
           coalesce(d.p_blended, d.p_pinnacle) AS p_entry
      FROM paper_orders o
      LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
     WHERE o.account_id = 'paper_acct_main' AND o.role = 'ENTRY'
       AND o.direction = 'BUY' AND o.filled_qty > 0
     ORDER BY o.group_id, o.created_at),
p AS (
    SELECT f.*, s.qty AS settled_qty, s.payout_usd, s.outcome, s.settled_at,
           coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0) AS buy_cost,
           CASE WHEN coalesce(f.bought, 0) > 0
                THEN (coalesce(f.buy_gross, 0) + coalesce(f.buy_fees, 0)) / f.bought
                ELSE 0 END AS avg_cost,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty,
           eo.policy_version, eo.entry_filled_qty, eo.proposed_qty,
           eo.exp_net_at_proposed, eo.p_entry
      FROM f
      LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:' || f.group_id
                                       || ':' || f.us_market_slug || ':' || f.holding_side
      LEFT JOIN eo ON eo.group_id = f.group_id),
q AS (
    SELECT p.*,
           (coalesce(p.sale_gross, 0) - coalesce(p.sale_fees, 0)) - p.avg_cost * coalesce(p.sold, 0)
           + CASE WHEN coalesce(p.settled_qty, 0) > 0
                  THEN coalesce(p.payout_usd, 0) - p.avg_cost * p.settled_qty ELSE 0 END AS realized,
           CASE WHEN p.open_qty <= 1e-9 THEN coalesce(p.settled_at, p.last_fill_at) END AS closed_at,
           CASE WHEN p.proposed_qty > 0 AND p.exp_net_at_proposed IS NOT NULL
                THEN p.exp_net_at_proposed * p.entry_filled_qty / p.proposed_qty END AS expected_at_entry,
           CASE WHEN p.strategy = 'DEREK_ENTRY_POLICY_V2' THEN 'PROPRIETARY_BETTOR'
                WHEN p.strategy = 'PINNACLE_EXPLORATION_PAPER' THEN 'TRAINING'
                ELSE 'BENCHMARK_PINNACLE_ONLY' END AS evidence_class
      FROM p)
SELECT q.strategy, q.us_market_slug, q.holding_side, q.fixture, round(q.open_qty, 4) AS open_qty,
       round(q.avg_cost * q.open_qty, 4) AS cost_basis_usd, round(q.realized, 4) AS realized_so_far_usd,
       q.first_fill_at, q.p_entry
  FROM q WHERE q.closed_at IS NULL ORDER BY q.strategy, q.first_fill_at;
