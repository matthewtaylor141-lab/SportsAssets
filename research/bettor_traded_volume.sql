-- TRADED VOLUME ONLY -- sections 1 and 2 of the opportunity bound.
--
-- Split out because get_job_logs returns the TAIL of a run's log, so the
-- first sections of a multi-part query are the ones that get truncated away.
-- A result nobody can retrieve is not a result.
--
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

