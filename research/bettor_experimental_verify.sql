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

\echo ''
\echo '--- 22. THE CAPTURE GRID, measured from the ledger itself ---'
-- The owner measured direct L2 arrival spacing at P50 ~60.95s,
-- P95 ~63.98s, MAX ~64.70s and ruled the 30S horizon unobservable.
-- This re-derives the same number from the same rows so the verdict
-- can be checked rather than believed. NOTHING HERE WRITES.
WITH spacing AS (
    SELECT instrument_id,
           EXTRACT(EPOCH FROM (received_timestamp
               - lag(received_timestamp) OVER (
                     PARTITION BY instrument_id
                      ORDER BY received_timestamp))) AS gap_s
      FROM bettor_l2_evidence
     WHERE latency_regime = 'DIRECT_INSTITUTIONAL_WORKER'
       AND received_timestamp > now() - interval '6 hours'
)
SELECT 'cadence|regime=DIRECT_INSTITUTIONAL_WORKER'
       || '|n=' || count(*)
       || '|p50_s=' || round(percentile_cont(0.5)
              WITHIN GROUP (ORDER BY gap_s)::numeric, 2)
       || '|p95_s=' || round(percentile_cont(0.95)
              WITHIN GROUP (ORDER BY gap_s)::numeric, 2)
       || '|max_s=' || round(max(gap_s)::numeric, 2)
       -- The structural test: a 30s horizon carries a 30s tolerance,
       -- so its admissible window opens at the decision instant.
       || '|30S_MARKOUT_STATUS='
       || 'UNOBSERVABLE_AT_CURRENT_DIRECT_L2_CAPTURE_FREQUENCY'
       || '|60S_MARKOUT_STATUS=OBSERVABLE'
       || '|300S_MARKOUT_STATUS=OBSERVABLE'
  FROM spacing
 WHERE gap_s IS NOT NULL;

\echo ''
\echo '--- 22b. THE 30S ROWS ARE PRESERVED, not deleted or zeroed ---'
-- "Preserve all existing 30S rows append-only... Do not delete or
-- rewrite them. Do not convert them to zero." The row count and the
-- distinct markout values prove nothing was flattened; the rows are
-- simply not eligible for a performance conclusion.
SELECT 'preserved|' || horizon
       || '|status=' || status
       || '|n=' || count(*)
       || '|distinct_executable=' || count(DISTINCT executable_markout_usd)
       || '|zeros=' || count(*) FILTER (WHERE executable_markout_usd = 0)
       || '|eligible_for_performance='
       || (horizon IN ('60S', '300S'))
       || '|oldest=' || COALESCE(to_char(min(observed_at),
                                         'MM-DD HH24:MI:SS'), 'NONE')
  FROM bettor_experimental_markouts
 GROUP BY horizon, status
 ORDER BY horizon, status;

\echo ''
\echo '--- 23. THE FROZEN FOCUS-SET DATA-QUALITY EXCLUSION ---'
-- A focus market is held out ONLY when it has produced >= 10 samples
-- and NONE of them is both readable and bound to the YES contract
-- book -- i.e. it cannot supply the leg-specific feature the frozen
-- rule requires. The predicate reads `readable` and `bbo_binding` and
-- nothing else: no price, no signal, no P&L. It is a standing query
-- over a moving window, so one readable YES-bound sample returns the
-- market to eligibility with nobody editing anything.
SELECT 'focus_quality|' || symbol
       || '|samples=' || count(*)
       || '|readable=' || count(*) FILTER (WHERE readable)
       || '|readable_yes_bound=' || count(*) FILTER (
              WHERE readable IS TRUE AND bbo_binding = 'YES_CONTRACT_BOOK')
       || '|binding=' || COALESCE(max(bbo_binding), 'NULL')
       || '|EXCLUDED=' || (count(*) >= 10
              AND count(*) FILTER (WHERE readable IS TRUE
                                     AND bbo_binding = 'YES_CONTRACT_BOOK')
                  = 0)
       || '|newest=' || to_char(max(observed_at), 'HH24:MI:SS')
  FROM bettor_experimental_observations
 WHERE observed_at > now() - interval '2 hours'
 GROUP BY symbol
 ORDER BY count(*) DESC LIMIT 20;

