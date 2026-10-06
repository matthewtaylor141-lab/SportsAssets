-- READ-ONLY. P0 first-loss plumbing: FEED_OWNERSHIP_NOT_HELD,
-- QUOTE_STALE_ON_ARRIVAL, PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE.
--
-- The coverage first-loss census (coverage_first_loss.py) reports these
-- three SOFTWARE codes over the last 24 h. This file asks production WHEN
-- and WHERE each one fired, so the defect is fixed at its cause:
--   F*  the PinnAPI feed's authority in the deciding process over time
--   Q*  the clocks of every QUOTE_STALE_ON_ARRIVAL row (whose time it was)
--   P*  the lane refusals behind PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE
--
-- No writes. Every statement is a SELECT.

\echo F1 feed control rows and the persisted heartbeat (state, authority, transitions)
SELECT key,
       CASE WHEN key = 'pinnapi_feed_last' THEN NULL ELSE value::text END AS value,
       value->>'state'                         AS state,
       value->>'refused'                       AS refused,
       to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->'cache'->'authority'             AS authority,
       value->'scope'                          AS scope,
       value->'cache'->'events_by_sport'       AS events_by_sport,
       value->'cache'->'counts'                AS cache_counts,
       value->'transitions'                    AS transitions
  FROM ingestion_state
 WHERE key IN ('pinnapi_feed', 'pinnapi_feed_scope', 'pinnapi_feed_last');

\echo F2 who holds the writer lock and the feed lease now, and since when
SELECT l.pid, a.application_name, a.backend_start, a.state,
       ((l.classid::bigint << 32) | l.objid::bigint) AS lock_key
  FROM pg_locks l LEFT JOIN pg_stat_activity a ON a.pid = l.pid
 WHERE l.locktype = 'advisory' AND l.granted
   AND ((l.classid::bigint << 32) | l.objid::bigint)
       IN (7723901544120034, 7723901544120036);

\echo F3 loop health of the deciding loop (restarts, last errors)
SELECT loop_name, process, last_start_at, last_success_at, last_error_at,
       left(last_error, 200) AS last_error, starts, successes, errors,
       commit_sha, host, pid, updated_at
  FROM runtime_loop_health
 WHERE loop_name LIKE 'ext_pinnacle%' OR loop_name LIKE 'pinnapi%'
 ORDER BY loop_name, process;

\echo F4 hourly: candidate rows, rows naming FEED_OWNERSHIP_NOT_HELD, other WS refusals, writers
SELECT date_trunc('hour', cycle_at) AS hour,
       count(*) AS rows,
       count(DISTINCT cycle_id) AS cycles,
       count(*) FILTER (WHERE codes ? 'FEED_OWNERSHIP_NOT_HELD'
                        OR first_refusal = 'FEED_OWNERSHIP_NOT_HELD') AS feed_not_held,
       count(*) FILTER (WHERE codes::text LIKE '%FEED_EPOCH_NOT_RESYNCHRONIZED%') AS not_resynced,
       count(*) FILTER (WHERE codes::text LIKE '%FEED_QUOTE_OLDER_THAN_LIMIT%') AS ws_stale,
       count(*) FILTER (WHERE codes::text LIKE '%FEED_QUOTE_AGE_UNKNOWN%') AS ws_age_unknown,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale_on_arrival,
       count(*) FILTER (WHERE outcome = 'ADMITTED') AS admitted,
       count(DISTINCT writer) AS writers,
       string_agg(DISTINCT left(writer, 16), ',') AS writer_ids
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '26 hours'
 GROUP BY 1 ORDER BY 1;

\echo F5 FEED_OWNERSHIP_NOT_HELD rows: per sport, codes, cycle size (1 row = a reactive job)
WITH c AS (SELECT cycle_id, count(*) AS n FROM ext_candidate_outcomes
            WHERE cycle_at >= now() - interval '25 hours' GROUP BY cycle_id)
