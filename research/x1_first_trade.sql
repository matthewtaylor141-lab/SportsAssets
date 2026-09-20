-- THE X1 FIRST-TRADE PREDICATE, EVALUATED DIRECTLY.
--
-- Owner's milestone, stated exactly:
--
--     experiment_id = 'X1_SHORT_HORIZON_DIRECTION'  (NOT X1C)
--     AND action = 'BUY_YES'
--     AND identity_binding_status = 'EXACT_SAME_CONTRACT'
--     AND executed_notional_usd > 0
--     AND position_id IS NOT NULL
--
-- Built as its own file rather than read out of a long verification
-- log, because this is the claim that must not be made loosely. Every
-- conjunct is printed SEPARATELY so a near-miss reads as a near-miss
-- rather than as a match, and X1C rows are excluded by the WHERE
-- clause rather than by the reader's attention.
--
-- READ-ONLY. Nothing here writes, backdates or invents a value.

\echo '--- 1. THE PREDICATE, CONJUNCT BY CONJUNCT (X1 ONLY) ---'
SELECT 'x1|' || d.experimental_decision_id
       || '|at=' || to_char(d.decision_timestamp, 'HH24:MI:SS')
       || '|market=' || d.market_id
       || '|leg=' || COALESCE(d.outcome_leg, 'NULL')
       || '|action=' || d.action
       || '|is_buy_yes=' || (d.action = 'BUY_YES')
       || '|binding=' || COALESCE(d.identity_binding_status, 'NULL')
       || '|is_exact=' || (d.identity_binding_status = 'EXACT_SAME_CONTRACT')
       || '|executed=' || COALESCE(d.executed_notional_usd::text, 'NULL')
       || '|executed_gt_0=' || (COALESCE(d.executed_notional_usd, 0) > 0)
       || '|position=' || COALESCE(d.position_id, 'NULL')
       || '|has_position=' || (d.position_id IS NOT NULL)
       || '|MILESTONE=' || (d.action = 'BUY_YES'
                            AND d.identity_binding_status
                                = 'EXACT_SAME_CONTRACT'
                            AND COALESCE(d.executed_notional_usd, 0) > 0
                            AND d.position_id IS NOT NULL)
  FROM bettor_experimental_decisions d
 WHERE d.experiment_id = 'X1_SHORT_HORIZON_DIRECTION'
   AND d.action <> 'NO_TRADE'
 ORDER BY d.decision_timestamp
 LIMIT 40;

\echo ''
\echo '--- 2. THE COUNT, so "none yet" is a number and not an absence ---'
SELECT 'count|experiment=' || d.experiment_id
       || '|buy_yes=' || count(*) FILTER (WHERE d.action = 'BUY_YES')
       || '|buy_no=' || count(*) FILTER (WHERE d.action = 'BUY_NO')
       || '|no_trade=' || count(*) FILTER (WHERE d.action = 'NO_TRADE')
       || '|with_position=' || count(*) FILTER (WHERE d.position_id
                                                     IS NOT NULL)
       || '|MILESTONE_ROWS=' || count(*) FILTER (
              WHERE d.action = 'BUY_YES'
                AND d.identity_binding_status = 'EXACT_SAME_CONTRACT'
                AND COALESCE(d.executed_notional_usd, 0) > 0
                AND d.position_id IS NOT NULL)
       || '|executed_usd=' || round(COALESCE(sum(
              d.executed_notional_usd), 0)::numeric, 2)
  FROM bettor_experimental_decisions d
 GROUP BY d.experiment_id
 ORDER BY d.experiment_id;