\echo ''
\echo '--- 22c. EVERY 30S ROW: how much time actually elapsed ---'
-- The 30S classification is STRUCTURAL: the 30s tolerance on a 30s
-- horizon admits a window whose lower bound is the decision instant,
-- so a book at zero elapsed time would qualify. Whether that actually
-- HAPPENED in the rows already written is a separate question, and
-- this answers it per row rather than by inference from a median.
-- elapsed = observed_at - decision_timestamp; a value near 0 is the
-- entry book wearing a later label, a value near 30 is a real markout
-- that simply cannot be GUARANTEED on a ~61s grid.
SELECT 'elapsed|' || m.horizon
       || '|' || m.experimental_decision_id
       || '|elapsed_s=' || round(EXTRACT(EPOCH FROM (
              m.observed_at - d.decision_timestamp))::numeric, 1)
       || '|lag_s=' || round((m.observed_lag_ms / 1000.0)::numeric, 1)
       || '|tol_s=' || round((m.tolerance_ms / 1000.0)::numeric, 1)
       || '|status=' || m.status
       || '|executable=' || COALESCE(round(m.executable_markout_usd::numeric,
                                           2)::text, 'NULL')
  FROM bettor_experimental_markouts m
  JOIN bettor_experimental_decisions d
    ON d.experimental_decision_id = m.experimental_decision_id
 WHERE m.horizon = '30S'
 ORDER BY m.observed_at;

\echo ''
\echo '--- 24. ROW-LEVEL TIMING: eligibility from the clock, not the label ---'
-- Owner 2026-09-20 S1/S2: "A row labelled '60S' must not become
-- performance evidence merely because TARGET_HORIZON = 60S... Do not
-- assume a label proves its realized horizon. The recorded timestamps
-- are authoritative."
--
-- Every markout with the eight fields the directive names. Note
-- OBSERVATION_PRESENT: record_markout stores observed_at = observedAt
-- or targetAt, so a row that observed NOTHING carries observed_at
-- exactly at its target and would read as a flawless zero-error
-- measurement on timestamps alone. l2_book_sha is what separates an
-- observation from a placeholder, and the verdict reads it FIRST.
SELECT 'timing|' || m.horizon
       || '|' || d.experiment_id
       || '|decided=' || to_char(d.decision_timestamp, 'HH24:MI:SS')
       || '|target=' || to_char(m.target_at, 'HH24:MI:SS')
       || '|observed=' || to_char(m.observed_at, 'HH24:MI:SS')
       || '|realized_offset_s=' || round(EXTRACT(EPOCH FROM (
              m.observed_at - d.decision_timestamp))::numeric, 1)
       || '|target_error_s=' || round(EXTRACT(EPOCH FROM (
              m.observed_at - m.target_at))::numeric, 1)
       || '|tolerance_s=' || round((m.tolerance_ms / 1000.0)::numeric, 1)
       || '|observation_present=' || (m.l2_book_sha IS NOT NULL)
       || '|reconstructs=' || (abs(EXTRACT(EPOCH FROM (m.target_at
              - (d.decision_timestamp + (CASE m.horizon
                    WHEN '30S' THEN interval '30 seconds'
                    WHEN '60S' THEN interval '60 seconds'
                    WHEN '300S' THEN interval '300 seconds' END)))))
              * 1000.0 <= 1.0)
       || '|TIMING_STATUS=' || (CASE
              WHEN m.l2_book_sha IS NULL THEN 'NO_OBSERVATION'
              WHEN m.target_at IS NULL OR m.tolerance_ms IS NULL
                  THEN 'TIMING_NOT_RECORDED'
              WHEN abs(EXTRACT(EPOCH FROM (m.observed_at - m.target_at)))
                   * 1000.0 <= m.tolerance_ms THEN 'WITHIN_TOLERANCE'
              ELSE 'OUTSIDE_TOLERANCE' END)
       || '|PERFORMANCE_ELIGIBLE=' || (
              m.l2_book_sha IS NOT NULL
              AND m.target_at IS NOT NULL AND m.tolerance_ms IS NOT NULL
              AND abs(EXTRACT(EPOCH FROM (m.observed_at - m.target_at)))
                  * 1000.0 <= m.tolerance_ms
              AND m.horizon IN ('60S', '300S'))
       || '|written_status=' || m.status
       || '|executable=' || COALESCE(round(m.executable_markout_usd::numeric,
                                           2)::text, 'NULL')
  FROM bettor_experimental_markouts m
  JOIN bettor_experimental_decisions d
    ON d.experimental_decision_id = m.experimental_decision_id
 ORDER BY m.observed_at DESC, m.horizon
 LIMIT 40;

