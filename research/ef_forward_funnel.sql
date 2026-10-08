-- ECONOMIC FUNNEL (owner directive section 6), STEP 2: THE FORWARD FUNNEL
-- PER MECHANISM AND FROZEN POLICY, FIXTURE BY FIXTURE.
--
-- READ-ONLY. Every statement is a SELECT. Run through research-sql.yml.
--
-- WINDOW: the profitability bind's forward cohort, migration 309 applied_at
-- (2026-10-06 17:38:06.220476Z; the production scoreboard's own cutover,
-- bettor_paper_profitability_stack.bind_cutover) to now(). Every PAPER entry
-- that ever filled was decided before it (last ENTRY fill 2026-10-06
-- 00:47:19Z, ef_discover.sql run 37787734139), so the historical cohort is a
-- separate file (ef_paper_realized.sql) and is never mixed in here.
--
-- THE STAGES (owner directive section 6), per strategy, counted on UNIQUE
-- FIXTURES (the independent unit; contract-sides beside them):
--   S1 eligible        a sealed valuation was decided by the strategy
--   S2 exact mapping   not lost at PROVIDER / NORMALIZED / MAPPED /
--                      SETTLEMENT (contract identity and payoff equivalence)
--   S3 fresh inputs    not lost at MODEL / FAIR_VALUE / BOOK (a current
--                      probability and a current, readable book)
--   S4 fully priced    reached the EV stage with a walked quantity
--   S5a policy EV > 0  the frozen policy's own net-of-fees EV and edge rule
--                      admitted it (no EV-stage refusal)
--   S5b executable EV  capital-eligibility executable EV > 0 (the decision
--                      reached the bind, or an exploration ENTER whose
--                      recorded total executable EV is > 0)
--   S5c all-in EV > 0  the profitability bind passed (calibrated, all-in
--                      executable EV after every learned cost) -- RECORDED
--                      only in paper_profitability_evaluations
--   S6 qualified       a paper ENTRY order was placed for the decision
--   S7 filled          that order filled (simulated)
--   S8 settled         its position carries a settlement
-- A fixture's stage is the FURTHEST any of its decisions reached; the stage
-- and class of a decision's loss are the coverage_first_loss census rule
-- (earliest chain stage among its codes, lane wrapper after the code it
-- carries). The code -> (stage, class) table below was generated from the
-- RC4 production code (9b94ef5c: coverage_first_loss.classify /
-- chain_stage(mapped=True)) over every code ef_codes.sql run 37788010927
-- returned; a code missing from it is UNCLASSIFIED at ENTER_PASS (the
-- census default for a decision), never guessed.

\echo '== 2.0 window'
SELECT applied_at AS cutover, now() AS until,
       round(extract(epoch FROM now() - applied_at) / 3600.0, 2) AS hours
  FROM schema_migrations WHERE version = '309_paper_profitability_bind.sql';

