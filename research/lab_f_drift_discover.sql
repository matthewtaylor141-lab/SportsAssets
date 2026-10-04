-- LAB-F POLICY / MARKET DRIFT SENTINEL: DISCOVERY (SELECT only, read-only).
-- Branch claude/lab-f, backend/sportsassets/lab/. Before the drift sentinel's
-- retrospective run, establish from production which strategies and policy
-- versions exist, when each version FIRST decided (the reference window is
-- derived from that), how many rows carry each monitored metric, and which
-- evidence classes (PAPER_SIMULATION / LIVE_SHADOW / ACTUAL) hold any rows.
-- Every statement is bounded by a time window and/or LIMIT.

\echo === read time
SELECT now() AS read_at;

\echo === approximate table sizes (planner statistics, no scan)
SELECT relname, reltuples::bigint AS approx_rows
  FROM pg_class
 WHERE relname IN ('paper_decisions', 'paper_orders', 'paper_fills',
                   'paper_settlements', 'external_valuations',
                   'pos_position_economics', 'eddie_execution_outcomes',
                   'intel_attribution', 'paper_sleeve_classifications',
                   'execmirror_orders', 'execmirror_fills',
                   'kalshi_live_fills', 'bettor_funded_fills')
 ORDER BY relname;

\echo === A. strategy x policy_version over the last 60 days: volume, first/last decision, metric coverage
SELECT strategy, policy_version, count(*) AS n,
       count(*) FILTER (WHERE verdict = 'ENTER') AS enter_n,
       min(decided_at) AS first_at, max(decided_at) AS last_at,
       count(*) FILTER (WHERE policy_decision->>'gross_edge_pp' IS NOT NULL) AS gross_n,
       count(*) FILTER (WHERE economics->'acquisition'->>'expected_net_profit_usd' IS NOT NULL) AS exec_n,
       count(*) FILTER (WHERE pinnacle->>'received_at' IS NOT NULL AND pinnacle->>'at' IS NOT NULL) AS latency_n,
       count(*) FILTER (WHERE pinnacle->>'age_s' IS NOT NULL) AS prob_age_n,
       count(*) FILTER (WHERE book->>'age_at_decision_s' IS NOT NULL) AS book_age_n,
       count(*) FILTER (WHERE label->>'competition' IS NOT NULL) AS league_n,
       count(*) FILTER (WHERE recorded_at < decided_at) AS recorded_before_decided
  FROM paper_decisions
 WHERE decided_at >= now() - interval '60 days'
 GROUP BY strategy, policy_version
 ORDER BY strategy, policy_version;

\echo === A2. earliest decision of every strategy x policy_version ever recorded (index on strategy, decided_at)
SELECT strategy, policy_version, min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions
 GROUP BY strategy, policy_version
 ORDER BY strategy, min(decided_at);

\echo === B. parameter version recorded on each decision (completed-game policies), last 60 days
SELECT strategy, policy_version,
       policy_decision->'parameters'->>'version_id' AS param_version_id,
       policy_decision->'parameters'->>'source' AS param_source,
       count(*) AS n, min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions
 WHERE decided_at >= now() - interval '60 days'
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, min(decided_at);

\echo === C. daily volume per strategy x version, last 21 days
SELECT date_trunc('day', decided_at) AS day, strategy, policy_version,
       count(*) AS n, count(*) FILTER (WHERE verdict = 'ENTER') AS enter_n,
       count(*) FILTER (WHERE policy_decision->>'gross_edge_pp' IS NOT NULL) AS gross_n,
       count(DISTINCT us_market_slug || ':' || coalesce(holding_side, '')) AS contracts
  FROM paper_decisions
 WHERE decided_at >= now() - interval '21 days'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo === D. paper_control (current state; mutable table, context only)
SELECT control_key, enabled, updated_by, updated_at FROM paper_control ORDER BY control_key;