\echo ''
\echo '--- 3. THE FIRST QUALIFYING X1 TRADE, IN FULL ---'
-- Ordered by decision_timestamp so "first" means first, not newest.
SELECT 'FIRST_X1|DECISION_ID=' || d.experimental_decision_id
       || '|MARKET=' || d.market_id
       || '|LEG=' || COALESCE(d.outcome_leg, 'NULL')
       || '|INSTRUMENT=' || COALESCE(d.institutional_instrument_id, 'NULL')
       || '|DECISION_TIMESTAMP=' || to_char(d.decision_timestamp,
                                            'YYYY-MM-DD HH24:MI:SS.MS')
       || '|ACTION=' || d.action
       || '|REASON=' || COALESCE(d.why, 'NULL')
       || '|SIGNAL_STRENGTH=' || COALESCE(d.signal_strength::text, 'NULL')
       || '|MODEL_VERSION=' || COALESCE(d.experiment_version, 'NULL')
       || '|POLICY_SHA=' || COALESCE(d.experiment_sha, 'NULL')
       || '|FEATURE_SOURCE_VERSION=' || COALESCE(d.feature_source_version,
                                                 'NULL')
       || '|IDENTITY=' || COALESCE(d.identity_binding_status, 'NULL')
       || '|IDENTITY_SHA=' || COALESCE(d.identity_binding_sha, 'NULL')
  FROM bettor_experimental_decisions d
 WHERE d.experiment_id = 'X1_SHORT_HORIZON_DIRECTION'
   AND d.action = 'BUY_YES'
   AND d.identity_binding_status = 'EXACT_SAME_CONTRACT'
   AND COALESCE(d.executed_notional_usd, 0) > 0
   AND d.position_id IS NOT NULL
 ORDER BY d.decision_timestamp
 LIMIT 5;

\echo ''
\echo '--- 3b. THE SAME TRADE: money, book and latency ---'
SELECT 'FIRST_X1_FILL|' || d.experimental_decision_id
       || '|INTENDED_NOTIONAL=' || COALESCE(d.intended_notional_usd::text,
                                            'NULL')
       || '|EXECUTED_ENTRY_NOTIONAL=' || COALESCE(
              d.executed_notional_usd::text, 'NULL')
       || '|UNFILLED_NOTIONAL=' || COALESCE(d.unfilled_notional_usd::text,
                                            'NULL')
       || '|FILLED_QTY=' || COALESCE(d.filled_qty::text, 'NULL')
       || '|ENTRY_VWAP=' || COALESCE(d.vwap::text, 'NULL')
       || '|SPREAD_COST=' || COALESCE(d.spread_cost::text, 'NULL')
       || '|SLIPPAGE=' || COALESCE(d.slippage::text, 'NULL')
       || '|DECISION_BOOK=' || COALESCE(d.l2_book_sha, 'NULL')
       || '|WALKED_BOOK=' || COALESCE(d.walked_book_sha, 'NULL')
       || '|L2_EVIDENCE_ID=' || COALESCE(d.l2_evidence_id, 'NULL')
       || '|BOOK_FRESHNESS=' || COALESCE(d.book_freshness_status, 'NULL')
       || '|BOOK_AGE_MS=' || COALESCE(round(d.book_age_ms::numeric, 1)::text,
                                      'NULL')
       || '|POSITION_ID=' || COALESCE(d.position_id, 'NULL')
       || '|EXECUTION_STATUS=' || d.execution_status
       || '|REAL_ORDER_SUBMITTED=' || d.real_order_submitted
       || '|CAPITAL_AT_RISK=' || COALESCE(d.capital_at_risk::text, 'NULL')
  FROM bettor_experimental_decisions d
 WHERE d.experiment_id = 'X1_SHORT_HORIZON_DIRECTION'
   AND d.action = 'BUY_YES'
   AND COALESCE(d.executed_notional_usd, 0) > 0
   AND d.position_id IS NOT NULL
 ORDER BY d.decision_timestamp
 LIMIT 5;

