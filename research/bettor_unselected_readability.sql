-- IS THE READABLE SUBSET MISSING-AT-RANDOM?
--
-- The sampling frame is unbiased: a market chosen by the rotation
-- writes a row whether or not its book could be read. But 33 of the
-- first 53 reads came back HTTP 429, and a 429 is caused by the shared
-- gateway's aggregate rate -- whose heaviest other consumer is the live
-- mirror, which is busiest when games are live.
--
-- If refusal probability rises with LIVE_STATUS, then conditioning any
-- analysis on readable rows under-represents live states, and live is
-- correlated with spread, volatility and the future-value dynamics this
-- dataset exists to measure. That is a selection on a variable that is
-- not independent of the estimand.
--
-- This MEASURES that correlation instead of arguing about it. Read
-- only, descriptive. Not one of the pre-registered tests: it is about
-- the instrument, not about the market.
--
-- No settlement-minus-quote term appears anywhere below.

SELECT 'A_BY_LIVE_STATUS' AS section,
       coalesce(live_status, 'NULL') || ' | ' ||
       CASE WHEN book_readability_status = 'READABLE' THEN 'READABLE'
            WHEN book_readability_status LIKE '%RateLimit%'
                 THEN 'REFUSED_RATE_LIMIT'
            ELSE 'UNREADABLE_OTHER' END AS k,
       count(*)::text AS v
  FROM bettor_state_observations
 WHERE universe_version = 'BETTOR_UNSELECTED_STATE_V2'
 GROUP BY 2

UNION ALL

-- The same cut as a rate, which is the number that actually matters:
-- if these differ materially between PREGAME and LIVE the caveat is
-- real and every readable-row estimate must carry it.
SELECT 'B_REFUSAL_RATE',
       coalesce(live_status, 'NULL'),
       round(100.0 * count(*) FILTER (
                 WHERE book_readability_status LIKE '%RateLimit%')
             / nullif(count(*), 0), 1)::text || '% of ' ||
       count(*)::text
  FROM bettor_state_observations
 WHERE universe_version = 'BETTOR_UNSELECTED_STATE_V2'
 GROUP BY 2

UNION ALL

-- By hour, because the mirror's load follows the sporting day. A flat
-- profile here would weaken the concern; a peaked one confirms it.
SELECT 'C_REFUSAL_BY_HOUR',
       lpad(extract(hour FROM observed_at)::text, 2, '0') || 'Z',
       round(100.0 * count(*) FILTER (
                 WHERE book_readability_status LIKE '%RateLimit%')
             / nullif(count(*), 0), 1)::text || '% of ' ||
       count(*)::text
  FROM bettor_state_observations
 WHERE universe_version = 'BETTOR_UNSELECTED_STATE_V2'
 GROUP BY 2

UNION ALL

-- And by time-to-event bucket, the variable a maker would actually
-- condition on.
SELECT 'D_REFUSAL_BY_TIME_TO_EVENT',
       CASE
         WHEN time_to_event_s IS NULL
              OR time_to_event_s = 'NOT_IDENTIFIED' THEN 'UNKNOWN'
         WHEN time_to_event_s::numeric < 0      THEN 'IN_PLAY_OR_PAST'
         WHEN time_to_event_s::numeric < 3600   THEN 'LT_1H'
         WHEN time_to_event_s::numeric < 86400  THEN 'LT_1D'
         ELSE 'GE_1D'
       END,
       round(100.0 * count(*) FILTER (
                 WHERE book_readability_status LIKE '%RateLimit%')
             / nullif(count(*), 0), 1)::text || '% of ' ||
       count(*)::text
  FROM bettor_state_observations
 WHERE universe_version = 'BETTOR_UNSELECTED_STATE_V2'
 GROUP BY 2

ORDER BY 1, 2
