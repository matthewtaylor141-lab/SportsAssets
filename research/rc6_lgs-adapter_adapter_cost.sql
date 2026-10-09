-- READ-ONLY. RC6 LANE G2: WHAT THE FIXED ADAPTER'S ONE READ COSTS ON PRODUCTION.
-- storage.fixture_query (lane head) adds one semi-join pass over us_premap
-- for the held events' team rows; the collector runs it every 30 s under
-- statement_timeout 2000 ms. EXPLAIN ANALYZE executes the SELECT and
-- returns its plan and timing; nothing is written.
--   1 us_premap size
--   2 the read for paper_acct_main, exactly as the collector issues it
--   3 the same read with the held population replaced by one market per
--     candidate event of [now-6h, now+48h] and the collector's own LIMIT
--     1001 -- the cost at (beyond) the collector's bound

\echo == 1. us_premap size ==
SELECT count(*) AS rows, count(DISTINCT event_slug) AS events,
       pg_size_pretty(pg_total_relation_size('us_premap')) AS total_size
  FROM us_premap;

\echo == 2. EXPLAIN ANALYZE: the adapter read for paper_acct_main ==
EXPLAIN (ANALYZE, BUFFERS, TIMING)
WITH held AS (

    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9

), markets AS (
 SELECT DISTINCT us_market_slug FROM held WHERE account_id='paper_acct_main'
), joined AS (
 SELECT p.us_market_slug,to_jsonb(pm) AS premap,to_jsonb(fm) AS fixture_metadata
 FROM markets p
 LEFT JOIN LATERAL (
  SELECT u.* FROM us_premap u WHERE u.identifier=p.us_market_slug
  AND u.market_slug=p.us_market_slug ORDER BY u.updated_at DESC,u.event_slug LIMIT 1
 ) pm ON true
 LEFT JOIN LATERAL (
  SELECT m.* FROM venue_fixture_metadata m
  WHERE m.venue IN ('PMUS','POLYMARKET_US') AND m.venue_fixture_key='event:'||pm.event_slug
  ORDER BY m.retrieved_at DESC LIMIT 1
 ) fm ON true
 ORDER BY p.us_market_slug LIMIT 1001
), team_rows AS (
 SELECT DISTINCT u.event_slug,u.team_id,u.team_name,u.team_safe_name,u.team_abbr,
        u.team_league,u.game_start,split_part(u.sports_type,'_',1) AS sport_family
 FROM us_premap u
 WHERE u.team_id IS NOT NULL
   AND u.event_slug IN (SELECT j.premap->>'event_slug' FROM joined j)
), ranked AS (
 SELECT t.*,row_number() OVER (PARTITION BY t.event_slug ORDER BY t.team_id,t.team_name,
        t.team_safe_name,t.team_abbr,t.team_league,t.game_start,t.sport_family) AS rk
 FROM team_rows t
), event_teams AS (
 SELECT r.event_slug,count(*) AS tuples,
        jsonb_agg(jsonb_build_object('team_id',r.team_id,'team_name',r.team_name,
          'team_safe_name',r.team_safe_name,'team_abbr',r.team_abbr,'team_league',r.team_league,
          'game_start',r.game_start,'sport_family',r.sport_family) ORDER BY r.rk)
          FILTER (WHERE r.rk<=16) AS teams
 FROM ranked r GROUP BY r.event_slug
)
SELECT j.us_market_slug,j.premap,j.fixture_metadata,et.teams AS event_teams,
       coalesce(et.tuples,0) AS event_team_tuples
FROM joined j
LEFT JOIN event_teams et ON et.event_slug=j.premap->>'event_slug'
ORDER BY j.us_market_slug;

\echo == 3. EXPLAIN ANALYZE: the adapter read over the candidate population at LIMIT 1001 ==
EXPLAIN (ANALYZE, BUFFERS, TIMING)
WITH held AS (

    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9

), markets AS (
 SELECT DISTINCT ON (u.event_slug) u.market_slug AS us_market_slug
   FROM us_premap u
  WHERE u.event_slug IS NOT NULL AND u.identifier = u.market_slug
    AND u.game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
  ORDER BY u.event_slug, u.market_slug
), joined AS (
 SELECT p.us_market_slug,to_jsonb(pm) AS premap,to_jsonb(fm) AS fixture_metadata
 FROM markets p
 LEFT JOIN LATERAL (
  SELECT u.* FROM us_premap u WHERE u.identifier=p.us_market_slug
  AND u.market_slug=p.us_market_slug ORDER BY u.updated_at DESC,u.event_slug LIMIT 1
 ) pm ON true
 LEFT JOIN LATERAL (
  SELECT m.* FROM venue_fixture_metadata m
  WHERE m.venue IN ('PMUS','POLYMARKET_US') AND m.venue_fixture_key='event:'||pm.event_slug
  ORDER BY m.retrieved_at DESC LIMIT 1
 ) fm ON true
 ORDER BY p.us_market_slug LIMIT 1001
), team_rows AS (
 SELECT DISTINCT u.event_slug,u.team_id,u.team_name,u.team_safe_name,u.team_abbr,
        u.team_league,u.game_start,split_part(u.sports_type,'_',1) AS sport_family
 FROM us_premap u
 WHERE u.team_id IS NOT NULL
   AND u.event_slug IN (SELECT j.premap->>'event_slug' FROM joined j)
), ranked AS (
 SELECT t.*,row_number() OVER (PARTITION BY t.event_slug ORDER BY t.team_id,t.team_name,
        t.team_safe_name,t.team_abbr,t.team_league,t.game_start,t.sport_family) AS rk
 FROM team_rows t
), event_teams AS (
 SELECT r.event_slug,count(*) AS tuples,
        jsonb_agg(jsonb_build_object('team_id',r.team_id,'team_name',r.team_name,
          'team_safe_name',r.team_safe_name,'team_abbr',r.team_abbr,'team_league',r.team_league,
          'game_start',r.game_start,'sport_family',r.sport_family) ORDER BY r.rk)
          FILTER (WHERE r.rk<=16) AS teams
 FROM ranked r GROUP BY r.event_slug
)
SELECT j.us_market_slug,j.premap,j.fixture_metadata,et.teams AS event_teams,
       coalesce(et.tuples,0) AS event_team_tuples
FROM joined j
LEFT JOIN event_teams et ON et.event_slug=j.premap->>'event_slug'
ORDER BY j.us_market_slug;