\echo ''
\echo '--- 3c. THE NINE INSTANTS, never collapsed into one number ---'
SELECT 'FIRST_X1_LATENCY|' || d.experimental_decision_id
       -- venue_source_timestamp is TEXT: the venue's own string, kept
       -- verbatim rather than reformatted into something it did not say.
       || '|VENUE_SOURCE=' || COALESCE(d.venue_source_timestamp, 'NULL')
       || '|BETTOR_RECEIVED=' || COALESCE(to_char(d.bettor_received_timestamp,
                                                  'HH24:MI:SS.MS'), 'NULL')
       || '|FEATURES_SEALED=' || COALESCE(to_char(d.features_sealed_timestamp,
                                                  'HH24:MI:SS.MS'), 'NULL')
       || '|MODEL_START=' || COALESCE(to_char(d.model_start_timestamp,
                                              'HH24:MI:SS.MS'), 'NULL')
       || '|MODEL_END=' || COALESCE(to_char(d.model_end_timestamp,
                                            'HH24:MI:SS.MS'), 'NULL')
       || '|DECISION=' || to_char(d.decision_timestamp, 'HH24:MI:SS.MS')
       || '|MODELED_SEND=' || COALESCE(to_char(d.modeled_send_timestamp,
                                               'HH24:MI:SS.MS'), 'NULL')
       || '|MODELED_ARRIVAL=' || COALESCE(to_char(d.modeled_arrival_timestamp,
                                                  'HH24:MI:SS.MS'), 'NULL')
       || '|PERSISTED=' || COALESCE(to_char(d.persisted_timestamp,
                                            'HH24:MI:SS.MS'), 'NULL')
       || '|MARKET_DATA_LAG_MS=' || COALESCE(round(
              d.market_data_lag_ms::numeric, 1)::text, 'NULL')
       || '|FEATURE_COMPUTE_MS=' || COALESCE(round(
              d.feature_compute_ms::numeric, 1)::text, 'NULL')
       || '|MODEL_COMPUTE_MS=' || COALESCE(round(
              d.model_compute_ms::numeric, 1)::text, 'NULL')
       || '|SOURCE_TO_DECISION_MS=' || COALESCE(round(
              d.source_to_decision_ms::numeric, 1)::text, 'NULL')
       || '|MODELED_EXECUTION_LATENCY_MS=' || COALESCE(round(
              d.modeled_execution_latency_ms::numeric, 1)::text, 'NULL')
       || '|EXECUTION_LATENCY_BASIS=' || COALESCE(d.execution_latency_basis,
                                                  'NULL')
       || '|LATENCY_REGIME=' || COALESCE(d.latency_regime, 'NULL')
  FROM bettor_experimental_decisions d
 WHERE d.experiment_id = 'X1_SHORT_HORIZON_DIRECTION'
   AND d.action = 'BUY_YES'
   AND COALESCE(d.executed_notional_usd, 0) > 0
   AND d.position_id IS NOT NULL
 ORDER BY d.decision_timestamp
 LIMIT 5;

\echo ''
\echo '--- 4. X1 POSITIONS, kept strictly apart from X1C ---'
SELECT 'positions|' || p.experiment_id
       || '|status=' || p.status
       || '|n=' || count(*)
       || '|entry_notional=' || round(COALESCE(sum(p.entry_notional_usd),
                                               0)::numeric, 2)
       || '|qty=' || round(COALESCE(sum(p.entry_qty), 0)::numeric, 2)
  FROM bettor_experimental_positions p
 GROUP BY p.experiment_id, p.status
 ORDER BY p.experiment_id, p.status;

\echo ''
\echo '--- 5. X1 MARKOUTS with their realized timing (eligible or not) ---'
-- 30S is UNOBSERVABLE_AT_CURRENT_DIRECT_L2_CAPTURE_FREQUENCY and is
-- shown but never eligible. A 60S or 300S row is eligible only if its
-- OWN recorded timestamps satisfy the frozen contract.
SELECT 'x1_markout|' || m.horizon
       || '|' || m.experimental_decision_id
       || '|realized_offset_s=' || round(EXTRACT(EPOCH FROM (
              m.observed_at - d.decision_timestamp))::numeric, 1)
       || '|target_error_s=' || round(EXTRACT(EPOCH FROM (
              m.observed_at - m.target_at))::numeric, 1)
       || '|tolerance_s=' || round((m.tolerance_ms / 1000.0)::numeric, 1)
       || '|PERFORMANCE_ELIGIBLE=' || (
              m.l2_book_sha IS NOT NULL
              AND m.target_at IS NOT NULL AND m.tolerance_ms IS NOT NULL
              AND abs(EXTRACT(EPOCH FROM (m.observed_at - m.target_at)))
                  * 1000.0 <= m.tolerance_ms
              AND m.horizon IN ('60S', '300S'))
       || '|mid=' || COALESCE(round(m.mid_markout_usd::numeric, 2)::text,
                              'NULL')
       || '|executable=' || COALESCE(round(m.executable_markout_usd::numeric,
                                           2)::text, 'NULL')
       || '|status=' || m.status
  FROM bettor_experimental_markouts m
  JOIN bettor_experimental_decisions d
    ON d.experimental_decision_id = m.experimental_decision_id
 WHERE d.experiment_id = 'X1_SHORT_HORIZON_DIRECTION'
 ORDER BY d.decision_timestamp, m.horizon
 LIMIT 40;

