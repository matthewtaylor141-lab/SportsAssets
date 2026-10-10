-- READ-ONLY. RC6.3c COLL-1 phase a, independent review of rc6/collector-book-latency 79527da3: what the single
-- 6 s head-of-queue read floor (VENUE_READ_HEAD_OF_QUEUE_FLOOR_S, applied at EVERY queue position) would have
-- refused without a read that the base read -- and what the base made of those reads. Slack at arrival =
-- 30 - provider_lag_s - our_processing_s (our_processing_s is measured at the arrival check, before the read;
-- ext_pinnacle_loop.arrival_split). Production ledger ext_candidate_outcomes. SELECT only.

\echo == R1 last 24 h: every row that reached the arrival check with a price, by slack-at-arrival band and shape
SELECT CASE WHEN 30 - provider_lag_s - our_processing_s <= 0 THEN 'a_past_limit_lever_A'
            WHEN 30 - provider_lag_s - our_processing_s < 6 THEN 'b_slack_0_6_THE_FLOOR_SKIPS'
            WHEN 30 - provider_lag_s - our_processing_s < 13 THEN 'c_slack_6_13'
            ELSE 'd_slack_ge_13' END AS slack_at_arrival_band,
       CASE WHEN outcome = 'ADMITTED' THEN 'ADMITTED'
            WHEN first_refusal IN ('QUOTE_STALE_ON_ARRIVAL', 'QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER') THEN 'STALE_ON_ARRIVAL'
            WHEN first_refusal = 'PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH' THEN 'READ_PAST_DEADLINE'
            WHEN first_refusal = 'QUOTE_STALE' THEN 'STALE_AFTER_THE_READ'
            ELSE 'OTHER_REFUSAL' END AS shape,
       count(*) AS rows,
       round(avg(our_processing_s)::numeric, 2) AS avg_our_processing_s
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours' AND provider_lag_s IS NOT NULL AND our_processing_s IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 40;

\echo == R2 last 24 h: the rows the floor would skip (0 < slack at arrival < 6 s), by queue band, outcome and first refusal
SELECT CASE WHEN queue_position = 0 THEN 'q0' WHEN queue_position <= 2 THEN 'q1_2' WHEN queue_position <= 5 THEN 'q3_5' ELSE 'q6_plus' END AS queue_band,
       outcome, first_refusal, count(*) AS rows,
       round(avg(30 - provider_lag_s - our_processing_s)::numeric, 2) AS avg_slack_at_arrival_s
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours' AND provider_lag_s IS NOT NULL AND our_processing_s IS NOT NULL
   AND 30 - provider_lag_s - our_processing_s > 0 AND 30 - provider_lag_s - our_processing_s < 6
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo == R3 last 7 d: ADMITTED rows per day by slack at arrival -- priced candidates that reached the read with less than one floor of slack
SELECT date_trunc('day', cycle_at)::date AS day,
       count(*) FILTER (WHERE 30 - provider_lag_s - our_processing_s < 6) AS admitted_slack_lt_6,
       count(*) FILTER (WHERE 30 - provider_lag_s - our_processing_s >= 6) AS admitted_slack_ge_6,
       count(*) AS admitted
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '7 days' AND outcome = 'ADMITTED' AND provider_lag_s IS NOT NULL AND our_processing_s IS NOT NULL
 GROUP BY 1 ORDER BY 1 LIMIT 10;

\echo == R4 last 24 h: the step in our processing between consecutive queue positions inside one fetch, by position band -- the cost of a read deep in the queue
WITH r AS (
  SELECT cycle_id, sport_key, queue_position, our_processing_s,
         our_processing_s - lag(our_processing_s) OVER (PARTITION BY cycle_id, sport_key ORDER BY queue_position) AS step_s
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours' AND our_processing_s IS NOT NULL)
SELECT CASE WHEN queue_position <= 1 THEN 'q1' WHEN queue_position <= 5 THEN 'q2_5' WHEN queue_position <= 20 THEN 'q6_20' ELSE 'q21_plus' END AS band,
       count(*) AS rows, round(avg(step_s)::numeric, 2) AS avg_step_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY step_s))::numeric, 2) AS p50_step_s,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY step_s))::numeric, 2) AS p90_step_s
  FROM r WHERE step_s IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 10;

\echo == R5 last 7 d: of the ADMITTED rows with less than one floor of slack at arrival, the slack distribution and queue positions
SELECT width_bucket(30 - provider_lag_s - our_processing_s, 0, 6, 6) AS slack_bucket_1s,
       count(*) AS admitted_rows, min(queue_position) AS min_q, max(queue_position) AS max_q,
       round(avg(queue_position)::numeric, 1) AS avg_q
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '7 days' AND outcome = 'ADMITTED' AND provider_lag_s IS NOT NULL AND our_processing_s IS NOT NULL
   AND 30 - provider_lag_s - our_processing_s > 0 AND 30 - provider_lag_s - our_processing_s < 6
 GROUP BY 1 ORDER BY 1 LIMIT 10;
