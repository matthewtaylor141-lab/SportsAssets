-- READ-ONLY. Software census closure: WHOSE AGE for every arrival refusal
-- (QUOTE_STALE_ON_ARRIVAL / QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER) in the
-- last 6 h, from the row's own measurement (migration 143 columns), and the
-- PinnAPI refusal that rode beside it. SELECT only.
SELECT first_refusal, sport_key,
       count(*) AS n,
       count(*) FILTER (WHERE provider_lag_s > 30) AS provider_lag_over_30,
       count(*) FILTER (WHERE provider_lag_s <= 30) AS ours_after_receipt,
       count(*) FILTER (WHERE provider_lag_s IS NULL) AS lag_unmeasured,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY provider_lag_s)::numeric, 1) AS p50_provider_lag_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY our_processing_s)::numeric, 1) AS p50_our_processing_s,
       min(queue_position) AS min_pos, max(queue_position) AS max_pos
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '6 hours'
   AND first_refusal IN ('QUOTE_STALE_ON_ARRIVAL',
                         'QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

SELECT first_refusal, codes ->> 1 AS beside, count(*) AS n
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '6 hours'
   AND first_refusal IN ('QUOTE_STALE_ON_ARRIVAL',
                         'QUOTE_STALE_AS_DELIVERED_BY_THE_PROVIDER',
                         'PINNAPI_PRIMARY_NO_EXACT_FIXTURE',
                         'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;
