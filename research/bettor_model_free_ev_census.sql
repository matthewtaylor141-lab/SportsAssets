-- IS THERE ANY EV THAT NEEDS NO FORECAST? Count it, do not assume it.
--
-- bettor_fair_value records the measured result: FV_BETTOR_INDEPENDENT is
-- NOT_IDENTIFIED, the blend scored WORSE than the market by 0.00926 log loss
-- on held-out events, and directional actions are therefore blocked. So an EV
-- engine cannot open by claiming the market is wrong.
--
-- One action class needs no such claim. A YES and a NO on the same market pay
-- exactly 1.00 between them at settlement, whatever happens. So if
--
--     yes_ask + no_ask  <  1.00 - fees
--
-- the pair is a locked positive cash flow, model-free. The mirror case,
-- yes_bid + no_bid > 1.00 + fees, is the sell side.
--
-- THIS IS THE NUMBER THE WHOLE MANDATE TURNS ON. If it is near zero the
-- engine is correct to decide NO_TRADE almost always, and a turnover target
-- cannot be met by identified EV. Measuring it is a query; assuming it either
-- way is not allowed.
--
-- Read only.
\echo == 1. HOW MANY OBSERVATIONS CARRY A READABLE TWO-SIDED BOOK ==
-- Quotes are TEXT. A row that does not parse is not a zero and not a miss --
-- it is unreadable, and it is counted as such.
SELECT count(*) AS observations,
       count(*) FILTER (WHERE yes_ask ~ '^[0-9.]+$' AND no_ask ~ '^[0-9.]+$')
           AS both_asks_readable,
       count(*) FILTER (WHERE yes_bid ~ '^[0-9.]+$' AND no_bid ~ '^[0-9.]+$')
           AS both_bids_readable,
       count(*) FILTER (WHERE yes_ask IS NULL OR no_ask IS NULL)
           AS an_ask_is_null,
       count(*) FILTER (WHERE yes_ask IS NOT NULL AND yes_ask !~ '^[0-9.]+$')
           AS yes_ask_unparsable,
       min(observed_at) AS earliest, max(observed_at) AS latest
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days';

\echo
\echo == 2. THE PAIR SUM, DISTRIBUTED ==
-- yes_ask + no_ask. Below 1.00 is a model-free buy; above is the normal
-- state of a market that charges a spread.
WITH q AS (
  SELECT yes_ask::numeric AS ya, no_ask::numeric AS na,
         yes_bid::numeric AS yb, no_bid::numeric AS nb
    FROM bettor_state_observations
   WHERE observed_at > now() - interval '7 days'
     AND yes_ask ~ '^[0-9.]+$' AND no_ask ~ '^[0-9.]+$'
     AND yes_bid ~ '^[0-9.]+$' AND no_bid ~ '^[0-9.]+$'
)
SELECT count(*) AS rows,
       round(min(ya + na), 4)  AS min_ask_sum,
       round(avg(ya + na), 4)  AS avg_ask_sum,
       round(max(ya + na), 4)  AS max_ask_sum,
       count(*) FILTER (WHERE ya + na < 1.00)  AS ask_sum_under_par,
       count(*) FILTER (WHERE ya + na < 0.99)  AS under_par_by_1c,
       count(*) FILTER (WHERE ya + na < 0.98)  AS under_par_by_2c,
       round(max(yb + nb), 4)  AS max_bid_sum,
       count(*) FILTER (WHERE yb + nb > 1.00)  AS bid_sum_over_par,
       count(*) FILTER (WHERE yb + nb > 1.01)  AS over_par_by_1c
  FROM q;

\echo
\echo == 3. IF ANY EXIST, WHAT ARE THEY ==
-- An arbitrage that exists only in a stale or one-sided book is not an
-- arbitrage. Print book_age_s and venue_state beside every candidate.
SELECT market_id, observed_at, yes_bid, yes_ask, no_bid, no_ask,
       round((yes_ask::numeric + no_ask::numeric), 4) AS ask_sum,
       book_age_s, venue_state, live_status, book_readability_status
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
   AND yes_ask ~ '^[0-9.]+$' AND no_ask ~ '^[0-9.]+$'
   AND (yes_ask::numeric + no_ask::numeric) < 1.00
 ORDER BY (yes_ask::numeric + no_ask::numeric)
 LIMIT 40;

\echo
\echo == 4. THE SPREAD, WHICH IS WHAT A MAKER WOULD BE PAID ==
-- Maker EV needs P_FILL, which is NOT_IDENTIFIED, so this cannot be turned
-- into an EV today. It bounds what the prize could be if it were.
WITH q AS (
  SELECT (yes_ask::numeric - yes_bid::numeric) AS sp
    FROM bettor_state_observations
   WHERE observed_at > now() - interval '7 days'
     AND yes_ask ~ '^[0-9.]+$' AND yes_bid ~ '^[0-9.]+$'
)
SELECT count(*) AS rows,
       round(percentile_cont(0.10) WITHIN GROUP (ORDER BY sp)::numeric, 4) AS p10,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY sp)::numeric, 4) AS p50,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY sp)::numeric, 4) AS p90,
       count(*) FILTER (WHERE sp <= 0) AS non_positive_spread,
       count(*) FILTER (WHERE sp >= 0.10) AS spread_10c_or_wider
  FROM q;

\echo
\echo == 5. HOW MANY DISTINCT MARKETS ARE EVEN ELIGIBLE PER DAY ==
-- Capacity starts here: an engine cannot trade markets it never observes.
SELECT date_trunc('day', observed_at) AS day,
       count(*) AS observations,
       count(DISTINCT market_id) AS distinct_markets,
       count(DISTINCT event_id) AS distinct_events
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 1 DESC;
