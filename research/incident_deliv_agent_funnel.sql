-- P0 INCIDENT (2026-10-04) -- AGENT OPPORTUNITY DELIVERY: per-agent
-- RECEIVED / REVIEWED / REJECTED / ENTER / ORDERED / FILLED over the last
-- 24 h, the refusal mix per strategy, and the split between ECONOMICS
-- (everything else passed, the price was not good enough) and SOFTWARE
-- CAPABILITY (the software could not establish the contract / settlement /
-- probability). READ-ONLY; every statement is bounded to a 24 h window and
-- every row dump has a LIMIT.

\echo == Q0 clock and table freshness ==
SELECT now() AS db_now,
       (SELECT max(decided_at) FROM paper_decisions) AS last_paper_decision,
       (SELECT max(decided_at) FROM external_valuations) AS last_valuation,
       (SELECT max(cycle_at) FROM ext_candidate_outcomes) AS last_candidate_cycle,
       (SELECT max(at) FROM paper_evaluation_attempts) AS last_attempt;

\echo == Q1 RECEIVED: entry-experiment valuations written in 24 h by sport_family / provider / purpose / market ==
SELECT v.sport_family, v.provider, v.record_purpose, v.market,
       count(*) AS valuation_rows,
       count(DISTINCT v.us_market_slug) AS distinct_markets,
       count(DISTINCT (v.us_market_slug, v.buy_intent)) AS distinct_market_sides,
       count(DISTINCT v.event_key) AS distinct_events,
       count(*) FILTER (WHERE v.us_market_slug IS NULL) AS rows_without_slug,
       count(*) FILTER (WHERE v.admissible) AS lane_admissible_rows
  FROM external_valuations v
 WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND v.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4
 ORDER BY valuation_rows DESC
 LIMIT 80;

\echo == Q2 REVIEWED / REJECTED / ENTER per strategy (24 h) ==
SELECT d.strategy, d.policy_version,
       count(*) AS decisions,
       count(DISTINCT d.valuation_id) AS valuations,
       count(DISTINCT d.us_market_slug) AS markets,
       count(DISTINCT (d.us_market_slug, d.holding_side)) AS market_sides,
       count(DISTINCT d.fixture) AS fixtures,
       count(*) FILTER (WHERE d.verdict = 'ENTER') AS enter_rows,
       count(DISTINCT d.us_market_slug) FILTER (WHERE d.verdict = 'ENTER') AS enter_markets,
       count(*) FILTER (WHERE d.verdict = 'REFUSE') AS refuse_rows
  FROM paper_decisions d
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY decisions DESC
 LIMIT 40;

\echo == Q3 per strategy x sport_family (24 h): decisions, enters, markets ==
SELECT d.strategy, coalesce(v.sport_family, 'NO_VALUATION_ROW') AS sport_family,
       count(*) AS decisions,
       count(DISTINCT d.us_market_slug) AS markets,
       count(DISTINCT v.event_key) AS events,
       count(*) FILTER (WHERE d.verdict = 'ENTER') AS enter_rows,
       count(DISTINCT d.us_market_slug) FILTER (WHERE d.verdict = 'ENTER') AS enter_markets
  FROM paper_decisions d
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 1, decisions DESC
 LIMIT 120;

\echo == Q4 first refusal per strategy (24 h) ==
SELECT d.strategy, d.refusal,
       count(*) AS decisions,
       count(DISTINCT d.us_market_slug) AS markets,
       count(DISTINCT d.fixture) AS fixtures
  FROM paper_decisions d
 WHERE d.decided_at > now() - interval '24 hours'
   AND d.verdict = 'REFUSE'
 GROUP BY 1, 2
 ORDER BY 1, decisions DESC
 LIMIT 200;

\echo == Q5 EVERY refusal code (unnested) per strategy x sport_family (24 h) ==
SELECT d.strategy, coalesce(v.sport_family, '?') AS sport_family, r.code,
       count(*) AS decisions,
       count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
  CROSS JOIN LATERAL unnest(d.refusals) AS r(code)
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, decisions DESC
 LIMIT 400;

