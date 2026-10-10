-- RC6.3 lifecycle-proof lane, readback 1 (SELECT only).
-- A: the inputs the strategy lifecycle (bettor_strategy_lifecycle.metrics /
--    rules_firing / check_manual) and the profitability-stack quarantine
--    (bettor_paper_profitability_stack.quarantine_triggers) read, for the
--    paper strategies on paper_acct_main.
-- B: Allie at the decision (canonical_components.allie_at_decision): a 24 h
--    census of the canonical intents' allie component, and the measured
--    duration of each read Allie runs inside her 2.0 s component bound.

\echo == A1 lifecycle events on paper_acct_main
SELECT event_id, strategy, from_state, to_state, rule_id, actor,
       to_char(recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS at_utc,
       left(why, 160) AS why
  FROM paper_strategy_lifecycle_events
 WHERE account_id = 'paper_acct_main'
 ORDER BY event_id;

\echo == A2 positions per strategy (POSITIONS_SQL shape; open = open_qty above 1e-9)
WITH f AS (
    SELECT group_id, us_market_slug, holding_side, max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
           sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
           min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
      FROM paper_fills WHERE account_id = 'paper_acct_main'
     GROUP BY group_id, us_market_slug, holding_side),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty, settled_at
      FROM paper_settlements WHERE account_id = 'paper_acct_main'
     ORDER BY position_key, version DESC),
p AS (
    SELECT f.*, coalesce(s.qty, 0) AS settled_qty, s.settled_at,
           coalesce(f.bought, 0) - coalesce(f.sold, 0) - coalesce(s.qty, 0) AS open_qty
      FROM f LEFT JOIN s ON s.position_key = 'paperpos:paper_acct_main:'
           || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side)
SELECT coalesce(strategy, 'NULL->DEREK_ENTRY_POLICY_V2') AS strategy,
       count(*) AS positions,
       count(*) FILTER (WHERE open_qty > 1e-9) AS open_positions,
       count(*) FILTER (WHERE open_qty <= 1e-9) AS closed_positions,
       count(*) FILTER (WHERE open_qty <= 1e-9
                          AND coalesce(settled_at, last_fill_at) >= timestamptz '2026-09-20') AS closed_since_0920,
       min(first_fill_at) AS first_fill, max(first_fill_at) AS last_first_fill,
       max(coalesce(settled_at, last_fill_at)) FILTER (WHERE open_qty <= 1e-9) AS last_close
  FROM p GROUP BY 1 ORDER BY 1;

\echo == A3 latest profitability models (kind, fitted_at, observations)
SELECT DISTINCT ON (kind) kind, model_id, fitted_at, observations
  FROM paper_profitability_models WHERE account_id = 'paper_acct_main'
 ORDER BY kind, fitted_at DESC, model_id DESC;

\echo == A4 latest RESIDUAL model: per-strategy cells (strategy|*|*)
WITH m AS (
    SELECT payload FROM paper_profitability_models
     WHERE account_id = 'paper_acct_main' AND kind = 'RESIDUAL'
     ORDER BY fitted_at DESC, model_id DESC LIMIT 1)
SELECT c.key AS cell, c.value::text AS cell_value
  FROM m, jsonb_each(m.payload->'cells') c
 WHERE c.key LIKE '%|*|*' ORDER BY 1;

\echo == A5 latest EXECUTION model: rows per strategy and style
WITH m AS (
    SELECT payload FROM paper_profitability_models
     WHERE account_id = 'paper_acct_main' AND kind = 'EXECUTION'
     ORDER BY fitted_at DESC, model_id DESC LIMIT 1)
SELECT r.key AS row_key, r.value->>'fills' AS fills,
       r.value->>'markout_per_contract_raw' AS markout_raw,
       r.value->>'terminal_orders' AS terminal_orders,
       r.value->>'fill_rate_raw' AS fill_rate_raw
  FROM m, jsonb_each(m.payload->'by_strategy_style') r ORDER BY 1;

\echo == A6 profitability evaluations per strategy (all time, last 120 d, distinct contract-sides with p_used in 120 d, latest)
SELECT strategy, count(*) AS evals,
       count(*) FILTER (WHERE evaluated_at >= now() - interval '120 days') AS evals_120d,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (
           WHERE evaluated_at >= now() - interval '120 days' AND p_used IS NOT NULL
             AND us_market_slug IS NOT NULL AND holding_side IS NOT NULL) AS sides_120d,
       min(evaluated_at) AS first_eval, max(evaluated_at) AS last_eval
  FROM paper_profitability_evaluations WHERE account_id = 'paper_acct_main'
 GROUP BY 1 ORDER BY 1;

