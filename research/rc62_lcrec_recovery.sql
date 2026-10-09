-- READ-ONLY. RC6.2 lifecycle recovery audit (PAPER_TURNAROUND_RULES_V1).
-- For each of the five paper strategies on paper_acct_main: the lifecycle
-- event log, the rolling-window figures the predeclared stopping rules read
-- (bettor_strategy_lifecycle.metrics / rules_firing), the forward evidence
-- the upward steps read (forward_since = max(RULES_DECLARED_AT, state event)),
-- the ledger capital authority's forward-economics verdict (fixed anchor
-- FORWARD_SINCE = 2026-10-05T04:00Z, paper + settled filled shadows), the
-- three profitability-stack quarantine triggers (calibration, residual,
-- execution), the roll-off projection of the 14-day window with no new
-- closes, and the decision / refusal census behind the 708 ENTER refusals.
-- Every statement is a SELECT. Nothing here changes a rule, a threshold, a
-- state or a historical loss.

\echo L0 read instant and lifecycle rules identity in the event log
SELECT now() AS read_at, extract(epoch FROM now())::float8 AS now_epoch;
SELECT rules_version, rules_sha, count(*) AS events
  FROM paper_strategy_lifecycle_events GROUP BY 1, 2;

\echo L1 the full lifecycle event log (every account), oldest first
SELECT event_id, account_id, strategy, from_state, to_state, rule_id, actor,
       recorded_at,
       evidence->'rolling'->>'closed_positions' AS roll_n,
       evidence->'rolling'->>'realized_pnl_usd' AS roll_pnl,
       evidence->'rolling'->>'max_drawdown_usd' AS roll_mdd,
       evidence->'forward'->>'closed_positions' AS fwd_n,
       evidence->'forward'->>'realized_pnl_usd' AS fwd_pnl
  FROM paper_strategy_lifecycle_events ORDER BY event_id;

\echo L2 the profitability-stack quarantine event evidence (triggers as recorded)
SELECT event_id, strategy, rule_id, recorded_at,
       left((evidence->'triggers')::text, 1500) AS triggers
  FROM paper_strategy_lifecycle_events
 WHERE rule_id LIKE 'PROFITABILITY_%' ORDER BY event_id;

