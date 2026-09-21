-- WHAT ONE DAY'S VOLUME ACTUALLY IS, AND WHETHER THE COUNTER CAN SAY.
--
-- The $396,361/day figure took max(stats_shares_traded) per market over
-- the whole table and multiplied it by max(mid). If the counter is
-- cumulative for the market's life, that is lifetime volume priced at
-- one arbitrary mid -- not a day, and not necessarily an upper bound on
-- a day either, because the price used to convert shares to dollars was
-- never the price the shares traded at.
--
-- This file establishes the denominator instead of assuming it:
--
--   1  how often each market is observed at all (can we difference?)
--   2  consecutive-pair deltas, with resets and non-monotone steps
--      counted rather than clipped
--   3  the gap distribution -- how much of the window is actually
--      covered by observation, since uncovered time carries unknown
--      volume in either direction
--   4  per-interval pricing: each delta priced at the mid prevailing
--      ACROSS that interval, so shares and dollars use one unit basis
--   5  a market listing export, to exercise paginated discovery and
--      the frozen universe rule against real rows
--   6  a bounded per-market time series, for hypothetical-quote
--      markout and opportunity persistence -- neither of which needs
--      an order of ours to measure
--
-- READ-ONLY. Every statement is a SELECT.

\echo
\echo ===== 0. WHICH TABLES EXIST (is there a trade-event table at all?)
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = 'public'
   AND (table_name LIKE '%trade%' OR table_name LIKE 'bettor%'
        OR table_name LIKE '%fill%' OR table_name LIKE '%print%')
 ORDER BY 1;

\echo
\echo ===== 1. OBSERVATION COVERAGE -- can the counter be differenced?
WITH per AS (
    SELECT market_id,
           count(*)          AS obs,
           min(observed_at)  AS first_at,
           max(observed_at)  AS last_at
      FROM bettor_state_observations
     WHERE stats_shares_traded ~ '^[0-9]+(\.[0-9]+)?$'
     GROUP BY market_id)
SELECT count(*)                                              AS markets_with_numeric_counter,
       sum(obs)                                              AS observations,
       min(obs)                                              AS min_obs_per_market,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY obs)      AS median_obs_per_market,
       max(obs)                                              AS max_obs_per_market,
       count(*) FILTER (WHERE obs = 1)                       AS markets_seen_once_only,
       min(first_at)                                         AS window_start,
       max(last_at)                                          AS window_end,
       round(extract(epoch FROM max(last_at) - min(first_at)) / 3600.0, 2)
                                                             AS window_hours
  FROM per;

\echo
\echo ===== 2. CONSECUTIVE-PAIR DELTAS, resets and non-monotone steps counted
WITH s AS (
    SELECT market_id, observed_at,
           stats_shares_traded::numeric AS v,
           CASE WHEN mid ~ '^[0-9]+(\.[0-9]+)?$' THEN mid::numeric END AS p
      FROM bettor_state_observations
     WHERE stats_shares_traded ~ '^[0-9]+(\.[0-9]+)?$'),
l AS (
    SELECT market_id, observed_at, v, p,
           lag(v)           OVER w AS pv,
           lag(observed_at) OVER w AS pt,
           lag(p)           OVER w AS pp
      FROM s
    WINDOW w AS (PARTITION BY market_id ORDER BY observed_at))
SELECT count(*) FILTER (WHERE pv IS NOT NULL)                  AS pairs,
       count(*) FILTER (WHERE pv IS NOT NULL AND v < pv)       AS pairs_counter_went_backwards,
       count(*) FILTER (WHERE pv IS NOT NULL AND v = pv)       AS pairs_unchanged,
       count(*) FILTER (WHERE pv IS NOT NULL AND v > pv)       AS pairs_increased,
       count(DISTINCT market_id) FILTER (WHERE pv IS NOT NULL AND v > pv)
                                                               AS markets_that_traded_between_observations,
       round(sum(v - pv) FILTER (WHERE pv IS NOT NULL AND v > pv), 4)
                                                               AS shares_measured_by_differencing,
       round(sum(extract(epoch FROM observed_at - pt))
             FILTER (WHERE pv IS NOT NULL), 1)                 AS covered_seconds_total,
       round(sum(v - pv) FILTER (WHERE pv IS NOT NULL AND v < pv), 4)
                                                               AS shares_lost_to_backwards_steps
  FROM l;

\echo
\echo ===== 3. GAP DISTRIBUTION -- how much of the window is uncovered
WITH s AS (
    SELECT market_id, observed_at
      FROM bettor_state_observations
     WHERE stats_shares_traded ~ '^[0-9]+(\.[0-9]+)?$'),
g AS (
    SELECT market_id,
           extract(epoch FROM observed_at
                   - lag(observed_at) OVER (PARTITION BY market_id
                                            ORDER BY observed_at)) AS gap_s
      FROM s)
