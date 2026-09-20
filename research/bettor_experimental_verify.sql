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

\echo ''
\echo '--- 13. THE DIRECT WORKER: is the credential authenticating? ---'
-- §1/§3. The heartbeat carries the boot verdict and the sweep's own
-- counters. AUTH_STATUS, TOKEN_SCOPES and READ_L2_PERMISSION are
-- written there at boot; nothing about any secret's VALUE is.
SELECT 'institutional_md|' || status
       || '|auth=' || COALESCE(detail #>> '{auth,AUTH_STATUS}',
                               detail ->> 'authStatus', 'ABSENT')
       || '|readL2=' || COALESCE(detail #>> '{auth,READ_L2_PERMISSION}',
                                 'ABSENT')
       || '|mechanism=' || COALESCE(detail ->> 'marketDataMechanism',
                                    'ABSENT')
       || '|orderImpl=' || COALESCE(
              detail ->> 'orderSubmissionImplementation', 'ABSENT')
       || '|books=' || COALESCE(detail ->> 'books', 'ABSENT')
       || '|priceable=' || COALESCE(detail ->> 'priceable', 'ABSENT')
       || '|read=' || COALESCE(detail ->> 'read', 'ABSENT')
       || '|failed=' || COALESCE(detail ->> 'failed', 'ABSENT')
       || '|venueMsP50=' || COALESCE(detail ->> 'venueMsP50', 'ABSENT')
       || '|at=' || to_char(beat_at, 'HH24:MI:SS')
  FROM service_heartbeats
 WHERE service = 'institutional_md'
 ORDER BY beat_at DESC LIMIT 5;

\echo ''
\echo '--- 14. THE THREE REGIMES, COUNTED APART (never pooled) ---'
-- A book the worker held in memory and a book a CI runner fetched
-- minutes late are different execution environments. If these two ever
-- appear as one row, the schema has stopped keeping them apart.
SELECT 'regime|' || COALESCE(latency_regime, 'ABSENT')
       || '|decisions=' || count(*)
       || '|filled=' || count(*) FILTER (
              WHERE COALESCE(executed_notional_usd, 0) > 0)
       || '|executed_usd=' || round(
              COALESCE(sum(executed_notional_usd), 0)::numeric, 2)
  FROM bettor_experimental_decisions
 WHERE decision_timestamp > now() - interval '24 hours'
 GROUP BY latency_regime
 ORDER BY count(*) DESC;

\echo ''
\echo '--- 15. §8: THE INSTANTS, NEVER COLLAPSED INTO ONE NUMBER ---'
-- Six intervals, each derived from two named instants. A slow venue, a
-- slow model and a slow persist have completely different remedies and
-- one number cannot tell them apart.
SELECT 'latency|' || experiment_id
       || '|n=' || count(*)
       || '|mdLagP50=' || COALESCE(round(percentile_cont(0.5)
              WITHIN GROUP (ORDER BY market_data_lag_ms)::numeric, 1)
              ::text, 'NULL')
       || '|featureP50=' || COALESCE(round(percentile_cont(0.5)
              WITHIN GROUP (ORDER BY feature_compute_ms)::numeric, 1)
              ::text, 'NULL')
       || '|modelP50=' || COALESCE(round(percentile_cont(0.5)
              WITHIN GROUP (ORDER BY model_compute_ms)::numeric, 1)
              ::text, 'NULL')
       || '|srcToDecisionP50=' || COALESCE(round(percentile_cont(0.5)
              WITHIN GROUP (ORDER BY source_to_decision_ms)::numeric, 1)
              ::text, 'NULL')
       || '|srcToDecisionP95=' || COALESCE(round(percentile_cont(0.95)
              WITHIN GROUP (ORDER BY source_to_decision_ms)::numeric, 1)
              ::text, 'NULL')
       || '|modeledExecP50=' || COALESCE(round(percentile_cont(0.5)
              WITHIN GROUP (ORDER BY modeled_execution_latency_ms)::numeric,
              1)::text, 'NULL')
  FROM bettor_experimental_decisions
 WHERE decision_timestamp > now() - interval '24 hours'
   AND evidence_environment = 'DIRECT_INSTITUTIONAL_WORKER'
 GROUP BY experiment_id
 ORDER BY experiment_id;

