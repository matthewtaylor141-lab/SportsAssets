\echo ''
\echo '=== BETTOR_EXPERIMENTAL_SHADOW: PRODUCTION VERIFICATION ==='
-- Owner directive 2026-09-19 23:0xZ. Read-only: SELECT statements only.
--
-- The questions this answers, in order of how much they matter:
--   Did the worker boot, and are the tables it needs there?
--   Are seals being written BEFORE their arrival evidence exists?
--   Did any decision execute against a book observed BEFORE it? (0 or
--   the lane is worthless.)
--   Did a genuine X1 action reach a position, and at what size?
--   Are the markouts honest about their lag?
--   Are the safety invariants still zero?

\echo ''
\echo '--- 1. THE WORKER, AND WHETHER ITS STORE IS READY ---'
SELECT 'heartbeat|' || COALESCE(status, 'ABSENT')
       || '|age_s=' || COALESCE(round(extract(epoch FROM
             (now() - beat_at)))::text, 'NONE')
       || '|storeReady=' || COALESCE(detail ->> 'storeReady', 'ABSENT')
       || '|problems=' || COALESCE(detail ->> 'problems', '[]')
       || '|armed=' || COALESCE(detail ->> 'armed', 'ABSENT')
       || '|hashesVerified=' || COALESCE(detail ->> 'hashesVerified',
                                         'ABSENT')
  FROM service_heartbeats WHERE service = 'shadow_experimental';

SELECT 'tick|drain=' || COALESCE(detail ->> 'drain', 'ABSENT')
       || '|seal=' || COALESCE(detail ->> 'seal', 'ABSENT')
       || '|markouts=' || COALESCE(detail ->> 'markouts', 'ABSENT')
  FROM service_heartbeats WHERE service = 'shadow_experimental';

\echo ''
\echo '--- 2. THE FROZEN REGISTRY AS WRITTEN DOWN ---'
SELECT experiment_id || '|' || role || '|' || readiness
       || '|sha=' || experiment_sha
       || '|frozen=' || to_char(frozen_at, 'HH24:MI:SS')
  FROM bettor_experiments ORDER BY experiment_id;

\echo ''
\echo '--- 3. SEALS: written before their evidence, by status ---'
SELECT 'seals|' || status || '|n=' || count(*)
       || '|oldest=' || COALESCE(to_char(min(sealed_at), 'HH24:MI:SS'), '-')
       || '|newest=' || COALESCE(to_char(max(sealed_at), 'HH24:MI:SS'), '-')
  FROM bettor_experimental_seals GROUP BY status ORDER BY status;

\echo ''
\echo '--- 4. THE LOOKAHEAD CHECK. THIS MUST BE ZERO. ---'
-- A decision walked against a book observed BEFORE it was sealed would
-- have filled at a price the decision already knew. Every result this
-- lane ever produces depends on this number being 0.
SELECT 'LOOKAHEAD_VIOLATIONS|' || count(*) AS lookahead
  FROM bettor_experimental_decisions d
  JOIN bettor_l2_evidence e ON e.l2_evidence_id = d.l2_evidence_id
 WHERE e.received_timestamp <= d.decision_timestamp;

SELECT 'arrival_lag_s|n=' || count(*)
       || '|min=' || COALESCE(round(min(extract(epoch FROM
             (e.received_timestamp - d.decision_timestamp))))::text, '-')
       || '|median=' || COALESCE(round(percentile_cont(0.5) WITHIN GROUP (
             ORDER BY extract(epoch FROM
               (e.received_timestamp - d.decision_timestamp))))::text, '-')
       || '|max=' || COALESCE(round(max(extract(epoch FROM
             (e.received_timestamp - d.decision_timestamp))))::text, '-')
  FROM bettor_experimental_decisions d
  JOIN bettor_l2_evidence e ON e.l2_evidence_id = d.l2_evidence_id;

\echo ''
\echo '--- 5. THE DECISIONS: action vs execution, kept apart ---'
SELECT experiment_id || '|' || action || '|' || execution_status
       || '|n=' || count(*)
       || '|intended=' || round(sum(intended_notional_usd), 2)
       || '|executed=' || COALESCE(round(sum(executed_notional_usd),
                                         2)::text, 'NONE')
       || '|unfilled=' || COALESCE(round(sum(unfilled_notional_usd),
                                         2)::text, 'NONE')
       || '|regime=' || COALESCE(max(latency_regime), '-')
  FROM bettor_experimental_decisions
 GROUP BY experiment_id, action, execution_status
 ORDER BY experiment_id, action, execution_status;

\echo ''
\echo '--- 6. EVERY FILLED TRADE, NAMED ---'
SELECT to_char(d.decision_timestamp, 'HH24:MI:SS')
       || '|' || d.experiment_id
       || '|' || d.market_id
       || '|' || d.action
       || '|' || d.execution_status
       || '|intended=$' || round(d.intended_notional_usd, 2)
       || '|executed=$' || COALESCE(round(d.executed_notional_usd,
                                          2)::text, 'NONE')
       || '|unfilled=$' || COALESCE(round(d.unfilled_notional_usd,
                                          2)::text, 'NONE')
       || '|qty=' || COALESCE(round(d.filled_qty::numeric, 2)::text, '-')
       || '|vwap=' || COALESCE(round(d.vwap::numeric, 4)::text, '-')
       || '|binding=' || d.identity_binding_status
       || '|lat_ms=' || COALESCE(round(
             d.observed_arrival_latency_ms::numeric)::text, '-')
       || '|book=' || COALESCE(d.l2_book_sha, '-')
  FROM bettor_experimental_decisions d
 WHERE d.position_id IS NOT NULL
 ORDER BY d.decision_timestamp DESC LIMIT 40;

