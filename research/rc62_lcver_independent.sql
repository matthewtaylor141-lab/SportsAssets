-- READ-ONLY. RC6.2 independent verification of the lifecycle recovery audit
-- (PAPER_TURNAROUND_RULES_V1 unchanged). Raw inputs only, so the verifier can
-- re-run bettor_strategy_lifecycle.metrics / rules_firing / check_manual and
-- bettor_capital_authority.forward_verdict offline on the exact rows:
--   V1 every paper position of the main account (the ledger's own
--      POSITIONS_SQL arithmetic), as epochs and exact numerics;
--   V2 the lifecycle event log;
--   V3 the integrator's 24 h window (2026-10-08 15:12:50Z .. 2026-10-09
--      15:12:50Z) of ENTER decisions: order, census stage / refusal /
--      lifecycle event, findings;
--   V4 every ENTER since the exploration quarantine, same classification;
--   V5 which strategies the profitability stack can see (it reads only
--      strategies with evaluation rows);
--   V6 the newest learned models and the residual cells;
--   V7 shadow counterfactuals: recorded after the stop, pending, and the
--      settled filled forward aggregates;
--   V8 open paper orders and fills since the quarantines;
--   V9 entry switches; V10 ledger-stage census by refusal.
-- Every statement is a SELECT.

\echo V0 read instant
SELECT now() AS read_at, extract(epoch FROM now())::float8 AS now_epoch;

\echo V1 positions (main account): strategy|first_fill|last_fill|settled_at|open_qty|realized|acq_cost|buy_fees|sale_fees
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
           coalesce(f.bought,0) AS bought,
           coalesce(f.buy_gross,0) + coalesce(f.buy_fees,0) AS buy_cost,
           coalesce(f.buy_fees,0) AS buy_fees, coalesce(f.sale_fees,0) AS sale_fees,
           coalesce(f.sold,0) AS sold, coalesce(s.qty,0) AS settled,
           coalesce(f.sale_gross,0) - coalesce(f.sale_fees,0) AS proceeds,
           coalesce(s.payout_usd,0) AS payout
      FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:'
           || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side)
SELECT strat,
       extract(epoch FROM first_fill_at)::float8 AS ff,
       extract(epoch FROM last_fill_at)::float8 AS lf,
       extract(epoch FROM settled_at)::float8 AS st,
       bought - sold - settled AS open_qty,
       (proceeds - (CASE WHEN bought > 0 THEN buy_cost / bought ELSE 0 END) * sold)
         + CASE WHEN settled > 0
                THEN payout - (CASE WHEN bought > 0 THEN buy_cost / bought ELSE 0 END) * settled
                ELSE 0 END AS realized,
       buy_cost AS acq_cost, buy_fees, sale_fees
  FROM q ORDER BY strat, first_fill_at;

\echo V2 lifecycle event log (every account)
SELECT event_id, account_id, strategy, from_state, to_state, rule_id,
       rules_version, rules_sha, actor,
       extract(epoch FROM recorded_at)::float8 AS at_epoch, recorded_at
  FROM paper_strategy_lifecycle_events ORDER BY event_id;

\echo V3 the integrator 24 h window: ENTER decisions 2026-10-08 15:12:50Z .. 2026-10-09 15:12:50Z by strategy and outcome
WITH d AS (
    SELECT d.decision_id, d.strategy, d.decided_at
      FROM paper_decisions d
     WHERE d.verdict = 'ENTER'
       AND d.decided_at >= '2026-10-08 15:12:50+00'
       AND d.decided_at <  '2026-10-09 15:12:50+00'),
c AS (
    SELECT d.*,
           EXISTS (SELECT 1 FROM paper_orders o
                    WHERE o.decision_id = d.decision_id
                       OR o.idempotency_key = d.decision_id || ':ENTRY') AS has_order,
           (SELECT count(*) FROM paper_entry_refusal_census r
             WHERE r.decision_id = d.decision_id) AS n_census,
           (SELECT string_agg(DISTINCT r.stage || '/' || r.refusal || '/ev'
                   || coalesce(r.detail->'lifecycle'->>'lifecycle_event_id', '-')
                   || '/' || coalesce(r.detail->'lifecycle'->>'state', '-'), ';')
              FROM paper_entry_refusal_census r
             WHERE r.decision_id = d.decision_id) AS census,
           (SELECT string_agg(DISTINCT fd.kind, ';') FROM paper_audrey_findings fd
             WHERE fd.subject = d.decision_id) AS finding_kinds
      FROM d)
SELECT strategy, has_order, n_census, census, finding_kinds, count(*) AS n,
       min(decided_at) AS first, max(decided_at) AS last
  FROM c GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 6 DESC;

\echo V3b ENTER decisions within 2 min of either window bound (boundary sensitivity)
SELECT strategy, decided_at FROM paper_decisions
 WHERE verdict = 'ENTER'
   AND (decided_at BETWEEN '2026-10-08 15:10:50+00' AND '2026-10-08 15:14:50+00'
     OR decided_at BETWEEN '2026-10-09 15:10:50+00' AND '2026-10-09 15:14:50+00')
 ORDER BY decided_at;

