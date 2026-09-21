-- BOUNDING THE OPPORTUNITY FROM PUBLIC DATA -- read only.
--
-- The capacity target is $500,000/day of EXECUTED NOTIONAL. At ~$0.50 per
-- contract that is 1,000,000 contract executions per day, about 11.57 per
-- second. The question this file answers is not whether our software can
-- issue that many -- it can, by four orders of magnitude -- but whether the
-- markets we observe TRADE that much, because we cannot execute volume the
-- market does not have.
--
-- `stats_shares_traded` is captured per observation and is the venue's own
-- traded-volume statistic. It is the first thing in this repository that
-- bounds turnover with a MEASUREMENT rather than an assumption, and the
-- earlier capacity section used none.
--
-- WHAT THIS CANNOT SHOW. Traded volume is what the market did WITHOUT us.
-- Our participation would change it, in either direction, and by an unknown
-- amount. It is an ORDER-OF-MAGNITUDE bound, not a forecast -- and Track B-L
-- BLOCK_4 is the reason to read even displayed depth conservatively: a
-- 22,297-share displayed bid queue against 180 shares traded in 16 minutes,
-- with zero touches.
--
-- Every column here was verified against information_schema first.

\echo == 1. TRADED VOLUME PER OBSERVATION ==
SELECT count(*) AS rows,
       count(*) FILTER (WHERE stats_shares_traded ~ '^[0-9.]+$') AS numeric_rows,
       round(percentile_cont(0.10) WITHIN GROUP (
             ORDER BY stats_shares_traded::numeric)::numeric, 2) AS p10,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY stats_shares_traded::numeric)::numeric, 2) AS median,
       round(percentile_cont(0.90) WITHIN GROUP (
             ORDER BY stats_shares_traded::numeric)::numeric, 2) AS p90,
       round(percentile_cont(0.99) WITHIN GROUP (
             ORDER BY stats_shares_traded::numeric)::numeric, 2) AS p99,
       round(max(stats_shares_traded::numeric), 2)                AS max
  FROM bettor_state_observations
 WHERE stats_shares_traded ~ '^[0-9.]+$';

\echo
\echo == 2. THE CEILING: total traded shares across DISTINCT markets ==
-- One observation per market, taking its largest observed volume figure, so
-- repeated observations of one market are not summed into a bigger number
-- than the market ever traded.
SELECT count(*)                                     AS markets,
       round(sum(v), 0)                             AS total_shares,
       round(sum(v * p), 0)                         AS total_notional_usd,
       round(avg(v), 2)                             AS mean_shares_per_market
  FROM (SELECT market_id,
               max(stats_shares_traded::numeric) AS v,
               max(mid::numeric)                 AS p
          FROM bettor_state_observations
         WHERE stats_shares_traded ~ '^[0-9.]+$'
           AND mid ~ '^[0-9.]+$'
         GROUP BY market_id) t;

\echo
\echo == 3. HOW MANY MARKETS TRADE ENOUGH TO MATTER ==
SELECT count(*) FILTER (WHERE v >= 1)      AS at_least_1_share,
       count(*) FILTER (WHERE v >= 10)     AS at_least_10,
       count(*) FILTER (WHERE v >= 100)    AS at_least_100,
       count(*) FILTER (WHERE v >= 1000)   AS at_least_1000,
       count(*) FILTER (WHERE v >= 10000)  AS at_least_10000,
       count(*)                            AS markets
  FROM (SELECT market_id, max(stats_shares_traded::numeric) AS v
          FROM bettor_state_observations
         WHERE stats_shares_traded ~ '^[0-9.]+$'
         GROUP BY market_id) t;

\echo
\echo == 4. THE SPREAD DISTRIBUTION, which bounds the maker opportunity ==
-- A maker earns at most the spread and pays adverse selection out of it. The
-- pilot requires >= 2 ticks; this is how many books qualify.
SELECT count(*) AS rows,
       round(percentile_cont(0.10) WITHIN GROUP (
             ORDER BY spread::numeric)::numeric, 4) AS p10,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY spread::numeric)::numeric, 4) AS median,
       round(percentile_cont(0.90) WITHIN GROUP (
             ORDER BY spread::numeric)::numeric, 4) AS p90,
       count(*) FILTER (WHERE spread::numeric >= 0.02) AS at_least_2_ticks,
       count(*) FILTER (WHERE spread::numeric >= 0.03) AS at_least_3_ticks
  FROM bettor_state_observations
 WHERE spread ~ '^[0-9.]+$';

\echo
\echo == 5. ELIGIBLE BOOKS: every pilot condition at once ==
-- Open, readable, two-sided, spread >= 2 ticks, a ladder present, and priced
-- at or below $0.50. This is the population a pilot would quote into.
SELECT count(*)                          AS eligible_rows,
       count(DISTINCT market_id)         AS eligible_markets
  FROM bettor_state_observations
 WHERE venue_state = 'MARKET_STATE_OPEN'
   AND book_readability_status = 'READABLE'
   AND yes_bid ~ '^[0-9.]+$' AND yes_ask ~ '^[0-9.]+$'
   AND spread ~ '^[0-9.]+$' AND spread::numeric >= 0.02
   AND multi_level_depth ? 'ask'
   AND yes_ask::numeric <= 0.50;

\echo
\echo == 6. FRESHNESS OF THE ELIGIBLE POPULATION ==
-- A book that qualifies economically is useless if it reaches us too late to
-- act on. The measured MEDIAN source-to-receipt delay across the feed is
-- 549.6 s against a 10 s decision bound; this asks the same of the eligible
-- subset specifically, rather than assuming it inherits the whole.
SELECT count(*) AS eligible_rows,
       count(*) FILTER (WHERE book_age_s::numeric <= 10)  AS within_10s,
       count(*) FILTER (WHERE book_age_s::numeric <= 60)  AS within_60s,
       count(*) FILTER (WHERE book_age_s::numeric <= 300) AS within_300s,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY book_age_s::numeric)::numeric, 1)   AS median_age_s
  FROM bettor_state_observations
 WHERE venue_state = 'MARKET_STATE_OPEN'
   AND book_readability_status = 'READABLE'
   AND book_age_s ~ '^[0-9.]+$'
   AND spread ~ '^[0-9.]+$' AND spread::numeric >= 0.02
   AND multi_level_depth ? 'ask';

\echo
\echo == 7. HOLDING PERIOD: what we have actually measured ==
-- Turnover requires knowing how long capital is tied up per turn, and one
-- hour and one day are SCENARIOS, not bounds. This asks whether the
-- repository holds a single measured holding period. A fill row is the only
-- thing that could supply one.
SELECT count(*)                                            AS settlement_rows,
       count(*) FILTER (WHERE is_not_a_fill IS FALSE)      AS actual_fills,
       count(*) FILTER (WHERE settlement_status = 'SETTLED') AS settled
  FROM bettor_state_settlements;