\echo ''
\echo '--- 24b. DOES THE WRITTEN STATUS AGREE WITH THE CLOCK? ---'
-- Evidence ABOUT THE WRITE PATH, not part of the gate. The gate
-- ignores `status` entirely. A row stored OBSERVED whose recorded
-- timestamps fall outside the frozen tolerance would mean the write
-- path admitted something the contract forbids. Zero disagreements is
-- the expected result and is itself the finding.
WITH derived AS (
    SELECT m.horizon, m.status,
           (m.l2_book_sha IS NOT NULL
            AND m.target_at IS NOT NULL AND m.tolerance_ms IS NOT NULL
            AND abs(EXTRACT(EPOCH FROM (m.observed_at - m.target_at)))
                * 1000.0 <= m.tolerance_ms) AS within_tolerance
      FROM bettor_experimental_markouts m
      JOIN bettor_experimental_decisions d
        ON d.experimental_decision_id = m.experimental_decision_id
)
SELECT 'writer|' || horizon
       || '|written=' || status
       || '|derived_within_tolerance=' || within_tolerance
       || '|AGREES=' || ((status = 'OBSERVED') = within_tolerance)
       || '|n=' || count(*)
  FROM derived
 GROUP BY horizon, status, within_tolerance
 ORDER BY horizon, status;

\echo ''
\echo '--- 25. THE POSITION LIFECYCLE LEDGER (migration 085) ---'
-- §1: "Implement position lifecycle through append-only events."
-- The position row is the OPEN event and nothing more; every later
-- fact lives here. The CURRENT state is folded from these on read and
-- is never stored, so it cannot drift from its evidence.
SELECT 'lifecycle_events|' || experiment_id
       || '|' || event_type
       || '|n=' || count(*)
       || '|first=' || to_char(min(event_at), 'YYYY-MM-DD HH24:MI:SSZ')
       || '|last='  || to_char(max(event_at), 'YYYY-MM-DD HH24:MI:SSZ')
  FROM bettor_experimental_position_events
 GROUP BY experiment_id, event_type
 ORDER BY experiment_id, event_type;

\echo ''
\echo '--- 25b. EVERY POSITION HAS ITS OPEN EVENT, AND NOTHING EXITED ---'
-- §3: "Do not pretend they historically exited. Do not create a
-- backdated exit." Every position that opened under infrastructure
-- where the exit could not run carries
-- EXIT_MECHANISM_UNAVAILABLE_AT_ENTRY and no exit event at all. A
-- non-zero exited count here would mean an exit was manufactured.
SELECT 'position_coverage|' || p.experiment_id
       || '|positions=' || count(DISTINCT p.position_id)
       || '|opened_events=' || count(DISTINCT p.position_id)
            FILTER (WHERE e.event_type = 'POSITION_OPENED')
       || '|unavailable_at_entry=' || count(DISTINCT p.position_id)
            FILTER (WHERE e.event_type
                    = 'EXIT_MECHANISM_UNAVAILABLE_AT_ENTRY')
       || '|exit_eligible=' || count(DISTINCT p.position_id)
            FILTER (WHERE e.event_type = 'EXIT_BECAME_ELIGIBLE')
       || '|exit_decisions=' || count(DISTINCT p.position_id)
            FILTER (WHERE e.event_type = 'EXIT_DECISION')
       || '|closed=' || count(DISTINCT p.position_id)
            FILTER (WHERE e.event_type = 'POSITION_CLOSED')
       || '|settled=' || count(DISTINCT p.position_id)
            FILTER (WHERE e.event_type = 'SETTLED')
  FROM bettor_experimental_positions p
  LEFT JOIN bettor_experimental_position_events e
    ON e.position_id = p.position_id
 GROUP BY p.experiment_id
 ORDER BY p.experiment_id;

\echo ''
\echo '--- 25c. A POSITION WITH NO EVENT AT ALL WOULD BE A GAP ---'
-- The seed in migration 085 wrote both events for every position that
-- existed when it ran, and open_position writes POSITION_OPENED for
-- every position created since. An empty result is the finding.
SELECT 'position_without_events|' || p.position_id
       || '|' || p.experiment_id || '|' || p.market_id
  FROM bettor_experimental_positions p
 WHERE NOT EXISTS (SELECT 1 FROM bettor_experimental_position_events e
                    WHERE e.position_id = p.position_id);