SELECT o.sport_key, o.stage, o.first_refusal, o.codes::text AS codes,
       (c.n = 1) AS single_row_cycle,
       count(*) AS rows, count(DISTINCT o.provider_event_id) AS events,
       min(o.cycle_at) AS first_at, max(o.cycle_at) AS last_at
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.cycle_at >= now() - interval '25 hours'
   AND (o.codes ? 'FEED_OWNERSHIP_NOT_HELD'
        OR o.first_refusal = 'FEED_OWNERSHIP_NOT_HELD')
 GROUP BY 1, 2, 3, 4, 5 ORDER BY rows DESC LIMIT 40;

\echo F6 minute timeline of FEED_OWNERSHIP_NOT_HELD rows vs all rows (where they cluster)
SELECT date_trunc('minute', cycle_at) AS minute, cycle_id,
       count(*) AS rows,
       count(*) FILTER (WHERE codes ? 'FEED_OWNERSHIP_NOT_HELD') AS feed_not_held,
       count(*) FILTER (WHERE codes::text LIKE '%PINNAPI%' OR codes::text LIKE '%FEED_%') AS any_ws_code,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS mapped,
       string_agg(DISTINCT sport_key, ',') AS sports
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '25 hours'
 GROUP BY 1, 2
HAVING count(*) FILTER (WHERE codes ? 'FEED_OWNERSHIP_NOT_HELD') > 0
 ORDER BY 1 LIMIT 120;

\echo F7 events with any FEED_OWNERSHIP_NOT_HELD row: every refusal they ever got in the window
WITH e AS (SELECT DISTINCT provider_event_id FROM ext_candidate_outcomes
            WHERE cycle_at >= now() - interval '25 hours'
              AND codes ? 'FEED_OWNERSHIP_NOT_HELD')
SELECT o.sport_key, o.provider_event_id, max(o.home) AS home, max(o.away) AS away,
       count(*) AS rows,
       count(*) FILTER (WHERE o.codes ? 'FEED_OWNERSHIP_NOT_HELD') AS feed_not_held,
       min(o.cycle_at) AS first_at, max(o.cycle_at) AS last_at,
       string_agg(DISTINCT coalesce(o.first_refusal, o.outcome), ' | ') AS outcomes
  FROM ext_candidate_outcomes o JOIN e USING (provider_event_id)
 WHERE o.cycle_at >= now() - interval '25 hours'
 GROUP BY 1, 2 ORDER BY 1, rows DESC LIMIT 80;

\echo F8 reactive attempts by hour and outcome (the feed own evaluation path)
SELECT date_trunc('hour', created_at) AS hour, state, count(*)
  FROM pinnapi_reactive_attempts
 WHERE created_at >= now() - interval '26 hours'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo Q1 QUOTE_STALE_ON_ARRIVAL rows: whose time was it, per sport
WITH c AS (SELECT cycle_id, count(*) AS n FROM ext_candidate_outcomes
            WHERE cycle_at >= now() - interval '25 hours' GROUP BY cycle_id)
SELECT o.sport_key, (c.n = 1) AS single_row_cycle, count(*) AS rows,
       count(DISTINCT o.provider_event_id) AS events,
       count(*) FILTER (WHERE o.provider_lag_s > 30) AS provider_lag_over_30,
       count(*) FILTER (WHERE o.provider_lag_s <= 30) AS provider_lag_within_30,
       count(*) FILTER (WHERE o.provider_lag_s IS NULL) AS provider_lag_null,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY o.provider_lag_s) AS lag_p50,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY o.provider_lag_s) AS lag_p90,
       min(o.provider_lag_s) AS lag_min,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY o.our_processing_s) AS ours_p50,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY o.our_processing_s) AS ours_p90,
       max(o.our_processing_s) AS ours_max,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY o.quote_age_s) AS age_p50
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.cycle_at >= now() - interval '25 hours'
   AND o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1, 2 ORDER BY rows DESC;