\echo == A7 ENTRY BUY fills and terminal ENTRY orders per strategy: earliest and latest (execution model window is 120 d)
SELECT 'fill' AS kind, strategy, count(*) AS n, min(filled_at) AS earliest, max(filled_at) AS latest
  FROM paper_fills WHERE account_id = 'paper_acct_main' AND role = 'ENTRY' AND direction = 'BUY'
 GROUP BY 1, 2
UNION ALL
SELECT 'terminal_order', strategy, count(*), min(decided_at), max(decided_at)
  FROM paper_orders WHERE account_id = 'paper_acct_main' AND role = 'ENTRY' AND direction = 'BUY'
   AND state IN ('FILLED', 'EXPIRED', 'CANCELED', 'REJECTED')
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == A8 settled filled shadow counterfactuals per strategy per settle day, last 12 days (residual model SHADOW rows)
SELECT s.strategy, date_trunc('day', o.settled_at) AS day, count(*) AS n,
       round(sum(o.counterfactual_pnl_usd)::numeric, 2) AS realized,
       round(sum(s.total_executable_ev_usd * o.filled_qty / NULLIF(s.qty, 0))::numeric, 2) AS expected,
       round(sum(o.filled_qty)::numeric, 1) AS qty
  FROM paper_shadow_counterfactuals s
  JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE s.account_id = 'paper_acct_main' AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND')
   AND o.filled_qty > 0 AND o.settled_at >= now() - interval '12 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == A9 unsettled shadow counterfactuals per strategy decided in the last 3 days (future residual rows pending)
SELECT s.strategy, count(*) AS decided, count(o.shadow_id) AS with_outcome
  FROM paper_shadow_counterfactuals s
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE s.account_id = 'paper_acct_main' AND s.decided_at >= now() - interval '3 days'
 GROUP BY 1 ORDER BY 1;

\echo == B1 canonical intents in the last 24 h by strategy, sleeve and allie status / why
SELECT strategy, sleeve, allie->>'status' AS allie_status,
       left(coalesce(allie->>'why', ''), 70) AS allie_why, count(*) AS n,
       min(created_at) AS first, max(created_at) AS last
  FROM canonical_decision_intents
 WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo == B2 the other components in the same 24 h (opportunity_score, eddie, karen) by status / why
SELECT 'opportunity_score' AS component, opportunity_score->>'status' AS status,
       left(coalesce(opportunity_score->>'why', ''), 70) AS why, count(*) AS n
  FROM canonical_decision_intents WHERE created_at >= now() - interval '24 hours' GROUP BY 1, 2, 3
UNION ALL
SELECT 'eddie', eddie->>'status', left(coalesce(eddie->>'why', ''), 70), count(*)
  FROM canonical_decision_intents WHERE created_at >= now() - interval '24 hours' GROUP BY 1, 2, 3
UNION ALL
SELECT 'karen', karen->>'state', left(coalesce(karen->>'why', ''), 70), count(*)
  FROM canonical_decision_intents WHERE created_at >= now() - interval '24 hours' GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

\echo == B3 decision-to-record latency (recorded_at - created_at, seconds) by allie status, last 24 h
SELECT allie->>'status' AS allie_status, count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - created_at))::numeric, 3) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - created_at))::numeric, 3) AS p90_s,
       round(max(extract(epoch FROM recorded_at - created_at))::numeric, 3) AS max_s
  FROM canonical_decision_intents WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1;

\echo == B4 the 7-day picture: intents per day by allie status, opportunity status
SELECT date_trunc('day', created_at) AS day, allie->>'status' AS allie_status,
       opportunity_score->>'status' AS opp_status, count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - created_at))::numeric, 3) AS p50_latency_s
  FROM canonical_decision_intents WHERE created_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo == B5 newest 8 intents (allie, opportunity why, latency)
SELECT decision_id, strategy, sleeve, created_at, allie->>'status' AS allie,
       left(coalesce(allie->>'why', ''), 50) AS allie_why,
       opportunity_score->>'status' AS opp, left(coalesce(opportunity_score->>'why', ''), 50) AS opp_why,
       round(extract(epoch FROM recorded_at - created_at)::numeric, 3) AS latency_s
  FROM canonical_decision_intents ORDER BY created_at DESC LIMIT 8;

\echo == B6 indexes on the tables Allie reads
SELECT tablename, indexname, left(indexdef, 150) AS def FROM pg_indexes
 WHERE tablename IN ('us_premap', 'paper_settlements', 'paper_orders', 'pos_snapshots', 'canonical_decision_intents', 'execmirror_control')
 ORDER BY 1, 2;