\echo == Q6 ECONOMICS vs CAPABILITY vs FRESHNESS vs BOOK vs POSITION per strategy x sport (24 h, by decision and by distinct market) ==
WITH cls AS (
  SELECT d.decision_id, d.strategy, d.verdict, d.us_market_slug,
         coalesce(v.sport_family, '?') AS sport_family,
         r.code,
         CASE
           WHEN r.code IN ('BELOW_MIN_GROSS_EDGE',
                           'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
                           'NET_EV_NOT_POSITIVE_AFTER_FEES',
                           'BELOW_MIN_NET_EV', 'NO_SIZED_QUANTITY',
                           'NO_RESTING_PRICE_BELOW_THE_ASK_CLEARS_THE_THRESHOLD_AND_FEES',
                           'ONE_CONTRACT_EXCEEDS_THE_EXPLORATION_ENTRY_BUDGET',
                           'ESTIMATES_DISAGREE_MODEL_BELOW_MIN_GROSS_EDGE')
             THEN 'ECONOMICS'
           WHEN r.code IN ('PROBABILITY_EVIDENCE_STALE',
                           'PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN',
                           'THE_PAPER_BOOK_OBSERVATION_IS_NOT_CURRENT')
                OR r.code LIKE 'PRIMARY_REFERENCE%'
             THEN 'FRESHNESS'
           WHEN r.code IN ('THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY',
                           'BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE',
                           'NO_ASK_TO_REST_BELOW',
                           'NO_ESTABLISHED_EXECUTABLE_DEPTH')
             THEN 'BOOK'
           WHEN r.code IN ('THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT',
                           'ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE',
                           'A_MAKER_ENTRY_ORDER_ALREADY_RESTS_ON_THIS_FIXTURE',
                           'EXPLORATION_ALREADY_HOLDS_THIS_FIXTURE',
                           'THIS_STRATEGY_ALREADY_HAS_A_LIVE_ENTRY_ON_THIS_FIXTURE')
             THEN 'POSITION_HELD'
           WHEN r.code IN ('FIXTURE_NOT_SELECTED_BY_THE_EXPLORATION_SAMPLE',
                           'EXPLORATION_AGGREGATE_EXPOSURE_LIMIT',
                           'EXPLORATION_REALIZED_LOSS_STOP_REACHED',
                           'EXPLORATION_LIMITS_UNREADABLE',
                           'STRATEGY_ENTRIES_DISABLED')
             THEN 'POLICY_LIMIT'
           ELSE 'CAPABILITY'
         END AS cls
    FROM paper_decisions d
    LEFT JOIN external_valuations v ON v.id = d.valuation_id
    CROSS JOIN LATERAL unnest(d.refusals) AS r(code)
   WHERE d.decided_at > now() - interval '24 hours'
     AND d.verdict = 'REFUSE'),
per AS (
  SELECT decision_id, strategy, sport_family, us_market_slug,
         bool_or(cls = 'CAPABILITY') AS cap,
         bool_or(cls = 'FRESHNESS') AS fresh,
         bool_or(cls = 'BOOK') AS book,
         bool_or(cls = 'POSITION_HELD') AS held,
         bool_or(cls = 'POLICY_LIMIT') AS lim,
         bool_or(cls = 'ECONOMICS') AS econ
    FROM cls GROUP BY 1, 2, 3, 4)
SELECT strategy, sport_family,
       count(*) AS refused_decisions,
       count(*) FILTER (WHERE cap) AS any_capability,
       count(*) FILTER (WHERE NOT cap AND fresh) AS freshness_not_capability,
       count(*) FILTER (WHERE NOT cap AND NOT fresh AND book) AS book_only,
       count(*) FILTER (WHERE NOT cap AND NOT fresh AND NOT book AND held) AS position_held_only,
       count(*) FILTER (WHERE NOT cap AND NOT fresh AND NOT book AND NOT held AND lim) AS policy_limit_only,
       count(*) FILTER (WHERE econ AND NOT cap AND NOT fresh AND NOT book AND NOT held AND NOT lim) AS economics_only,
       count(DISTINCT us_market_slug) AS markets,
       count(DISTINCT us_market_slug) FILTER (WHERE cap) AS markets_capability,
       count(DISTINCT us_market_slug) FILTER (WHERE econ AND NOT cap AND NOT fresh AND NOT book AND NOT held AND NOT lim) AS markets_economics_only
  FROM per
 GROUP BY 1, 2
 ORDER BY 1, refused_decisions DESC
 LIMIT 120;

