-- READ-ONLY. RC6.3b software-reds CRITIC readback 2. How many of the NO_EXACT events actually flipped to
-- PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED while the feed cache eviction counter was zero (the cycles right after an
-- API deploy): per cycle, distinct events by first refusal, around the 2026-10-09 21:07Z deploy and the
-- 2026-10-10 04:26Z deploy. Settles the expected movement of fixing the cumulative-eviction rule. SELECT only.
\echo == N1 per cycle around the 2026-10-09 21:07Z API deploy: NOT_YET_POSTED vs NO_EXACT distinct events
SELECT date_trunc('minute', cycle_at) AS cycle_min,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED') AS nyp_events,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE') AS no_exact_events,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'
                                                  AND codes::text LIKE '%THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK%') AS no_exact_payload_no_book,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale_events
  FROM ext_candidate_outcomes
 WHERE cycle_at >= '2026-10-09 20:30:00+00' AND cycle_at < '2026-10-09 22:30:00+00'
   AND first_refusal IN ('PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE', 'QUOTE_STALE_ON_ARRIVAL')
 GROUP BY 1 ORDER BY 1 LIMIT 40;

\echo == N2 per cycle around the 2026-10-10 04:26Z API deploy
SELECT date_trunc('minute', cycle_at) AS cycle_min,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED') AS nyp_events,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE') AS no_exact_events,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'
                                                  AND codes::text LIKE '%THEODDSAPI_PAYLOAD_HAS_NO_PINNACLE_BOOK%') AS no_exact_payload_no_book,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale_events
  FROM ext_candidate_outcomes
 WHERE cycle_at >= '2026-10-10 04:00:00+00' AND cycle_at < '2026-10-10 05:30:00+00'
   AND first_refusal IN ('PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE', 'QUOTE_STALE_ON_ARRIVAL')
 GROUP BY 1 ORDER BY 1 LIMIT 40;

\echo == N3 the events that were NOT_YET_POSTED in the 2026-10-09 21:0x-21:3x cycles: sport, hours to start, and their refusal one cycle later
SELECT c.sport_key, c.home, c.away, c.commence_time,
       CASE WHEN c.commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN round((extract(epoch FROM (c.commence_time::timestamptz - c.cycle_at)) / 3600.0)::numeric, 1) END AS hours_to_start,
       (SELECT string_agg(DISTINCT x.first_refusal, ',') FROM ext_candidate_outcomes x
         WHERE x.provider_event_id = c.provider_event_id AND x.cycle_at >= '2026-10-09 21:30:00+00' AND x.cycle_at < '2026-10-09 22:30:00+00') AS later_refusals
  FROM ext_candidate_outcomes c
 WHERE c.cycle_at >= '2026-10-09 21:00:00+00' AND c.cycle_at < '2026-10-09 21:30:00+00'
   AND c.first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED'
 GROUP BY c.provider_event_id, c.sport_key, c.home, c.away, c.commence_time, c.cycle_at
 ORDER BY c.sport_key, c.commence_time LIMIT 30;