\echo == B7 table sizes (estimated rows)
SELECT relname, reltuples::bigint AS est_rows, pg_size_pretty(pg_total_relation_size(oid)) AS total
  FROM pg_class WHERE relname IN ('us_premap', 'paper_settlements', 'paper_orders', 'pos_snapshots', 'canonical_decision_intents', 'paper_fills')
 ORDER BY 1;

\echo == B8 distinct WON/LOST settled markets in the settlement-lag lookback (180 d) = lateral us_premap probes per lag read
SELECT count(DISTINCT us_market_slug) AS markets
  FROM paper_settlements WHERE settled_at >= now() - interval '180 days' AND outcome IN ('WON', 'LOST');

\echo == C1 TIMING: settlement_lag_samples (profitability/reads.py, cached 300 s under key lol_lags, shared by the Opportunity Score and Allie)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT extract(epoch FROM s.recorded_at)::float8 AS rec,
       extract(epoch FROM s.settled_at - g.game_start)::float8 AS lag
  FROM (SELECT DISTINCT ON (us_market_slug) us_market_slug, recorded_at, settled_at
          FROM paper_settlements
         WHERE settled_at >= to_timestamp(extract(epoch FROM now()) - 180 * 86400.0)
           AND outcome IN ('WON', 'LOST')
         ORDER BY us_market_slug, settled_at) s
  JOIN LATERAL (SELECT game_start FROM us_premap
                 WHERE market_slug = s.us_market_slug AND game_start IS NOT NULL
                 ORDER BY updated_at DESC NULLS LAST LIMIT 1) g ON true
 LIMIT 20000;

\echo == C2 TIMING: snapshots_since CAPITAL (lost_opportunity/reads.py)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT extract(epoch FROM computed_at)::float8 AS t, payload->'idle_capital_usd' AS v,
       payload->'unmeasured'->>'idle_capital_usd' AS why
  FROM pos_snapshots WHERE component = 'CAPITAL' AND book = 'PAPER'
   AND computed_at >= coalesce((SELECT max(computed_at) FROM pos_snapshots
         WHERE component = 'CAPITAL' AND book = 'PAPER' AND computed_at <= now()), now())
 ORDER BY computed_at ASC LIMIT 2000;

\echo == C3 TIMING: snapshots_since CAPACITY
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT extract(epoch FROM computed_at)::float8 AS t, payload->'rates'->'fill_probability' AS v
  FROM pos_snapshots WHERE component = 'CAPACITY' AND book = 'NONE'
   AND computed_at >= coalesce((SELECT max(computed_at) FROM pos_snapshots
         WHERE component = 'CAPACITY' AND book = 'NONE' AND computed_at <= now()), now())
 ORDER BY computed_at ASC LIMIT 2000;

\echo == C4 TIMING: Allie hurdle (canonical_components.allie_at_decision)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT (allie->'correlation_concentration'->>'adjusted_profit_per_capital_hour')::float8 AS a
  FROM canonical_decision_intents
 WHERE sleeve = 'INVESTMENT' AND allie->>'status' = 'MEASURED'
   AND created_at > now() - interval '7 days'
 ORDER BY created_at DESC LIMIT 50;

\echo == C5 TIMING: Allie fixture exposure (fixture of the newest intent)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT count(DISTINCT o.group_id) AS n, coalesce(sum(o.filled_qty * o.limit_price), 0) AS usd
  FROM paper_orders o
 WHERE o.role = 'ENTRY' AND o.fixture = (SELECT contract->>'fixture' FROM canonical_decision_intents ORDER BY created_at DESC LIMIT 1)
   AND o.filled_qty > 0
   AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = o.group_id);

\echo == C6 TIMING: Allie open book (all accounts, no account filter in the code)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT coalesce(sum(o.filled_qty * o.limit_price), 0)
  FROM paper_orders o
 WHERE o.role = 'ENTRY' AND o.filled_qty > 0
   AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = o.group_id);

\echo == C7 open book value by account (what the unfiltered sum adds up)
SELECT o.account_id, count(*) AS orders, round(coalesce(sum(o.filled_qty * o.limit_price), 0)::numeric, 2) AS usd
  FROM paper_orders o
 WHERE o.role = 'ENTRY' AND o.filled_qty > 0
   AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = o.group_id)
 GROUP BY 1 ORDER BY 1;

\echo == C8 TIMING: event_starts for the newest intent slug (at_decision, before Archer)
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)
SELECT DISTINCT ON (market_slug) market_slug, extract(epoch FROM game_start)::float8 AS t
  FROM us_premap
 WHERE market_slug = ANY(ARRAY[(SELECT us_market_slug FROM canonical_decision_intents ORDER BY created_at DESC LIMIT 1)]::text[])
   AND game_start IS NOT NULL
 ORDER BY market_slug, updated_at DESC NULLS LAST;
