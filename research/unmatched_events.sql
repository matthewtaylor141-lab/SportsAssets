-- READ-ONLY. WHY COLLECTED EVENTS DO NOT BECOME VALUATIONS (last 24 h).
-- Investigates unmatched events without changing any identity, market or
-- settlement matching rule.
-- U1 outcome / first refusal by sport (latest record per event and market)
-- U2 NO_PINNACLE_ON_EVENT by sport and family: commence-time distribution
-- U3 sample unmatched events per sport (names, commence time, venue slug)
-- U4 stale quotes: quote age distribution by sport
\echo '== U1 · first refusal by sport (latest per event, 24 h) =='
WITH latest AS (
  SELECT DISTINCT ON (provider_event_id, coalesce(us_market_slug, ''))
         sport_key, family, stage, outcome, first_refusal, codes, commence_time,
         cycle_at, quote_age_s, mapped_by
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours'
   ORDER BY provider_event_id, coalesce(us_market_slug, ''), cycle_at DESC)
SELECT sport_key, outcome, coalesce(first_refusal, '-') AS first_refusal,
       count(*)
  FROM latest GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;

\echo '== U2 · NO_PINNACLE_ON_EVENT by sport/family and time to start =='
SELECT sport_key, family, count(*) AS records,
       count(DISTINCT provider_event_id) AS events,
       count(*) FILTER (WHERE commence_time > cycle_at + interval '24 hours')
         AS starts_after_24h,
       count(*) FILTER (WHERE commence_time BETWEEN cycle_at
                        AND cycle_at + interval '24 hours') AS starts_within_24h,
       count(*) FILTER (WHERE commence_time < cycle_at) AS already_started
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
   AND (first_refusal = 'NO_PINNACLE_ON_EVENT'
        OR codes::text LIKE '%NO_PINNACLE_ON_EVENT%')
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo '== U3 · sample unmatched events (3 per sport) =='
SELECT * FROM (
  SELECT sport_key, home, away, commence_time, us_market_slug, global_slug,
         first_refusal, mapped_by, left(codes::text, 160) AS codes,
         row_number() OVER (PARTITION BY sport_key ORDER BY cycle_at DESC) AS rn
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '6 hours'
     AND outcome IS DISTINCT FROM 'VALUED'
     AND first_refusal IS NOT NULL) s
 WHERE rn <= 3 ORDER BY sport_key, rn;

\echo '== U4 · quote age on QUOTE_STALE by sport =='
SELECT sport_key, count(*),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY quote_age_s)::numeric, 1)
         AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY quote_age_s)::numeric, 1)
         AS p90_s,
       round(avg(provider_lag_s)::numeric, 1) AS avg_provider_lag_s,
       round(avg(our_processing_s)::numeric, 1) AS avg_our_processing_s
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
   AND (first_refusal = 'QUOTE_STALE' OR codes::text LIKE '%QUOTE_STALE%')
 GROUP BY 1 ORDER BY 2 DESC;