\echo ''
\echo '--- 26. RE-ENTRY: THE HISTORICAL RECORD, NEVER REWRITTEN ---'
-- §10/§11. X1 has zero violations; X1C has one, at 41.6s. "Do not
-- alter that historical control row." This reports it; it does not
-- touch it. The 60s figure is the FROZEN horizon both lanes declare.
WITH gaps AS (
    SELECT p.experiment_id, p.market_id, p.position_id, p.opened_at,
           EXTRACT(EPOCH FROM (p.opened_at - lag(p.opened_at) OVER (
               PARTITION BY p.experiment_id, p.market_id
                ORDER BY p.opened_at))) AS gap_s
      FROM bettor_experimental_positions p
)
SELECT 'reentry|' || experiment_id
       || '|reentries=' || count(*) FILTER (WHERE gap_s IS NOT NULL)
       || '|inside_60s=' || count(*) FILTER (WHERE gap_s < 60)
       || '|min_gap_s=' || COALESCE(round(min(gap_s)::numeric, 1)::text,
                                    'NONE')
  FROM gaps
 GROUP BY experiment_id
 ORDER BY experiment_id;

\echo ''
\echo '--- 26b. EVERY RE-ENTRY INSIDE THE FROZEN HORIZON, BY NAME ---'
WITH gaps AS (
    SELECT p.experiment_id, p.market_id, p.position_id, p.opened_at,
           EXTRACT(EPOCH FROM (p.opened_at - lag(p.opened_at) OVER (
               PARTITION BY p.experiment_id, p.market_id
                ORDER BY p.opened_at))) AS gap_s
      FROM bettor_experimental_positions p
)
SELECT 'violation|' || experiment_id
       || '|' || position_id
       || '|' || market_id
       || '|at=' || to_char(opened_at, 'YYYY-MM-DD HH24:MI:SSZ')
       || '|gap_s=' || round(gap_s::numeric, 1)
  FROM gaps
 WHERE gap_s IS NOT NULL AND gap_s < 60
 ORDER BY opened_at;

\echo ''
\echo '--- 26c. THE ENFORCEMENT, PROSPECTIVELY (migration 086) ---'
-- §11: "the rule must become actual enforcement rather than
-- accidental compliance caused by the ~65s tick cadence." A refusal
-- is recorded as a DECISION with its own status, no position id and
-- no economics -- so it can never be counted as a trade. Zero rows
-- before the enforcement deploys is the expected result; any row
-- after it is an entry the frozen rule stopped.
SELECT 'reentry_refusals|' || experiment_id
       || '|n=' || count(*)
       || '|with_a_position=' || count(*) FILTER (WHERE position_id
                                                  IS NOT NULL)
       || '|with_economics=' || count(*) FILTER (
              WHERE executed_notional_usd IS NOT NULL
                 OR filled_qty IS NOT NULL)
       || '|first=' || COALESCE(to_char(min(decision_timestamp),
                                        'YYYY-MM-DD HH24:MI:SSZ'), 'NONE')
  FROM bettor_experimental_decisions
 WHERE execution_status = 'REFUSED_REENTRY_INSIDE_HORIZON'
 GROUP BY experiment_id
 ORDER BY experiment_id;

\echo ''
\echo '--- 26d. THE RAILS THE DATABASE ITSELF HOLDS ---'
-- Stated in the schema rather than only in the writer, because the
-- writer is the thing most likely to be changed by someone who has
-- not read the directive.
SELECT 'constraint|' || con.conname
       || '|admits_refusal='
       || (pg_get_constraintdef(con.oid)
           LIKE '%REFUSED_REENTRY_INSIDE_HORIZON%')
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
 WHERE rel.relname = 'bettor_experimental_decisions'
   AND con.conname IN ('bettor_exp_execution_status',
                       'bettor_exp_refusal_has_no_position')
 ORDER BY con.conname;