\echo '== 2.1 per strategy x frozen policy: decisions by the stage and class they were lost at'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
m(code, rnk, stage, cls) AS (VALUES
        ('BELOW_MIN_GROSS_EDGE', 8, 'EV', 'ECONOMIC'),
        ('BELOW_MIN_NET_EV', 8, 'EV', 'ECONOMIC'),
        ('BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE', 7, 'BOOK', 'SOFTWARE'),
        ('CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('CASH_WAIT_CALIBRATED_ALL_IN_EV_NOT_POSITIVE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE', 8, 'EV', 'ECONOMIC'),
        ('DEPTH_NOT_ESTABLISHED', 7, 'BOOK', 'SOFTWARE'),
        ('DRAW_HANDLING_NOT_RECONCILED', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('EXECUTION_ESTIMATE_NOT_IDENTIFIED', 7, 'BOOK', 'SOFTWARE'),
        ('FEED_MARKET_NOT_IN_CURRENT_STATE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_OWNERSHIP_NOT_HELD', 5, 'MODEL', 'SOFTWARE'),
        ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_QUOTE_CHANGE_TIME_IN_THE_FUTURE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_QUOTE_OLDER_THAN_LIMIT', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_SOCKET_CLOSED', 1, 'PROVIDER', 'SOFTWARE'),
        ('GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT', 8, 'EV', 'ECONOMIC'),
        ('INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('LINE_EXCEPTIONAL_SETTLEMENT_TERMS_DIFFER', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('MARKET_STATE_UNREADABLE', 9, 'ENTER_PASS', 'SOFTWARE'),
        ('NET_EV_NOT_POSITIVE_AFTER_FEES', 8, 'EV', 'ECONOMIC'),
        ('NO_ACTION_HAS_POSITIVE_NET_EDGE', 8, 'EV', 'ECONOMIC'),
        ('NO_DEPTH_AT_THE_BEST_LEVEL', 9, 'ENTER_PASS', 'UNCLASSIFIED'),
        ('NO_EXECUTABLE_ASK', 7, 'BOOK', 'SOFTWARE'),
        ('NO_QUALIFIED_MODEL', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('NO_QUALIFIED_PINNACLE_PROBABILITY', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('NO_SIZED_QUANTITY', 8, 'EV', 'ECONOMIC'),
        ('NO_VENUE_CONTRACT_FOR_EVENT', 3, 'MAPPED', 'EXTERNAL'),
        ('NO_VENUE_NATIVE_CONTRACT_IN_PREMAP', 3, 'MAPPED', 'EXTERNAL'),
        ('OUTCOME_DEPTH_BELOW_FLOOR', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_LINE_INPUT_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_CLOCK_INVALID', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FIXTURE_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_INPUT_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_NO_EXACT_FIXTURE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PROBABILITY_EVIDENCE_STALE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('QUOTE_STALE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('QUOTE_STALE_ON_ARRIVAL', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('RISK_GATE_BLOCKED', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('SETTLEMENT_DIFFERENCE_PRICED_VALUE_IS_ZERO', 8, 'EV', 'ECONOMIC'),
        ('SIZING_POLICY_NOT_APPLICABLE', 9, 'ENTER_PASS', 'SOFTWARE'),
        ('STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('THE_OBSERVED_BOOK_HAS_NO_LEVEL_ON_THE_SIDE_BOUGHT', 7, 'BOOK', 'ECONOMIC'),
        ('THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY', 7, 'BOOK', 'SOFTWARE'),
        ('THE_PROVIDER_COMPETITION_FIXTURES_DO_NOT_MATCH_THE_VENUE_COMPETITION', 3, 'MAPPED', 'SOFTWARE'),
        ('THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('VENUE_ASK_HAS_NO_DEPTH', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_BOOK_CURRENCY_NOT_ESTABLISHED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_BOOK_READ_FAILED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_BOOK_READ_RETURNED_ERROR', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_GATE_COOLDOWN', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_MAPPING_AMBIGUOUS', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_DISCOVERED_EVENT_NOT_IN_THE_WINDOW', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_LEAGUE_NOT_ADMITTED', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_RATE_LIMITED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_TIMEOUT', 7, 'BOOK', 'EXTERNAL'),
        ('VOID_ABANDONMENT_BOOK_RULE_NOT_HELD', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('WS_REFERENCE_NOT_USABLE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('WS_TRIGGER_SUPERSEDED', 1, 'PROVIDER', 'SOFTWARE'),
        ('XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT', 9, 'ENTER_PASS', 'SOFTWARE')),
d AS (
    SELECT dd.decision_id, dd.strategy, dd.policy_version, dd.verdict,
           dd.fixture, dd.us_market_slug, dd.holding_side, dd.refusals
      FROM paper_decisions dd CROSS JOIN c
     WHERE dd.decided_at >= c.t),
lc AS (
    SELECT DISTINCT ON (d.decision_id) d.decision_id, x.code,
           coalesce(m.rnk, 9) AS rnk, coalesce(m.stage, 'ENTER_PASS') AS stage,
           coalesce(m.cls, 'UNCLASSIFIED') AS cls
      FROM d CROSS JOIN LATERAL unnest(d.refusals) WITH ORDINALITY AS x(code, pos)
      LEFT JOIN m ON m.code = x.code
     ORDER BY d.decision_id, coalesce(m.rnk, 9),
              (x.code = 'PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE'), x.pos),
ev AS (
    SELECT e.decision_id, bool_or(e.detail ->> 'bind_refusal' IS NULL) AS bind_passed,
           min(e.detail ->> 'bind_refusal') AS bind_refusal
      FROM paper_profitability_evaluations e CROSS JOIN c
     WHERE e.evaluated_at >= c.t AND e.decision_id IS NOT NULL
     GROUP BY 1),
od AS (
    SELECT o.decision_id, bool_or(o.filled_qty > 0) AS filled,
           bool_or(s.position_key IS NOT NULL) AS settled
      FROM paper_orders o
      LEFT JOIN paper_settlements s ON s.group_id = o.group_id
     WHERE o.role = 'ENTRY' AND o.decision_id IS NOT NULL
     GROUP BY 1),
r AS (
    SELECT d.strategy, d.policy_version, d.decision_id, d.verdict, d.fixture,
           d.us_market_slug || ':' || coalesce(d.holding_side, '?') AS cs,
           CASE WHEN d.verdict = 'ENTER' THEN 10 ELSE coalesce(lc.rnk, 9) END AS rnk,
           CASE WHEN d.verdict = 'ENTER' THEN 'ENTERED' ELSE coalesce(lc.stage, 'ENTER_PASS') END AS stage,
           CASE WHEN d.verdict = 'ENTER' THEN NULL
                WHEN lc.code IS NULL THEN 'PAPER_DECISION_REFUSED_WITHOUT_A_CODE'
                ELSE lc.code END AS code,
           CASE WHEN d.verdict = 'ENTER' THEN NULL ELSE coalesce(lc.cls, 'SOFTWARE') END AS cls,
           (ev.decision_id IS NOT NULL) AS has_eval,
           coalesce(ev.bind_passed, false) AS bind_passed,
           ev.bind_refusal,
           (od.decision_id IS NOT NULL) AS ordered,
           coalesce(od.filled, false) AS filled,
           coalesce(od.settled, false) AS settled
      FROM d LEFT JOIN lc USING (decision_id)
      LEFT JOIN ev USING (decision_id)
      LEFT JOIN od USING (decision_id))
SELECT r.strategy, r.policy_version, r.stage, coalesce(r.cls, '-') AS class,
       count(*) AS decisions, count(DISTINCT r.cs) AS contract_sides,
       count(DISTINCT r.fixture) AS fixtures
  FROM r GROUP BY 1, 2, 3, 4
 ORDER BY 1, 2, min(r.rnk), 4;

\echo '== 2.2 per strategy x frozen policy: the FIXTURE FUNNEL (furthest stage any decision of the fixture reached)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
m(code, rnk, stage, cls) AS (VALUES
        ('BELOW_MIN_GROSS_EDGE', 8, 'EV', 'ECONOMIC'),
        ('BELOW_MIN_NET_EV', 8, 'EV', 'ECONOMIC'),
        ('BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE', 7, 'BOOK', 'SOFTWARE'),
        ('CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('CASH_WAIT_CALIBRATED_ALL_IN_EV_NOT_POSITIVE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE', 8, 'EV', 'ECONOMIC'),
        ('DEPTH_NOT_ESTABLISHED', 7, 'BOOK', 'SOFTWARE'),
        ('DRAW_HANDLING_NOT_RECONCILED', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('EXECUTION_ESTIMATE_NOT_IDENTIFIED', 7, 'BOOK', 'SOFTWARE'),
        ('FEED_MARKET_NOT_IN_CURRENT_STATE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_OWNERSHIP_NOT_HELD', 5, 'MODEL', 'SOFTWARE'),
        ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_QUOTE_CHANGE_TIME_IN_THE_FUTURE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_QUOTE_OLDER_THAN_LIMIT', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_SOCKET_CLOSED', 1, 'PROVIDER', 'SOFTWARE'),
        ('GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT', 8, 'EV', 'ECONOMIC'),
        ('INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('LINE_EXCEPTIONAL_SETTLEMENT_TERMS_DIFFER', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('MARKET_STATE_UNREADABLE', 9, 'ENTER_PASS', 'SOFTWARE'),
        ('NET_EV_NOT_POSITIVE_AFTER_FEES', 8, 'EV', 'ECONOMIC'),
        ('NO_ACTION_HAS_POSITIVE_NET_EDGE', 8, 'EV', 'ECONOMIC'),
        ('NO_DEPTH_AT_THE_BEST_LEVEL', 9, 'ENTER_PASS', 'UNCLASSIFIED'),
        ('NO_EXECUTABLE_ASK', 7, 'BOOK', 'SOFTWARE'),
        ('NO_QUALIFIED_MODEL', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('NO_QUALIFIED_PINNACLE_PROBABILITY', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('NO_SIZED_QUANTITY', 8, 'EV', 'ECONOMIC'),
        ('NO_VENUE_CONTRACT_FOR_EVENT', 3, 'MAPPED', 'EXTERNAL'),
        ('NO_VENUE_NATIVE_CONTRACT_IN_PREMAP', 3, 'MAPPED', 'EXTERNAL'),
        ('OUTCOME_DEPTH_BELOW_FLOOR', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_LINE_INPUT_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_CLOCK_INVALID', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FIXTURE_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_INPUT_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_NO_EXACT_FIXTURE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PROBABILITY_EVIDENCE_STALE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('QUOTE_STALE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('QUOTE_STALE_ON_ARRIVAL', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('RISK_GATE_BLOCKED', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('SETTLEMENT_DIFFERENCE_PRICED_VALUE_IS_ZERO', 8, 'EV', 'ECONOMIC'),
        ('SIZING_POLICY_NOT_APPLICABLE', 9, 'ENTER_PASS', 'SOFTWARE'),
        ('STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('THE_OBSERVED_BOOK_HAS_NO_LEVEL_ON_THE_SIDE_BOUGHT', 7, 'BOOK', 'ECONOMIC'),
        ('THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY', 7, 'BOOK', 'SOFTWARE'),
        ('THE_PROVIDER_COMPETITION_FIXTURES_DO_NOT_MATCH_THE_VENUE_COMPETITION', 3, 'MAPPED', 'SOFTWARE'),
        ('THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('VENUE_ASK_HAS_NO_DEPTH', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_BOOK_CURRENCY_NOT_ESTABLISHED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_BOOK_READ_FAILED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_BOOK_READ_RETURNED_ERROR', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_GATE_COOLDOWN', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_MAPPING_AMBIGUOUS', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_DISCOVERED_EVENT_NOT_IN_THE_WINDOW', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_LEAGUE_NOT_ADMITTED', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_RATE_LIMITED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_TIMEOUT', 7, 'BOOK', 'EXTERNAL'),
        ('VOID_ABANDONMENT_BOOK_RULE_NOT_HELD', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('WS_REFERENCE_NOT_USABLE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('WS_TRIGGER_SUPERSEDED', 1, 'PROVIDER', 'SOFTWARE'),
        ('XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT', 9, 'ENTER_PASS', 'SOFTWARE')),
d AS (
    SELECT dd.decision_id, dd.strategy, dd.policy_version, dd.verdict,
           dd.fixture, dd.us_market_slug, dd.holding_side, dd.refusals
      FROM paper_decisions dd CROSS JOIN c
     WHERE dd.decided_at >= c.t),
lc AS (
    SELECT DISTINCT ON (d.decision_id) d.decision_id, x.code,
           coalesce(m.rnk, 9) AS rnk, coalesce(m.stage, 'ENTER_PASS') AS stage,
           coalesce(m.cls, 'UNCLASSIFIED') AS cls
      FROM d CROSS JOIN LATERAL unnest(d.refusals) WITH ORDINALITY AS x(code, pos)
      LEFT JOIN m ON m.code = x.code
     ORDER BY d.decision_id, coalesce(m.rnk, 9),
              (x.code = 'PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE'), x.pos),
ev AS (
    SELECT e.decision_id, bool_or(e.detail ->> 'bind_refusal' IS NULL) AS bind_passed,
           min(e.detail ->> 'bind_refusal') AS bind_refusal
      FROM paper_profitability_evaluations e CROSS JOIN c
     WHERE e.evaluated_at >= c.t AND e.decision_id IS NOT NULL
     GROUP BY 1),
od AS (
    SELECT o.decision_id, bool_or(o.filled_qty > 0) AS filled,
           bool_or(s.position_key IS NOT NULL) AS settled
      FROM paper_orders o
      LEFT JOIN paper_settlements s ON s.group_id = o.group_id
     WHERE o.role = 'ENTRY' AND o.decision_id IS NOT NULL
     GROUP BY 1),
r AS (
    SELECT d.strategy, d.policy_version, d.decision_id, d.verdict, d.fixture,
           d.us_market_slug || ':' || coalesce(d.holding_side, '?') AS cs,
           CASE WHEN d.verdict = 'ENTER' THEN 10 ELSE coalesce(lc.rnk, 9) END AS rnk,
           CASE WHEN d.verdict = 'ENTER' THEN 'ENTERED' ELSE coalesce(lc.stage, 'ENTER_PASS') END AS stage,
           CASE WHEN d.verdict = 'ENTER' THEN NULL
                WHEN lc.code IS NULL THEN 'PAPER_DECISION_REFUSED_WITHOUT_A_CODE'
                ELSE lc.code END AS code,
           CASE WHEN d.verdict = 'ENTER' THEN NULL ELSE coalesce(lc.cls, 'SOFTWARE') END AS cls,
           (ev.decision_id IS NOT NULL) AS has_eval,
           coalesce(ev.bind_passed, false) AS bind_passed,
           ev.bind_refusal,
           (od.decision_id IS NOT NULL) AS ordered,
           coalesce(od.filled, false) AS filled,
           coalesce(od.settled, false) AS settled
      FROM d LEFT JOIN lc USING (decision_id)
      LEFT JOIN ev USING (decision_id)
      LEFT JOIN od USING (decision_id)),
fx AS (
    SELECT r.strategy, r.policy_version, r.fixture,
           max(r.rnk) AS rnk,
           bool_or(r.code IS DISTINCT FROM 'NO_SIZED_QUANTITY' AND r.rnk >= 8) AS priced,
           bool_or(r.rnk >= 9 AND r.code IS DISTINCT FROM 'THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT'
                   AND r.code IS DISTINCT FROM 'XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT'
                   AND r.code IS DISTINCT FROM 'XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE'
                   AND r.code IS DISTINCT FROM 'NO_DEPTH_AT_THE_BEST_LEVEL') AS policy_pos,
           bool_or(r.has_eval OR r.verdict = 'ENTER') AS exec_pos,
           bool_or(r.bind_passed) AS bind_pos,
           bool_or(r.ordered) AS ordered, bool_or(r.filled) AS filled,
           bool_or(r.settled) AS settled
      FROM r GROUP BY 1, 2, 3)
SELECT fx.strategy, fx.policy_version,
       count(*)                                      AS s1_eligible_fixtures,
       count(*) FILTER (WHERE fx.rnk > 4)            AS s2_exact_mapping,
       count(*) FILTER (WHERE fx.rnk > 7)            AS s3_fresh_inputs,
       count(*) FILTER (WHERE fx.priced)             AS s4_fully_priced,
       count(*) FILTER (WHERE fx.policy_pos)         AS s5a_policy_ev_pos,
       count(*) FILTER (WHERE fx.exec_pos)           AS s5b_exec_ev_pos,
       count(*) FILTER (WHERE fx.bind_pos)           AS s5c_all_in_ev_pos,
       count(*) FILTER (WHERE fx.ordered)            AS s6_qualified,
       count(*) FILTER (WHERE fx.filled)             AS s7_filled,
       count(*) FILTER (WHERE fx.settled)            AS s8_settled
  FROM fx GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 2.3 per strategy x frozen policy: the same funnel on CONTRACT-SIDES'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
m(code, rnk, stage, cls) AS (VALUES
        ('BELOW_MIN_GROSS_EDGE', 8, 'EV', 'ECONOMIC'),
        ('BELOW_MIN_NET_EV', 8, 'EV', 'ECONOMIC'),
        ('BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE', 7, 'BOOK', 'SOFTWARE'),
        ('CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('CASH_WAIT_CALIBRATED_ALL_IN_EV_NOT_POSITIVE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE', 8, 'EV', 'ECONOMIC'),
        ('DEPTH_NOT_ESTABLISHED', 7, 'BOOK', 'SOFTWARE'),
        ('DRAW_HANDLING_NOT_RECONCILED', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('EXECUTION_ESTIMATE_NOT_IDENTIFIED', 7, 'BOOK', 'SOFTWARE'),
        ('FEED_MARKET_NOT_IN_CURRENT_STATE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_OWNERSHIP_NOT_HELD', 5, 'MODEL', 'SOFTWARE'),
        ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_QUOTE_CHANGE_TIME_IN_THE_FUTURE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_QUOTE_OLDER_THAN_LIMIT', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_SOCKET_CLOSED', 1, 'PROVIDER', 'SOFTWARE'),
        ('GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT', 8, 'EV', 'ECONOMIC'),
        ('INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('LINE_EXCEPTIONAL_SETTLEMENT_TERMS_DIFFER', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('MARKET_STATE_UNREADABLE', 9, 'ENTER_PASS', 'SOFTWARE'),
        ('NET_EV_NOT_POSITIVE_AFTER_FEES', 8, 'EV', 'ECONOMIC'),
        ('NO_ACTION_HAS_POSITIVE_NET_EDGE', 8, 'EV', 'ECONOMIC'),
        ('NO_DEPTH_AT_THE_BEST_LEVEL', 9, 'ENTER_PASS', 'UNCLASSIFIED'),
        ('NO_EXECUTABLE_ASK', 7, 'BOOK', 'SOFTWARE'),
        ('NO_QUALIFIED_MODEL', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('NO_QUALIFIED_PINNACLE_PROBABILITY', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('NO_SIZED_QUANTITY', 8, 'EV', 'ECONOMIC'),
        ('NO_VENUE_CONTRACT_FOR_EVENT', 3, 'MAPPED', 'EXTERNAL'),
        ('NO_VENUE_NATIVE_CONTRACT_IN_PREMAP', 3, 'MAPPED', 'EXTERNAL'),
        ('OUTCOME_DEPTH_BELOW_FLOOR', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_LINE_INPUT_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_CLOCK_INVALID', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FIXTURE_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_INPUT_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_NO_EXACT_FIXTURE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PROBABILITY_EVIDENCE_STALE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('QUOTE_STALE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('QUOTE_STALE_ON_ARRIVAL', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('RISK_GATE_BLOCKED', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('SETTLEMENT_DIFFERENCE_PRICED_VALUE_IS_ZERO', 8, 'EV', 'ECONOMIC'),
        ('SIZING_POLICY_NOT_APPLICABLE', 9, 'ENTER_PASS', 'SOFTWARE'),
        ('STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('THE_OBSERVED_BOOK_HAS_NO_LEVEL_ON_THE_SIDE_BOUGHT', 7, 'BOOK', 'ECONOMIC'),
        ('THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY', 7, 'BOOK', 'SOFTWARE'),
        ('THE_PROVIDER_COMPETITION_FIXTURES_DO_NOT_MATCH_THE_VENUE_COMPETITION', 3, 'MAPPED', 'SOFTWARE'),
        ('THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('VENUE_ASK_HAS_NO_DEPTH', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_BOOK_CURRENCY_NOT_ESTABLISHED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_BOOK_READ_FAILED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_BOOK_READ_RETURNED_ERROR', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_GATE_COOLDOWN', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_MAPPING_AMBIGUOUS', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_DISCOVERED_EVENT_NOT_IN_THE_WINDOW', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_LEAGUE_NOT_ADMITTED', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_RATE_LIMITED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_TIMEOUT', 7, 'BOOK', 'EXTERNAL'),
        ('VOID_ABANDONMENT_BOOK_RULE_NOT_HELD', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('WS_REFERENCE_NOT_USABLE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('WS_TRIGGER_SUPERSEDED', 1, 'PROVIDER', 'SOFTWARE'),
        ('XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT', 9, 'ENTER_PASS', 'SOFTWARE')),
d AS (
    SELECT dd.decision_id, dd.strategy, dd.policy_version, dd.verdict,
           dd.fixture, dd.us_market_slug, dd.holding_side, dd.refusals
      FROM paper_decisions dd CROSS JOIN c
     WHERE dd.decided_at >= c.t),
lc AS (
    SELECT DISTINCT ON (d.decision_id) d.decision_id, x.code,
           coalesce(m.rnk, 9) AS rnk, coalesce(m.stage, 'ENTER_PASS') AS stage,
           coalesce(m.cls, 'UNCLASSIFIED') AS cls
      FROM d CROSS JOIN LATERAL unnest(d.refusals) WITH ORDINALITY AS x(code, pos)
      LEFT JOIN m ON m.code = x.code
     ORDER BY d.decision_id, coalesce(m.rnk, 9),
              (x.code = 'PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE'), x.pos),
ev AS (
    SELECT e.decision_id, bool_or(e.detail ->> 'bind_refusal' IS NULL) AS bind_passed,
           min(e.detail ->> 'bind_refusal') AS bind_refusal
      FROM paper_profitability_evaluations e CROSS JOIN c
     WHERE e.evaluated_at >= c.t AND e.decision_id IS NOT NULL
     GROUP BY 1),
od AS (
    SELECT o.decision_id, bool_or(o.filled_qty > 0) AS filled,
           bool_or(s.position_key IS NOT NULL) AS settled
      FROM paper_orders o
      LEFT JOIN paper_settlements s ON s.group_id = o.group_id
     WHERE o.role = 'ENTRY' AND o.decision_id IS NOT NULL
     GROUP BY 1),
r AS (
    SELECT d.strategy, d.policy_version, d.decision_id, d.verdict, d.fixture,
           d.us_market_slug || ':' || coalesce(d.holding_side, '?') AS cs,
           CASE WHEN d.verdict = 'ENTER' THEN 10 ELSE coalesce(lc.rnk, 9) END AS rnk,
           CASE WHEN d.verdict = 'ENTER' THEN 'ENTERED' ELSE coalesce(lc.stage, 'ENTER_PASS') END AS stage,
           CASE WHEN d.verdict = 'ENTER' THEN NULL
                WHEN lc.code IS NULL THEN 'PAPER_DECISION_REFUSED_WITHOUT_A_CODE'
                ELSE lc.code END AS code,
           CASE WHEN d.verdict = 'ENTER' THEN NULL ELSE coalesce(lc.cls, 'SOFTWARE') END AS cls,
           (ev.decision_id IS NOT NULL) AS has_eval,
           coalesce(ev.bind_passed, false) AS bind_passed,
           ev.bind_refusal,
           (od.decision_id IS NOT NULL) AS ordered,
           coalesce(od.filled, false) AS filled,
           coalesce(od.settled, false) AS settled
      FROM d LEFT JOIN lc USING (decision_id)
      LEFT JOIN ev USING (decision_id)
      LEFT JOIN od USING (decision_id)),
cx AS (
    SELECT r.strategy, r.policy_version, r.cs,
           max(r.rnk) AS rnk,
           bool_or(r.code IS DISTINCT FROM 'NO_SIZED_QUANTITY' AND r.rnk >= 8) AS priced,
           bool_or(r.rnk >= 9 AND r.code IS DISTINCT FROM 'THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT'
                   AND r.code IS DISTINCT FROM 'XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT'
                   AND r.code IS DISTINCT FROM 'XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE'
                   AND r.code IS DISTINCT FROM 'NO_DEPTH_AT_THE_BEST_LEVEL') AS policy_pos,
           bool_or(r.has_eval OR r.verdict = 'ENTER') AS exec_pos,
           bool_or(r.bind_passed) AS bind_pos,
           bool_or(r.ordered) AS ordered, bool_or(r.filled) AS filled,
           bool_or(r.settled) AS settled
      FROM r GROUP BY 1, 2, 3)
SELECT cx.strategy, cx.policy_version,
       count(*)                                      AS s1_contract_sides,
       count(*) FILTER (WHERE cx.rnk > 4)            AS s2_exact_mapping,
       count(*) FILTER (WHERE cx.rnk > 7)            AS s3_fresh_inputs,
       count(*) FILTER (WHERE cx.priced)             AS s4_fully_priced,
       count(*) FILTER (WHERE cx.policy_pos)         AS s5a_policy_ev_pos,
       count(*) FILTER (WHERE cx.exec_pos)           AS s5b_exec_ev_pos,
       count(*) FILTER (WHERE cx.bind_pos)           AS s5c_all_in_ev_pos,
       count(*) FILTER (WHERE cx.ordered)            AS s6_qualified,
       count(*) FILTER (WHERE cx.filled)             AS s7_filled,
       count(*) FILTER (WHERE cx.settled)            AS s8_settled
  FROM cx GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== 2.4 per strategy: where each FIXTURE stopped (its furthest decision), by stage, class and code'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
m(code, rnk, stage, cls) AS (VALUES
        ('BELOW_MIN_GROSS_EDGE', 8, 'EV', 'ECONOMIC'),
        ('BELOW_MIN_NET_EV', 8, 'EV', 'ECONOMIC'),
        ('BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE', 7, 'BOOK', 'SOFTWARE'),
        ('CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('CASH_WAIT_CALIBRATED_ALL_IN_EV_NOT_POSITIVE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE', 8, 'EV', 'ECONOMIC'),
        ('DEPTH_NOT_ESTABLISHED', 7, 'BOOK', 'SOFTWARE'),
        ('DRAW_HANDLING_NOT_RECONCILED', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('EXECUTION_ESTIMATE_NOT_IDENTIFIED', 7, 'BOOK', 'SOFTWARE'),
        ('FEED_MARKET_NOT_IN_CURRENT_STATE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_OWNERSHIP_NOT_HELD', 5, 'MODEL', 'SOFTWARE'),
        ('FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_QUOTE_CHANGE_TIME_IN_THE_FUTURE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_QUOTE_OLDER_THAN_LIMIT', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('FEED_SOCKET_CLOSED', 1, 'PROVIDER', 'SOFTWARE'),
        ('GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT', 8, 'EV', 'ECONOMIC'),
        ('INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('LINE_EXCEPTIONAL_SETTLEMENT_TERMS_DIFFER', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('MARKET_STATE_UNREADABLE', 9, 'ENTER_PASS', 'SOFTWARE'),
        ('NET_EV_NOT_POSITIVE_AFTER_FEES', 8, 'EV', 'ECONOMIC'),
        ('NO_ACTION_HAS_POSITIVE_NET_EDGE', 8, 'EV', 'ECONOMIC'),
        ('NO_DEPTH_AT_THE_BEST_LEVEL', 9, 'ENTER_PASS', 'UNCLASSIFIED'),
        ('NO_EXECUTABLE_ASK', 7, 'BOOK', 'SOFTWARE'),
        ('NO_QUALIFIED_MODEL', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('NO_QUALIFIED_PINNACLE_PROBABILITY', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('NO_SIZED_QUANTITY', 8, 'EV', 'ECONOMIC'),
        ('NO_VENUE_CONTRACT_FOR_EVENT', 3, 'MAPPED', 'EXTERNAL'),
        ('NO_VENUE_NATIVE_CONTRACT_IN_PREMAP', 3, 'MAPPED', 'EXTERNAL'),
        ('OUTCOME_DEPTH_BELOW_FLOOR', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_LINE_INPUT_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_CLOCK_INVALID', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FIXTURE_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('PINNAPI_PRIMARY_INPUT_CHANGED', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PINNAPI_PRIMARY_NO_EXACT_FIXTURE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('PROBABILITY_EVIDENCE_STALE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('QUOTE_STALE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER', 6, 'FAIR_VALUE', 'EXTERNAL'),
        ('QUOTE_STALE_ON_ARRIVAL', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('RISK_GATE_BLOCKED', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('SETTLEMENT_DIFFERENCE_PRICED_VALUE_IS_ZERO', 8, 'EV', 'ECONOMIC'),
        ('SIZING_POLICY_NOT_APPLICABLE', 9, 'ENTER_PASS', 'SOFTWARE'),
        ('STRATEGY_LIFECYCLE_QUARANTINED_NO_PAPER_ENTRY', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('THE_OBSERVED_BOOK_HAS_NO_LEVEL_ON_THE_SIDE_BOUGHT', 7, 'BOOK', 'ECONOMIC'),
        ('THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY', 7, 'BOOK', 'SOFTWARE'),
        ('THE_PROVIDER_COMPETITION_FIXTURES_DO_NOT_MATCH_THE_VENUE_COMPETITION', 3, 'MAPPED', 'SOFTWARE'),
        ('THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('VENUE_ASK_HAS_NO_DEPTH', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_BOOK_CURRENCY_NOT_ESTABLISHED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_BOOK_READ_FAILED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_BOOK_READ_RETURNED_ERROR', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_GATE_COOLDOWN', 7, 'BOOK', 'SOFTWARE'),
        ('VENUE_MAPPING_AMBIGUOUS', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_DISCOVERED_EVENT_NOT_IN_THE_WINDOW', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_NATIVE_LEAGUE_NOT_ADMITTED', 3, 'MAPPED', 'SOFTWARE'),
        ('VENUE_RATE_LIMITED', 7, 'BOOK', 'EXTERNAL'),
        ('VENUE_TIMEOUT', 7, 'BOOK', 'EXTERNAL'),
        ('VOID_ABANDONMENT_BOOK_RULE_NOT_HELD', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE', 4, 'SETTLEMENT', 'SOFTWARE'),
        ('WS_REFERENCE_NOT_USABLE', 6, 'FAIR_VALUE', 'SOFTWARE'),
        ('WS_TRIGGER_SUPERSEDED', 1, 'PROVIDER', 'SOFTWARE'),
        ('XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE', 9, 'ENTER_PASS', 'ECONOMIC'),
        ('XAVIER_HELD_READ_CANNOT_PRICE_THIS_CONTRACT', 9, 'ENTER_PASS', 'SOFTWARE')),
d AS (
    SELECT dd.decision_id, dd.strategy, dd.policy_version, dd.verdict,
           dd.fixture, dd.us_market_slug, dd.holding_side, dd.refusals
      FROM paper_decisions dd CROSS JOIN c
     WHERE dd.decided_at >= c.t),
lc AS (
    SELECT DISTINCT ON (d.decision_id) d.decision_id, x.code,
           coalesce(m.rnk, 9) AS rnk, coalesce(m.stage, 'ENTER_PASS') AS stage,
           coalesce(m.cls, 'UNCLASSIFIED') AS cls
      FROM d CROSS JOIN LATERAL unnest(d.refusals) WITH ORDINALITY AS x(code, pos)
      LEFT JOIN m ON m.code = x.code
     ORDER BY d.decision_id, coalesce(m.rnk, 9),
              (x.code = 'PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE'), x.pos),
ev AS (
    SELECT e.decision_id, bool_or(e.detail ->> 'bind_refusal' IS NULL) AS bind_passed,
           min(e.detail ->> 'bind_refusal') AS bind_refusal
      FROM paper_profitability_evaluations e CROSS JOIN c
     WHERE e.evaluated_at >= c.t AND e.decision_id IS NOT NULL
     GROUP BY 1),
od AS (
    SELECT o.decision_id, bool_or(o.filled_qty > 0) AS filled,
           bool_or(s.position_key IS NOT NULL) AS settled
      FROM paper_orders o
      LEFT JOIN paper_settlements s ON s.group_id = o.group_id
     WHERE o.role = 'ENTRY' AND o.decision_id IS NOT NULL
     GROUP BY 1),
r AS (
    SELECT d.strategy, d.policy_version, d.decision_id, d.verdict, d.fixture,
           d.us_market_slug || ':' || coalesce(d.holding_side, '?') AS cs,
           CASE WHEN d.verdict = 'ENTER' THEN 10 ELSE coalesce(lc.rnk, 9) END AS rnk,
           CASE WHEN d.verdict = 'ENTER' THEN 'ENTERED' ELSE coalesce(lc.stage, 'ENTER_PASS') END AS stage,
           CASE WHEN d.verdict = 'ENTER' THEN NULL
                WHEN lc.code IS NULL THEN 'PAPER_DECISION_REFUSED_WITHOUT_A_CODE'
                ELSE lc.code END AS code,
           CASE WHEN d.verdict = 'ENTER' THEN NULL ELSE coalesce(lc.cls, 'SOFTWARE') END AS cls,
           (ev.decision_id IS NOT NULL) AS has_eval,
           coalesce(ev.bind_passed, false) AS bind_passed,
           ev.bind_refusal,
           (od.decision_id IS NOT NULL) AS ordered,
           coalesce(od.filled, false) AS filled,
           coalesce(od.settled, false) AS settled
      FROM d LEFT JOIN lc USING (decision_id)
      LEFT JOIN ev USING (decision_id)
      LEFT JOIN od USING (decision_id)),
best AS (
    SELECT DISTINCT ON (r.strategy, r.fixture) r.strategy, r.fixture, r.rnk,
           r.stage, r.cls, r.code, r.has_eval, r.bind_refusal
      FROM r
     ORDER BY r.strategy, r.fixture, r.rnk DESC, r.has_eval DESC, r.code)
SELECT best.strategy, best.stage, coalesce(best.cls, '-') AS class,
       coalesce(best.code, '(entered)') AS code,
       coalesce(best.bind_refusal, '-') AS bind_refusal_if_evaluated,
       count(*) AS fixtures
  FROM best GROUP BY 1, 2, 3, 4, 5
 ORDER BY 1, min(best.rnk), fixtures DESC;

\echo '== 2.5 shared upstream: provider events -> venue contract -> sealed valuation -> paper decision (since 309)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql'),
pe AS (
    SELECT o.sport_key, o.provider_event_id,
           bool_or(o.us_market_slug IS NOT NULL) AS has_contract,
           array_remove(array_agg(DISTINCT o.us_market_slug), NULL) AS slugs
      FROM ext_candidate_outcomes o CROSS JOIN c
     WHERE o.cycle_at >= c.t AND o.provider_event_id IS NOT NULL
     GROUP BY 1, 2),
v AS (
    SELECT DISTINCT v.event_key, v.us_market_slug
      FROM external_valuations v CROSS JOIN c
     WHERE v.decided_at >= c.t),
pv AS (
    SELECT pe.sport_key, pe.provider_event_id, pe.has_contract,
           EXISTS (SELECT 1 FROM v WHERE v.event_key = pe.provider_event_id
                      OR v.us_market_slug = ANY (pe.slugs)) AS valued
      FROM pe)
SELECT pv.sport_key, count(*) AS provider_events,
       count(*) FILTER (WHERE pv.has_contract) AS with_venue_contract,
       count(*) FILTER (WHERE pv.valued) AS with_sealed_valuation
  FROM pv GROUP BY ROLLUP (pv.sport_key)
 ORDER BY pv.sport_key NULLS FIRST;

\echo '== 2.6 decisions per frozen policy: the valuations they decided (S1 linkage check)'
WITH c AS (SELECT applied_at AS t FROM schema_migrations
            WHERE version = '309_paper_profitability_bind.sql')
SELECT d.strategy, d.policy_version, count(*) AS decisions,
       count(DISTINCT d.valuation_id) AS valuations,
       count(DISTINCT d.fixture) AS fixtures,
       count(*) FILTER (WHERE d.fixture IS NULL) AS without_fixture,
       count(DISTINCT v.event_key) AS valuation_event_keys
  FROM paper_decisions d CROSS JOIN c
  LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.decided_at >= c.t
 GROUP BY 1, 2 ORDER BY 1, 2;
