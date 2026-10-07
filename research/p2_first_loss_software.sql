-- READ-ONLY. The SOFTWARE first losses of the last 2 h, decomposed: stale on
-- arrival by provider lag vs our processing and the PinnAPI refusal that
-- left the metered quote; probability deadline passes by sport.
SELECT first_refusal, sport_key,
       CASE WHEN provider_lag_s IS NULL THEN 'LAG_UNMEASURED'
            WHEN provider_lag_s > 30 THEN 'PROVIDER_LAG_GT_30'
            ELSE 'DELIVERED_INSIDE_30' END lag_class,
       round(avg(provider_lag_s)::numeric,1) avg_lag, round(avg(our_processing_s)::numeric,1) avg_ours,
       (SELECT string_agg(DISTINCT c, ',') FROM jsonb_array_elements_text(codes) c
          WHERE c LIKE 'PINNAPI%' OR c LIKE 'FEED%') ws,
       count(*)
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '2 hours' AND outcome = 'REFUSED'
   AND first_refusal IN ('QUOTE_STALE_ON_ARRIVAL','PROBABILITY_DEADLINE_PASSED_BEFORE_THE_READ_COULD_FINISH',
                         'FEED_MARKET_NOT_IN_CURRENT_STATE','FEED_QUOTE_OLDER_THAN_LIMIT','PINNAPI_PRIMARY_INPUT_CHANGED')
 GROUP BY 1,2,3,6 ORDER BY 7 DESC LIMIT 60;
