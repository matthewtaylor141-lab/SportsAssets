-- READ-ONLY. KALSHI CANONICAL VENUE V1 -- what the Kalshi sports catalogue has
-- put in the market-plane registry: series prefix x active, with a sample
-- ticker and the newest catalogue time. SELECT only.
SELECT split_part(substr(contract_id, 8), '-', 1) AS series,
       count(*) FILTER (WHERE active) AS active, count(*) AS total,
       min(contract_id) AS sample, max(updated_at) AS newest,
       count(*) FILTER (WHERE refdata IS NOT NULL) AS with_refdata
  FROM market_plane_registry
 WHERE venue = 'KALSHI'
 GROUP BY 1 ORDER BY 2 DESC, 3 DESC LIMIT 200;
SELECT kind, count(*), max(at) FROM market_plane_events
 WHERE kind ILIKE '%KALSHI%' GROUP BY 1;
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_name = 'market_plane_rules' ORDER BY ordinal_position;
SELECT venue, count(*) FROM market_plane_rules GROUP BY 1;