\echo ''
\echo '--- 16. §9: THE BASIS IS MODELED, ON EVERY DIRECT ROW ---'
-- "Never represent it as observed production execution latency." This
-- lane has never sent an order, so no execution latency has been
-- observed and no row may claim one.
SELECT 'basis|' || COALESCE(execution_latency_basis, 'NULL')
       || '|n=' || count(*)
  FROM bettor_experimental_decisions
 WHERE evidence_environment = 'DIRECT_INSTITUTIONAL_WORKER'
 GROUP BY execution_latency_basis
 ORDER BY count(*) DESC;

\echo ''
\echo '--- 17. §6: WAS THE BOOK CURRENT WHEN IT WAS WALKED? ---'
-- A stale book is not executable evidence, so a STALE or ABSENT row
-- must carry no fill. That is asserted rather than described.
SELECT 'book|' || COALESCE(book_freshness_status, 'NULL')
       || '|n=' || count(*)
       || '|filled=' || count(*) FILTER (
              WHERE COALESCE(executed_notional_usd, 0) > 0)
       || '|ageMsP50=' || COALESCE(round(percentile_cont(0.5)
              WITHIN GROUP (ORDER BY book_age_ms)::numeric, 1)::text, 'NULL')
  FROM bettor_experimental_decisions
 WHERE evidence_environment = 'DIRECT_INSTITUTIONAL_WORKER'
 GROUP BY book_freshness_status
 ORDER BY count(*) DESC;

\echo ''
\echo '--- 18. THE DIRECT SAFETY RAILS. EVERY ONE MUST BE ZERO. ---'
SELECT 'STALE_BOOK_FILLED|' || count(*) FILTER (
             WHERE book_freshness_status IS DISTINCT FROM 'CURRENT'
               AND COALESCE(executed_notional_usd, 0) > 0)
       || '|OBSERVED_EXECUTION_LATENCY_CLAIMED|' || count(*) FILTER (
             WHERE execution_latency_basis = 'OBSERVED_TRANSPORT_LATENCY'
                                             '_NOT_EXECUTION')
       || '|ARRIVAL_BEFORE_DECISION|' || count(*) FILTER (
             WHERE modeled_arrival_timestamp < decision_timestamp)
       || '|BOOK_RECEIVED_AFTER_ARRIVAL|' || count(*) FILTER (
             WHERE bettor_received_timestamp > modeled_arrival_timestamp)
       || '|NEGATIVE_MODEL_TIME|' || count(*) FILTER (
             WHERE model_compute_ms < 0)
  FROM bettor_experimental_decisions
 WHERE evidence_environment = 'DIRECT_INSTITUTIONAL_WORKER';

