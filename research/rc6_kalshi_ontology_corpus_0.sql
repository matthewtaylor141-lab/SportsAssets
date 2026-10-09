-- RC6 lane K (Kalshi ontology), read only. Partition 0 of 4 (by
-- hashtext(contract_id)) of the ACTIVE Kalshi registry rows with the
-- venue's own series ticker, event ticker, series tags and rules_primary,
-- packed 36 rows per output line as JSON, so the ontology mapper can be run
-- over the real catalogue for the before / after counts by family. No write.
\echo === corpus partition 0 of 4: [contract_id, series, event, tags, rules_primary] x 36 per line ===
WITH a AS (
  SELECT r.contract_id, r.competition, r.event_id,
         r.ontology -> 'series' -> 'tags' AS tags, m.rules_text
    FROM market_plane_registry r
    LEFT JOIN market_plane_rules m ON m.contract_id = r.contract_id
   WHERE r.active AND r.venue = 'KALSHI'
     AND mod(abs(hashtext(r.contract_id)), 4) = 0),
b AS (SELECT a.*, (row_number() OVER (ORDER BY contract_id) - 1) / 36 AS grp FROM a)
SELECT json_agg(json_build_array(contract_id, competition, event_id, tags, rules_text)
                ORDER BY contract_id)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
\echo === series: [series, title, tags, active rows, one rules_secondary] x 10 per line ===
WITH s AS (
  SELECT r.competition, max(r.ontology -> 'series' ->> 'title') AS title,
         max((r.ontology -> 'series' -> 'tags')::text) AS tags, count(*) AS n,
         max(m.rules_secondary) AS rs
    FROM market_plane_registry r
    LEFT JOIN market_plane_rules m ON m.contract_id = r.contract_id
   WHERE r.active AND r.venue = 'KALSHI' GROUP BY 1),
b AS (SELECT s.*, (row_number() OVER (ORDER BY competition) - 1) / 10 AS grp FROM s)
SELECT json_agg(json_build_array(competition, title, tags, n, left(rs, 600)) ORDER BY competition)::text AS j
  FROM b GROUP BY grp ORDER BY grp;
