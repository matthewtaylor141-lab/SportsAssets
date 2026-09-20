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
