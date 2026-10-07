-- READ-ONLY. SOFTWARE first-loss evidence on the live release (last 90 min):
-- per code counts by sport_key, and full code arrays for the two largest
-- (QUOTE_STALE_ON_ARRIVAL, PINNAPI_PRIMARY_NO_EXACT_FIXTURE). SELECT only.
SELECT first_refusal, sport_key, count(*) AS n,
       count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '90 minutes' AND first_refusal IS NOT NULL
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key, home,
       away, commence_time, writer, stage, left(codes::text, 900) AS codes
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '90 minutes'
   AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 ORDER BY provider_event_id, cycle_at DESC LIMIT 12;

SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key, home,
       away, commence_time, writer, left(codes::text, 900) AS codes
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '90 minutes'
   AND first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'
 ORDER BY provider_event_id, cycle_at DESC LIMIT 12;
