-- RC6 lane D2, read only. Full-game money lines (the core of the target
-- universe) per venue league -- the event slug's FIRST segment, the venue's
-- own league code (the registry's `competition` carried a team code until
-- the lane's slug-grammar fix) -- within 48 h and in all, by the stage at
-- which each stopped; then, for the never-valued ones, the collector's
-- latest first refusal in 24 h.
\echo === M1. full-game money lines per league: stage of loss (top 50 by n) ===
WITH t AS (
  SELECT g.contract_id, coalesce(g.sport, 'UNKNOWN') AS sport,
         split_part(g.event_id, '-', 1) AS league, g.coverage_state,
         coalesce(g.coverage_why, '') AS why,
         coalesce(g.event_start BETWEEN now() - interval '6 hours'
                                    AND now() + interval '48 hours', false) AS in48,
         EXISTS (SELECT 1 FROM external_valuations v
                  WHERE v.us_market_slug = g.contract_id
                    AND v.decided_at > now() - interval '24 hours'
                    AND v.probability IS NOT NULL) AS valued
    FROM market_plane_registry g
   WHERE g.active AND g.venue = 'POLYMARKET_US'
     AND g.market_type ~ '(_full_game_winner|_full_time_winner|_match_winner|_fight_winner)$')
SELECT sport, league, count(*) AS n, count(*) FILTER (WHERE in48) AS in48,
       count(*) FILTER (WHERE valued) AS valued,
       count(*) FILTER (WHERE coverage_state = 'PRICEABLE') AS priceable,
       count(*) FILTER (WHERE in48 AND coverage_state = 'PRICEABLE') AS in48_priceable,
       count(*) FILTER (WHERE coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN') AS l_settle,
       count(*) FILTER (WHERE coverage_state = 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE') AS l_fv,
       count(*) FILTER (WHERE coverage_state = 'CODE_CONTROLLED_GAP'
                          AND why LIKE 'NO_CURRENT_CANONICAL_BOOK%') AS l_fresh,
       count(*) FILTER (WHERE coverage_state = 'CODE_CONTROLLED_GAP'
                          AND why NOT LIKE 'NO_CURRENT_CANONICAL_BOOK%') AS l_map,
       count(*) FILTER (WHERE coverage_state IS NULL) AS unclass
  FROM t GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 50;
\echo === M2. major leagues (nfl cfb nba wnba nhl mlb) within 48 h: settlement reason x valued ===
SELECT split_part(g.event_id, '-', 1) AS league, g.coverage_state,
       left(coalesce(g.coverage_why, '-'), 100) AS why,
       EXISTS (SELECT 1 FROM external_valuations v
                WHERE v.us_market_slug = g.contract_id
                  AND v.decided_at > now() - interval '24 hours'
                  AND v.probability IS NOT NULL) AS valued,
       count(*) AS n
  FROM market_plane_registry g
 WHERE g.active AND g.venue = 'POLYMARKET_US'
   AND g.market_type ~ '_full_game_winner$'
   AND split_part(g.event_id, '-', 1) IN ('nfl', 'cfb', 'nba', 'wnba', 'nhl', 'mlb')
   AND g.event_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC;
\echo === M3. major leagues within 48 h never valued in 24 h: hours to start and the latest collector first refusal ===
WITH nv AS (
  SELECT g.contract_id, split_part(g.event_id, '-', 1) AS league,
         extract(epoch FROM (g.event_start - now())) / 3600.0 AS h_to_start
    FROM market_plane_registry g
   WHERE g.active AND g.venue = 'POLYMARKET_US'
     AND g.market_type ~ '_full_game_winner$'
     AND split_part(g.event_id, '-', 1) IN ('nfl', 'cfb', 'nba', 'wnba', 'nhl', 'mlb')
     AND g.event_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
     AND NOT EXISTS (SELECT 1 FROM external_valuations v
                      WHERE v.us_market_slug = g.contract_id
                        AND v.decided_at > now() - interval '24 hours'
                        AND v.probability IS NOT NULL)),
lc AS (SELECT DISTINCT ON (o.us_market_slug) o.us_market_slug, o.first_refusal, o.stage
         FROM ext_candidate_outcomes o
        WHERE o.cycle_at > now() - interval '24 hours'
          AND o.us_market_slug IN (SELECT contract_id FROM nv)
        ORDER BY o.us_market_slug, o.cycle_at DESC)
SELECT nv.league,
       CASE WHEN nv.h_to_start < 0 THEN 'started'
            WHEN nv.h_to_start < 12 THEN '0-12h'
            WHEN nv.h_to_start < 24 THEN '12-24h' ELSE '24-48h' END AS starts_in,
       coalesce(lc.first_refusal, 'NOT_A_COLLECTOR_CANDIDATE_IN_24H') AS first_refusal,
       count(*) AS n
  FROM nv LEFT JOIN lc ON lc.us_market_slug = nv.contract_id
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC;