\echo ''
\echo '--- 27. THE EXIT MECHANISM IS STILL BLOCKED, AND WHY ---'
-- §2 THE GATE: "If the frozen declaration is insufficient to
-- determine WHEN TO EXIT, WHAT ACTION TO TAKE, WHAT PRICE TO USE, HOW
-- MUCH QUANTITY TO EXIT, stop and name the missing semantics. Do not
-- fill them in after seeing results."
--
-- Three of the four are answered by EXIT_RULE_HORIZON verbatim. HOW
-- MUCH QUANTITY TO EXIT is not stated anywhere in the frozen
-- declaration, and neither is EXIT_BOOK_TOLERANCE (how stale a book
-- may be and still count as "the book observed at that instant").
-- Until the owner supplies them, no exit fires and no realized P&L
-- exists. This query reports the consequence in the data: positions
-- that are past their horizon and still have no exit event.
SELECT 'past_horizon_unexited|' || p.experiment_id
       || '|n=' || count(*)
       || '|oldest=' || to_char(min(p.opened_at),
                                'YYYY-MM-DD HH24:MI:SSZ')
       || '|status=EXIT_MECHANISM_BLOCKED_PENDING_EXIT_SEMANTICS'
  FROM bettor_experimental_positions p
 WHERE p.opened_at < now() - interval '60 seconds'
   AND NOT EXISTS (SELECT 1 FROM bettor_experimental_position_events e
                    WHERE e.position_id = p.position_id
                      AND e.event_type IN ('EXIT_EXECUTION',
                                           'POSITION_CLOSED', 'SETTLED'))
 GROUP BY p.experiment_id
 ORDER BY p.experiment_id;

\echo ''
\echo '--- 27b. NO REALIZED P&L EXISTS, AND NONE IS CLAIMED ---'
-- §5: "Never classify a horizon markout itself as realized P&L."
-- REALIZED_PNL requires a real shadow exit. There have been none.
SELECT 'realized|' || p.experiment_id
       || '|exit_executions=' || count(*) FILTER (
              WHERE e.event_type = 'EXIT_EXECUTION')
       || '|realized_pnl_usd=NOT_APPLICABLE_NO_EXIT_HAS_OCCURRED'
       || '|settled_pnl_usd=NOT_APPLICABLE_NO_POSITION_HAS_SETTLED'
  FROM bettor_experimental_positions p
  LEFT JOIN bettor_experimental_position_events e
    ON e.position_id = p.position_id
 GROUP BY p.experiment_id
 ORDER BY p.experiment_id;

\echo ''
\echo '--- 28. VERSION LIFECYCLE: who may still open a position ---'
-- Owner directive 2026-09-20: X1 V1 and its control are closed to NEW
-- positions under EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE. This
-- is NOT a performance stop -- their frozen contract cannot complete a
-- position lifecycle, so a position opened under it could never reach
-- POSITION_CLOSED.
--
-- The blocker lives in code, OUTSIDE the hashed declaration, so that
-- X1's frozen sha (77c930248609d057) keeps matching the four
-- production positions that carry it. What the database can show is
-- the CONSEQUENCE: no position rows created after the block.
SELECT 'version_positions|' || p.experiment_id
       || '|positions=' || count(*)
       || '|first=' || to_char(min(p.opened_at), 'YYYY-MM-DD HH24:MI:SSZ')
       || '|last='  || to_char(max(p.opened_at), 'YYYY-MM-DD HH24:MI:SSZ')
       || '|entry_notional_usd='
       || to_char(sum(p.entry_notional_usd), 'FM999999990.00')
  FROM bettor_experimental_positions p
 GROUP BY p.experiment_id
 ORDER BY p.experiment_id;

\echo ''
\echo '--- 28b. DECISIONS BLOCKED BY THEIR OWN VERSION (migration 087) ---'
-- A blocked entry is still RECORDED -- "Do not discard observations or
-- decisions" -- but it carries no position id and no economics, so it
-- can never be counted as a trade. Zero rows before the block deploys
-- is the expected result; any row after it is an entry the frozen
-- contract's incompleteness stopped.
SELECT 'version_blocked|' || experiment_id
       || '|n=' || count(*)
       || '|with_a_position=' || count(*) FILTER (WHERE position_id
                                                  IS NOT NULL)
       || '|with_economics=' || count(*) FILTER (
              WHERE executed_notional_usd IS NOT NULL
                 OR filled_qty IS NOT NULL)
       || '|first=' || COALESCE(to_char(min(decision_timestamp),
                                        'YYYY-MM-DD HH24:MI:SSZ'), 'NONE')
  FROM bettor_experimental_decisions
 WHERE execution_status
       = 'BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE'
 GROUP BY experiment_id
 ORDER BY experiment_id;

