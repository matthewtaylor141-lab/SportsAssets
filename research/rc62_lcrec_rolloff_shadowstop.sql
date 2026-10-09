-- READ-ONLY. RC6.2 lifecycle recovery audit, part 2.
-- (R) The EXACT instants at which the 14-day rolling window of each
--     quarantined strategy stops firing each predeclared stopping rule
--     (bettor_strategy_lifecycle.rules_firing), assuming no new closes (none
--     can occur: no open positions, no entries). A position leaves the window
--     the instant now - 14 d passes its close.
-- (S) Why no SHADOW_COUNTERFACTUAL has been recorded since 2026-10-06: the
--     bind's own refusal on every decision-stage evaluation, the residual
--     haircut it charged, and the exploration ledger refusals' pre-bind EV.
-- (E) The exploration ENTER decisions since its quarantine with neither an
--     order nor a census row.
-- Every statement is a SELECT.

\echo R1 exact roll-off: per strategy, each instant the window loses a close (T = that close + 14 d), the remaining window figures and which rule families still fire
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
           f.group_id || f.us_market_slug || f.holding_side AS pk,
           f.last_fill_at, s.settled_at,
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
    SELECT strat, pk, cost,
           (proceeds - avgc * sold_q) + CASE WHEN settled_q > 0
               THEN payout - avgc * settled_q ELSE 0 END AS realized,
           CASE WHEN open_qty > 1e-9 THEN NULL
                ELSE coalesce(settled_at, last_fill_at) END AS closed_at
      FROM q),
qp AS (SELECT * FROM pos WHERE closed_at IS NOT NULL
          AND closed_at >= now() - interval '14 days' AND closed_at <= now()
          AND strat IN ('DEREK_ENTRY_POLICY_V2','PINNACLE_COMPLETED_GAME_PAPER',
                        'PINNACLE_EXPLORATION_PAPER')),
cuts AS (SELECT DISTINCT strat, closed_at AS cut_at FROM qp
         UNION SELECT DISTINCT strat, '-infinity'::timestamptz FROM qp),
w AS (SELECT cuts.strat, cuts.cut_at, qp.closed_at, qp.pk, qp.realized, qp.cost
        FROM cuts JOIN qp ON qp.strat = cuts.strat AND qp.closed_at > cuts.cut_at),
c AS (SELECT w.*, sum(realized) OVER (PARTITION BY strat, cut_at ORDER BY closed_at, pk
                                      ROWS UNBOUNDED PRECEDING) AS cum FROM w),
pe AS (SELECT c.*, greatest(0, max(cum) OVER (PARTITION BY strat, cut_at
                   ORDER BY closed_at, pk ROWS UNBOUNDED PRECEDING)) AS peak FROM c),
agg AS (SELECT strat, cut_at, count(*) AS n, sum(realized) AS pnl,
               max(peak - cum) AS mdd, sum(cost) AS cost,
               avg(realized) + 1.96 * stddev_samp(realized) / sqrt(count(*)) AS ci_hi
          FROM pe GROUP BY strat, cut_at),
fl AS (SELECT agg.*,
              CASE WHEN cut_at = '-infinity' THEN now()
                   ELSE cut_at + interval '14 days' END AS t_from,
              (pnl <= -10000 OR mdd >= 12500) AS q_fires,
              (pnl <= -5000) AS shadow_fires,
              (pnl <= -2500) AS reduce_fires,
              (n >= 20 AND ci_hi < 0) AS neg_edge_fires,
              (n >= 20 AND cost > 0 AND mdd / cost >= 0.10) AS dd_rate_fires
         FROM agg),
ch AS (SELECT fl.*,
              lag(q_fires) OVER w1 AS pq, lag(shadow_fires) OVER w1 AS ps,
              lag(reduce_fires) OVER w1 AS pr, lag(neg_edge_fires) OVER w1 AS pn,
              lag(dd_rate_fires) OVER w1 AS pd
         FROM fl WINDOW w1 AS (PARTITION BY strat ORDER BY cut_at))
SELECT strat, to_char(t_from, 'YYYY-MM-DD HH24:MI:SS') AS from_utc, n,
       round(pnl::numeric,2) AS pnl, round(mdd::numeric,2) AS mdd,
       round((mdd / nullif(cost,0))::numeric,4) AS dd_rate,
       round(ci_hi::numeric,3) AS ci_hi,
       q_fires, shadow_fires, reduce_fires, neg_edge_fires, dd_rate_fires
  FROM ch
 WHERE cut_at = '-infinity' OR pq IS DISTINCT FROM q_fires
    OR ps IS DISTINCT FROM shadow_fires OR pr IS DISTINCT FROM reduce_fires
    OR pn IS DISTINCT FROM neg_edge_fires OR pd IS DISTINCT FROM dd_rate_fires
 ORDER BY strat, cut_at;

\echo R2 the last close in each quarantined strategy window (+14 d = the window is empty and no rolling rule can fire)
WITH f AS (
    SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction='BUY') AS bought,
           sum(qty) FILTER (WHERE direction='SELL') AS sold,
           max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY group_id, us_market_slug, holding_side),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC)
