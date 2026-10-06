-- READ-ONLY. First refusals AFTER release 2514236 booted (workers boot
-- 2026-10-06 17:40:33Z): the candidate outcomes and the paper decisions,
-- by code, with samples. Every statement is a SELECT.
SELECT first_refusal, sport_key, count(*) AS n,
       count(DISTINCT provider_event_id) AS events,
       min(cycle_at) AS first_at, max(cycle_at) AS last_at
  FROM ext_candidate_outcomes
 WHERE cycle_at > timestamptz '2026-10-06 17:41:00+00'
   AND first_refusal IS NOT NULL
 GROUP BY 1, 2 ORDER BY n DESC LIMIT 60;

SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key, home,
       away, commence_time, left(codes::text, 600) AS codes
  FROM ext_candidate_outcomes
 WHERE cycle_at > timestamptz '2026-10-06 17:41:00+00'
   AND first_refusal IN ('PINNAPI_PRIMARY_NO_EXACT_FIXTURE',
                         'QUOTE_STALE_ON_ARRIVAL')
 ORDER BY provider_event_id, cycle_at DESC LIMIT 40;

SELECT strategy, coalesce(refusal, 'ENTER') AS why, count(*) AS n,
       count(DISTINCT us_market_slug) AS markets
  FROM paper_decisions
 WHERE decided_at > timestamptz '2026-10-06 17:41:00+00'
 GROUP BY 1, 2 ORDER BY n DESC LIMIT 60;
