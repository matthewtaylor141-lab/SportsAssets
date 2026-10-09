-- RC6 lane D2 (coverage waterfall), read only. The vocabulary the waterfall
-- classifier needs, from the plane's own registry: every PMUS market type
-- with its ontology family / period and coverage state, the rows whose sport
-- or metric is not normalized (with sample event ids), the Kalshi series,
-- and the decision valuations of the last 24 h by family (the only fair-value
-- source the coverage matrix accepts).
\echo === A. active registry by venue ===
SELECT venue, count(*) AS active,
       count(*) FILTER (WHERE coverage_state = 'PRICEABLE') AS priceable
  FROM market_plane_registry WHERE active GROUP BY 1 ORDER BY 1;
\echo === B. POLYMARKET_US active by market_type x family x period (top 160) ===
SELECT coalesce(market_type, '<null>') AS market_type,
       coalesce(family, '?') AS family, coalesce(period, '?') AS period,
       count(*) AS n,
       count(*) FILTER (WHERE coverage_state = 'PRICEABLE') AS priceable,
       count(*) FILTER (WHERE settlement_state IN (
           'SETTLEMENT_PROVEN_COMPATIBLE',
           'SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED')) AS settle_proven,
       count(DISTINCT sport) AS sports
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 160;
\echo === B2. distinct market_type count and rows outside the top 160 ===
SELECT count(DISTINCT coalesce(market_type, '<null>')) AS market_types,
       count(*) AS rows_total
  FROM market_plane_registry WHERE active AND venue = 'POLYMARKET_US';
\echo === C. POLYMARKET_US rows not mapped: market_type x competition with sample event ids (top 30) ===
SELECT coalesce(market_type, '<null>') AS market_type,
       coalesce(competition, '<null>') AS competition,
       count(*) AS n, min(event_id) AS sample_a, max(event_id) AS sample_b
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
   AND coverage_state = 'CODE_CONTROLLED_GAP'
   AND coverage_why LIKE 'ONTOLOGY_GAPS%'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;
\echo === D. KALSHI active by series ticker (top 120) ===
SELECT coalesce(competition, '<null>') AS series, count(*) AS n
  FROM market_plane_registry WHERE active AND venue = 'KALSHI'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 120;
\echo === D2. KALSHI series count ===
SELECT count(DISTINCT competition) AS series, count(*) AS markets
  FROM market_plane_registry WHERE active AND venue = 'KALSHI';
\echo === E. decision valuations, last 24 h, by sport_family x market x period x purpose ===
SELECT sport_family, market, coalesce(period, '?') AS period,
       coalesce(record_purpose, '?') AS purpose,
       count(*) AS rows_n,
       count(DISTINCT us_market_slug) AS slugs,
       count(DISTINCT us_market_slug) FILTER (WHERE probability IS NOT NULL)
           AS slugs_with_p
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 60;
\echo === F. POLYMARKET_US full-game line types: coverage_state x settlement_why (truncated) ===
SELECT market_type, coverage_state, left(coalesce(settlement_why, '-'), 100) AS settlement_why,
       count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
   AND market_type IN (
       'football_team_full_game_spread', 'football_team_full_game_total',
       'football_team_points_full_game_total', 'hockey_team_full_game_spread',
       'hockey_team_full_game_total', 'hockey_team_total_goals',
       'basketball_team_full_game_spread', 'basketball_team_full_game_total',
       'baseball_team_full_game_spread', 'baseball_team_full_game_total',
       'baseball_team_total_runs', 'soccer_team_full_game_spread',
       'soccer_team_full_game_total', 'tennis_match_games_spread',
       'tennis_match_total_games')
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 80;
\echo === G. rules rows for active POLYMARKET_US contracts: parse_status x published ===
SELECT r.parse_status, r.rules_published, count(*) AS n
  FROM market_plane_registry g JOIN market_plane_rules r USING (contract_id)
 WHERE g.active AND g.venue = 'POLYMARKET_US'
 GROUP BY 1, 2 ORDER BY 3 DESC;
\echo === G2. active POLYMARKET_US contracts with no rules row ===
SELECT count(*) AS no_rules_row
  FROM market_plane_registry g
 WHERE g.active AND g.venue = 'POLYMARKET_US'
   AND NOT EXISTS (SELECT 1 FROM market_plane_rules r
                    WHERE r.contract_id = g.contract_id);
