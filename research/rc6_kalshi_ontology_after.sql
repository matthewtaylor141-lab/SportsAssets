-- RC6 lane K (Kalshi ontology), read only. The ACTIVE Kalshi registry rows by
-- coverage state x ontology family x period class x sport, the named gap
-- reasons and the settlement whys of the mapped rows. Run before the RC6
-- deploy (every row ONTOLOGY_GAPS:KALSHI_ONTOLOGY_NOT_MAPPED) and after it
-- (kalshi_ontology: mapped, or refused by a named code). No write.
\echo === A1 active Kalshi rows by coverage_state ===
SELECT coverage_state, count(*) AS n FROM market_plane_registry
 WHERE active AND venue = 'KALSHI' GROUP BY 1 ORDER BY 2 DESC;
\echo === A2 by coverage_state x family x period class x sport ===
SELECT coverage_state, coalesce(family, '-') AS family,
       CASE WHEN period IS NULL THEN '-' WHEN period = 'FULL_EVENT' THEN 'FULL_EVENT'
            ELSE 'PERIOD' END AS period_class,
       coalesce(sport, '-') AS sport, count(*) AS n
  FROM market_plane_registry WHERE active AND venue = 'KALSHI'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 80;
\echo === A3 CODE_CONTROLLED_GAP reasons, by name ===
SELECT coverage_why, count(*) AS n FROM market_plane_registry
 WHERE active AND venue = 'KALSHI' AND coverage_state = 'CODE_CONTROLLED_GAP'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;
\echo === A4 mapped rows: settlement state x why (top) ===
SELECT settlement_state, settlement_basis, left(settlement_why, 150) AS why, count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'KALSHI' AND coverage_state <> 'CODE_CONTROLLED_GAP'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;
\echo === A5 the ontology verdict recorded on each row (status x family class x version) ===
SELECT ontology -> 'kalshi' ->> 'status' AS status,
       ontology -> 'kalshi' ->> 'family_class' AS family_class,
       ontology -> 'kalshi' ->> 'version' AS version, count(*) AS n
  FROM market_plane_registry WHERE active AND venue = 'KALSHI'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;
\echo === A6 freshness of the rows (the walk that wrote them) ===
SELECT min(updated_at) AS oldest_write, max(updated_at) AS newest_write,
       min(coverage_at) AS oldest_coverage, max(coverage_at) AS newest_coverage,
       count(*) AS n
  FROM market_plane_registry WHERE active AND venue = 'KALSHI';
