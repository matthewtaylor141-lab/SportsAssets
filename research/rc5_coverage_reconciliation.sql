-- RC5 coverage denominator reconciliation (read only). The plane's coverage
-- states over the active registry, by venue x sport x family x period, the
-- full-game winner (moneyline) universe the frozen strategies price, and a
-- sample of the SPORT_NOT_NORMALIZED and Kalshi rows to judge what is
-- addressable.
\echo === active registry by venue x coverage_state ===
SELECT venue, coverage_state, count(*) AS n FROM market_plane_registry
 WHERE active GROUP BY 1,2 ORDER BY 1,3 DESC;
\echo === POLYMARKET_US full-event winners (moneyline family) by sport x state, start within 48h vs later ===
SELECT sport, coverage_state,
       count(*) FILTER (WHERE event_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours') AS within_48h,
       count(*) AS all_active
  FROM market_plane_registry
 WHERE active AND venue='POLYMARKET_US' AND upper(coalesce(family,'')) IN ('WINNER','MONEYLINE','MONEYLINE_GAME','H2H')
   AND upper(coalesce(period,'FULL_EVENT')) IN ('FULL_EVENT','FULL_GAME','GAME','')
 GROUP BY 1,2 ORDER BY 1,4 DESC;
\echo === family x period census on POLYMARKET_US (active) ===
SELECT upper(coalesce(family,'?')) AS family, upper(coalesce(period,'?')) AS period, coverage_state, count(*) AS n
  FROM market_plane_registry WHERE active AND venue='POLYMARKET_US'
 GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 60;
\echo === the moneyline coverage_why for non-priceable within 48h ===
SELECT coverage_why, count(*) AS n FROM market_plane_registry
 WHERE active AND venue='POLYMARKET_US' AND upper(coalesce(family,'')) IN ('WINNER','MONEYLINE','MONEYLINE_GAME','H2H')
   AND upper(coalesce(period,'FULL_EVENT')) IN ('FULL_EVENT','FULL_GAME','GAME','')
   AND event_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
   AND coverage_state <> 'PRICEABLE'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;
\echo === SPORT_NOT_NORMALIZED sample (market_type, competition) ===
SELECT market_type, competition, count(*) AS n FROM market_plane_registry
 WHERE active AND coverage_why LIKE '%SPORT_NOT_NORMALIZED%'
 GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30;
\echo === Kalshi rows by sport / market_type (the ontology gap) ===
SELECT sport, market_type, count(*) AS n FROM market_plane_registry
 WHERE active AND venue='KALSHI' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30;