\echo P1 per strategy NOW: positions, open book, rolling 14 d, lifecycle forward sample, capital-authority forward paper sample
WITH f AS (
    SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction='BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction='BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction='BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction='SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction='SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction='SELL') AS sale_fees,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY group_id, us_market_slug, holding_side),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
q AS (
    SELECT coalesce(f.strategy, 'DEREK_ENTRY_POLICY_V2') AS strat,
           f.first_fill_at, f.last_fill_at, s.settled_at,
           coalesce(f.buy_gross,0) + coalesce(f.buy_fees,0) AS cost,
           CASE WHEN coalesce(f.bought,0) > 0
                THEN (coalesce(f.buy_gross,0) + coalesce(f.buy_fees,0)) / f.bought
                ELSE 0 END AS avgc,
           coalesce(f.bought,0) - coalesce(f.sold,0) - coalesce(s.qty,0) AS open_qty,
           coalesce(f.sale_gross,0) - coalesce(f.sale_fees,0) AS proceeds,
           coalesce(f.sold,0) AS sold_q, coalesce(s.qty,0) AS settled_q,
           coalesce(s.payout_usd,0) AS payout
      FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:'
           || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side),
pos AS (
    SELECT q.*,
           (proceeds - avgc * sold_q) + CASE WHEN settled_q > 0
               THEN payout - avgc * settled_q ELSE 0 END AS realized,
           CASE WHEN open_qty > 1e-9 THEN NULL
                ELSE coalesce(settled_at, last_fill_at) END AS closed_at,
           avgc * open_qty AS cost_basis
      FROM q),
cur AS (
    SELECT DISTINCT ON (strategy) strategy, to_state, rule_id, recorded_at
      FROM paper_strategy_lifecycle_events WHERE account_id = 'paper_acct_main'
     ORDER BY strategy, event_id DESC),
names AS (
    SELECT unnest(ARRAY['DEREK_ENTRY_POLICY_V2','PINNACLE_ONLY_PAPER_BENCHMARK',
                        'PINNACLE_COMPLETED_GAME_PAPER',
                        'PINNACLE_COMPLETED_GAME_MAKER_PAPER',
                        'PINNACLE_EXPLORATION_PAPER']) AS strat
    UNION SELECT DISTINCT strat FROM pos)
SELECT n.strat,
       coalesce(cur.to_state, 'ACTIVE_CHALLENGER(no row)') AS state,
       cur.rule_id AS state_rule, cur.recorded_at AS state_since,
       count(p.*) AS positions,
       count(p.*) FILTER (WHERE p.closed_at IS NULL) AS open_n,
       round(coalesce(sum(p.cost_basis) FILTER (WHERE p.closed_at IS NULL),0),2) AS open_cost_basis,
       min(p.first_fill_at) FILTER (WHERE p.closed_at IS NULL) AS oldest_open_fill,
       count(p.*) FILTER (WHERE p.closed_at IS NOT NULL) AS closed_all,
       round(sum(p.realized) FILTER (WHERE p.closed_at IS NOT NULL),2) AS realized_all,
       count(p.*) FILTER (WHERE p.closed_at >= now() - interval '14 days'
                          AND p.closed_at <= now()) AS roll_n,
       round(sum(p.realized) FILTER (WHERE p.closed_at >= now() - interval '14 days'
                                     AND p.closed_at <= now()),2) AS roll_pnl,
       count(p.*) FILTER (WHERE p.closed_at IS NOT NULL
             AND p.first_fill_at >= greatest(to_timestamp(1791158400),
                                             coalesce(cur.recorded_at, to_timestamp(0)))) AS lc_fwd_closed,
       round(coalesce(sum(p.realized) FILTER (WHERE p.closed_at IS NOT NULL
             AND p.first_fill_at >= greatest(to_timestamp(1791158400),
                                             coalesce(cur.recorded_at, to_timestamp(0)))),0),2) AS lc_fwd_pnl,
       count(p.*) FILTER (WHERE p.first_fill_at >= coalesce(cur.recorded_at, to_timestamp(0))) AS filled_since_state,
       count(p.*) FILTER (WHERE p.closed_at IS NOT NULL
             AND p.first_fill_at >= to_timestamp(1791172800)) AS ca_fwd_paper_n,
       round(coalesce(sum(p.realized) FILTER (WHERE p.closed_at IS NOT NULL
             AND p.first_fill_at >= to_timestamp(1791172800)),0),2) AS ca_fwd_paper_net,
       max(p.first_fill_at) AS newest_first_fill, max(p.closed_at) AS newest_close
  FROM names n LEFT JOIN pos p ON p.strat = n.strat
  LEFT JOIN cur ON cur.strategy = n.strat
 GROUP BY n.strat, cur.to_state, cur.rule_id, cur.recorded_at ORDER BY 1;

\echo P2 rolling 14 d figures at now + k days with NO new closes (k = 0 is now): n, P&L, max drawdown, CI95 high, drawdown rate
WITH f AS (
    SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction='BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction='BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction='BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction='SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction='SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction='SELL') AS sale_fees,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY group_id, us_market_slug, holding_side),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
q AS (
    SELECT coalesce(f.strategy, 'DEREK_ENTRY_POLICY_V2') AS strat,
           f.group_id, f.us_market_slug, f.holding_side,
           f.first_fill_at, f.last_fill_at, s.settled_at,
           coalesce(f.buy_gross,0) + coalesce(f.buy_fees,0) AS cost,
           CASE WHEN coalesce(f.bought,0) > 0
                THEN (coalesce(f.buy_gross,0) + coalesce(f.buy_fees,0)) / f.bought
                ELSE 0 END AS avgc,
           coalesce(f.bought,0) - coalesce(f.sold,0) - coalesce(s.qty,0) AS open_qty,
           coalesce(f.sale_gross,0) - coalesce(f.sale_fees,0) AS proceeds,
           coalesce(f.sold,0) AS sold_q, coalesce(s.qty,0) AS settled_q,
           coalesce(s.payout_usd,0) AS payout
      FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:'
           || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side),
pos AS (
    SELECT q.*,
           (proceeds - avgc * sold_q) + CASE WHEN settled_q > 0
               THEN payout - avgc * settled_q ELSE 0 END AS realized,
           CASE WHEN open_qty > 1e-9 THEN NULL
                ELSE coalesce(settled_at, last_fill_at) END AS closed_at
      FROM q),
days AS (SELECT k, now() + make_interval(days => k) AS d
           FROM generate_series(0, 15) k),
w AS (SELECT days.k, days.d, pos.strat, pos.closed_at, pos.realized, pos.cost,
             pos.group_id || pos.us_market_slug || pos.holding_side AS pk
        FROM days JOIN pos ON pos.closed_at IS NOT NULL
         AND pos.closed_at >= days.d - interval '14 days'
         AND pos.closed_at <= now()
       WHERE pos.strat IN ('DEREK_ENTRY_POLICY_V2','PINNACLE_COMPLETED_GAME_PAPER',
                           'PINNACLE_EXPLORATION_PAPER')),
c AS (SELECT w.*, sum(realized) OVER (PARTITION BY k, strat ORDER BY closed_at, pk
                                      ROWS UNBOUNDED PRECEDING) AS cum
        FROM w),
pk2 AS (SELECT c.*, greatest(0, max(cum) OVER (PARTITION BY k, strat ORDER BY closed_at, pk
                                               ROWS UNBOUNDED PRECEDING)) AS peak
          FROM c)
SELECT strat, k, to_char(d, 'YYYY-MM-DD HH24:MI') AS at_utc, count(*) AS n,
       round(sum(realized),2) AS pnl, round(max(peak - cum),2) AS mdd,
       round(sum(cost),2) AS cost,
       round((avg(realized) + 1.96 * stddev_samp(realized) / sqrt(count(*)))::numeric,4) AS ci95_hi,
       round(max(peak - cum) / nullif(sum(cost),0),5) AS dd_rate,
       min(closed_at) AS oldest_close_in_window
  FROM pk2 GROUP BY strat, k, d ORDER BY strat, k;

\echo P3 closed P&L by close day (UTC) since 2026-09-25, quarantined strategies (what rolls out of the window, and when)
WITH f AS (
    SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction='BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction='BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction='BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction='SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction='SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction='SELL') AS sale_fees,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY group_id, us_market_slug, holding_side),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
q AS (
    SELECT coalesce(f.strategy, 'DEREK_ENTRY_POLICY_V2') AS strat,
           f.first_fill_at, f.last_fill_at, s.settled_at,
           CASE WHEN coalesce(f.bought,0) > 0
                THEN (coalesce(f.buy_gross,0) + coalesce(f.buy_fees,0)) / f.bought
                ELSE 0 END AS avgc,
           coalesce(f.bought,0) - coalesce(f.sold,0) - coalesce(s.qty,0) AS open_qty,
           coalesce(f.sale_gross,0) - coalesce(f.sale_fees,0) AS proceeds,
           coalesce(f.sold,0) AS sold_q, coalesce(s.qty,0) AS settled_q,
           coalesce(s.payout_usd,0) AS payout
      FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:'
           || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side),
pos AS (
    SELECT q.*,
           (proceeds - avgc * sold_q) + CASE WHEN settled_q > 0
               THEN payout - avgc * settled_q ELSE 0 END AS realized,
           CASE WHEN open_qty > 1e-9 THEN NULL
                ELSE coalesce(settled_at, last_fill_at) END AS closed_at
      FROM q)
SELECT strat, date_trunc('day', closed_at) AS close_day, count(*) AS n,
       round(sum(realized),2) AS pnl,
       count(*) FILTER (WHERE first_fill_at >= to_timestamp(1791172800)) AS n_fwd_anchor,
       round(sum(realized) FILTER (WHERE first_fill_at >= to_timestamp(1791172800)),2) AS pnl_fwd_anchor
  FROM pos
 WHERE closed_at >= '2026-09-25' AND strat IN ('DEREK_ENTRY_POLICY_V2',
       'PINNACLE_COMPLETED_GAME_PAPER','PINNACLE_EXPLORATION_PAPER')
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo F1 the capital authority forward-economics verdict NOW per strategy (FORWARD_SINCE 2026-10-05T04:00Z; paper closed + settled filled shadows) and the absolute-positive champion CI
WITH f AS (
    SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction='BUY') AS bought,
           sum(gross_usd) FILTER (WHERE direction='BUY') AS buy_gross,
           sum(fee_usd) FILTER (WHERE direction='BUY') AS buy_fees,
           sum(qty) FILTER (WHERE direction='SELL') AS sold,
           sum(gross_usd) FILTER (WHERE direction='SELL') AS sale_gross,
           sum(fee_usd) FILTER (WHERE direction='SELL') AS sale_fees,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY group_id, us_market_slug, holding_side),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
q AS (
    SELECT coalesce(f.strategy, 'DEREK_ENTRY_POLICY_V2') AS strat,
           f.first_fill_at, f.last_fill_at, s.settled_at,
           CASE WHEN coalesce(f.bought,0) > 0
                THEN (coalesce(f.buy_gross,0) + coalesce(f.buy_fees,0)) / f.bought
                ELSE 0 END AS avgc,
           coalesce(f.bought,0) - coalesce(f.sold,0) - coalesce(s.qty,0) AS open_qty,
           coalesce(f.sale_gross,0) - coalesce(f.sale_fees,0) AS proceeds,
           coalesce(f.sold,0) AS sold_q, coalesce(s.qty,0) AS settled_q,
           coalesce(s.payout_usd,0) AS payout
      FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:'
           || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side),
pos AS (
    SELECT q.*,
           (proceeds - avgc * sold_q) + CASE WHEN settled_q > 0
               THEN payout - avgc * settled_q ELSE 0 END AS realized,
           CASE WHEN open_qty > 1e-9 THEN NULL
                ELSE coalesce(settled_at, last_fill_at) END AS closed_at
      FROM q),
pp AS (SELECT strat, realized::float8 AS x, 'P' AS src FROM pos
        WHERE closed_at IS NOT NULL AND first_fill_at >= to_timestamp(1791172800)),
sp AS (SELECT sc.strategy AS strat, o.counterfactual_pnl_usd::float8 AS x, 'S' AS src
         FROM paper_shadow_counterfactuals sc
         JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
        WHERE sc.account_id = 'paper_acct_main'
          AND sc.decided_at >= to_timestamp(1791172800)
          AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND') AND o.filled_qty > 0),
al AS (SELECT * FROM pp UNION ALL SELECT * FROM sp)
SELECT strat,
       count(*) FILTER (WHERE src='P') AS n_paper,
       round(coalesce(sum(x) FILTER (WHERE src='P'),0)::numeric,2) AS net_paper,
       count(*) FILTER (WHERE src='S') AS n_shadow,
       round(coalesce(sum(x) FILTER (WHERE src='S'),0)::numeric,2) AS net_shadow,
       count(*) AS n, round(sum(x)::numeric,2) AS net,
       round(avg(x)::numeric,4) AS mean,
       round((avg(x) - 1.96 * stddev_samp(x) / sqrt(count(*)))::numeric,4) AS ci95_lo,
       CASE WHEN count(*) < 20 THEN 'UNKNOWN'
            WHEN sum(x) <= 0 THEN 'NEGATIVE'
            WHEN count(*) FILTER (WHERE src='P') >= 20
                 AND sum(x) FILTER (WHERE src='P') < 0 THEN 'NEGATIVE(paper n>=20 net<0)'
            ELSE 'POSITIVE' END AS forward_verdict
  FROM al GROUP BY strat ORDER BY strat;

\echo F2 shadow counterfactuals per strategy per day since FORWARD_SINCE: recorded, by source, settled filled, P&L, still pending
SELECT sc.strategy, date_trunc('day', sc.decided_at) AS day, count(*) AS recorded,
       count(*) FILTER (WHERE sc.source = 'DECISION_CAPITAL_GATE') AS from_decision,
       count(*) FILTER (WHERE sc.source = 'LEDGER_CAPITAL_AUTHORITY') AS from_ledger,
       count(o.*) AS with_outcome,
       count(o.*) FILTER (WHERE o.outcome NOT IN ('NO_FILL','VOID_REFUND') AND o.filled_qty > 0) AS settled_filled,
       round(coalesce(sum(o.counterfactual_pnl_usd) FILTER (
             WHERE o.outcome NOT IN ('NO_FILL','VOID_REFUND') AND o.filled_qty > 0),0),2) AS pnl,
       count(*) FILTER (WHERE o.shadow_id IS NULL) AS pending
  FROM paper_shadow_counterfactuals sc
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE sc.account_id = 'paper_acct_main' AND sc.decided_at >= to_timestamp(1791172800)
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo F3 shadow capital refusals and lifecycle state recorded on the shadows, last 72 h
SELECT strategy, source, capital_refusal, lifecycle_state, count(*) AS n,
       min(decided_at) AS first, max(decided_at) AS last
  FROM paper_shadow_counterfactuals
 WHERE account_id = 'paper_acct_main' AND decided_at >= now() - interval '72 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;

\echo Q1 stack trigger RESIDUAL: the newest RESIDUAL model, per-strategy cells (fires at n >= 30 and residual_per_contract <= -0.05)
WITH m AS (
    SELECT DISTINCT ON (kind) kind, model_id, fitted_at,
           CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb
                ELSE payload END AS pl
      FROM paper_profitability_models WHERE account_id = 'paper_acct_main'
     ORDER BY kind, fitted_at DESC, model_id DESC)
SELECT m.model_id, m.fitted_at, c.key AS cell,
       c.value->>'n' AS n, c.value->>'paper' AS paper, c.value->>'shadow' AS shadow,
       c.value->>'expected_ev_usd' AS expected, c.value->>'realized_pnl_usd' AS realized,
       c.value->>'residual_usd' AS residual_usd,
       c.value->>'residual_per_contract' AS residual_per_contract
  FROM m, jsonb_each(m.pl->'cells') c
 WHERE m.kind = 'RESIDUAL' AND c.key LIKE '%|*|*' ORDER BY 3;

\echo Q2 the RESIDUAL per-strategy cell over time (the newest fit of each 6 h bucket since 2026-10-05)
WITH m AS (
    SELECT DISTINCT ON (date_bin('6 hours', fitted_at, '2026-10-05'))
           date_bin('6 hours', fitted_at, '2026-10-05') AS bucket, model_id, fitted_at,
           CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb
                ELSE payload END AS pl
      FROM paper_profitability_models
     WHERE account_id = 'paper_acct_main' AND kind = 'RESIDUAL'
       AND fitted_at >= '2026-10-05'
     ORDER BY date_bin('6 hours', fitted_at, '2026-10-05'), fitted_at DESC, model_id DESC)
SELECT m.bucket, c.key AS cell, c.value->>'n' AS n, c.value->>'paper' AS paper,
       c.value->>'shadow' AS shadow, c.value->>'residual_usd' AS residual_usd,
       c.value->>'residual_per_contract' AS rpc
  FROM m, jsonb_each(m.pl->'cells') c
 WHERE c.key LIKE '%|*|*' ORDER BY c.key, m.bucket;

\echo Q3 stack trigger EXECUTION: the newest EXECUTION model rows (fires at fills >= 20 and raw markout >= 0.05, or terminal orders >= 30 and raw fill rate < 0.05)
WITH m AS (
    SELECT DISTINCT ON (kind) kind, model_id, fitted_at,
           CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb
                ELSE payload END AS pl
      FROM paper_profitability_models WHERE account_id = 'paper_acct_main'
     ORDER BY kind, fitted_at DESC, model_id DESC)
SELECT m.model_id, m.fitted_at, r.key, r.value->>'fills' AS fills,
       r.value->>'markout_per_contract_raw' AS markout_raw,
       r.value->>'terminal_orders' AS terminal_orders,
       r.value->>'fill_rate_raw' AS fill_rate_raw
  FROM m, jsonb_each(m.pl->'by_strategy_style') r
 WHERE m.kind = 'EXECUTION' ORDER BY r.key;

\echo Q4 stack trigger CALIBRATION, replicated: distinct contract-sides evaluated in 120 d with an authoritative WON/LOST (fires at n >= 50 and Brier - market Brier > 0.02 or ECE > 0.15)
WITH ev AS (
    SELECT DISTINCT ON (strategy, us_market_slug, holding_side)
           strategy, us_market_slug, holding_side, p_used::float8 AS p,
           market_price::float8 AS mkt
      FROM paper_profitability_evaluations
     WHERE account_id = 'paper_acct_main' AND p_used IS NOT NULL
       AND us_market_slug IS NOT NULL AND holding_side IS NOT NULL
       AND evaluated_at >= now() - interval '120 days'
     ORDER BY strategy, us_market_slug, holding_side, evaluated_at DESC, eval_id DESC),
sl AS (SELECT DISTINCT us_market_slug FROM ev),
xv AS (
    SELECT x.us_market_slug, x.buy_intent, x.outcome, x.outcome_known, x.outcome_basis
      FROM external_valuations x JOIN sl USING (us_market_slug)
     WHERE x.outcome_basis IS NOT NULL),
sides AS (SELECT unnest(ARRAY['LONG','SHORT']) AS side),
oc AS (
    SELECT xv.us_market_slug, sides.side,
           array_agg(DISTINCT CASE
               WHEN xv.outcome_basis = 'CONFIRMED_VOID' THEN 'VOID_REFUND'
               WHEN xv.outcome_basis IN ('VENUE_SETTLEMENT_PRICE','VENUE_REPORTED_OUTCOME')
                    AND xv.outcome_known AND xv.outcome IN (0, 1)
               THEN CASE WHEN (xv.outcome = 1) = (xv.buy_intent = CASE sides.side
                         WHEN 'LONG' THEN 'ORDER_INTENT_BUY_LONG'
                         ELSE 'ORDER_INTENT_BUY_SHORT' END)
                         THEN 'WON' ELSE 'LOST' END END) AS seen
      FROM xv CROSS JOIN sides GROUP BY 1, 2),
y AS (
    SELECT us_market_slug, side,
           CASE WHEN array_remove(seen, NULL) = ARRAY['WON'] THEN 1.0
                WHEN array_remove(seen, NULL) = ARRAY['LOST'] THEN 0.0 END AS y
      FROM oc),
obs AS (
    SELECT ev.strategy, ev.p, ev.mkt, y.y,
           least(9, floor(ev.p * 10))::int AS bin
      FROM ev JOIN y ON y.us_market_slug = ev.us_market_slug
                    AND y.side = ev.holding_side
     WHERE y.y IS NOT NULL),
tot AS (SELECT strategy, count(*) AS n, avg((p - y) ^ 2) AS brier,
               avg((mkt - y) ^ 2) FILTER (WHERE mkt IS NOT NULL) AS mbrier,
               count(*) FILTER (WHERE mkt IS NOT NULL) AS mn
          FROM obs GROUP BY strategy),
bins AS (SELECT strategy, bin, count(*) AS nb, avg(p) AS ap, avg(y) AS ay
           FROM obs GROUP BY strategy, bin)
SELECT t.strategy, t.n, round(t.brier::numeric,5) AS brier,
       round(t.mbrier::numeric,5) AS market_brier, t.mn AS market_n,
       round((t.brier - t.mbrier)::numeric,5) AS brier_minus_market,
       round(sum(b.nb::float8 / t.n * abs(b.ap - b.ay))::numeric,5) AS ece,
       (t.n >= 50 AND ((t.mbrier IS NOT NULL AND t.brier - t.mbrier > 0.02)
                       OR sum(b.nb::float8 / t.n * abs(b.ap - b.ay)) > 0.15)) AS fires
  FROM tot t JOIN bins b USING (strategy)
 GROUP BY t.strategy, t.n, t.brier, t.mbrier, t.mn ORDER BY 1;

\echo D1 decisions per strategy and verdict: last 24 h, and since the first quarantine (2026-10-06 05:37:53Z)
SELECT strategy, verdict,
       count(*) FILTER (WHERE decided_at >= now() - interval '24 hours') AS last_24h,
       count(*) FILTER (WHERE decided_at >= '2026-10-06 05:37:53+00') AS since_quarantine,
       min(decided_at) FILTER (WHERE decided_at >= '2026-10-06 05:37:53+00') AS first,
       max(decided_at) AS last
  FROM paper_decisions
 WHERE account_id = 'paper_acct_main' AND decided_at >= '2026-10-06 05:37:53+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo D2 top decision-stage refusals per strategy, last 24 h
SELECT strategy, refusal, count(*) AS n
  FROM paper_decisions
 WHERE account_id = 'paper_acct_main' AND decided_at >= now() - interval '24 hours'
   AND verdict <> 'ENTER'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo D3 every EXPLORATION ENTER since its quarantine: order, census stage/refusal, lifecycle state and event on the refusal, shadow recorded
SELECT (d.decided_at >= now() - interval '24 hours') AS in_last_24h,
       count(*) AS enter,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o
                                       WHERE o.decision_id = d.decision_id)) AS with_order,
       count(*) FILTER (WHERE r.refusal_id IS NULL) AS no_census_row,
       r.stage, r.refusal,
       r.detail->'lifecycle'->>'state' AS state_at_refusal,
       r.detail->'lifecycle'->>'lifecycle_event_id' AS lifecycle_event,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_shadow_counterfactuals sc
                                       WHERE sc.decision_id = d.decision_id)) AS with_shadow,
       min(d.decided_at) AS first, max(d.decided_at) AS last
  FROM paper_decisions d
  LEFT JOIN paper_entry_refusal_census r ON r.decision_id = d.decision_id
 WHERE d.account_id = 'paper_acct_main' AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
   AND d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'
 GROUP BY 1, 5, 6, 7, 8 ORDER BY 1, 2 DESC;