\echo Q2 the first-loss QUOTE_STALE_ON_ARRIVAL events (never admitted): their rows, lags and other refusals
WITH e AS (
    SELECT provider_event_id FROM ext_candidate_outcomes
     WHERE cycle_at >= now() - interval '25 hours'
     GROUP BY provider_event_id
    HAVING bool_or(first_refusal = 'QUOTE_STALE_ON_ARRIVAL')
       AND NOT bool_or(outcome IN ('ADMITTED', 'ALREADY_RECORDED')))
SELECT o.sport_key, o.provider_event_id, max(o.us_market_slug) AS slug,
       count(*) AS rows,
       count(*) FILTER (WHERE o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale_rows,
       round(min(o.provider_lag_s) FILTER (WHERE o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL')::numeric, 1) AS lag_min,
       round(max(o.provider_lag_s) FILTER (WHERE o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL')::numeric, 1) AS lag_max,
       round(max(o.our_processing_s) FILTER (WHERE o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL')::numeric, 1) AS ours_max,
       string_agg(DISTINCT coalesce(o.first_refusal, o.outcome), ' | ') AS outcomes
  FROM ext_candidate_outcomes o JOIN e USING (provider_event_id)
 WHERE o.cycle_at >= now() - interval '25 hours'
 GROUP BY 1, 2 ORDER BY 1, rows DESC LIMIT 80;

\echo Q3 QUOTE_STALE_ON_ARRIVAL: the codes list carried with it (which other refusals ride along)
SELECT o.sport_key, o.codes::text AS codes, count(*) AS rows
  FROM ext_candidate_outcomes o
 WHERE o.cycle_at >= now() - interval '25 hours'
   AND o.first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1, 2 ORDER BY rows DESC LIMIT 30;

\echo Q4 valuations on CFB in the window: provider and freshness refusals (is any WS-priced?)
SELECT provider, record_purpose, admissible,
       array_to_string(refusals[1:3], ',') AS first_refusals, count(*) AS n
  FROM external_valuations
 WHERE decided_at >= now() - interval '25 hours' AND sport_family = 'football'
 GROUP BY 1, 2, 3, 4 ORDER BY n DESC LIMIT 30;

\echo P1 decisions refused PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE: the valuation lane refusals
SELECT pd.strategy, ev.sport_family, ev.provider, ev.record_purpose,
       ev.refusals::text AS valuation_refusals,
       pd.refusals::text AS decision_refusals,
       count(*) AS n, count(DISTINCT coalesce(ev.event_key, ev.us_market_slug)) AS events,
       min(ev.event_key) AS sample_event_key, min(ev.us_market_slug) AS sample_slug
  FROM paper_decisions pd JOIN external_valuations ev ON ev.id = pd.valuation_id
 WHERE pd.decided_at >= now() - interval '25 hours'
   AND 'PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE' = ANY (pd.refusals)
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY n DESC LIMIT 40;

\echo P2 the sample events: every valuation and decision
SELECT ev.id, ev.decided_at, ev.event_key, ev.us_market_slug, ev.sport_family,
       ev.provider, ev.record_purpose, ev.admissible, ev.probability,
       ev.refusals::text AS valuation_refusals,
       pd.strategy, pd.verdict, pd.refusals::text AS decision_refusals
  FROM external_valuations ev
  LEFT JOIN paper_decisions pd ON pd.valuation_id = ev.id
 WHERE ev.decided_at >= now() - interval '30 hours'
   AND (ev.event_key IN ('pinnapi:1637471033', 'pinnapi:1637608659',
                         '9241420b127929f2a3797fe6ea2fb9c6')
        OR ev.us_market_slug IN ('aec-npb-hta-hci-2026-10-06',
                                 'atc-brb-ava-lon-2026-10-07-ava'))
 ORDER BY ev.decided_at DESC LIMIT 40;