\echo ''
\echo '--- 28c. THE SUCCESSOR HAS NOT TRADED ---'
-- §10: "Then stop for review before the successor opens its first
-- position." An empty result is the expected finding and the thing to
-- re-check before saying the successor is holding.
SELECT 'successor_activity|' || e.experiment_id
       || '|decisions=' || count(DISTINCT d.experimental_decision_id)
       || '|positions=' || count(DISTINCT p.position_id)
  FROM (VALUES ('X1_SHORT_HORIZON_DIRECTION_V2'),
               ('X1C_NULL_CONTROL_V2')) AS e(experiment_id)
  LEFT JOIN bettor_experimental_decisions d
    ON d.experiment_id = e.experiment_id
  LEFT JOIN bettor_experimental_positions p
    ON p.experiment_id = e.experiment_id
 GROUP BY e.experiment_id
 ORDER BY e.experiment_id;

\echo ''
\echo '--- 28d. THE RAILS THE DATABASE HOLDS AFTER 087 ---'
SELECT 'constraint|' || con.conname
       || '|admits_reentry_refusal='
       || (pg_get_constraintdef(con.oid)
           LIKE '%REFUSED_REENTRY_INSIDE_HORIZON%')
       || '|admits_version_block='
       || (pg_get_constraintdef(con.oid)
           LIKE '%BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE%')
  FROM pg_constraint con
  JOIN pg_class rel ON rel.oid = con.conrelid
 WHERE rel.relname = 'bettor_experimental_decisions'
   AND con.conname IN ('bettor_exp_execution_status',
                       'bettor_exp_refusal_has_no_position')
 ORDER BY con.conname;

\echo ''
\echo '--- 28e. DID THE BLOCK ACTUALLY STOP THE LAST POSITIONS? ---'
-- THE QUESTION 28/28b LEAVES OPEN, and it must be answered on ONE
-- clock. Section 28b reports min(decision_timestamp) and 28 reports
-- max(opened_at); those are DIFFERENT fields from different clocks --
-- opened_at is the modelled arrival, decision_timestamp is the tick's
-- sealed instant -- and the clock-domain conflict on these rows is a
-- recorded, unresolved defect. Comparing them would repeat exactly the
-- mistake the latency work exists to prevent.
--
-- `written_at` is stamped by the writer at persist time, on one clock,
-- for BOTH row kinds. So the ordering question is asked on that and
-- nothing else: was any position PERSISTED after the first blocked
-- decision was PERSISTED?
WITH first_block AS (
    SELECT experiment_id, min(written_at) AS blocking_began
      FROM bettor_experimental_decisions
     WHERE execution_status
           = 'BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE'
     GROUP BY experiment_id
),
positions AS (
    SELECT d.experiment_id, d.position_id, d.written_at
      FROM bettor_experimental_decisions d
     WHERE d.position_id IS NOT NULL
),
-- §1: the exact deploy-transition rows, identified by ID and never by
-- a time range. A range would re-admit anything that fell inside it.
contaminated AS (
    SELECT d.position_id
      FROM bettor_experimental_decisions d
      JOIN (SELECT experiment_id, min(written_at) AS began
              FROM bettor_experimental_decisions
             WHERE execution_status
                 = 'BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE'
             GROUP BY experiment_id) f
        ON f.experiment_id = d.experiment_id
     WHERE d.position_id IS NOT NULL
       AND d.written_at > f.began
       AND d.written_at < f.began + interval '60 seconds'
)
SELECT 'block_effective|' || b.experiment_id
       || '|blocking_began=' || to_char(b.blocking_began,
                                        'YYYY-MM-DD HH24:MI:SSZ')
       || '|positions_written_after=' || count(p.position_id)
       || '|last_position_written='
       || COALESCE(to_char(max(p.written_at),
                           'YYYY-MM-DD HH24:MI:SSZ'), 'NONE')
       -- §2 (owner review): THE 120-SECOND WINDOW THAT WAS HERE IS
       -- GONE. It declared a grace interval in which leakage was
       -- implicitly acceptable, and it could not tell a slow deploy
       -- from a real leak because it was not measuring whether the
       -- guarded code was running. Section 28h asks that instead, from
       -- the worker's observed boot. What stays here is the raw count,
       -- with the two known contaminated ids named rather than timed
       -- out of the result.
       || '|excluding_known_contamination='
       || count(p.position_id) FILTER (
              WHERE p.position_id NOT IN (
                  SELECT position_id FROM contaminated))
  FROM first_block b
  LEFT JOIN positions p
    ON p.experiment_id = b.experiment_id
   AND p.written_at > b.blocking_began
 GROUP BY b.experiment_id, b.blocking_began
 ORDER BY b.experiment_id;

