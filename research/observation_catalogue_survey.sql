-- READ-ONLY. WHAT THE VENUE CATALOGUE CAN FEED THE NON-FUNDED PAIR OBSERVER.
--
-- The observer builds a first leg and every settlement-compatible second leg
-- on one fixture from `us_premap`. Only rows whose sports_type names a graded
-- variable (bettor_funded_hedge_supply.GRADED_SUFFIXES) can become a leg, and
-- only the baseball and soccer families have captured overtime prose
-- (bettor_venue_settlement.OVERTIME_PROSE). This reads how many real,
-- not-yet-started fixtures in the premap sweep's window meet each condition,
-- so a catalogue-fed observer's yield is measured before it is claimed.
--
-- No balance, cash, credential or account row is selected.

\echo '== K1 · premap sweep status (full and fast lanes) =='
SELECT key, value->>'mode' AS mode, value->>'events' AS events,
       value->>'rows' AS rows, value->>'truncated' AS truncated,
       value->>'err' AS err, value->>'pages_walked' AS pages,
       (value->>'at')::timestamptz AS at,
       round(extract(epoch FROM now() - (value->>'at')::timestamptz)::numeric,
             0)                                            AS age_s
  FROM ingestion_state
 WHERE key IN ('premap_last', 'premap_last_fast')
 ORDER BY key;

\echo '== K2 · upcoming, recently re-seen rows by family and graded suffix =='
WITH w AS (
  SELECT p.*, split_part(coalesce(p.sports_type, ''), '_', 1) AS family,
         (p.sports_type ~ '(_game_first_half_total_points|_game_second_half_total_points|_team_first_half_spread|_team_second_half_spread|_team_full_game_spread|_team_first_half_winner|_team_full_game_winner|_team_full_time_winner|_game_total_points|_match_winner|_fight_winner)$')
                                                            AS graded
    FROM us_premap p
   WHERE p.game_start > now() + interval '10 minutes'
     AND p.game_start <= now() + interval '96 hours'
     AND p.updated_at > now() - interval '65 minutes'
     AND p.event_slug IS NOT NULL
)
SELECT family, graded, count(*) AS rows, count(DISTINCT event_slug) AS events,
       count(DISTINCT market_slug) AS contracts
  FROM w
 GROUP BY 1, 2
 ORDER BY 4 DESC
 LIMIT 30;

\echo '== K3 · baseball/soccer graded contracts per fixture (a pair needs two) =='
WITH g AS (
  SELECT event_slug, count(DISTINCT market_slug) AS graded_contracts,
         string_agg(DISTINCT sports_type, ',') AS types
    FROM us_premap
   WHERE game_start > now() + interval '10 minutes'
     AND game_start <= now() + interval '96 hours'
     AND updated_at > now() - interval '65 minutes'
     AND event_slug IS NOT NULL
     AND (sports_type LIKE 'baseball\_%' OR sports_type LIKE 'soccer\_%')
     AND sports_type ~ '(_team_first_half_spread|_team_second_half_spread|_team_full_game_spread|_team_first_half_winner|_team_full_game_winner|_team_full_time_winner|_game_total_points|_game_first_half_total_points|_game_second_half_total_points)$'
   GROUP BY event_slug
)
SELECT split_part(event_slug, '-', 1) AS league, graded_contracts,
       count(*) AS fixtures, min(types) AS example_types
  FROM g
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '== K4 · baseball/soccer sports_types present in the window, all kinds =='
SELECT sports_type, count(DISTINCT event_slug) AS events,
       count(DISTINCT market_slug) AS contracts
  FROM us_premap
 WHERE game_start > now() + interval '10 minutes'
   AND game_start <= now() + interval '96 hours'
   AND updated_at > now() - interval '65 minutes'
   AND (sports_type LIKE 'baseball\_%' OR sports_type LIKE 'soccer\_%')
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 40;

\echo '== K5 · the 12 soonest baseball/soccer fixtures with 2+ graded contracts =='
SELECT event_slug, min(game_start) AS starts,
       count(DISTINCT market_slug) AS graded_contracts,
       string_agg(DISTINCT sports_type, ',') AS types,
       max(updated_at) AS last_seen
  FROM us_premap
 WHERE game_start > now() + interval '10 minutes'
   AND game_start <= now() + interval '96 hours'
   AND updated_at > now() - interval '65 minutes'
   AND (sports_type LIKE 'baseball\_%' OR sports_type LIKE 'soccer\_%')
   AND sports_type ~ '(_team_full_game_spread|_team_full_game_winner|_team_full_time_winner|_team_first_half_winner|_team_first_half_spread)$'
 GROUP BY event_slug
HAVING count(DISTINCT market_slug) >= 2
 ORDER BY min(game_start)
 LIMIT 12;

\echo '== K6 · observer tables right now =='
SELECT (SELECT count(*) FROM bettor_pair_observations) AS observations,
       (SELECT count(*) FROM bettor_pair_observation_labels) AS label_versions,
       (SELECT count(*) FROM bettor_funded_models) AS models;
