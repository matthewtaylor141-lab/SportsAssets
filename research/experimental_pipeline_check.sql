-- TWO NARROW OPERATIONAL CHECKS. Owner, 2026-09-20 01:2xZ.
--
--   1. The X1C position opened 00:58:01Z; 30S/60S/300S are all mature
--      and bettor_experimental_markouts is still EMPTY.
--   2. bettor_experimental_observations continues past 01:16Z while the
--      newest decision is still ~00:58Z.
--
-- THE HYPOTHESIS BOTH SYMPTOMS POINT AT, from the tick's own ordering:
--
--     sample_focus -> drain_seals -> take_markouts -> seal_population
--
-- `take_markouts` runs BEFORE `seal_population`. If it raises, the
-- sample has already been written (observations keep arriving) while
-- the markouts AND the seals never happen -- which is exactly the pair
-- of symptoms reported. And before 00:58 there were no positions, so
-- `markout_subjects` returned nothing and the body of `take_markouts`
-- had NEVER executed in production; the first position is the first
-- time that code ran against real rows.
--
-- The tick is wrapped, so a failure is recorded rather than lost:
-- run() writes status='tick_failed' and a `tickError` string into the
-- heartbeat. Section 1 reads that. If it says tick_failed, the error
-- names itself and nothing below needs interpreting.
--
-- NOTHING HERE WRITES. No backdating, no fabricated observation, no
-- markout invented for a book that was never captured.