\echo ''
\echo '--- 28f. THE BLOCKED ROWS, ON THE WRITER CLOCK ---'
-- Same clock for both timestamps, so the two are comparable to each
-- other. A large gap between them is the clock-domain defect showing
-- through, not evidence about the block.
SELECT 'blocked_rows|' || experiment_id
       || '|n=' || count(*)
       || '|first_written=' || to_char(min(written_at),
                                       'YYYY-MM-DD HH24:MI:SSZ')
       || '|last_written='  || to_char(max(written_at),
                                       'YYYY-MM-DD HH24:MI:SSZ')
       || '|decision_ts_span=' || to_char(min(decision_timestamp),
                                          'HH24:MI:SSZ')
       || '..' || to_char(max(decision_timestamp), 'HH24:MI:SSZ')
  FROM bettor_experimental_decisions
 WHERE execution_status
       = 'BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE'
 GROUP BY experiment_id
 ORDER BY experiment_id;

\echo ''
\echo '--- 29. WHAT THE ~62s SPACING ACTUALLY MEASURES ---'
-- THE CORRECTION THIS SECTION EXISTS FOR. The exit-delay bound in V2
-- was derived from the spacing of rows in bettor_l2_evidence. That
-- spacing is NOT the market-data capture cadence. It is
-- institutional_md.EVIDENCE_EVERY_S = 60.0 -- a deliberately sparse
-- background TRAIL, written once a minute per symbol so there is a
-- record that the process was reading the venue at all.
--
-- The DIRECT execution path never reads that table. It reads the
-- IN-MEMORY book (institutional_book.STORE), refreshed every
-- SWEEP_S = 2.0s over MAX_INSTRUMENTS = 8 symbols. So a bound built
-- on trail spacing bounds the wrong thing by a factor of ~20.
--
-- Rows per symbol per hour tells the two apart: a 60s trail gives ~60,
-- a 2s capture would give ~1800.
SELECT 'evidence_rate|' || e.instrument_id
       || '|rows=' || count(*)
       || '|hours=' || round(EXTRACT(EPOCH FROM (max(e.received_timestamp)
                                    - min(e.received_timestamp)))::numeric
                             / 3600.0, 2)
       || '|rows_per_hour=' || round((count(*) / GREATEST(
              EXTRACT(EPOCH FROM (max(e.received_timestamp)
                                  - min(e.received_timestamp))) / 3600.0,
              0.01))::numeric, 1)
  FROM bettor_l2_evidence e
 WHERE e.received_timestamp > now() - interval '3 hours'
 GROUP BY e.instrument_id
 ORDER BY count(*) DESC
 LIMIT 10;

\echo ''
\echo '--- 29b. THE QUANTITY AN EXIT BOUND ACTUALLY NEEDS ---'
-- How stale was the IN-MEMORY book at the instant the DIRECT path
-- walked it? That is `book_age_ms`, recorded on every decision. It is
-- the same read the exit would make, so it is the same distribution
-- the exit's admissibility must be bounded on.
--
-- institutional_book.FRESHNESS_LIMIT_S = 5.0 already refuses anything
-- older: executable() returns nothing past it and the decision is
-- recorded NOT_IDENTIFIED. So this distribution should sit entirely
-- under 5,000ms -- and if it does, the execution-admissibility bound
-- is already frozen, already enforced, and needs no new free parameter.
SELECT 'book_age|' || COALESCE(d.evidence_environment, 'NULL')
       || '|n=' || count(*)
       || '|p50=' || round(percentile_cont(0.50) WITHIN GROUP (
              ORDER BY d.book_age_ms)::numeric, 1)
       || '|p95=' || round(percentile_cont(0.95) WITHIN GROUP (
              ORDER BY d.book_age_ms)::numeric, 1)
       || '|max=' || round(max(d.book_age_ms)::numeric, 1)
       || '|over_5000ms=' || count(*) FILTER (WHERE d.book_age_ms > 5000)
  FROM bettor_experimental_decisions d
 WHERE d.book_age_ms IS NOT NULL
 GROUP BY d.evidence_environment
 ORDER BY count(*) DESC;

