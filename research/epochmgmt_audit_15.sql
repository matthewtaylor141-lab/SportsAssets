-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 15:
-- does the Kalshi series record the registry already holds name a settlement source for the game series.
\echo == A keys of the series object held in the active Kalshi registry ontology (distinct keys with counts)
SELECT k AS series_key, count(*) AS rows_n
  FROM (SELECT jsonb_object_keys(ontology -> 'series') AS k
          FROM market_plane_registry
         WHERE active AND venue = 'KALSHI' AND jsonb_typeof(ontology -> 'series') = 'object' LIMIT 20000) t
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;

\echo == B settlement-source-like content of the series object for the game series (distinct values)
SELECT competition, left((ontology -> 'series' -> 'settlement_sources')::text, 300) AS settlement_sources, count(*) AS rows_n
  FROM market_plane_registry
 WHERE active AND venue = 'KALSHI'
   AND competition IN ('KXMLSGAME', 'KXMLBGAME', 'KXNHLGAME', 'KXNBAGAME', 'KXEPLGAME', 'KXNFLGAME', 'KXNCAAFGAME')
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 30;

\echo == C how many active Kalshi rows carry a non-empty settlement_sources in the series object
SELECT count(*) AS active_kalshi_rows,
       count(*) FILTER (WHERE jsonb_typeof(ontology -> 'series' -> 'settlement_sources') = 'array'
                          AND jsonb_array_length(ontology -> 'series' -> 'settlement_sources') > 0) AS with_settlement_sources
  FROM market_plane_registry WHERE active AND venue = 'KALSHI';