\echo == Q7 CAPABILITY codes only, per strategy (24 h): which software gap, how many markets ==
SELECT d.strategy, r.code,
       count(*) AS decisions,
       count(DISTINCT d.us_market_slug) AS markets,
       array_agg(DISTINCT v.sport_family) AS families
  FROM paper_decisions d
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
  CROSS JOIN LATERAL unnest(d.refusals) AS r(code)
 WHERE d.decided_at > now() - interval '24 hours'
   AND d.verdict = 'REFUSE'
   AND r.code NOT IN ('BELOW_MIN_GROSS_EDGE',
                      'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
                      'NET_EV_NOT_POSITIVE_AFTER_FEES', 'BELOW_MIN_NET_EV',
                      'NO_SIZED_QUANTITY',
                      'PROBABILITY_EVIDENCE_STALE',
                      'PROBABILITY_EVIDENCE_FRESHNESS_UNKNOWN',
                      'THE_PAPER_BOOK_OBSERVATION_IS_NOT_CURRENT',
                      'THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY',
                      'BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE',
                      'THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT')
 GROUP BY 1, 2
 ORDER BY 1, decisions DESC
 LIMIT 200;

\echo == Q8 RECEIVED but NOT REVIEWED: entry valuations (24 h) with no decision by each strategy ==
WITH v AS (
  SELECT id, sport_family, us_market_slug, record_purpose
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at > now() - interval '24 hours'),
dd AS (
  SELECT valuation_id, strategy FROM paper_decisions
   WHERE decided_at > now() - interval '25 hours'
   GROUP BY 1, 2),
s AS (
  SELECT DISTINCT strategy FROM dd)
SELECT s.strategy, v.sport_family, v.record_purpose,
       count(*) AS valuations,
       count(dd.valuation_id) AS decided,
       count(*) - count(dd.valuation_id) AS not_decided,
       count(*) FILTER (WHERE v.us_market_slug IS NULL) AS no_slug
  FROM v CROSS JOIN s
  LEFT JOIN dd ON dd.valuation_id = v.id AND dd.strategy = s.strategy
 GROUP BY 1, 2, 3
 ORDER BY 1, valuations DESC
 LIMIT 120;

\echo == Q9 attempts per strategy x via x outcome (24 h) ==
SELECT strategy, via, outcome, coalesce(book_source, '-') AS book_source,
       count(*) AS attempts,
       count(DISTINCT valuation_id) AS valuations,
       round(avg(elapsed_s)::numeric, 3) AS avg_elapsed_s,
       round(max(elapsed_s)::numeric, 3) AS max_elapsed_s
  FROM paper_evaluation_attempts
 WHERE at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, attempts DESC
 LIMIT 150;

\echo == Q10 hook failures (24 h) ==
SELECT strategy, stage, outcome, left(coalesce(error, ''), 120) AS error,
       count(*) AS n, count(DISTINCT valuation_id) AS valuations
  FROM paper_hook_failures
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4
 ORDER BY n DESC
 LIMIT 60;

\echo == Q11 ORDERED / FILLED per strategy (24 h, ENTRY orders) ==
SELECT o.strategy, o.order_type, o.state,
       count(*) AS orders,
       count(DISTINCT o.us_market_slug) AS markets,
       count(*) FILTER (WHERE o.filled_qty > 0) AS orders_with_fill
  FROM paper_orders o
 WHERE o.role = 'ENTRY' AND o.created_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, orders DESC
 LIMIT 80;

