-- THE CEILING ON MAKER ECONOMICS, COMPUTED WITHOUT A FILL MODEL.
--
-- A passive maker's GROSS edge is the spread it captures. That is an
-- upper bound and it does not need P_FILL at all: whatever the fill
-- rate turns out to be, the gross edge per filled contract cannot
-- exceed the spread that was displayed. So if the displayed spread
-- cannot cover fees plus adverse selection, the strategy is falsified
-- before any fill model is built.
--
-- WHAT THIS IS NOT. It is not an estimate of what BETTOR would earn.
-- It is the CEILING. Realised edge is the ceiling times a fill rate
-- below 1, minus adverse selection, minus fees, minus the residual
-- cost of legs that never pair. Every one of those reduces it.
--
-- BINARY BOOK ARITHMETIC. On a two-sided binary market the complement
-- ask is 1 - bid. So a TAKER pair costs ask + (1 - bid) = 1 + spread:
-- crossing both legs pays the spread and can never be an arbitrage.
-- A MAKER pair resting both legs collects the spread instead. The
-- distribution below is therefore both numbers at once.
--
-- COHORTS ARE T0-KNOWABLE ONLY: sport, league, market type, price band,
-- time of day. No outcome, no future price, no hindsight.
--
-- READ-ONLY.

-- ── 1. THE HEADLINE SPREAD DISTRIBUTION ──────────────────────────────
SELECT 'SPREAD_ALL' AS section,
       count(*)                                          AS observations,
       round(avg(s.ask - s.bid)::numeric, 5)             AS mean_spread,
       round(percentile_cont(0.10) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS p10,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS median,
       round(percentile_cont(0.90) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS p90,
       min(s.ask - s.bid)                                AS min_spread,
       max(s.ask - s.bid)                                AS max_spread,
       count(*) FILTER (WHERE s.ask - s.bid <= 0.01)     AS at_one_cent,
       count(*) FILTER (WHERE s.ask - s.bid <= 0.02)     AS at_or_under_two,
       count(*) FILTER (WHERE s.ask - s.bid >= 0.05)     AS five_cents_plus
  FROM bettor_opportunities o
  JOIN shadow_market_states s ON s.market_state_id = o.market_state_id
 WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL;

-- ── 2. BY SPORT: is any sport's book materially wider? ───────────────
-- Joined through us_premap because the sport mapping is not yet
-- backfilled onto the existing observation rows.
SELECT 'SPREAD_BY_SPORT' AS section,
       split_part(p.sports_type, '_', 1)                 AS sport_token,
       count(DISTINCT o.bettor_opportunity_id)           AS observations,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS median_spread,
       round(percentile_cont(0.90) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS p90_spread,
       round(avg((s.bid + s.ask) / 2)::numeric, 4)       AS mean_mid
  FROM bettor_opportunities o
  JOIN shadow_market_states s ON s.market_state_id = o.market_state_id
  LEFT JOIN us_premap p ON p.market_slug = o.symbol
 WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL
 GROUP BY 1, 2
HAVING count(DISTINCT o.bettor_opportunity_id) >= 30
 ORDER BY observations DESC
 LIMIT 20;

-- ── 3. BY PRICE BAND: spread as a FRACTION of the price ─────────────
-- A 2c spread on a 0.05 contract is 40% of it; the same 2c on 0.50 is
-- 4%. Relative spread is what a maker actually captures per dollar of
-- capital, so the bands are the cohort that matters most.
SELECT 'SPREAD_BY_PRICE_BAND' AS section,
       t.band,
       count(*)                                          AS observations,
       round(min(t.mid)::numeric, 3)                     AS band_lo_mid,
       round(max(t.mid)::numeric, 3)                     AS band_hi_mid,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY t.spread)::numeric, 4)             AS median_spread,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY t.spread / nullif(t.mid, 0))::numeric, 4)
                                                         AS median_rel_spread
  FROM (SELECT (s.bid + s.ask) / 2                       AS mid,
               s.ask - s.bid                             AS spread,
               width_bucket((s.bid + s.ask) / 2, 0, 1, 10) AS band
          FROM bettor_opportunities o
          JOIN shadow_market_states s
            ON s.market_state_id = o.market_state_id
         WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL) t
 GROUP BY t.band
 ORDER BY t.band;