\echo V4 every ENTER since 2026-10-06 05:37:53Z (exploration quarantine), all strategies, by outcome
WITH d AS (
    SELECT d.decision_id, d.strategy, d.decided_at
      FROM paper_decisions d
     WHERE d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'),
c AS (
    SELECT d.*,
           EXISTS (SELECT 1 FROM paper_orders o
                    WHERE o.decision_id = d.decision_id
                       OR o.idempotency_key = d.decision_id || ':ENTRY') AS has_order,
           (SELECT string_agg(DISTINCT r.stage || '/' || r.refusal || '/ev'
                   || coalesce(r.detail->'lifecycle'->>'lifecycle_event_id', '-'), ';')
              FROM paper_entry_refusal_census r
             WHERE r.decision_id = d.decision_id) AS census,
           (SELECT string_agg(DISTINCT fd.kind || ':' || coalesce(fd.detail->>'refusal', '-'), ';')
              FROM paper_audrey_findings fd WHERE fd.subject = d.decision_id) AS findings
      FROM d)
SELECT strategy, has_order, census, findings, count(*) AS n,
       min(decided_at) AS first, max(decided_at) AS last
  FROM c GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;

\echo V5 profitability evaluations per account, strategy, stage (the stack iterates only strategies present here)
SELECT account_id, strategy, stage, count(*) AS n, min(evaluated_at) AS first,
       max(evaluated_at) AS last
  FROM paper_profitability_evaluations GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo V6 newest model of each kind (main account) and the RESIDUAL per-strategy cells
SELECT DISTINCT ON (kind) kind, model_id, fitted_at, observations
  FROM paper_profitability_models WHERE account_id = 'paper_acct_main'
 ORDER BY kind, fitted_at DESC, model_id DESC;
WITH m AS (
    SELECT model_id, fitted_at,
           CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb
                ELSE payload END AS pl
      FROM paper_profitability_models
     WHERE account_id = 'paper_acct_main' AND kind = 'RESIDUAL'
     ORDER BY fitted_at DESC, model_id DESC LIMIT 1)
SELECT m.model_id, m.fitted_at, c.key AS cell, c.value->>'n' AS n,
       c.value->>'paper' AS paper, c.value->>'shadow' AS shadow,
       c.value->>'expected_ev_usd' AS expected, c.value->>'realized_pnl_usd' AS realized,
       c.value->>'residual_usd' AS residual_usd,
       c.value->>'residual_per_contract' AS rpc
  FROM m, jsonb_each(m.pl->'cells') c ORDER BY 3;
\echo V6b distinct RESIDUAL per-strategy cell values since 2026-10-08 06:00Z (one row per distinct value)
WITH m AS (
    SELECT model_id, fitted_at,
           CASE WHEN jsonb_typeof(payload) = 'string' THEN (payload #>> '{}')::jsonb
                ELSE payload END AS pl
      FROM paper_profitability_models
     WHERE account_id = 'paper_acct_main' AND kind = 'RESIDUAL'
       AND fitted_at >= '2026-10-08 06:00+00')
SELECT c.key AS cell, c.value->>'n' AS n, c.value->>'residual_usd' AS residual_usd,
       count(*) AS fits, min(m.fitted_at) AS first_fit, max(m.fitted_at) AS last_fit
  FROM m, jsonb_each(m.pl->'cells') c WHERE c.key LIKE '%|*|*'
 GROUP BY 1, 2, 3 ORDER BY 1, 5;

\echo V7 shadow counterfactuals per strategy: all, recorded after 2026-10-06 17:36:47Z, pending (no outcome), settled filled since FORWARD_SINCE (n, sum, sum of squares)
SELECT sc.strategy, count(*) AS recorded_all,
       max(sc.recorded_at) AS last_recorded, max(sc.decided_at) AS last_decided,
       count(*) FILTER (WHERE sc.recorded_at > '2026-10-06 17:36:47+00') AS recorded_after_stop,
       count(*) FILTER (WHERE o.shadow_id IS NULL) AS pending,
       string_agg(to_char(sc.decided_at, 'MM-DD HH24:MI'), ',') FILTER (WHERE o.shadow_id IS NULL) AS pending_decided,
       count(*) FILTER (WHERE sc.decided_at >= to_timestamp(1791172800)
                          AND o.outcome NOT IN ('NO_FILL','VOID_REFUND') AND o.filled_qty > 0) AS fwd_n,
       sum(o.counterfactual_pnl_usd::float8) FILTER (WHERE sc.decided_at >= to_timestamp(1791172800)
                          AND o.outcome NOT IN ('NO_FILL','VOID_REFUND') AND o.filled_qty > 0) AS fwd_sum,
       sum((o.counterfactual_pnl_usd::float8)^2) FILTER (WHERE sc.decided_at >= to_timestamp(1791172800)
                          AND o.outcome NOT IN ('NO_FILL','VOID_REFUND') AND o.filled_qty > 0) AS fwd_sumsq
  FROM paper_shadow_counterfactuals sc
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE sc.account_id = 'paper_acct_main'
 GROUP BY 1 ORDER BY 1;

\echo V8 paper orders by strategy, role, state (main account), and fills since 2026-10-06 05:37:53Z
SELECT strategy, role, direction, state, count(*) AS n, max(decided_at) AS last_decided
  FROM paper_orders WHERE account_id = 'paper_acct_main'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;
SELECT strategy, direction, count(*) AS fills, min(filled_at) AS first, max(filled_at) AS last
  FROM paper_fills WHERE account_id = 'paper_acct_main'
   AND filled_at >= '2026-10-06 05:37:53+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo V9 entry switches and decisions per strategy since 2026-10-05 (verdict counts, last decision)
SELECT control_key, enabled FROM paper_control ORDER BY 1;
SELECT strategy, verdict, count(*) AS n, max(decided_at) AS last
  FROM paper_decisions WHERE decided_at >= '2026-10-05'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo V10 refusal census by stage, strategy, refusal (all time), first and last
SELECT stage, strategy, refusal, count(*) AS n, min(refused_at) AS first, max(refused_at) AS last
  FROM paper_entry_refusal_census
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC;
