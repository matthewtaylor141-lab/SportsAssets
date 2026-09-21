-- WHY CAN THE ONE MODEL-FREE EV NOT BE EVALUATED? Because half the book
-- is not there.
--
-- The census returned both_asks_readable = 0 out of 1,392 observations while
-- yes_ask parsed on 695 of them. So yes_ask is fine and NO_ASK IS NOT, and
-- the pair arithmetic -- the only EV in the engine that needs no forecast --
-- cannot be computed on a single captured row.
--
-- THIS IS A COLLECTOR DEFECT, NOT AN ECONOMIC FINDING. It would be wrong to
-- report "no arbitrage exists"; what exists is a dataset that cannot answer
-- the question. Establish exactly what is in the column before proposing a
-- repair.
--
-- Read only.
\echo == 1. WHAT IS ACTUALLY IN THE NO-LEG COLUMNS ==
SELECT
  count(*) AS rows,
  count(*) FILTER (WHERE no_ask IS NULL)                     AS no_ask_null,
  count(*) FILTER (WHERE no_ask = '')                        AS no_ask_empty,
  count(*) FILTER (WHERE no_ask = 'None')                    AS no_ask_none_str,
  count(*) FILTER (WHERE no_ask ~ '^[0-9.]+$')               AS no_ask_numeric,
  count(*) FILTER (WHERE no_bid ~ '^[0-9.]+$')               AS no_bid_numeric,
  count(*) FILTER (WHERE yes_ask ~ '^[0-9.]+$')              AS yes_ask_numeric,
  count(*) FILTER (WHERE yes_bid ~ '^[0-9.]+$')              AS yes_bid_numeric
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days';

\echo
\echo == 2. THE DISTINCT VALUES THE NO LEG TAKES ==
-- If it is one sentinel repeated, the collector is writing a placeholder. If
-- it is varied text, something else is wrong.
SELECT coalesce(no_ask, '<NULL>') AS no_ask_value, count(*) AS rows
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo
\echo == 3. AND THE YES LEG, FOR COMPARISON ==
SELECT coalesce(yes_ask, '<NULL>') AS yes_ask_value, count(*) AS rows
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo
\echo == 4. IS outcome_leg TELLING US ONE ROW IS ONE LEG, NOT ONE MARKET ==
-- If each observation is a single OUTCOME rather than a market, then the
-- complement lives in a different row and the pair sum has to be computed
-- by joining two rows -- not by reading four columns of one.
SELECT outcome_leg, count(*) AS rows,
       count(*) FILTER (WHERE yes_ask ~ '^[0-9.]+$') AS yes_ask_numeric,
       count(*) FILTER (WHERE no_ask  ~ '^[0-9.]+$') AS no_ask_numeric
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC;

\echo
\echo == 5. DO COMPLEMENT ROWS EXIST WITHIN THE SAME EVENT AND BUCKET ==
-- The pair needs two prices at the SAME instant. If two rows of the same
-- event share an observation_bucket, the pair sum is a join away. If not,
-- the data cannot support pair EV however the columns are fixed.
SELECT count(*) AS buckets_with_2plus_markets
  FROM (SELECT event_id, observation_bucket, count(DISTINCT market_id) AS n
          FROM bettor_state_observations
         WHERE observed_at > now() - interval '7 days'
           AND event_id IS NOT NULL
         GROUP BY 1, 2 HAVING count(DISTINCT market_id) > 1) t;

\echo
\echo == 6. THE READABILITY STATUS THE COLLECTOR ITSELF RECORDED ==
-- The collector has its own opinion about whether the book was readable.
-- If it says READABLE while no_ask is unusable, the status is wrong too.
SELECT book_readability_status, count(*) AS rows,
       count(*) FILTER (WHERE no_ask ~ '^[0-9.]+$') AS no_ask_numeric,
       count(*) FILTER (WHERE yes_ask ~ '^[0-9.]+$') AS yes_ask_numeric
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC;