\echo ''
\echo '--- 6. SAFETY. EVERY ONE MUST BE ZERO. ---'
SELECT 'SAFETY|REAL_ORDERS=' || count(*) FILTER (
           WHERE d.real_order_submitted)
       || '|CAPITAL_AT_RISK=' || count(*) FILTER (
           WHERE COALESCE(d.capital_at_risk, 0) <> 0)
       || '|DECISION_GRADE=' || count(*) FILTER (
           WHERE NOT d.not_decision_grade)
  FROM bettor_experimental_decisions d;

\echo ''
\echo '--- 7. CAPITAL FROM THE POSITION EVENT SERIES (not entry notional) ---'
-- S4. The ledger carries ONE event per position: opened_at. There is
-- no closed_at and the only write in the codebase is the INSERT, so a
-- position can never leave 'OPEN' -- an UPDATE is refused by the
-- append-only trigger. Every position therefore overlaps every later
-- one and capital is strictly ADDITIVE: CURRENT == PEAK, and
-- CAPITAL_TURNS of 1.0 means no capital has ever been recycled.
WITH pos AS (
    SELECT experiment_id, market_id, opened_at,
           entry_notional_usd::numeric AS notional
      FROM bettor_experimental_positions
)
SELECT 'capital|' || experiment_id
       || '|POSITIONS=' || count(*)
       || '|ENTRY_NOTIONAL_PLAYED=' || round(sum(notional), 2)
       || '|CURRENT_CAPITAL_DEPLOYED=' || round(sum(notional), 2)
       || '|PEAK_CAPITAL_DEPLOYED=' || round(sum(notional), 2)
       || '|CAPITAL_HOURS=' || round(sum(notional * EXTRACT(EPOCH FROM (
              now() - opened_at)) / 3600.0), 4)
       || '|AVERAGE_CAPITAL_DEPLOYED=' || round(
              sum(notional * EXTRACT(EPOCH FROM (now() - opened_at)))
              / NULLIF(EXTRACT(EPOCH FROM (now() - min(opened_at))), 0), 2)
       || '|CAPITAL_TURNS=1.0_NO_RECYCLING'
       || '|CLOSED_POSITIONS=' || count(*) FILTER (WHERE FALSE)
       || '|first=' || to_char(min(opened_at), 'HH24:MI:SS')
  FROM pos
 GROUP BY experiment_id
 ORDER BY experiment_id;

\echo ''
\echo '--- 8. CONCENTRATION: four entries in one market are not four samples ---'
SELECT 'concentration|' || p.experiment_id
       || '|POSITIONS=' || count(*)
       || '|UNIQUE_MARKETS_TRADED=' || count(DISTINCT p.market_id)
       || '|UNIQUE_EVENTS_TRADED=' || count(DISTINCT array_to_string(
              (string_to_array(p.market_id, '-'))[1:5], '-'))
       || '|MAX_MARKET_CONCENTRATION_PCT=' || round(100.0 * max(
              per_market.notional) / NULLIF(sum(DISTINCT
              per_market.total), 0), 2)
  FROM bettor_experimental_positions p
  JOIN LATERAL (
      SELECT sum(x.entry_notional_usd::numeric) AS notional,
             (SELECT sum(y.entry_notional_usd::numeric)
                FROM bettor_experimental_positions y
               WHERE y.experiment_id = p.experiment_id) AS total
        FROM bettor_experimental_positions x
       WHERE x.experiment_id = p.experiment_id
         AND x.market_id = p.market_id
  ) per_market ON TRUE
 GROUP BY p.experiment_id
 ORDER BY p.experiment_id;