\echo === E. parameter versions, activations, heads
SELECT version_id, policy_key, version_no, params, source, proposal_id,
       evaluation_id, approved_by, created_at, recorded_at
  FROM paper_policy_parameter_versions ORDER BY policy_key, version_no;
SELECT activation_id, kind, version_id, previous_version_id, proposal_id,
       evaluation_id, actor, at, recorded_at
  FROM paper_policy_parameter_activations ORDER BY at;
SELECT policy_key, active_version_id, activation_id, updated_at
  FROM paper_policy_parameter_heads;

\echo === F. league mix of the last 7 days per strategy x version (top 60)
SELECT strategy, policy_version,
       coalesce(label->>'competition', 'NULL') AS league,
       count(*) AS n, count(*) FILTER (WHERE verdict = 'ENTER') AS enter_n,
       count(*) FILTER (WHERE policy_decision->>'gross_edge_pp' IS NOT NULL) AS gross_n,
       count(DISTINCT us_market_slug || ':' || coalesce(holding_side, '')) AS contracts
  FROM paper_decisions
 WHERE decided_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3
 ORDER BY 4 DESC
 LIMIT 60;

\echo === F2. refusal mix of the last 7 days per strategy x version (top 60)
SELECT strategy, policy_version, coalesce(refusal, '(ENTER)') AS first_refusal,
       count(*) AS n
  FROM paper_decisions
 WHERE decided_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3
 ORDER BY 4 DESC
 LIMIT 60;

\echo === G. JSON shapes: one recent completed-game decision that reached the book
SELECT decision_id, decided_at, recorded_at, verdict, refusal,
       (SELECT array_agg(k) FROM jsonb_object_keys(pinnacle) k) AS pinnacle_keys,
       pinnacle->>'at' AS pin_at, pinnacle->>'received_at' AS pin_received_at,
       pinnacle->>'age_s' AS pin_age_s, pinnacle->>'provider' AS pin_provider,
       (SELECT array_agg(k) FROM jsonb_object_keys(book) k) AS book_keys,
       book->>'age_at_decision_s' AS book_age,
       policy_decision->>'gross_edge_pp' AS gross_edge_pp,
       policy_decision->>'edge_at_vwap_pp' AS edge_at_vwap_pp,
       policy_decision->>'net_expected_profit_usd' AS net_ev,
       (SELECT array_agg(k) FROM jsonb_object_keys(economics->'acquisition') k) AS acquisition_keys,
       economics->'acquisition'->>'qty' AS acq_qty,
       label->>'competition' AS league, label->>'market_type' AS market_type,
       label->>'event_key' AS event_key, fixture, proposed_qty, limit_price
  FROM paper_decisions
 WHERE decided_at >= now() - interval '2 days'
   AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND policy_decision->>'gross_edge_pp' IS NOT NULL
 ORDER BY (economics->'acquisition') IS NULL, decided_at DESC
 LIMIT 3;

\echo === H. paper ENTRY orders per strategy, last 21 days: terminal states and fill fraction
SELECT o.strategy, o.state, o.terminal_reason, count(*) AS n,
       count(*) FILTER (WHERE o.filled_qty > 0) AS any_fill,
       round(avg(o.filled_qty / nullif(o.qty, 0))::numeric, 4) AS mean_fill_fraction,
       count(*) FILTER (WHERE o.decision_id IS NULL) AS no_decision_id,
       min(o.created_at) AS first_at, max(o.created_at) AS last_at
  FROM paper_orders o
 WHERE o.role = 'ENTRY' AND o.created_at >= now() - interval '21 days'
 GROUP BY 1, 2, 3
 ORDER BY 1, 4 DESC
 LIMIT 80;

\echo === I. paper settlements by the group's strategy (sleeve classification), last 30 days
SELECT c.strategy, c.policy_version, s.outcome, s.version, count(*) AS n,
       min(s.settled_at) AS first_at, max(s.settled_at) AS last_at
  FROM paper_settlements s
  LEFT JOIN paper_sleeve_classifications c ON c.group_id = s.group_id
 WHERE s.settled_at >= now() - interval '30 days'
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, 5 DESC
 LIMIT 80;

