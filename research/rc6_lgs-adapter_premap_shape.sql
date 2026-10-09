-- READ-ONLY. RC6 LANE G2 (LIVE GAME STATE FIXTURE ADAPTER): WHAT us_premap
-- ACTUALLY CARRIES FOR THE HELD AND CANDIDATE FIXTURES.
--
-- research/lgs_held_fixture_census.sql (main line) reads premap keys
-- home_team / away_team / league / sport. The collector that owns us_premap
-- (workers/premap._ensure_table) never creates those columns; it writes the
-- venue's own per-side team fields (team_id, team_name, team_safe_name,
-- team_abbr, team_league), the market-level game_start and sports_type. This
-- file measures, on production, exactly which of those the held and the
-- candidate events carry, so the adapter can be fixed from facts:
--
--   1 the us_premap column list (information_schema)
--   2 the held PAPER population of paper_acct_main and, per held market, the
--     premap row the adapter joins (identifier = market_slug = the slug)
--   3 per held EVENT: every distinct venue team on the event's rows (team_id,
--     names, abbreviation, league), the distinct game_start values and the
--     sports types -- the participants the venue itself states
--   4 the candidate population: every us_premap event whose game_start is in
--     [now - 6 h, now + 48 h], by venue team_league: how many name exactly
--     two distinct team ids, one league, one start
--   5 a sample of candidate events (public venue catalogue data) with their
--     two teams' venue fields, to see the naming the venue uses per league
--   6 venue_fixture_metadata coverage (the only other source the adapter reads)
--
-- Nothing here parses a title, a slug or a question. Every statement is a
-- SELECT. Account literal: 'paper_acct_main'.

\echo == 1. us_premap columns ==
SELECT column_name, data_type
  FROM information_schema.columns
 WHERE table_name = 'us_premap'
 ORDER BY ordinal_position;

\echo == 2. held PAPER markets (paper_acct_main) and the premap row the adapter joins ==
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
    SELECT us_market_slug, count(*) AS positions,
           count(*) FILTER (WHERE account_id = 'paper_acct_main') AS main_positions
      FROM held GROUP BY us_market_slug
)
SELECT m.us_market_slug, m.positions, m.main_positions,
       pm.event_slug, pm.kind, pm.side_norm, pm.sports_type, pm.team_league,
       pm.team_id, pm.team_name, pm.team_safe_name, pm.team_abbr,
       pm.game_start, pm.listing_state, pm.updated_at,
       (SELECT count(*) FROM us_premap u WHERE u.identifier = m.us_market_slug) AS rows_for_identifier
  FROM markets m
  LEFT JOIN LATERAL (
    SELECT u.* FROM us_premap u
     WHERE u.identifier = m.us_market_slug AND u.market_slug = m.us_market_slug
     ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
 ORDER BY m.main_positions DESC, m.us_market_slug
 LIMIT 200;

\echo == 3. per held event: the venue own teams on the event rows ==
WITH held AS (
    SELECT f.account_id, f.us_market_slug
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
), ev AS (
    SELECT DISTINCT pm.event_slug
      FROM (SELECT DISTINCT us_market_slug FROM held) m
      JOIN LATERAL (
        SELECT u.event_slug FROM us_premap u
         WHERE u.identifier = m.us_market_slug AND u.market_slug = m.us_market_slug
         ORDER BY u.updated_at DESC, u.event_slug LIMIT 1) pm ON true
     WHERE pm.event_slug IS NOT NULL
)
SELECT e.event_slug, u.team_id, u.team_name, u.team_safe_name, u.team_abbr,
       u.team_league, count(*) AS rows,
       count(DISTINCT u.market_slug) AS markets,
       string_agg(DISTINCT u.sports_type, ',') AS sports_types,
       count(DISTINCT u.game_start) AS distinct_starts,
       min(u.game_start) AS min_start, max(u.game_start) AS max_start
  FROM ev e
  JOIN us_premap u ON u.event_slug = e.event_slug
 GROUP BY 1, 2, 3, 4, 5, 6
 ORDER BY 1, 2 NULLS LAST, 3
 LIMIT 400;

\echo == 4. candidate events (game_start in [now-6h, now+48h]) by venue team_league ==
WITH ev AS (
    SELECT u.event_slug,
           count(DISTINCT u.team_id) AS team_ids,
           count(DISTINCT u.team_name) AS team_names,
           count(DISTINCT u.team_league) AS leagues,
           min(u.team_league) AS league,
           count(DISTINCT u.game_start) AS starts,
           count(*) AS rows
      FROM us_premap u
     WHERE u.event_slug IS NOT NULL
       AND u.game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
     GROUP BY u.event_slug
)
SELECT coalesce(league, '<no team_league>') AS venue_team_league,
       count(*) AS events,
       count(*) FILTER (WHERE team_ids = 2) AS two_team_ids,
       count(*) FILTER (WHERE team_names = 2) AS two_team_names,
       count(*) FILTER (WHERE leagues = 1) AS one_league,
       count(*) FILTER (WHERE starts = 1) AS one_start,
       count(*) FILTER (WHERE team_ids = 2 AND team_names = 2 AND leagues = 1 AND starts = 1) AS all_four,
       count(*) FILTER (WHERE team_ids = 0) AS no_team_rows,
       count(*) FILTER (WHERE team_ids > 2) AS more_than_two_ids,
       count(*) FILTER (WHERE starts > 1) AS several_starts
  FROM ev
 GROUP BY 1
 ORDER BY events DESC;

\echo == 5. sample candidate events per league: the two venue teams (public catalogue fields) ==
WITH ev AS (
    SELECT u.event_slug, min(u.team_league) AS league, min(u.game_start) AS start_at
      FROM us_premap u
     WHERE u.event_slug IS NOT NULL
       AND u.game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
     GROUP BY u.event_slug
    HAVING count(DISTINCT u.team_id) = 2
), ranked AS (
    SELECT ev.*, row_number() OVER (PARTITION BY league ORDER BY start_at, event_slug) AS rk
      FROM ev
)
SELECT r.league, r.event_slug, r.start_at, t.team_id, t.team_name, t.team_safe_name,
       t.team_abbr, t.sports_types
  FROM ranked r
  JOIN LATERAL (
    SELECT u.team_id, min(u.team_name) AS team_name, min(u.team_safe_name) AS team_safe_name,
           min(u.team_abbr) AS team_abbr, string_agg(DISTINCT u.sports_type, ',') AS sports_types
      FROM us_premap u
     WHERE u.event_slug = r.event_slug AND u.team_id IS NOT NULL
     GROUP BY u.team_id) t ON true
 WHERE r.rk <= 4
 ORDER BY r.league, r.start_at, r.event_slug, t.team_id
 LIMIT 300;

\echo == 6. venue_fixture_metadata: competitions, sources, orientations ==
SELECT venue, competition, source, orientation, count(*) AS rows,
       count(*) FILTER (WHERE home_team IS NOT NULL AND away_team IS NOT NULL) AS with_home_away,
       max(retrieved_at) AS newest_retrieval
  FROM venue_fixture_metadata
 GROUP BY 1, 2, 3, 4
 ORDER BY rows DESC
 LIMIT 60;