SELECT o.strategy, count(*) AS entry_fills,
       count(DISTINCT f.order_id) AS filled_orders,
       count(DISTINCT f.us_market_slug) AS filled_markets,
       round(sum(f.qty * f.price)::numeric, 2) AS notional_usd
  FROM paper_fills f
  JOIN paper_orders o ON o.order_id = f.order_id
 WHERE f.role = 'ENTRY' AND f.filled_at > now() - interval '24 hours'
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 40;

\echo == Q12 ENTER decisions without an order, per strategy (24 h): where does ENTER die? ==
SELECT d.strategy,
       count(*) AS enter_decisions,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o
                                      WHERE o.decision_id = d.decision_id)) AS with_order,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM paper_orders o
                                          WHERE o.decision_id = d.decision_id)) AS without_order
  FROM paper_decisions d
 WHERE d.decided_at > now() - interval '24 hours' AND d.verdict = 'ENTER'
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 40;

SELECT f.kind, left(coalesce(f.detail->>'refusal', ''), 80) AS order_refusal,
       f.detail->>'strategy' AS strategy, count(*) AS n
  FROM paper_audrey_findings f
 WHERE f.found_at > now() - interval '24 hours'
   AND f.kind = 'PAPER_RISK_REFUSED_THE_ORDER'
 GROUP BY 1, 2, 3
 ORDER BY n DESC
 LIMIT 30;

\echo == Q13 execution intents per strategy x actual_state (24 h) ==
SELECT strategy, actual_state, left(coalesce(actual_refusal, ''), 80) AS refusal,
       count(*) AS intents, count(DISTINCT us_market_slug) AS markets
  FROM execution_intents
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY intents DESC
 LIMIT 40;

\echo == Q14 per-hour decisions by strategy (24 h): loop cadence and gaps ==
SELECT date_trunc('hour', decided_at) AS hour, strategy, count(*) AS decisions,
       count(*) FILTER (WHERE verdict = 'ENTER') AS enters
  FROM paper_decisions
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 1, 2
 LIMIT 200;

\echo == Q15 collector candidate outcomes (24 h) by sport_key x outcome: DEFERRED = the per-cycle cap bit ==
SELECT sport_key, outcome,
       count(*) AS rows,
       count(DISTINCT provider_event_id) AS events,
       count(DISTINCT cycle_id) AS cycles
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 1, rows DESC
 LIMIT 150;

\echo == Q16 collector first refusal (24 h) by sport_key, distinct events ==
SELECT sport_key, coalesce(first_refusal, outcome) AS first_refusal, stage,
       count(DISTINCT provider_event_id) AS events, count(*) AS rows
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, events DESC
 LIMIT 250;

\echo == Q17 collector cycles per hour (24 h) and writers ==
SELECT date_trunc('hour', cycle_at) AS hour, coalesce(writer, '?') AS writer,
       count(DISTINCT cycle_id) AS cycles,
       count(DISTINCT sport_key) AS sport_keys,
       count(*) AS rows
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 1, 2
 LIMIT 120;

\echo == Q18 latest collector heartbeat: selection, budget_dropped, evaluated, deferred ==
SELECT key,
       value->>'at' AS at,
       value->>'state' AS state,
       value->>'evaluated' AS evaluated,
       value->>'written' AS written,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'metered_budget' AS metered_budget,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'sports_selection'->'rejected' AS rejected,
       value->'sports_selection'->'never_requested' AS never_requested,
       left((value->'refusals')::text, 1500) AS refusals,
       left((value->'deferred')::text, 600) AS deferred,
       value->'deferred_total' AS deferred_total
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_cycle_standby');

\echo == Q19 paper pass heartbeat and the active session cadence ==
SELECT key, left(value::text, 3000) AS value
  FROM ingestion_state
 WHERE key = 'paper_session_last_pass';

SELECT session_id, account_id, started_at,
       config->'cadence' AS cadence,
       config->'entry' AS entry
  FROM paper_sessions
 ORDER BY started_at DESC
 LIMIT 3;

\echo == Q20 paper control rows (kill switches) ==
SELECT control_key, enabled, left(coalesce(why, ''), 160) AS why, updated_by
  FROM paper_control
 ORDER BY control_key
 LIMIT 40;
