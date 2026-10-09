-- RC6 lane K (Kalshi ontology), read only. The long tail: active Kalshi
-- series ranked 161-700 by rows, each with one rules_primary sample, and the
-- leading words of every rules_primary (the contract-term templates). No write.
\echo === K5 rules_primary leading pattern (first 3 words, digits normalised) ===
SELECT regexp_replace(substring(coalesce(m.rules_text, '<NONE>') FROM '^(\S+\s+\S+\s+\S+)'), '[0-9]+', '#', 'g') AS lead3,
       count(*) AS n, count(DISTINCT r.competition) AS series
  FROM market_plane_registry r LEFT JOIN market_plane_rules m ON m.contract_id = r.contract_id
 WHERE r.active AND r.venue = 'KALSHI' GROUP BY 1 ORDER BY 2 DESC LIMIT 60;
\echo === K6 series ranked 161-700: one rules_primary sample each ===
WITH a AS (
  SELECT r.competition, m.rules_text
    FROM market_plane_registry r
    LEFT JOIN market_plane_rules m ON m.contract_id = r.contract_id
   WHERE r.active AND r.venue = 'KALSHI'),
s AS (
  SELECT competition, count(*) AS n FROM a GROUP BY 1 ORDER BY 2 DESC, 1 OFFSET 160 LIMIT 540),
x AS (
  SELECT DISTINCT ON (a.competition) a.competition, left(a.rules_text, 200) AS rp
    FROM a JOIN s ON s.competition = a.competition ORDER BY a.competition, a.rules_text)
SELECT s.competition, s.n, x.rp FROM s JOIN x ON x.competition = s.competition ORDER BY s.n DESC, 1;
