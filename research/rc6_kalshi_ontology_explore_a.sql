-- RC6 lane K (Kalshi ontology), read only. What the active Kalshi registry
-- rows are, by series, with the venue's own series title / tags and one
-- sample of each series' rules_primary / rules_secondary (the contract
-- terms the ontology mapper is built from). No write.
\echo === K1 active Kalshi registry rows by coverage_state x coverage_why ===
SELECT coverage_state, coverage_why, count(*) AS n
  FROM market_plane_registry WHERE active AND venue = 'KALSHI'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10;
\echo === K2 Kalshi rows (all / active) and rules rows ===
SELECT (SELECT count(*) FROM market_plane_registry WHERE venue = 'KALSHI') AS registry_all,
       (SELECT count(*) FROM market_plane_registry WHERE venue = 'KALSHI' AND active) AS registry_active,
       (SELECT count(DISTINCT competition) FROM market_plane_registry WHERE venue = 'KALSHI' AND active) AS series_active,
       (SELECT count(*) FROM market_plane_rules WHERE venue = 'KALSHI') AS rules_rows,
       (SELECT max(last_seen_at) FROM market_plane_registry WHERE venue = 'KALSHI') AS newest_seen,
       (SELECT min(last_seen_at) FROM market_plane_registry WHERE venue = 'KALSHI' AND active) AS oldest_active_seen;
\echo === K3 series tags census (active Kalshi rows) ===
SELECT (ontology -> 'series' -> 'tags')::text AS tags, count(*) AS n,
       count(DISTINCT competition) AS series
  FROM market_plane_registry WHERE active AND venue = 'KALSHI'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 50;
\echo === K4 top 160 series by active rows: title, market_type, distinct digit-normalised rules templates, one sample ===
WITH a AS (
  SELECT r.competition, r.market_type, r.ontology -> 'series' ->> 'title' AS stitle,
         m.rules_text, m.rules_secondary
    FROM market_plane_registry r
    LEFT JOIN market_plane_rules m ON m.contract_id = r.contract_id
   WHERE r.active AND r.venue = 'KALSHI'),
s AS (
  SELECT competition, count(*) AS n, max(stitle) AS stitle, max(market_type) AS mt,
         count(DISTINCT regexp_replace(coalesce(rules_text, ''), '[0-9]+', '#', 'g')) AS templates
    FROM a GROUP BY 1 ORDER BY 2 DESC LIMIT 160),
x AS (
  SELECT DISTINCT ON (a.competition) a.competition, left(a.rules_text, 300) AS rp,
         left(a.rules_secondary, 160) AS rs
    FROM a JOIN s ON s.competition = a.competition
   ORDER BY a.competition, a.rules_text)
SELECT s.competition, s.n, s.mt, s.templates, left(s.stitle, 50) AS series_title, x.rp, x.rs
  FROM s JOIN x ON x.competition = s.competition ORDER BY s.n DESC;