-- ── 4. THE TAKER PAIR, PRICED DIRECTLY ───────────────────────────────
-- ask + (1 - bid) = 1 + spread. Anything at or below 1.00 would be a
-- structural arbitrage. Counted rather than asserted.
SELECT 'TAKER_PAIR_BASIS' AS section,
       count(*)                                          AS observations,
       count(*) FILTER (WHERE (s.ask + (1 - s.bid)) < 1.0)
                                                         AS below_par,
       count(*) FILTER (WHERE (s.ask + (1 - s.bid)) = 1.0)
                                                         AS at_par,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY (s.ask + (1 - s.bid)))::numeric, 4) AS median_basis,
       round(min(s.ask + (1 - s.bid))::numeric, 4)        AS min_basis
  FROM bettor_opportunities o
  JOIN shadow_market_states s ON s.market_state_id = o.market_state_id
 WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL;

-- ── 5. TIME OF DAY, a T0-knowable cohort ─────────────────────────────
SELECT 'SPREAD_BY_HOUR_UTC' AS section,
       EXTRACT(HOUR FROM o.observed_at)::int             AS hour_utc,
       count(*)                                          AS observations,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS median_spread
  FROM bettor_opportunities o
  JOIN shadow_market_states s ON s.market_state_id = o.market_state_id
 WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL
 GROUP BY 1, 2
 ORDER BY hour_utc;

-- ── 6. THE BIMODALITY BAND 5 EXPOSED ─────────────────────────────────
-- Band 5 (mid 0.40-0.50) reported a MEDIAN spread of 0.94. That is not
-- a wide market, it is an EMPTY one: a book quoted 0.03 / 0.97 has a
-- mid near 0.50 and no liquidity at all. Averaging those together with
-- genuine 1c markets produces a number that describes neither.
--
-- So the population is cut at a spread a maker could actually work.
SELECT 'SPREAD_BIMODALITY' AS section,
       CASE WHEN s.ask - s.bid <= 0.05 THEN 'A_TIGHT_LE_5C'
            WHEN s.ask - s.bid <= 0.20 THEN 'B_WIDE_5_TO_20C'
            ELSE 'C_EMPTY_GT_20C' END                   AS regime,
       count(*)                                          AS observations,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS median_spread,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY (s.bid + s.ask) / 2)::numeric, 4)  AS median_mid,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY (s.ask - s.bid)
                      / nullif((s.bid + s.ask) / 2, 0))::numeric, 4)
                                                         AS median_rel_spread
  FROM bettor_opportunities o
  JOIN shadow_market_states s ON s.market_state_id = o.market_state_id
 WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL
 GROUP BY 1, 2
 ORDER BY regime;

-- ── 7. THE TRADEABLE COHORT, PRICED ──────────────────────────────────
-- Only books a maker could work: spread <= 5c and a mid away from the
-- extremes. HALF the spread is what one resting leg captures if it
-- fills; the whole spread is what a both-legs-filled pair captures.
SELECT 'TRADEABLE_COHORT' AS section,
       count(*)                                          AS observations,
       count(DISTINCT o.symbol)                          AS distinct_markets,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS median_spread,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY (s.ask - s.bid) / 2)::numeric, 4)  AS median_half_spread,
       round(percentile_cont(0.25) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS p25_spread,
       round(percentile_cont(0.75) WITHIN GROUP (
             ORDER BY s.ask - s.bid)::numeric, 4)        AS p75_spread,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY (s.bid + s.ask) / 2)::numeric, 4)  AS median_mid
  FROM bettor_opportunities o
  JOIN shadow_market_states s ON s.market_state_id = o.market_state_id
 WHERE s.bid IS NOT NULL AND s.ask IS NOT NULL
   AND s.ask - s.bid <= 0.05
   AND (s.bid + s.ask) / 2 BETWEEN 0.05 AND 0.95;