\echo ''
\echo '--- 9. LATENCY CLOCK DOMAIN: is the lifecycle monotonic? ---'
-- S6. DECISION_COMMIT comes from the tick's sealed clock while
-- MODEL_START/END and BETTOR_RECEIVED come from wall-clock, so their
-- difference has no physical meaning. Where the ordering fails, no
-- latency number is reported at all.
SELECT 'clockdomain|' || d.experiment_id
       || '|n=' || count(*)
       || '|monotonic=' || count(*) FILTER (
              WHERE d.bettor_received_timestamp
                    <= d.features_sealed_timestamp
                AND d.features_sealed_timestamp <= d.model_start_timestamp
                AND d.model_start_timestamp <= d.model_end_timestamp
                AND d.model_end_timestamp <= d.decision_timestamp
                AND d.decision_timestamp <= d.modeled_arrival_timestamp)
       || '|decision_before_model_end=' || count(*) FILTER (
              WHERE d.decision_timestamp < d.model_end_timestamp)
       || '|LATENCY_STATUS=' || (CASE WHEN count(*) FILTER (
              WHERE d.decision_timestamp < d.model_end_timestamp) > 0
              THEN 'NOT_IDENTIFIED_CLOCK_DOMAIN_CONFLICT'
              ELSE 'MONOTONIC' END)
       || '|modeled_arrival_minus_model_end_ms_p50=' || round(
              percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM
                  (d.modeled_arrival_timestamp - d.model_end_timestamp))
                  * 1000.0)::numeric, 1)
  FROM bettor_experimental_decisions d
 WHERE d.position_id IS NOT NULL
 GROUP BY d.experiment_id
 ORDER BY d.experiment_id;

\echo ''
\echo '--- 10. SETTLEMENT SEMANTICS on the traded markets (not identity) ---'
SELECT 'settlement|' || b.market_id
       || '|leg=' || b.outcome_leg
       || '|identity=' || b.identity_status
       || '|SETTLEMENT_SEMANTICS_STATUS=' || (CASE
              WHEN b.settlement_prose_conflict IS NOT NULL
              THEN 'CONFLICTING_VENUE_PROSE' ELSE 'IDENTIFIED' END)
       || '|conflict=' || COALESCE(left(b.settlement_prose_conflict, 90),
                                   'NONE')
  FROM bettor_identity_bindings b
 WHERE b.market_id IN (SELECT DISTINCT market_id
                         FROM bettor_experimental_positions)
 ORDER BY b.market_id, b.outcome_leg
 LIMIT 20;

\echo ''
\echo '--- 11. RE-ENTRY: spacing against the frozen 60S horizon ---'
-- S8. EXIT_RULE_HORIZON says "no re-entry inside the horizon". X1's
-- frozen horizon is 60S. This prints the gap between consecutive
-- entries in the SAME market so the rule can be checked rather than
-- assumed either way.
SELECT 'reentry|' || p.experiment_id
       || '|' || p.market_id
       || '|opened=' || to_char(p.opened_at, 'HH24:MI:SS')
       || '|since_previous_s=' || COALESCE(round(EXTRACT(EPOCH FROM (
              p.opened_at - lag(p.opened_at) OVER (
                  PARTITION BY p.experiment_id, p.market_id
                   ORDER BY p.opened_at)))::numeric, 1)::text, 'FIRST')
       || '|INSIDE_60S_HORIZON=' || COALESCE((EXTRACT(EPOCH FROM (
              p.opened_at - lag(p.opened_at) OVER (
                  PARTITION BY p.experiment_id, p.market_id
                   ORDER BY p.opened_at))) < 60)::text, 'N/A')
       || '|notional=' || round(p.entry_notional_usd::numeric, 2)
  FROM bettor_experimental_positions p
 ORDER BY p.experiment_id, p.market_id, p.opened_at
 LIMIT 40;