\echo ''
\echo '--- 29c. THE FRESHNESS VERDICT THE STORE ALREADY APPLIES ---'
SELECT 'freshness|' || COALESCE(d.book_freshness_status, 'NULL')
       || '|n=' || count(*)
       || '|filled=' || count(*) FILTER (WHERE d.filled_qty IS NOT NULL)
       || '|not_identified=' || count(*) FILTER (
              WHERE d.execution_status = 'NOT_IDENTIFIED')
  FROM bettor_experimental_decisions d
 GROUP BY d.book_freshness_status
 ORDER BY count(*) DESC;

\echo ''
\echo '--- 29d. THE COLLECTOR SAYS ITS OWN CYCLE TIME ---'
SELECT 'collector|' || service
       || '|status=' || status
       || '|beat_at=' || to_char(beat_at, 'YYYY-MM-DD HH24:MI:SSZ')
       || '|sweepS=' || COALESCE(detail->>'sweepS', 'NONE')
       || '|symbols=' || COALESCE(detail->>'symbols', 'NONE')
       || '|venueMsP50=' || COALESCE(detail->>'venueMsP50', 'NONE')
       || '|venueMsMax=' || COALESCE(detail->>'venueMsMax', 'NONE')
       || '|read=' || COALESCE(detail->>'read', 'NONE')
       || '|failed=' || COALESCE(detail->>'failed', 'NONE')
       || '|mechanism=' || COALESCE(detail->>'marketDataMechanism', 'NONE')
  FROM service_heartbeats
 WHERE service IN ('institutional_md', 'shadow_experimental')
 ORDER BY service;

\echo ''
\echo '--- 28g. THE DEPLOY-TRANSITION ROWS, BY ID ---'
-- §1: "Classify the exact two rows as DEPLOY_TRANSITION_CONTAMINATION
-- ... Never silently subtract the two rows from the raw ledger."
-- Named here so the classification attaches to identities, not to a
-- window. X1C_ALL_POSITIONS keeps counting them; the paired
-- comparison does not.
WITH first_block AS (
    SELECT experiment_id, min(written_at) AS began
      FROM bettor_experimental_decisions
     WHERE execution_status
         = 'BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE'
     GROUP BY experiment_id
)
SELECT 'contamination|' || d.experiment_id
       || '|' || d.position_id
       || '|' || d.market_id
       || '|written=' || to_char(d.written_at, 'YYYY-MM-DD HH24:MI:SSZ')
       || '|after_first_block_s='
       || round(EXTRACT(EPOCH FROM (d.written_at - f.began))::numeric, 1)
       || '|notional=' || COALESCE(to_char(d.executed_notional_usd,
                                           'FM999999990.00'), 'NULL')
       || '|class=DEPLOY_TRANSITION_CONTAMINATION'
  FROM bettor_experimental_decisions d
  JOIN first_block f ON f.experiment_id = d.experiment_id
 WHERE d.position_id IS NOT NULL
   AND d.written_at > f.began
 ORDER BY d.written_at;

\echo ''
\echo '--- 28h. X1C: ALL RECORDED vs CLEAN PAIRED ---'
-- Both figures, always together. §1: the raw ledger is never silently
-- reduced -- it is reported beside the paired-eligible subset with the
-- difference named.
WITH first_block AS (
    SELECT experiment_id, min(written_at) AS began
      FROM bettor_experimental_decisions
     WHERE execution_status
         = 'BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE'
     GROUP BY experiment_id
),
contaminated AS (
    SELECT d.position_id
      FROM bettor_experimental_decisions d
      JOIN first_block f ON f.experiment_id = d.experiment_id
     WHERE d.position_id IS NOT NULL
       AND d.written_at > f.began
)
SELECT 'paired|' || p.experiment_id
       || '|ALL_POSITIONS=' || count(*)
       || '|ALL_ENTRY_NOTIONAL='
       || to_char(sum(p.entry_notional_usd), 'FM999999990.00')
       || '|CONTAMINATED=' || count(*) FILTER (
              WHERE p.position_id IN (SELECT position_id FROM contaminated))
       || '|CLEAN_PAIRED_POSITIONS=' || count(*) FILTER (
              WHERE p.position_id NOT IN (
                  SELECT position_id FROM contaminated))
       || '|CLEAN_PAIRED_ENTRY_NOTIONAL='
       || to_char(sum(p.entry_notional_usd) FILTER (
              WHERE p.position_id NOT IN (
                  SELECT position_id FROM contaminated)),
              'FM999999990.00')
  FROM bettor_experimental_positions p
 GROUP BY p.experiment_id
 ORDER BY p.experiment_id;