SELECT coalesce(f.strategy, 'DEREK_ENTRY_POLICY_V2') AS strat,
       count(*) FILTER (WHERE coalesce(f.bought,0) - coalesce(f.sold,0) - coalesce(s.qty,0) > 1e-9) AS open_n,
       max(coalesce(s.settled_at, f.last_fill_at)) AS last_close,
       max(coalesce(s.settled_at, f.last_fill_at)) + interval '14 days' AS window_empty_after
  FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:'
       || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side
 GROUP BY 1 ORDER BY 1;

\echo S1 the profitability-bind evaluations per strategy and stage: span and verdicts
SELECT strategy, stage, verdict, count(*) AS n, min(evaluated_at) AS first,
       max(evaluated_at) AS last
  FROM paper_profitability_evaluations
 WHERE account_id = 'paper_acct_main'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo S2 decision-stage evaluations since 2026-10-06 12:00 by 6 h: the final refusal, the bind own refusal, the residual haircut and EV per contract it charged
SELECT strategy, date_bin('6 hours', evaluated_at, '2026-10-06') AS bucket,
       refusal, detail->>'bind_refusal' AS bind_refusal, count(*) AS n,
       round(avg(residual_haircut_per_contract),4) AS avg_residual_haircut,
       round(avg(ev_per_contract_usd),4) AS avg_ev_per_contract,
       round(max(ev_per_contract_usd),4) AS max_ev_per_contract
  FROM paper_profitability_evaluations
 WHERE account_id = 'paper_acct_main' AND stage = 'DECISION'
   AND evaluated_at >= '2026-10-06 12:00+00'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 5 DESC;

\echo S3 shadow counterfactuals recorded per hour on 2026-10-06 (the stop)
SELECT strategy, source, date_trunc('hour', recorded_at) AS hour, count(*) AS n,
       min(recorded_at) AS first, max(recorded_at) AS last
  FROM paper_shadow_counterfactuals
 WHERE account_id = 'paper_acct_main' AND recorded_at >= '2026-10-06'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo S4 exploration LEDGER lifecycle refusals since 2026-10-06 12:00 by 6 h: shadow recorded, and the order pre-bind executable EV per contract against the residual haircut now charged (0.3195 x 261 / 281 = 0.2967)
SELECT date_bin('6 hours', r.refused_at, '2026-10-06') AS bucket, count(*) AS n,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_shadow_counterfactuals sc
                                       WHERE sc.decision_id = r.decision_id)) AS with_shadow,
       round(avg(r.total_executable_ev_usd / nullif(r.qty,0)),4) AS avg_ev_per_contract,
       round(max(r.total_executable_ev_usd / nullif(r.qty,0)),4) AS max_ev_per_contract,
       count(*) FILTER (WHERE r.total_executable_ev_usd / nullif(r.qty,0) > 0.2967) AS above_haircut,
       count(*) FILTER (WHERE r.total_executable_ev_usd > 0) AS ev_positive_pre_bind
  FROM paper_entry_refusal_census r
 WHERE r.account_id = 'paper_acct_main' AND r.strategy = 'PINNACLE_EXPLORATION_PAPER'
   AND r.stage = 'LEDGER' AND r.refused_at >= '2026-10-06 12:00+00'
 GROUP BY 1 ORDER BY 1;

\echo S5 the newest RESIDUAL model exploration and completed-game cells by sport and family (where the haircut comes from)
WITH m AS (
    SELECT model_id, fitted_at,
           CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb
                ELSE payload END AS pl
      FROM paper_profitability_models
     WHERE account_id = 'paper_acct_main' AND kind = 'RESIDUAL'
     ORDER BY fitted_at DESC, model_id DESC LIMIT 1)
SELECT m.model_id, c.key AS cell, c.value->>'n' AS n,
       c.value->>'residual_per_contract' AS rpc,
       round(greatest(0, -(c.value->>'residual_per_contract')::numeric)
             * (c.value->>'n')::numeric / ((c.value->>'n')::numeric + 20), 4) AS haircut
  FROM m, jsonb_each(m.pl->'cells') c ORDER BY 2;

\echo E1 exploration ENTER decisions since 2026-10-06 05:37:53Z with neither an order nor a census row, and every finding naming them
SELECT d.decision_id, d.decided_at, d.policy_version,
       (SELECT string_agg(fd.kind || ':' || left(coalesce(fd.detail->>'refusal', fd.detail->>'why', ''), 80), ' | ')
          FROM paper_audrey_findings fd WHERE fd.subject = d.decision_id) AS findings,
       EXISTS (SELECT 1 FROM paper_orders o
                WHERE o.idempotency_key = d.decision_id || ':ENTRY') AS order_by_key
  FROM paper_decisions d
 WHERE d.account_id = 'paper_acct_main' AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
   AND d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'
   AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
   AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r
                    WHERE r.decision_id = d.decision_id)
 ORDER BY d.decided_at;