SELECT count(*)                                                  AS gaps,
       round(min(gap_s)::numeric, 1)                             AS min_gap_s,
       round(percentile_disc(0.10) WITHIN GROUP (ORDER BY gap_s)::numeric, 1) AS p10_gap_s,
       round(percentile_disc(0.50) WITHIN GROUP (ORDER BY gap_s)::numeric, 1) AS median_gap_s,
       round(percentile_disc(0.90) WITHIN GROUP (ORDER BY gap_s)::numeric, 1) AS p90_gap_s,
       round(max(gap_s)::numeric, 1)                             AS max_gap_s,
       round(sum(gap_s)::numeric, 1)                             AS summed_covered_s
  FROM g
 WHERE gap_s IS NOT NULL;

\echo
\echo ===== 4. NOTIONAL FROM DELTAS PRICED AT THE INTERVAL'S OWN MID
WITH s AS (
    SELECT market_id, observed_at,
           stats_shares_traded::numeric AS v,
           CASE WHEN mid ~ '^[0-9]+(\.[0-9]+)?$' THEN mid::numeric END AS p
      FROM bettor_state_observations
     WHERE stats_shares_traded ~ '^[0-9]+(\.[0-9]+)?$'),
l AS (
    SELECT market_id, observed_at, v, p,
           lag(v)           OVER w AS pv,
           lag(observed_at) OVER w AS pt,
           lag(p)           OVER w AS pp
      FROM s
    WINDOW w AS (PARTITION BY market_id ORDER BY observed_at)),
d AS (
    SELECT market_id,
           v - pv                                        AS dshares,
           extract(epoch FROM observed_at - pt)          AS dt_s,
           (COALESCE(p, pp) + COALESCE(pp, p)) / 2.0     AS interval_mid
      FROM l
     WHERE pv IS NOT NULL AND v > pv)
SELECT count(*)                                          AS priced_intervals,
       count(*) FILTER (WHERE interval_mid IS NULL)      AS intervals_without_any_mid,
       round(sum(dshares), 2)                            AS shares,
       round(sum(dshares * interval_mid)
             FILTER (WHERE interval_mid IS NOT NULL), 2) AS notional_usd,
       round((sum(dshares * interval_mid) FILTER (WHERE interval_mid IS NOT NULL))
             / NULLIF(sum(dshares) FILTER (WHERE interval_mid IS NOT NULL), 0), 4)
                                                         AS effective_price_usd,
       round(sum(dt_s)::numeric / 3600.0, 3)             AS measured_interval_hours
  FROM d;

\echo
\echo ===== 4b. THE SAME NUMBER THE OLD WAY, for the record
SELECT count(*) AS markets, round(sum(v), 2) AS total_shares,
       round(sum(v * p), 2) AS total_notional_usd
  FROM (SELECT market_id,
               max(stats_shares_traded::numeric) AS v,
               max(mid::numeric)                 AS p
          FROM bettor_state_observations
         WHERE stats_shares_traded ~ '^[0-9]+(\.[0-9]+)?$'
           AND mid ~ '^[0-9]+(\.[0-9]+)?$'
         GROUP BY market_id) t;

\echo
\echo ===== 5. MARKET LISTING EXPORT (latest row per market) as one JSON line
WITH latest AS (
    SELECT DISTINCT ON (market_id)
           market_id, yes_bid, yes_ask, stats_shares_traded,
           venue_state, observed_at
      FROM bettor_state_observations
     ORDER BY market_id, observed_at DESC)
SELECT jsonb_agg(jsonb_build_object(
           'slug', market_id, 'bestBid', yes_bid, 'bestAsk', yes_ask,
           'sharesTraded', stats_shares_traded, 'state', venue_state,
           'observedAt', observed_at))::text AS listing_json
  FROM latest;

\echo
\echo ===== 6. TIME SERIES for markout and persistence (top 120 by observations)
WITH per AS (
    SELECT market_id, count(*) AS obs
      FROM bettor_state_observations
     WHERE yes_bid ~ '^[0-9]+(\.[0-9]+)?$' AND yes_ask ~ '^[0-9]+(\.[0-9]+)?$'
     GROUP BY market_id
     ORDER BY count(*) DESC
     LIMIT 120),
ser AS (
    SELECT o.market_id, o.observed_at, o.yes_bid, o.yes_ask, o.mid,
           o.stats_shares_traded, o.venue_state
      FROM bettor_state_observations o
      JOIN per USING (market_id)
     WHERE o.yes_bid ~ '^[0-9]+(\.[0-9]+)?$' AND o.yes_ask ~ '^[0-9]+(\.[0-9]+)?$'
     ORDER BY o.market_id, o.observed_at)
SELECT jsonb_agg(jsonb_build_object(
           'slug', market_id, 'at', observed_at, 'bid', yes_bid,
           'ask', yes_ask, 'mid', mid, 'vol', stats_shares_traded,
           'state', venue_state))::text AS series_json
  FROM ser;