\echo === J. pos_position_economics latest revision per position, PAPER/ACTUAL, released in the last 30 days
SELECT e.book, e.strategy, e.state, count(*) AS n,
       count(*) FILTER (WHERE e.time_committed_h IS NOT NULL) AS with_time_committed,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY e.time_committed_h) AS p50_h,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY e.time_committed_h) AS p90_h,
       min(e.released_at) AS first_release, max(e.released_at) AS last_release,
       max(e.computed_at) AS last_computed
  FROM pos_economics_latest e
 WHERE e.released_at >= now() - interval '30 days'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
 LIMIT 60;

\echo === K. Eddie execution outcomes (realized slippage / adverse selection), last 30 days
SELECT o.source, count(*) AS n,
       count(*) FILTER (WHERE o.realized_slippage_pp IS NOT NULL) AS slippage_n,
       count(*) FILTER (WHERE o.realized_adverse_selection_pp IS NOT NULL) AS adverse_n,
       count(*) FILTER (WHERE o.filled_qty > 0) AS filled_n,
       min(o.measured_at) AS first_at, max(o.measured_at) AS last_at,
       (SELECT array_agg(DISTINCT k) FROM eddie_execution_outcomes x,
               jsonb_object_keys(x.unmeasured) k
         WHERE x.measured_at >= now() - interval '30 days') AS unmeasured_keys
  FROM eddie_execution_outcomes o
 WHERE o.measured_at >= now() - interval '30 days'
 GROUP BY o.source;

\echo === K2. Eddie outcomes joined to decisions: strategy x version coverage, last 30 days
SELECT d.strategy, d.policy_version, o.source, count(*) AS n,
       count(*) FILTER (WHERE o.realized_slippage_pp IS NOT NULL) AS slippage_n,
       count(*) FILTER (WHERE o.realized_adverse_selection_pp IS NOT NULL) AS adverse_n
  FROM eddie_execution_outcomes o
  JOIN paper_decisions d ON d.decision_id = o.decision_id
 WHERE o.measured_at >= now() - interval '30 days'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo === L. intel_attribution slippage (PAPER/ACTUAL), last 30 days
SELECT book, strategy, count(*) AS rows, count(DISTINCT subject_id) AS subjects,
       count(*) FILTER (WHERE slippage_pc IS NOT NULL) AS slippage_rows,
       min(computed_at) AS first_at, max(computed_at) AS last_at
  FROM intel_attribution
 WHERE computed_at >= now() - interval '30 days'
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo === M. LIVE_SHADOW / ACTUAL execution evidence (counts only)
SELECT 'execmirror_orders' AS tbl, state AS k, count(*) AS n, min(created_at) AS first_at, max(created_at) AS last_at
  FROM execmirror_orders GROUP BY state
UNION ALL
SELECT 'execmirror_fills', NULL, count(*), min(observed_at), max(observed_at) FROM execmirror_fills
UNION ALL
SELECT 'kalshi_live_fills', NULL, count(*), min(observed_at), max(observed_at) FROM kalshi_live_fills
UNION ALL
SELECT 'bettor_funded_fills', NULL, count(*), min(recorded_at), max(recorded_at) FROM bettor_funded_fills
ORDER BY 1, 2;
SELECT to_regclass('canonical_intent_executions') IS NOT NULL AS has_canonical_intent_executions,
       to_regclass('live_parity_cutover') IS NOT NULL AS has_live_parity_cutover,
       to_regclass('agent_work_requests') IS NOT NULL AS has_agent_work_requests,
       to_regclass('paper_sleeve_classifications') IS NOT NULL AS has_sleeves;

\echo === N. sleeve classification per strategy x policy_version
SELECT strategy, policy_version, sleeve, count(*) AS groups,
       min(entry_at) AS first_entry, max(entry_at) AS last_entry
  FROM paper_sleeve_classifications
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;
