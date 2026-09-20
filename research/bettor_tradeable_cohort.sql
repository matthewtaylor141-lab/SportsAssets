-- The tradeable-cohort cuts, split out so the workflow head does not
-- truncate them. Same statements as bettor_spread_economics.sql §6-7.
-- READ-ONLY.

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
