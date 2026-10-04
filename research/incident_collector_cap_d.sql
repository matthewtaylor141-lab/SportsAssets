-- P0 INCIDENT (2026-10-04) segment COLLECTOR SPORT CAP, fourth read (read-only).
-- THE PRODUCTION BOARD PER SCHEDULED CYCLE, for replaying the new coverage
-- scheduler against what the old four-key rule did. One row per scheduled
-- cycle in the last 7 days: for every mapped venue token, the venue's events
-- starting in [cycle_at - 6 h, cycle_at + 24 h] (the decision horizon), the
-- minutes from cycle_at to its next event start (any time after cycle_at - 6 h),
-- and whether the old rule fetched its provider key in that cycle.
-- A scheduled cycle is a cycle_id with >= 2 ext_candidate_outcomes rows.
-- Bounded: 7 days, ~460 rows of compact json.
\echo '== D0 read instant =='
SELECT now() AS read_at;

\echo '== D1 per scheduled cycle: {token: [events_in_horizon, minutes_to_next_start, fetched]} =='
WITH tok(token, provider_key) AS (VALUES
  ('mlb', 'baseball_mlb'), ('cfb', 'americanfootball_ncaaf'),
  ('nfl', 'americanfootball_nfl'), ('unl', 'soccer_uefa_nations_league'),
  ('mls', 'soccer_usa_mls'), ('lmx', 'soccer_mexico_ligamx'),
  ('uwcl', 'soccer_uefa_champs_league_women'),
  ('cnl', 'soccer_concacaf_nations_league'),
  ('uslc', 'soccer_usa_usl_championship'),
  ('arg2', 'soccer_argentina_primera_nacional'),
  ('brb', 'soccer_brazil_serie_b'), ('lco', 'soccer_colombia_primera_a'),
  ('uru1', 'soccer_uruguay_primera_division'), ('nwsl', 'soccer_usa_nwsl')
), ve AS (
  SELECT DISTINCT split_part(market_slug, '-', 2) AS token, event_slug, game_start
    FROM us_premap
   WHERE game_start >= now() - interval '8 days' AND game_start < now() + interval '6 days'
     AND split_part(market_slug, '-', 2) IN (SELECT token FROM tok)
     AND (sports_type LIKE 'soccer%' OR sports_type LIKE 'football%' OR sports_type LIKE 'baseball%')
     AND lower(coalesce(event_title, '')) NOT LIKE '%ebattle%'
), cyc AS (
  SELECT cycle_id, min(cycle_at) AS cycle_at FROM ext_candidate_outcomes
   WHERE cycle_at >= now() - interval '7 days'
   GROUP BY cycle_id HAVING count(*) >= 2
), fetched AS (
  SELECT DISTINCT o.cycle_id, o.sport_key FROM ext_candidate_outcomes o JOIN cyc USING (cycle_id)
), grid AS (
  SELECT c.cycle_id, c.cycle_at, t.token, t.provider_key,
         count(ve.event_slug) FILTER (WHERE ve.game_start <= c.cycle_at + interval '24 hours') AS ev,
         round(extract(epoch FROM min(ve.game_start) - c.cycle_at) / 60.0) AS next_min
    FROM cyc c CROSS JOIN tok t
    LEFT JOIN ve ON ve.token = t.token AND ve.game_start > c.cycle_at - interval '6 hours'
   GROUP BY 1, 2, 3, 4
)
SELECT to_char(g.cycle_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS cycle_at,
       jsonb_object_agg(g.token, jsonb_build_array(g.ev, g.next_min,
                        (f.cycle_id IS NOT NULL)::int)
                        ORDER BY g.token) AS board
  FROM grid g LEFT JOIN fetched f ON f.cycle_id = g.cycle_id AND f.sport_key = g.provider_key
 GROUP BY g.cycle_id, g.cycle_at
 ORDER BY g.cycle_at;
