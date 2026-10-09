-- READ-ONLY. RC6 lane C (software reds): where and when each SOFTWARE first-loss
-- code of the RC5 runtime fired, so the cause is fixed rather than the count.
--   D1  QUOTE_STALE_ON_ARRIVAL / PROBABILITY_DEADLINE_PASSED rows per hour:
--       feed state on the row, metered vs reactive cycle, the row's clocks
--   D2  the deciding cycle's last heartbeat: latency split, venue book sources,
--       step timing, the named refusals
--   D3  every VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT row and valuation,
--       with the currency evidence the valuation sealed
--   D4  every VENUE_MAPPING_AMBIGUOUS row
--   D5  reactive attempts per hour and state; the deciding loops now; the
--       PinnAPI owner heartbeat now
-- No writes. Every statement is a SELECT.

\echo D1 QSOA and PDL rows per hour: feed flag, metered or reactive cycle, clocks
WITH c AS (SELECT cycle_id, count(*) AS n FROM ext_candidate_outcomes
            WHERE cycle_at >= timestamptz '2026-10-08 16:00:00+00' GROUP BY cycle_id)
SELECT date_trunc('hour', o.cycle_at) AS hour, o.first_refusal, o.sport_key,
       (o.codes ? 'FEED_OWNERSHIP_NOT_HELD') AS feed_down,
       (c.n = 1) AS reactive,
       count(*) AS rows, count(DISTINCT o.provider_event_id) AS events,
       count(*) FILTER (WHERE o.provider_lag_s > 30) AS lag_gt30,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY o.provider_lag_s)::numeric, 1) AS lag_p50,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY o.our_processing_s)::numeric, 1) AS ours_p50,
       round(percentile_disc(0.9) WITHIN GROUP (ORDER BY o.our_processing_s)::numeric, 1) AS ours_p90,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY o.quote_age_s)::numeric, 1) AS age_p50,
       percentile_disc(0.1) WITHIN GROUP (ORDER BY o.queue_position) AS qp_p10,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY o.queue_position) AS qp_p50,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM o.recorded_at - o.cycle_at))::numeric, 1) AS rec_p50
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.cycle_at >= timestamptz '2026-10-08 16:00:00+00'
   AND o.first_refusal IN ('QUOTE_STALE_ON_ARRIVAL',
                           'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH')
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 4, 5;

\echo D1b every first refusal of the RC5 runtime per hour (rows, events, reactive rows)
WITH c AS (SELECT cycle_id, count(*) AS n FROM ext_candidate_outcomes
            WHERE cycle_at >= timestamptz '2026-10-08 16:00:00+00' GROUP BY cycle_id)
SELECT date_trunc('hour', o.cycle_at) AS hour, coalesce(o.first_refusal, o.outcome) AS code,
       count(*) AS rows, count(DISTINCT o.provider_event_id) AS events,
       count(*) FILTER (WHERE c.n = 1) AS reactive_rows
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.cycle_at >= timestamptz '2026-10-08 16:00:00+00'
 GROUP BY 1, 2 ORDER BY 1, rows DESC;

\echo D2 the deciding cycle heartbeat now (latency split, book sources, step timing, refusals)
SELECT to_timestamp((value->>'at')::float8) AS at, value->>'state' AS state,
       value->>'writer' AS writer, value->'elapsed_s' AS elapsed_s,
       value->'written' AS written, value->'cycle_label' AS label,
       value->'odds_freshness' AS odds_freshness,
       value->'step_timing_s' AS step_timing_s,
       value->'refusals' AS refusals,
       value->'candidate_outcomes' AS candidate_outcomes
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo D2b the heartbeat mapped-candidate ledger entries for the five codes (first 60)
SELECT e->>'first_refusal' AS code, e->>'us_market_slug' AS slug,
       e->>'stage' AS stage, e->>'age_s' AS age_s, e->>'limit_s' AS limit_s,
       e->>'age_basis' AS age_basis, e->>'provider_lag_s' AS lag,
       e->>'our_processing_s' AS ours, e->>'attribution' AS attribution,
       e->>'ws_refusal' AS ws, e->>'decision_instant_epoch_s' AS decided,
       e->>'observed_at_epoch_s' AS observed
  FROM ingestion_state s,
       jsonb_array_elements(coalesce(s.value->'mapped_candidate_ledger', '[]'::jsonb)) e
 WHERE s.key = 'ext_pinnacle_last_cycle'
   AND e->>'first_refusal' IN ('QUOTE_STALE_ON_ARRIVAL',
        'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH',
        'VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT', 'VENUE_MAPPING_AMBIGUOUS')
 LIMIT 60;

