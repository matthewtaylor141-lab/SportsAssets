-- READ-ONLY. A BOUNDED STRUCTURAL CENSUS OF THE PAIR COLLECTOR'S WINDOW,
-- mirroring bettor_pair_observations.structural_verdict from the catalogue
-- alone (no venue read): per fixture in the captured families, the graded
-- instruments whose two rows name DIFFERENT participants (a per-outcome
-- yes/no contract names one team on both rows and is refused on both sides),
-- grouped by period and variable. A fixture is structurally pairable when two
-- usable instruments share a period and variable; what remains after that is
-- the venue's overtime/void wording and a price, which only an attempt reads.
-- Approximations, stated: orientation is "team_abbr present" (the screen also
-- checks it against the slug's participants), and a total is usable only when
-- side_norm states over/under.

\echo '== C1 · census by family x verdict, in fixtures and contracts =='
WITH win AS (
  SELECT lower(event_slug) AS fixture, market_slug, intent, sports_type,
         lower(coalesce(team_abbr, '')) AS abbr, lower(coalesce(side_norm, '')) AS sn,
         split_part(sports_type, '_', 1) AS family
    FROM us_premap
   WHERE game_start > now() + interval '600 seconds'
     AND game_start <= now() + interval '96 hours'
     AND updated_at > now() - interval '3900 seconds'
     AND event_slug IS NOT NULL AND market_slug IS NOT NULL
     AND split_part(coalesce(sports_type, ''), '_', 1) IN ('baseball', 'soccer')
     AND sports_type ~ '(_game_first_half_total_points|_game_second_half_total_points|_team_first_half_spread|_team_second_half_spread|_team_full_game_spread|_team_first_half_winner|_team_full_game_winner|_team_full_time_winner|_game_total_points|_match_winner|_fight_winner)$'
     AND intent IN ('ORDER_INTENT_BUY_LONG', 'ORDER_INTENT_BUY_SHORT')),
inst AS (
  SELECT fixture, family, market_slug, min(sports_type) AS st,
         count(*) AS rows_, count(DISTINCT abbr) FILTER (WHERE abbr <> '') AS abbrs,
         bool_and(abbr <> '') AS all_oriented,
         bool_or(sn IN ('over', 'under')) AS states_direction
    FROM win GROUP BY 1, 2, 3),
k AS (
  SELECT *,
         CASE WHEN st ~ '(first_half)' THEN 'FIRST_HALF'
              WHEN st ~ '(second_half)' THEN 'SECOND_HALF' ELSE 'FULL_GAME' END AS period,
         CASE WHEN st ~ 'total_points$' THEN 'TOTAL' ELSE 'MARGIN' END AS variable,
         CASE WHEN st ~ 'total_points$' THEN states_direction
              ELSE all_oriented AND NOT (rows_ >= 2 AND abbrs = 1) END AS usable
    FROM inst),
fx AS (
  SELECT fixture, family, count(*) AS contracts,
         count(*) FILTER (WHERE usable) AS usable_contracts,
         max(n) AS best_shared
    FROM (SELECT k.*, count(*) FILTER (WHERE usable)
                   OVER (PARTITION BY fixture, period, variable) AS n FROM k) z
   GROUP BY 1, 2)
SELECT family,
       CASE WHEN contracts < 2 THEN 'ONE_GRADED_CONTRACT'
            WHEN usable_contracts < 2 THEN 'SIBLINGS_ARE_UNSUPPORTED_SHAPES'
            WHEN best_shared < 2 THEN 'NO_TWO_CONTRACTS_SHARE_A_PERIOD_AND_VARIABLE'
            ELSE 'STRUCTURALLY_PAIRABLE_PENDING_PROSE_AND_PRICE' END AS verdict,
       count(*) AS fixtures, sum(contracts) AS contracts,
       sum(usable_contracts) AS usable_contracts, min(fixture) AS example
  FROM fx GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== C2 · the structurally pairable fixtures, soonest first =='
WITH win AS (
  SELECT lower(event_slug) AS fixture, market_slug, sports_type, game_start,
         lower(coalesce(team_abbr, '')) AS abbr
    FROM us_premap
   WHERE game_start > now() + interval '600 seconds'
     AND game_start <= now() + interval '96 hours'
     AND updated_at > now() - interval '3900 seconds'
     AND event_slug IS NOT NULL AND market_slug IS NOT NULL
     AND split_part(coalesce(sports_type, ''), '_', 1) IN ('baseball', 'soccer')
     AND sports_type ~ '(_team_first_half_spread|_team_second_half_spread|_team_full_game_spread|_team_first_half_winner|_team_full_game_winner|_team_full_time_winner|_match_winner)$'),
inst AS (
  SELECT fixture, market_slug, min(sports_type) AS st, min(game_start) AS starts,
         count(*) AS rows_, count(DISTINCT abbr) FILTER (WHERE abbr <> '') AS abbrs,
         bool_and(abbr <> '') AS oriented
    FROM win GROUP BY 1, 2)
SELECT fixture, min(starts) AS starts,
       CASE WHEN st ~ 'first_half' THEN 'FIRST_HALF' ELSE 'FULL_GAME' END AS period,
       count(*) AS usable_contracts, string_agg(DISTINCT st, ',') AS types
  FROM inst
 WHERE oriented AND NOT (rows_ >= 2 AND abbrs = 1)
 GROUP BY fixture, 3 HAVING count(*) >= 2
 ORDER BY 2 LIMIT 40;
