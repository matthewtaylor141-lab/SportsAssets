-- READ-ONLY. RC6.3b root-cause audit, group software-reds, part 2: the four
-- SOFTWARE first-loss codes of the 1 h census in the approved-judge packet
-- (completion.json computed_at 1791626074.8284767 -> window
-- [1791622474.8284767, 1791626075.8284767)): PINNAPI_PRIMARY_NO_EXACT_FIXTURE 35,
-- FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE 1, QUOTE_STALE_ON_ARRIVAL 38,
-- PROBABILITY_EVIDENCE_STALE 10 (sum 84). SELECT only.

\echo == Q1 hourly distinct events by ledger first refusal, last 24 h
SELECT date_trunc('hour', cycle_at) AS hr, first_refusal,
       count(DISTINCT provider_event_id) AS events, count(*) AS rows
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
   AND first_refusal IN ('PINNAPI_PRIMARY_NO_EXACT_FIXTURE',
                         'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE',
                         'QUOTE_STALE_ON_ARRIVAL',
                         'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED')
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 120;

\echo == Q2 QUOTE_STALE_ON_ARRIVAL rows in the packet window by sport, beside-codes, lag class
SELECT sport_key, left(codes::text, 150) AS codes_list, mapped_by,
       CASE WHEN provider_lag_s IS NULL THEN 'LAG_UNMEASURED'
            WHEN provider_lag_s > 30 THEN 'PROVIDER_LAG_GT_30'
            ELSE 'DELIVERED_INSIDE_30' END AS lag_class,
       count(*) AS rows, count(DISTINCT provider_event_id) AS events,
       round(avg(provider_lag_s)::numeric, 1) AS avg_provider_lag_s,
       round(avg(our_processing_s)::numeric, 1) AS avg_our_processing_s,
       round(avg(quote_age_s)::numeric, 1) AS avg_quote_age_s,
       min(queue_position) AS min_q, max(queue_position) AS max_q,
       round(avg(extract(epoch FROM (CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN commence_time::timestamptz END) - cycle_at) / 3600.0)::numeric, 1) AS avg_hours_to_start
  FROM ext_candidate_outcomes
 WHERE cycle_at >= to_timestamp(1791622474.8284767) AND cycle_at < to_timestamp(1791626075.8284767)
   AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40;

\echo == Q3 PROBABILITY_EVIDENCE_STALE paper decisions in the packet window by strategy
WITH d AS (
  SELECT d.strategy, d.decision_id, d.valuation_id, d.us_market_slug, d.decided_at,
         d.pinnacle->>'decided_via' AS via, d.pinnacle->>'provider' AS provider,
         CASE WHEN d.pinnacle->>'age_s' ~ '^-?[0-9.]+$' THEN (d.pinnacle->>'age_s')::numeric END AS age_s,
         CASE WHEN d.pinnacle->>'limit_s' ~ '^-?[0-9.]+$' THEN (d.pinnacle->>'limit_s')::numeric END AS limit_s,
         CASE WHEN d.pinnacle->>'decision_lag_after_valuation_s' ~ '^-?[0-9.]+$' THEN (d.pinnacle->>'decision_lag_after_valuation_s')::numeric END AS lag_after_val
    FROM paper_decisions d
   WHERE d.decided_at >= to_timestamp(1791622474.8284767) AND d.decided_at < to_timestamp(1791626075.8284767)
     AND 'PROBABILITY_EVIDENCE_STALE' = ANY(d.refusals))
SELECT strategy, via, provider, count(*) AS decisions, count(DISTINCT valuation_id) AS valuations,
       count(DISTINCT us_market_slug) AS slugs, min(age_s) AS min_age, round(avg(age_s), 1) AS avg_age,
       max(age_s) AS max_age, min(limit_s) AS limit_s,
       round(avg(lag_after_val), 1) AS avg_lag_after_valuation, max(lag_after_val) AS max_lag_after_valuation
  FROM d GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo == Q3b PROBABILITY_EVIDENCE_STALE decisions one by one (cap 40)
SELECT d.strategy, d.decided_at, d.us_market_slug,
       d.pinnacle->>'decided_via' AS via, d.pinnacle->>'age_s' AS age_s, d.pinnacle->>'limit_s' AS limit_s,
       d.pinnacle->>'decision_lag_after_valuation_s' AS lag_after_valuation_s,
       v.record_purpose, v.decided_at AS valuation_decided_at, left(d.refusals::text, 120) AS refusals
  FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.decided_at >= to_timestamp(1791622474.8284767) AND d.decided_at < to_timestamp(1791626075.8284767)
   AND 'PROBABILITY_EVIDENCE_STALE' = ANY(d.refusals)
 ORDER BY d.decided_at LIMIT 40;

