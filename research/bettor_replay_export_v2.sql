-- REPLAY EXPORT v2: observations WITH their settlement outcomes.
--
-- v1 pulled the most recent 200 rows and every one was stale at the 10s
-- decision bound -- median age hours. That is the honest state of a RESEARCH
-- sampler, which reads at 60/300/900/3600s horizons and was never built to
-- produce decision-time books.
--
-- This pulls the FRESHEST rows available so the replay runs on the best data
-- that exists, joins the settlement table so positions can actually resolve,
-- and reports the age distribution so the sensitivity analysis has a basis.
-- Read only.
\echo == 1. THE AGE DISTRIBUTION, WHICH DECIDES WHAT IS ELIGIBLE ==
SELECT count(*) AS rows,
       count(*) FILTER (WHERE book_age_s ~ '^[0-9.]+$'
                          AND book_age_s::numeric <= 10)   AS within_10s,
       count(*) FILTER (WHERE book_age_s ~ '^[0-9.]+$'
                          AND book_age_s::numeric <= 60)   AS within_60s,
       count(*) FILTER (WHERE book_age_s ~ '^[0-9.]+$'
                          AND book_age_s::numeric <= 300)  AS within_300s,
       count(*) FILTER (WHERE book_age_s ~ '^[0-9.]+$'
                          AND book_age_s::numeric <= 900)  AS within_900s,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY nullif(book_age_s, 'NOT_IDENTIFIED')::numeric)::numeric,
             1) AS median_age_s
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days';

\echo
\echo == 2. HOW MANY ARE FULLY USABLE: fresh, open, readable, two-sided ==
SELECT count(*) AS usable
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
   AND book_age_s ~ '^[0-9.]+$' AND book_age_s::numeric <= 900
   AND venue_state = 'MARKET_STATE_OPEN'
   AND book_readability_status = 'READABLE'
   AND yes_bid ~ '^[0-9.]+$' AND yes_ask ~ '^[0-9.]+$';

\echo
\echo == 3. THE EXPORT: freshest usable rows, with settlement where known ==
SELECT o.observation_id, o.market_id, o.event_id, o.outcome_leg,
       o.observed_at, o.book_source_ts, o.book_age_s, o.venue_state,
       o.book_readability_status, o.yes_bid, o.yes_ask, o.no_bid, o.no_ask,
       o.sport, o.league, o.market_type,
       s.settlement_outcome, s.settlement_status
  FROM bettor_state_observations o
  LEFT JOIN bettor_state_settlements s USING (observation_id)
 WHERE o.observed_at > now() - interval '7 days'
   AND o.book_age_s ~ '^[0-9.]+$'
   AND o.venue_state = 'MARKET_STATE_OPEN'
   AND o.book_readability_status = 'READABLE'
   AND o.yes_bid ~ '^[0-9.]+$' AND o.yes_ask ~ '^[0-9.]+$'
 ORDER BY o.book_age_s::numeric ASC
 LIMIT 120;

\echo
\echo == 4. DO ANY CONTRACTS HAVE TWO DISTINCT OUTCOME LEGS OBSERVED ==
-- The pair question, asked on contract identity rather than on the event.
SELECT count(*) AS contracts_with_two_legs
  FROM (SELECT market_id
          FROM bettor_state_observations
         WHERE observed_at > now() - interval '7 days'
         GROUP BY market_id
        HAVING count(DISTINCT outcome_leg) > 1) t;

\echo
\echo == 5. SETTLEMENT COVERAGE ==
SELECT count(*) AS settlement_rows,
       count(*) FILTER (WHERE settlement_status = 'SETTLED') AS settled,
       count(DISTINCT settlement_outcome) AS distinct_outcomes
  FROM bettor_state_settlements;