\echo D2c the heartbeat venue errors (first 30)
SELECT e::text AS venue_error
  FROM ingestion_state s,
       jsonb_array_elements(coalesce(s.value->'venue_errors', '[]'::jsonb)) e
 WHERE s.key = 'ext_pinnacle_last_cycle' LIMIT 30;

\echo D3 VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT ledger rows since 16:00Z
SELECT o.cycle_at, o.cycle_id, o.sport_key, o.queue_position, o.provider_event_id,
       o.us_market_slug, o.outcome, o.first_refusal, o.codes::text AS codes,
       extract(epoch FROM o.recorded_at - o.cycle_at) AS rec_s
  FROM ext_candidate_outcomes o
 WHERE o.cycle_at >= timestamptz '2026-10-08 16:00:00+00'
   AND (o.first_refusal = 'VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT'
        OR o.codes ? 'VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT')
 ORDER BY o.cycle_at LIMIT 80;

\echo D3b valuations sealed on a contradicted read since 16:00Z: the currency evidence
SELECT v.id, v.decided_at, v.event_key, v.us_market_slug, v.buy_intent,
       v.record_purpose, v.refusals::text AS refusals,
       v.calibration_only_evidence->'book_currency' AS book_currency,
       v.calibration_only_evidence->'displayed'->'book_source' AS book_source,
       left((v.calibration_only_evidence->'displayed')::text, 1500) AS displayed
  FROM external_valuations v
 WHERE v.decided_at >= timestamptz '2026-10-08 16:00:00+00'
   AND 'VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT' = ANY (v.refusals)
 ORDER BY v.decided_at LIMIT 40;

\echo D3c how often each currency verdict was sealed per hour (calibration-only valuations)
SELECT date_trunc('hour', v.decided_at) AS hour,
       v.calibration_only_evidence->'book_currency'->>'verdict' AS verdict,
       v.calibration_only_evidence->'book_currency'->>'mechanism' AS mechanism,
       count(*) AS valuations
  FROM external_valuations v
 WHERE v.decided_at >= timestamptz '2026-10-08 16:00:00+00'
   AND v.record_purpose = 'CALIBRATION_ONLY'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;

\echo D4 VENUE_MAPPING_AMBIGUOUS ledger rows since 16:00Z
SELECT o.cycle_at, o.sport_key, o.provider_event_id, o.home, o.away,
       o.commence_time, o.global_slug, o.us_market_slug, o.mapped_by,
       o.global_refusal_replaced, o.outcome, o.codes::text AS codes
  FROM ext_candidate_outcomes o
 WHERE o.cycle_at >= timestamptz '2026-10-08 16:00:00+00'
   AND (o.first_refusal = 'VENUE_MAPPING_AMBIGUOUS'
        OR o.codes ? 'VENUE_MAPPING_AMBIGUOUS')
 ORDER BY o.cycle_at LIMIT 60;

\echo D4b the same provider events every other outcome since 16:00Z
WITH e AS (SELECT DISTINCT provider_event_id FROM ext_candidate_outcomes
            WHERE cycle_at >= timestamptz '2026-10-08 16:00:00+00'
              AND (first_refusal = 'VENUE_MAPPING_AMBIGUOUS'
                   OR codes ? 'VENUE_MAPPING_AMBIGUOUS'))
SELECT o.provider_event_id, coalesce(o.first_refusal, o.outcome) AS code,
       o.mapped_by, o.us_market_slug, count(*) AS rows,
       min(o.cycle_at) AS first_at, max(o.cycle_at) AS last_at
  FROM ext_candidate_outcomes o JOIN e USING (provider_event_id)
 WHERE o.cycle_at >= timestamptz '2026-10-08 16:00:00+00'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, rows DESC;

\echo D5 reactive attempts per hour and state since 16:00Z
SELECT date_trunc('hour', created_at) AS hour, state, count(*)
  FROM pinnapi_reactive_attempts
 WHERE created_at >= timestamptz '2026-10-08 16:00:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo D5b the deciding loops now
SELECT loop_name, process, last_start_at, last_success_at, last_error_at,
       left(last_error, 120) AS last_error, starts, successes, errors,
       left(commit_sha, 12) AS sha, updated_at
  FROM runtime_loop_health
 WHERE loop_name LIKE 'ext_pinnacle%' OR loop_name LIKE 'pinnapi%'
 ORDER BY loop_name, process;

\echo D5c the PinnAPI owner heartbeat now
SELECT value->>'state' AS state, value->>'refused' AS refused,
       to_timestamp((value->>'beat_at')::float8) AS beat_at,
       left((value->'transitions')::text, 2500) AS transitions
  FROM ingestion_state WHERE key = 'pinnapi_feed';