\echo == Q4 paper decisions in the packet window by verdict and first refusal (context)
SELECT d.strategy, d.verdict, d.refusal, count(*) AS decisions
  FROM paper_decisions d
 WHERE d.decided_at >= to_timestamp(1791622474.8284767) AND d.decided_at < to_timestamp(1791626075.8284767)
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo == Q5 NO_EXACT horizon: hours to start of every ledger row by bucket, 72 h, four sport keys
WITH r AS (
  SELECT sport_key, provider_event_id, cycle_at, outcome, first_refusal,
         extract(epoch FROM (CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN commence_time::timestamptz END) - cycle_at) / 3600.0 AS hrs
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '72 hours' AND provider_event_id IS NOT NULL
     AND sport_key IN ('americanfootball_ncaaf', 'baseball_mlb', 'soccer_mexico_ligamx', 'soccer_usa_mls')),
b AS (SELECT *, CASE WHEN hrs IS NULL THEN 'na' WHEN hrs < 6 THEN 'a_0_6h' WHEN hrs < 24 THEN 'b_6_24h'
                     WHEN hrs < 48 THEN 'c_24_48h' WHEN hrs < 96 THEN 'd_48_96h' ELSE 'e_96h_plus' END AS bucket FROM r)
SELECT sport_key, bucket, count(*) AS rows, count(DISTINCT provider_event_id) AS events,
       count(*) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE') AS no_exact_rows,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE') AS no_exact_events,
       count(*) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED') AS not_yet_posted_rows,
       count(*) FILTER (WHERE first_refusal = 'NO_PINNACLE_ON_EVENT') AS no_pinnacle_rows,
       count(*) FILTER (WHERE first_refusal IS NULL OR first_refusal NOT LIKE 'PINNAPI_PRIMARY_NO_EXACT%'
                         AND first_refusal NOT IN ('PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED', 'NO_PINNACLE_ON_EVENT')) AS other_rows
  FROM b GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 60;

\echo == Q6 events that had NO_EXACT rows in 72 h: did the same event ever reach another outcome, and how far from the start
WITH r AS (
  SELECT sport_key, provider_event_id, cycle_at, first_refusal,
         extract(epoch FROM (CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN commence_time::timestamptz END) - cycle_at) / 3600.0 AS hrs
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '72 hours' AND provider_event_id IS NOT NULL),
e AS (SELECT sport_key, provider_event_id,
             max(hrs) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE') AS max_hrs_no_exact,
             min(hrs) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE') AS min_hrs_no_exact,
             max(hrs) FILTER (WHERE first_refusal IS DISTINCT FROM 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'
                                AND first_refusal IS DISTINCT FROM 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED'
                                AND first_refusal IS DISTINCT FROM 'NO_PINNACLE_ON_EVENT') AS max_hrs_other
        FROM r GROUP BY 1, 2)
SELECT sport_key, count(*) FILTER (WHERE max_hrs_no_exact IS NOT NULL) AS events_with_no_exact,
       count(*) FILTER (WHERE max_hrs_no_exact IS NOT NULL AND max_hrs_other IS NOT NULL AND max_hrs_other < min_hrs_no_exact) AS no_exact_then_other_later,
       count(*) FILTER (WHERE max_hrs_no_exact IS NOT NULL AND max_hrs_other IS NULL) AS no_exact_never_other,
       count(*) FILTER (WHERE min_hrs_no_exact IS NOT NULL AND min_hrs_no_exact < 24) AS no_exact_seen_inside_24h_of_start,
       round(min(min_hrs_no_exact)::numeric, 1) AS smallest_hrs_to_start_at_no_exact,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY max_hrs_other) FILTER (WHERE max_hrs_no_exact IS NOT NULL)::numeric, 1) AS median_hrs_to_start_when_first_other
  FROM e GROUP BY 1 HAVING count(*) FILTER (WHERE max_hrs_no_exact IS NOT NULL) > 0 ORDER BY 2 DESC LIMIT 30;

\echo == Q7 NO_EXACT events seen inside 30 h of the start in 72 h (names that fail), cap 60
SELECT sport_key, home, away, commence_time, count(*) AS rows,
       round(min(extract(epoch FROM (CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN commence_time::timestamptz END) - cycle_at) / 3600.0)::numeric, 1) AS min_hrs_to_start,
       left(max(codes::text), 130) AS codes_list
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '72 hours' AND first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'
   AND (CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN commence_time::timestamptz END) - cycle_at < interval '30 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 6, 1 LIMIT 60;

\echo == Q8 the FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE row(s) in the packet window
SELECT cycle_at, sport_key, home, away, commence_time, stage, outcome, first_refusal, left(codes::text, 200) AS codes_list,
       queue_position, provider_lag_s, our_processing_s, quote_age_s, mapped_by, us_market_slug
  FROM ext_candidate_outcomes
 WHERE cycle_at >= to_timestamp(1791622474.8284767) AND cycle_at < to_timestamp(1791626075.8284767)
   AND first_refusal = 'FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE'
 ORDER BY cycle_at LIMIT 10;
