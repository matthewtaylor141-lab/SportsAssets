-- RC6.2 lane p-coverage: THE PRODUCTION READBACK of branch rc6/p-coverage,
-- read only. Run it once before the deploy (the baseline) and once after
-- (the proof); every section names the expected after-state.
--   R1  segment markets (regulation / inning / first five / set / map /
--       game N): period and settlement reason head. AFTER: no FULL_EVENT
--       period, hockey_team_regulation_winner reads BOOKMAKER_TERMS_NOT_
--       HELD:hockey/WINNER/REGULATION_TIME (never INCOMPATIBLE_NOT_PRICEABLE)
--   R2  team statistic totals and the compound props: family. AFTER: the
--       statistic (FIRST_DOWNS, ...), never TEAM_SCORE / POINTS / RUNS
--   R3  venue codes wbc / pdc / ppa / powerslap / dfb / uefa / motogp / cdb /
--       lib / fide / boxing / football / vkl / btla: sport and gap. AFTER:
--       wbc boxing, pdc LEAGUE_CODE_AMBIGUOUS, the rest their sport
--   R4  Kalshi game winners: settlement reason head per sport. AFTER:
--       basketball / hockey / football INCOMPATIBLE_NOT_PRICEABLE, never
--       VENUE_RULES_SILENT_ON
--   R5  soccer / baseball h2h valuations (24 h): quote-context basis,
--       fixture source basis, verdict. AFTER: PROVIDER_QUOTE_LABEL present,
--       VENUE_LEAGUE_CODE present for unl / ucl / uel / uecl events
--   R6  venue_fixture_metadata by competition (UCL / UEL / UECL rows prove
--       the declared organiser ids; a COMPETITION refusal disproves them)
--   R7  the coverage unit: active, PRICEABLE, by venue and state
-- No write, no secret.
\echo === R1. segment markets: market_type x period x settlement reason head ===
SELECT market_type, period, split_part(coalesce(settlement_why, '-'), ':', 1)
         AS head,
       left(coalesce(settlement_why, '-'), 70) AS why, count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
   AND (market_type LIKE '%regulation%' OR market_type LIKE '%inning%'
        OR market_type LIKE '%first_five%' OR market_type LIKE '%set\_%'
        OR market_type LIKE 'esports_map_%' OR market_type LIKE 'esports_game_%')
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 60;
\echo === R2. statistic totals and compound props: market_type x family ===
SELECT market_type, family, count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
   AND (market_type LIKE 'football_team_total_%'
        OR market_type IN ('football_player_fantasy_points_ppr',
                           'football_game_race_to_points',
                           'baseball_player_hits_runs_rbis',
                           'baseball_player_home_runs',
                           'football_player_most_passing_yards')
        OR market_type LIKE '%both_teams_score_points')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;
\echo === R3. venue codes: code x sport x coverage reason ===
SELECT split_part(coalesce(event_id, contract_id), '-', 1) AS code,
       coalesce(sport, '-') AS sport,
       left(coalesce(coverage_why, coverage_state, '-'), 70) AS why,
       count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US'
   AND split_part(coalesce(event_id, contract_id), '-', 1) IN (
       'wbc', 'pdc', 'ppa', 'powerslap', 'dfb', 'uefa', 'motogp', 'cdb',
       'lib', 'fide', 'boxing', 'football', 'vkl', 'btla')
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 60;
\echo === R4. Kalshi game winners: sport x settlement reason head ===
SELECT coalesce(sport, '-') AS sport,
       split_part(coalesce(settlement_why, '-'), ':', 1) AS head,
       count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'KALSHI' AND family = 'WINNER'
   AND period = 'FULL_EVENT'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 40;
\echo === R5. soccer / baseball h2h valuations (24 h): context basis x source basis x verdict ===
WITH v AS MATERIALIZED (
  SELECT DISTINCT ON (e.us_market_slug) e.us_market_slug AS slug,
         e.sport_family, e.settlement_comparison AS s
    FROM external_valuations e
   WHERE e.decided_at > now() - interval '24 hours'
     AND e.us_market_slug IS NOT NULL AND e.market = 'h2h'
     AND e.sport_family IN ('soccer', 'baseball')
   ORDER BY e.us_market_slug, e.decided_at DESC, e.id DESC)
SELECT v.sport_family,
       coalesce(split_part(g.event_id, '-', 1), '?') AS code,
       coalesce(v.s ->> 'quote_context_basis', '-') AS ctx_basis,
       coalesce(v.s ->> 'quote_context', '-') AS ctx,
       coalesce(v.s -> 'fixture_acquisition' ->> 'source_basis', '-')
         AS src_basis,
       left(coalesce(v.s -> 'fixture_acquisition' ->> 'refusal', '-'), 60)
         AS acq_refusal,
       coalesce(v.s ->> 'verdict', v.s ->> 'compatibility', '-') AS verdict,
       count(*) AS n
  FROM v LEFT JOIN market_plane_registry g ON g.contract_id = v.slug
 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 8 DESC LIMIT 60;
\echo === R6. venue_fixture_metadata by competition ===
SELECT competition, phase, game_format, count(*) AS n,
       max(retrieved_at) AS newest
  FROM venue_fixture_metadata GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
\echo === R7. the coverage unit ===
SELECT venue, coverage_state, count(*) AS n
  FROM market_plane_registry WHERE active GROUP BY 1, 2 ORDER BY 1, 2;
