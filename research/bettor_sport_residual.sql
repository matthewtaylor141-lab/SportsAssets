-- THE ROWS THE MARKET-TYPE SOURCE CANNOT ANSWER.
--
-- bettor_sport_mapping resolves SPORT from the sportsMarketType prefix
-- wherever one of the declared prefixes matches. That is the large
-- majority, so listing every pair to count them would return 370 rows
-- to learn one number.
--
-- This returns only the RESIDUAL: observations whose sports_type
-- matches none of the declared sport prefixes. Those are the only rows
-- where the league fallback, the non-sport category or NOT_IDENTIFIED
-- can apply, and there are few enough to enumerate completely.
--
-- THE PREFIX LIST BELOW IS A FILTER, NOT A SECOND MAPPING. It selects
-- which rows to look at; it assigns no sport to anything. It is still
-- a duplicate of a list that lives in Python, so
-- test_bettor_sport_mapping pins the two together and fails if they
-- drift.
--
-- READ-ONLY.

SELECT 'RESIDUAL_PAIRS' AS section,
       coalesce(p.sports_type, '(null)')       AS sports_type,
       coalesce(p.team_league, '(no league)')  AS team_league,
       count(DISTINCT o.bettor_opportunity_id) AS observations
  FROM bettor_opportunities o
  LEFT JOIN us_premap p ON p.market_slug = o.symbol
 WHERE p.sports_type IS NULL
    OR NOT (p.sports_type LIKE 'table\_tennis\_%'
         OR p.sports_type LIKE 'basketball\_%'
         OR p.sports_type LIKE 'baseball\_%'
         OR p.sports_type LIKE 'cricket\_%'
         OR p.sports_type LIKE 'esports\_%'
         OR p.sports_type LIKE 'football\_%'
         OR p.sports_type LIKE 'hockey\_%'
         OR p.sports_type LIKE 'soccer\_%'
         OR p.sports_type LIKE 'tennis\_%'
         OR p.sports_type LIKE 'boxing\_%'
         OR p.sports_type LIKE 'darts\_%'
         OR p.sports_type LIKE 'ufc\_%')
 GROUP BY 1, 2, 3
 ORDER BY observations DESC
 LIMIT 200;

-- The headline split, so the rate has a denominator that does not
-- depend on the LIMIT above.
SELECT 'SPORT_SPLIT' AS section,
       count(DISTINCT o.bettor_opportunity_id)              AS observations,
       count(DISTINCT o.bettor_opportunity_id) FILTER (
         WHERE p.sports_type LIKE 'table\_tennis\_%'
            OR p.sports_type LIKE 'basketball\_%'
            OR p.sports_type LIKE 'baseball\_%'
            OR p.sports_type LIKE 'cricket\_%'
            OR p.sports_type LIKE 'esports\_%'
            OR p.sports_type LIKE 'football\_%'
            OR p.sports_type LIKE 'hockey\_%'
            OR p.sports_type LIKE 'soccer\_%'
            OR p.sports_type LIKE 'tennis\_%'
            OR p.sports_type LIKE 'boxing\_%'
            OR p.sports_type LIKE 'darts\_%'
            OR p.sports_type LIKE 'ufc\_%')
           AS sport_from_market_type,
       count(DISTINCT o.bettor_opportunity_id) FILTER (
         WHERE p.sports_type = 'futures')                   AS futures_rows,
       count(DISTINCT o.bettor_opportunity_id) FILTER (
         WHERE p.sports_type = 'moneyline')                 AS moneyline_rows,
       count(DISTINCT o.bettor_opportunity_id) FILTER (
         WHERE p.sports_type LIKE 'election\_%')            AS election_rows,
       count(DISTINCT o.bettor_opportunity_id) FILTER (
         WHERE p.team_league IS NOT NULL)                   AS league_present
  FROM bettor_opportunities o
  LEFT JOIN us_premap p ON p.market_slug = o.symbol;
