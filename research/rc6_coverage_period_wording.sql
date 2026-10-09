-- RC6 lane D2, read only. The second venue wording of football PERIOD game
-- totals (the after-projection run 37872560158 found 319 half / 413 quarter
-- totals whose text does not carry the "point total settles over" wording):
-- two full sample rows per market type, with their registry identity.
\echo === W1. football period game totals NOT in the "point total settles over" wording: count and two samples per type ===
SELECT g.market_type, count(*) AS n,
       min(g.contract_id) AS sample_a, max(g.contract_id) AS sample_b
  FROM market_plane_registry g JOIN market_plane_rules r USING (contract_id)
 WHERE g.active AND g.venue = 'POLYMARKET_US'
   AND g.market_type IN ('football_game_first_half_total',
                         'football_game_second_half_total',
                         'football_game_first_quarter_total',
                         'football_game_second_quarter_total',
                         'football_game_third_quarter_total',
                         'football_game_fourth_quarter_total')
   AND lower(r.rules_text) !~ 'point total settles over'
 GROUP BY 1 ORDER BY 1;
\echo === W2. the full rows of those samples ===
WITH s AS (
  SELECT g.market_type, min(g.contract_id) AS a, max(g.contract_id) AS b
    FROM market_plane_registry g JOIN market_plane_rules r USING (contract_id)
   WHERE g.active AND g.venue = 'POLYMARKET_US'
     AND g.market_type IN ('football_game_first_half_total',
                           'football_game_second_half_total',
                           'football_game_first_quarter_total',
                           'football_game_second_quarter_total',
                           'football_game_third_quarter_total',
                           'football_game_fourth_quarter_total')
     AND lower(r.rules_text) !~ 'point total settles over'
   GROUP BY 1)
SELECT g.contract_id, g.market_type, g.event_id, g.sport, g.family, g.period,
       regexp_replace(r.rules_text, '\s+', ' ', 'g') AS rules_text
  FROM market_plane_registry g JOIN market_plane_rules r USING (contract_id)
 WHERE g.contract_id IN (SELECT a FROM s UNION SELECT b FROM s)
 ORDER BY 2, 1;