\echo ''
\echo '--- 7. OPEN POSITIONS ---'
SELECT 'positions|' || status || '|n=' || count(*)
       || '|entry_notional=$' || round(sum(entry_notional_usd), 2)
       || '|qty=' || round(sum(entry_qty)::numeric, 2)
  FROM bettor_experimental_positions GROUP BY status ORDER BY status;

\echo ''
\echo '--- 8. MARKOUTS, AND HOW LATE THE BOOKS ACTUALLY WERE ---'
SELECT horizon || '|' || status || '|n=' || count(*)
       || '|mid=$' || COALESCE(round(sum(mid_markout_usd), 2)::text, 'NONE')
       || '|executable=$' || COALESCE(round(sum(executable_markout_usd),
                                            2)::text, 'NONE')
       || '|median_lag_s=' || COALESCE(round((percentile_cont(0.5)
             WITHIN GROUP (ORDER BY observed_lag_ms)
             / 1000.0)::numeric, 1)::text, '-')
       || '|tolerance_s=' || COALESCE(round((max(tolerance_ms)
                                            / 1000.0)::numeric, 1)::text,
                                      '-')
  FROM bettor_experimental_markouts
 GROUP BY horizon, status ORDER BY horizon, status;

\echo ''
\echo '--- 9. THE BRIDGE: requests and the evidence it returned ---'
SELECT 'requests|' || status || '|n=' || count(*)
       || '|oldest=' || COALESCE(to_char(min(requested_at), 'HH24:MI:SS'),
                                 '-')
  FROM bettor_l2_requests GROUP BY status ORDER BY status;

SELECT 'evidence|' || latency_regime || '|n=' || count(*)
       || '|instruments=' || count(DISTINCT instrument_id)
       || '|scaled=' || count(*) FILTER (WHERE price_scale IS NOT NULL)
       || '|newest=' || COALESCE(to_char(max(received_timestamp),
                                         'HH24:MI:SS'), '-')
       || '|median_venue_ms=' || COALESCE(round((percentile_cont(0.5)
             WITHIN GROUP (ORDER BY venue_request_ms))::numeric)::text, '-')
       || '|median_bridge_ms=' || COALESCE(round((percentile_cont(0.5)
             WITHIN GROUP (ORDER BY bridge_latency_ms))::numeric)::text,
                                           '-')
  FROM bettor_l2_evidence
 GROUP BY latency_regime ORDER BY latency_regime;

\echo ''
\echo '--- 10. THE ELIGIBLE POPULATION (X1 and its control on one set) ---'
SELECT 'population|' || eligible_population_id
       || '|n_experiments=' || count(DISTINCT experiment_id)
       || '|n_decisions=' || count(*)
       || '|at=' || to_char(min(decision_timestamp), 'HH24:MI:SS')
  FROM bettor_experimental_decisions
 WHERE eligible_population_id IS NOT NULL
 GROUP BY eligible_population_id
 ORDER BY min(decision_timestamp) DESC LIMIT 10;

\echo ''
\echo '--- 11. SAFETY. EVERY ONE OF THESE MUST BE ZERO. ---'
SELECT 'REAL_ORDERS|' || count(*) FILTER (WHERE real_order_submitted)
       || '|CAPITAL_AT_RISK|' || count(*) FILTER (WHERE capital_at_risk <> 0)
       || '|DECISION_GRADE|' || count(*) FILTER (WHERE NOT not_decision_grade)
       || '|FILL_WITHOUT_A_BOOK|' || count(*) FILTER (
             WHERE COALESCE(executed_notional_usd, 0) > 0
               AND l2_book_sha IS NULL)
       || '|NOT_IDENTIFIED_WITH_A_ZERO|' || count(*) FILTER (
             WHERE execution_status = 'NOT_IDENTIFIED'
               AND executed_notional_usd IS NOT NULL)
  FROM bettor_experimental_decisions;

\echo ''
\echo '--- 12. THE COLLECTOR: are corrected, YES-bound rows arriving? ---'
-- The BINDING and the book's own READABILITY are separate facts. A
-- market-level row because the venue returned no quote is the market
-- being shut; a market-level row on a MEASURED book is a defect in the
-- binding, and only printing both tells them apart.
SELECT 'opportunities_1h|' || COALESCE(microstructure ->> 'bboBinding',
                                       'ABSENT')
       || '|' || COALESCE(microstructure ->> 'featureSourceVersion',
                          'ABSENT')
       || '|status=' || COALESCE(microstructure ->> 'status', 'ABSENT')
       || '|leg=' || COALESCE(outcome_leg, 'ABSENT')
       || '|n=' || count(*)
       || '|symbols=' || count(DISTINCT symbol)
  FROM bettor_opportunities
 WHERE observed_at > now() - interval '1 hour'
 GROUP BY microstructure ->> 'bboBinding',
          microstructure ->> 'featureSourceVersion',
          microstructure ->> 'status', outcome_leg
 ORDER BY count(*) DESC LIMIT 12;