\echo '--- 1. THE TICK ITSELF: did it fail, and where? ---'
SELECT 'tick|' || status
       || '|at=' || to_char(beat_at, 'HH24:MI:SS')
       || '|error=' || COALESCE(detail ->> 'tickError', 'NONE')
       || '|sample=' || COALESCE(detail #>> '{sample,written}', '-')
       || '|drain_open=' || COALESCE(detail #>> '{drain,open}', '-')
       || '|markout_subjects=' || COALESCE(detail #>> '{markouts,subjects}',
                                           'NOT_REACHED')
       || '|markout_observed=' || COALESCE(detail #>> '{markouts,observed}',
                                           '-')
       || '|markout_notid=' || COALESCE(detail #>> '{markouts,notIdentified}',
                                        '-')
       || '|markout_notmature=' || COALESCE(
              detail #>> '{markouts,notYetMature}', '-')
       || '|seal_status=' || COALESCE(detail #>> '{seal,status}',
                                      'NOT_REACHED')
       || '|seal_markets=' || COALESCE(detail #>> '{seal,markets}', '-')
       || '|seal_eligible=' || COALESCE(detail #>> '{seal,eligible}', '-')
       || '|seal_sealed=' || COALESCE(detail #>> '{seal,sealed}', '-')
       || '|tickS=' || COALESCE(detail ->> 'tickS', '-')
  FROM service_heartbeats
 WHERE service = 'shadow_experimental'
 ORDER BY beat_at DESC LIMIT 6;

\echo ''
\echo '--- 2. THE MARKOUT SUBJECT QUERY, conjunct by conjunct ---'
-- `markout_subjects` requires THREE things at once: a decision inside
-- the window, a position joined on position_id, and at least one L2
-- evidence row for the market. Printed separately so the one that
-- fails is named rather than inferred from an empty result.
SELECT 'subject|' || d.experimental_decision_id
       || '|exp=' || d.experiment_id
       || '|market=' || d.market_id
       || '|decided=' || to_char(d.decision_timestamp, 'HH24:MI:SS')
       || '|age_s=' || round(EXTRACT(EPOCH FROM (now()
                                                 - d.decision_timestamp)))
       || '|in_24h_window=' || (d.decision_timestamp
                                > now() - interval '24 hours')
       || '|position_joins=' || (p.position_id IS NOT NULL)
       || '|entry_qty=' || COALESCE(p.entry_qty::text, 'NULL')
       || '|entry_vwap=' || COALESCE(p.entry_vwap::text, 'NULL')
       || '|evidence_rows_for_market=' || (
              SELECT count(*) FROM bettor_l2_evidence e
               WHERE e.instrument_id = d.market_id)
       || '|WOULD_BE_A_SUBJECT=' || (
              d.decision_timestamp > now() - interval '24 hours'
              AND p.position_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM bettor_l2_evidence e
                           WHERE e.instrument_id = d.market_id))
  FROM bettor_experimental_decisions d
  LEFT JOIN bettor_experimental_positions p
         ON p.position_id = d.position_id
 WHERE d.position_id IS NOT NULL
 ORDER BY d.decision_timestamp DESC LIMIT 10;

\echo ''
\echo '--- 3. WHAT BOOKS EXIST to mark that position against ---'
-- A markout needs a book NEAR the horizon, not just any book. If the
-- only evidence for this market is the arrival book itself, every
-- horizon is legitimately unobservable and must be recorded as missed
-- -- never as a markout against the entry price wearing a later label.
SELECT 'book|' || e.instrument_id
       || '|regime=' || COALESCE(e.latency_regime, 'NULL')
       || '|received=' || to_char(e.received_timestamp, 'HH24:MI:SS')
       || '|since_decision_s=' || round(EXTRACT(EPOCH FROM (
              e.received_timestamp - (
                  SELECT min(d.decision_timestamp)
                    FROM bettor_experimental_decisions d
                   WHERE d.position_id IS NOT NULL
                     AND d.market_id = e.instrument_id))))
  FROM bettor_l2_evidence e
 WHERE e.instrument_id IN (SELECT market_id
                             FROM bettor_experimental_decisions
                            WHERE position_id IS NOT NULL)
 ORDER BY e.received_timestamp LIMIT 30;

\echo ''
\echo '--- 4. THE MARKOUT TABLE, whatever is in it ---'
SELECT 'markout|' || horizon || '|' || status
       || '|n=' || count(*)
       || '|lag_ms_median=' || COALESCE(round(percentile_cont(0.5)
              WITHIN GROUP (ORDER BY observed_lag_ms)::numeric, 0)::text,
              'NULL')
  FROM bettor_experimental_markouts
 GROUP BY horizon, status
 ORDER BY horizon, status;

\echo ''
\echo '--- 5. OBSERVATIONS vs DECISIONS: where the pipeline stops ---'
SELECT 'clock|newest_observation=' || COALESCE(to_char((
           SELECT max(observed_at) FROM bettor_experimental_observations),
           'HH24:MI:SS'), 'NONE')
       || '|newest_decision=' || COALESCE(to_char((
           SELECT max(decision_timestamp)
             FROM bettor_experimental_decisions), 'HH24:MI:SS'), 'NONE')
       || '|newest_seal=' || COALESCE(to_char((
           SELECT max(sealed_at) FROM bettor_experimental_seals),
           'HH24:MI:SS'), 'NONE')
       || '|open_seals=' || (
           SELECT count(*) FROM bettor_experimental_seals
            WHERE status = 'OPEN');

\echo ''
\echo '--- 6. THE ELIGIBLE POPULATION the seal half would see ---'
-- X1 needs N successive CAPTURED samples on one market with the right
-- feature source and a YES-bound book. This counts what the last half
-- hour actually offers, so "no new decisions" can be told apart from
-- "no eligible population".
SELECT 'eligible|' || symbol
       || '|samples_30m=' || count(*)
       || '|readable=' || count(*) FILTER (WHERE readable)
       || '|bbo=' || COALESCE(max(bbo_binding), 'NULL')
       || '|fsv=' || COALESCE(max(feature_source_version), 'NULL')
       || '|newest=' || to_char(max(observed_at), 'HH24:MI:SS')
       || '|mids=' || count(*) FILTER (WHERE mid IS NOT NULL)
  FROM bettor_experimental_observations
 WHERE observed_at > now() - interval '30 minutes'
 GROUP BY symbol
 ORDER BY count(*) DESC LIMIT 15;

\echo ''
\echo '--- 7. ALREADY-SEALED? the dedup that would quietly stop sealing ---'
-- A tick seals the NEWEST observation per market. If that observation
-- was already sealed, the pass reports already_sealed and writes
-- nothing -- which looks identical to a stalled loop from outside.
SELECT 'sealed_obs|' || COALESCE(d.experimental_observation_id, 'NULL')
       || '|exp=' || d.experiment_id
       || '|market=' || d.market_id
       || '|at=' || to_char(d.decision_timestamp, 'HH24:MI:SS')
  FROM bettor_experimental_decisions d
 ORDER BY d.decision_timestamp DESC LIMIT 8;

\echo ''
\echo '--- 8. THE L2 REQUEST QUEUE (markout requests the bridge serves) ---'
SELECT 'l2req|' || purpose
       || '|' || COALESCE(status, 'NULL')
       || '|n=' || count(*)
       || '|newest=' || to_char(max(requested_at), 'HH24:MI:SS')
  FROM bettor_l2_requests
 WHERE requested_at > now() - interval '6 hours'
 GROUP BY purpose, status
 ORDER BY max(requested_at) DESC LIMIT 10;
