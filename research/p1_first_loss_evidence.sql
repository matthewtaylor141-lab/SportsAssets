-- READ-ONLY. P1 first-loss census evidence (coverage SOFTWARE -> 0).
--
-- Asks production, for the last 24 h of ext_candidate_outcomes (the census
-- source), the actual rows behind each SOFTWARE first-loss code:
--   1. metered events refused PINNAPI_PRIMARY_NO_EXACT_FIXTURE (their names)
--   2. PinnAPI-native seeds (provider_event_id 'pinnapi:<fixture>') of the
--      same sport keys -- their home/away ARE the feed fixture names -- so
--      the unmatched name pairs can be read side by side
--   3. QUOTE_STALE_ON_ARRIVAL arrival split per sport key
--   4. the small codes, with their rows
--   5. SETTLEMENT_NOT_SUPPORTED decisions by family/slug and their clauses
--   6. VENUE_NATIVE_LEAGUE_NOT_ADMITTED rows
--
-- No writes. Every statement is a SELECT.

\echo == 1. metered events refused PINNAPI_PRIMARY_NO_EXACT_FIXTURE (24 h) ==
SELECT sport_key, home, away, commence_time,
       count(*) AS rows, count(DISTINCT provider_event_id) AS events,
       max(first_refusal) AS first_refusal
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '24 hours'
   AND provider_event_id NOT LIKE 'pinnapi:%'
   AND (first_refusal LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%'
        OR codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%')
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 4, 2
 LIMIT 400;

\echo == 2. PinnAPI-native seeds (feed fixture names) in the same sport keys (48 h) ==
SELECT sport_key, home, away, commence_time,
       min(provider_event_id) AS feed_id, count(*) AS rows
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '48 hours'
   AND provider_event_id LIKE 'pinnapi:%'
   AND sport_key IN (
       SELECT DISTINCT sport_key FROM ext_candidate_outcomes
        WHERE cycle_at >= now() - interval '24 hours'
          AND provider_event_id NOT LIKE 'pinnapi:%'
          AND (first_refusal LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%'
               OR codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%'))
 GROUP BY 1, 2, 3, 4
 ORDER BY 1, 4, 2
 LIMIT 600;

\echo == 3. QUOTE_STALE_ON_ARRIVAL arrival split per sport key (24 h) ==
SELECT sport_key, count(*) AS rows, count(DISTINCT provider_event_id) AS events,
       round(avg(provider_lag_s)::numeric, 2) AS avg_provider_lag_s,
       round(avg(our_processing_s)::numeric, 2) AS avg_ours_s,
       round(avg(quote_age_s)::numeric, 2) AS avg_age_s,
       round(max(quote_age_s)::numeric, 2) AS max_age_s,
       max(codes::text) AS sample_codes
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '24 hours'
   AND (first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
        OR codes::text LIKE '%QUOTE_STALE_ON_ARRIVAL%')
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;

\echo == 3b. QUOTE_STALE_ON_ARRIVAL rows (24 h, newest 60) ==
SELECT cycle_at, sport_key, queue_position, provider_event_id, home, away,
       round(provider_lag_s::numeric, 2) AS lag_s,
       round(our_processing_s::numeric, 2) AS ours_s,
       round(quote_age_s::numeric, 2) AS age_s, mapped_by, left(codes::text, 220) AS codes
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '24 hours'
   AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 ORDER BY cycle_at DESC, queue_position LIMIT 60;

\echo == 4. the small codes, rows (24 h) ==
SELECT cycle_at, sport_key, provider_event_id, home, away, commence_time,
       stage, outcome, first_refusal, left(codes::text, 300) AS codes
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '24 hours'
   AND (codes::text ~ '(PINNAPI_PRIMARY_INPUT_CHANGED|PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE|FEED_MARKET_NOT_IN_CURRENT_STATE|FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE|FEED_QUOTE_OLDER_THAN_LIMIT)'
        OR first_refusal ~ '(PINNAPI_PRIMARY_INPUT_CHANGED|PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE|FEED_MARKET_NOT_IN_CURRENT_STATE|FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE|FEED_QUOTE_OLDER_THAN_LIMIT)')
 ORDER BY cycle_at DESC LIMIT 80;

\echo == 4b. valuations / decisions carrying the small codes (24 h) ==
SELECT v.decided_at, v.sport_family, v.us_market_slug, v.event_key,
       v.provider, v.age_s, v.admissible, left(v.refusals::text, 300) AS refusals
  FROM external_valuations v
 WHERE v.decided_at >= now() - interval '24 hours'
   AND v.refusals::text ~ '(PINNAPI_PRIMARY_INPUT_CHANGED|PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE|FEED_MARKET_NOT_IN_CURRENT_STATE|FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE|FEED_QUOTE_OLDER_THAN_LIMIT)'
 ORDER BY v.decided_at DESC LIMIT 60;

SELECT d.decided_at, d.us_market_slug, d.verdict, d.refusal,
       left(array_to_string(d.refusals, ','), 300) AS refusals,
       left(d.pinnacle::text, 400) AS pinnacle
  FROM paper_decisions d
 WHERE d.decided_at >= now() - interval '24 hours'
   AND array_to_string(d.refusals, ',') ~ '(PINNAPI_PRIMARY_INPUT_CHANGED|PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE|FEED_MARKET_NOT_IN_CURRENT_STATE|FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE|FEED_QUOTE_OLDER_THAN_LIMIT)'
 ORDER BY d.decided_at DESC LIMIT 40;

\echo == 5. SETTLEMENT_NOT_SUPPORTED decisions by slug family and clauses (24 h) ==
SELECT split_part(d.us_market_slug, '-', 2) AS league_token,
       count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets,
       left(array_to_string(d.refusals, ','), 400) AS refusals
  FROM paper_decisions d
 WHERE d.decided_at >= now() - interval '24 hours'
   AND 'SETTLEMENT_NOT_SUPPORTED' = ANY(d.refusals)
 GROUP BY 1, 4 ORDER BY 2 DESC LIMIT 60;

SELECT v.sport_family, split_part(v.us_market_slug, '-', 2) AS league_token,
       count(*) AS valuations, left(v.refusals::text, 400) AS refusals
  FROM external_valuations v
 WHERE v.decided_at >= now() - interval '24 hours'
   AND v.refusals::text LIKE '%VOID_ABANDONMENT%'
 GROUP BY 1, 2, 4 ORDER BY 3 DESC LIMIT 40;

\echo == 6. VENUE_NATIVE_LEAGUE_NOT_ADMITTED rows (24 h) ==
SELECT sport_key, home, away, commence_time, count(*) AS rows,
       left(max(codes::text), 300) AS codes
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '24 hours'
   AND codes::text LIKE '%VENUE_NATIVE_LEAGUE_NOT_ADMITTED%'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 4 LIMIT 60;