\echo ''
\echo '--- 19. THE FUNNEL: why activity is or is not occurring ---'
-- Owner 2026-09-20. Every bucket is a predicate over rows that already
-- exist, so the funnel cannot disagree with the ledger it describes.
-- The NO_TRADE split is exhaustive because X1's frozen gate emits
-- exactly two reasons (SPREAD_NOT_IDENTIFIED, SPREAD_ABOVE_FROZEN_MAX)
-- and returns nothing otherwise; the remainder is printed so the
-- partition can be checked rather than trusted.
SELECT 'FUNNEL_24H'
       || '|OPPORTUNITIES=' || (
              SELECT count(*) FROM bettor_experimental_observations
               WHERE observed_at > now() - interval '24 hours')
       || '|X1_ELIGIBLE=' || count(*) FILTER (
              WHERE experiment_id = 'X1_SHORT_HORIZON_DIRECTION')
       || '|NO_TRADE_SPREAD=' || count(*) FILTER (
              WHERE action = 'NO_TRADE' AND why LIKE 'SPREAD\_%')
       || '|NO_TRADE_SIGNAL=' || count(*) FILTER (
              WHERE action = 'NO_TRADE'
                AND (why IS NULL OR why NOT LIKE 'SPREAD\_%'))
       || '|BUY_YES=' || count(*) FILTER (WHERE action = 'BUY_YES')
       || '|BUY_NO=' || count(*) FILTER (WHERE action = 'BUY_NO')
       || '|BUY_BLOCKED_IDENTITY=' || count(*) FILTER (
              WHERE action LIKE 'BUY%'
                AND execution_status =
                    'BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE')
       || '|BUY_BLOCKED_STALE_BOOK=' || count(*) FILTER (
              WHERE action LIKE 'BUY%'
                AND book_freshness_status IN ('STALE', 'ABSENT'))
       || '|BUY_EXECUTED=' || count(*) FILTER (
              WHERE action LIKE 'BUY%'
                AND COALESCE(executed_notional_usd, 0) > 0)
       || '|PARTIAL_FILLS=' || count(*) FILTER (
              WHERE COALESCE(executed_notional_usd, 0) > 0
                AND COALESCE(unfilled_notional_usd, 0) > 0)
       || '|FULL_FILLS=' || count(*) FILTER (
              WHERE COALESCE(executed_notional_usd, 0) > 0
                AND COALESCE(unfilled_notional_usd, 0) = 0)
       || '|UNACCOUNTED_BUYS=' || count(*) FILTER (
              WHERE action LIKE 'BUY%'
                AND COALESCE(executed_notional_usd, 0) = 0
                AND execution_status <>
                    'BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE'
                AND (book_freshness_status IS NULL
                     OR book_freshness_status NOT IN ('STALE', 'ABSENT')))
  FROM bettor_experimental_decisions
 WHERE decision_timestamp > now() - interval '24 hours';

\echo ''
\echo '--- 20. THE IDENTITY CENSUS: is the funnel quiet or is it empty? ---'
-- Zero trades with zero eligible markets and zero trades with eight
-- eligible markets are different situations. This says which.
WITH current_binding AS (
    SELECT DISTINCT ON (market_id, outcome_leg) *
      FROM bettor_identity_bindings
     ORDER BY market_id, outcome_leg, resolved_at DESC
)
SELECT 'IDENTITY'
       || '|MARKETS_BOUND=' || count(DISTINCT market_id)
       || '|YES_EXECUTION_ELIGIBLE=' || count(*) FILTER (
              WHERE execution_eligible AND outcome_leg IN ('yes', 'long'))
       || '|NO_EXECUTION_ELIGIBLE=' || count(*) FILTER (
              WHERE execution_eligible AND outcome_leg IN ('no', 'short'))
       || '|EXACT=' || count(*) FILTER (
              WHERE identity_status = 'EXACT_SAME_CONTRACT')
       || '|COMPLEMENT_PENDING=' || count(*) FILTER (
              WHERE identity_status LIKE 'STRUCTURALLY%')
       || '|AMBIGUOUS=' || count(*) FILTER (
              WHERE identity_status = 'AMBIGUOUS')
       || '|UNRESOLVED=' || count(*) FILTER (
              WHERE identity_status = 'NOT_IDENTIFIED')
       || '|PROSE_CONFLICTS=' || count(*) FILTER (
              WHERE settlement_prose_conflict IS NOT NULL)
  FROM current_binding;

\echo ''
\echo '--- 21. THE NO_TRADE REASONS, in the frozen rule own words ---'
SELECT 'why|' || COALESCE(why, 'NULL')
       || '|n=' || count(*)
       || '|markets=' || count(DISTINCT market_id)
  FROM bettor_experimental_decisions
 WHERE action = 'NO_TRADE'
   AND decision_timestamp > now() - interval '24 hours'
 GROUP BY why
 ORDER BY count(*) DESC LIMIT 12;