\echo D4 every ENTER decision since 2026-10-05 per strategy: with order, and ledger census refusals other than the lifecycle state
SELECT d.strategy, count(*) AS enter,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o
                                       WHERE o.decision_id = d.decision_id)) AS with_order,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_entry_refusal_census r
             WHERE r.decision_id = d.decision_id AND r.stage = 'LEDGER'
               AND r.refusal NOT LIKE 'STRATEGY_LIFECYCLE_%')) AS other_ledger_refusal,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM paper_orders o
                                          WHERE o.decision_id = d.decision_id)
                          AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r
                                          WHERE r.decision_id = d.decision_id)) AS enter_no_order_no_census
  FROM paper_decisions d
 WHERE d.account_id = 'paper_acct_main' AND d.verdict = 'ENTER'
   AND d.decided_at >= '2026-10-05'
 GROUP BY 1 ORDER BY 1;

\echo D5 ledger-stage refusal census since 2026-10-05 by strategy, refusal and day
SELECT strategy, refusal, date_trunc('day', refused_at) AS day, count(*) AS n
  FROM paper_entry_refusal_census
 WHERE account_id = 'paper_acct_main' AND stage = 'LEDGER'
   AND refused_at >= '2026-10-05'
 GROUP BY 1, 2, 3 ORDER BY 1, 3, 4 DESC;

\echo C1 the per-strategy entry switches (paper_control)
SELECT control_key, enabled, updated_by, left(why, 160) AS why
  FROM paper_control ORDER BY control_key;
