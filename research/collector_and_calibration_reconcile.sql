-- READ-ONLY. RECONCILES TWO NUMBERS AND TRACES ONE COLLECTOR.
--
--   K  calibration: every stage from the valuations table to the evaluator's
--      "resolved fixtures", mirroring bettor_source_calibration exactly:
--      experiment EXT_PINNACLE_DEVIG_V1_SHADOW, decided in the last 90 days,
--      in scope (PINNACLE_DEVIG_V1 / power / baseball|soccer / h2h), the
--      EARLIEST valuation per event_key, classified RESOLVED only with a
--      verified venue basis and a probability. Alternative counts are shown
--      beside it so an earlier figure can be matched to what it counted.
--   P  the pair-observation collector: every attempt since the ledger
--      existed, in full; repetition per fixture (rotation); and a census of
--      the catalogue window by FAMILY x CONTRACT TYPE in distinct fixtures
--      and distinct contracts -- rows are not opportunities.
--
-- No balance, credential or account field is selected.

\echo '== K1 · calibration funnel (90 days, experiment EXT_PINNACLE_DEVIG_V1_SHADOW) =='
WITH w AS (
  SELECT * FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at >= now() - interval '90 days'),
s AS (
  SELECT * FROM w
   WHERE version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
     AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'),
f AS (
  SELECT DISTINCT ON (event_key) *
    FROM s WHERE coalesce(event_key, '') <> ''
   ORDER BY event_key, observed_at, id),
c AS (
  SELECT f.*,
         CASE WHEN outcome_basis = 'CONFIRMED_VOID' THEN 'VOID'
              WHEN NOT coalesce(outcome_known, false) THEN 'UNRESOLVED'
              WHEN outcome NOT IN (0, 1) OR outcome IS NULL THEN 'UNVERIFIED'
              WHEN outcome_basis IN ('VENUE_SETTLEMENT_PRICE',
                                     'VENUE_REPORTED_OUTCOME')
                   AND probability IS NOT NULL THEN 'RESOLVED'
              WHEN outcome_basis IN ('VENUE_SETTLEMENT_PRICE',
                                     'VENUE_REPORTED_OUTCOME')
                   THEN 'RESOLVED_BUT_NO_PROBABILITY'
              ELSE 'UNVERIFIED' END AS cls
    FROM f)
SELECT (SELECT count(*) FROM w)                                AS rows_in_window,
       (SELECT count(*) FROM w WHERE record_purpose = 'ENTRY_DECISION')
                                                               AS entry_rows,
       (SELECT count(*) FROM w WHERE record_purpose = 'CALIBRATION_ONLY')
                                                               AS calibration_only_rows,
       (SELECT count(*) FROM s)                                AS rows_in_scope,
       (SELECT count(*) FROM w) - (SELECT count(*) FROM s)     AS rows_out_of_scope,
       (SELECT count(*) FROM f)                                AS fixtures_in_scope,
       (SELECT count(*) FROM c WHERE cls = 'RESOLVED')         AS resolved_fixtures,
       (SELECT count(*) FROM c WHERE cls = 'UNRESOLVED')       AS unresolved_fixtures,
       (SELECT count(*) FROM c WHERE cls = 'VOID')             AS void_fixtures,
       (SELECT count(*) FROM c WHERE cls = 'UNVERIFIED')       AS unverified_fixtures,
       (SELECT count(*) FROM c WHERE cls = 'RESOLVED_BUT_NO_PROBABILITY')
                                                               AS resolved_no_probability;

\echo '== K2 · alternative counts an earlier figure may have used =='
WITH w AS (
  SELECT * FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW')
SELECT (SELECT count(*) FROM w WHERE outcome_known)            AS all_time_rows_outcome_known,
       (SELECT count(DISTINCT (event_key, payout_event)) FROM w WHERE outcome_known)
                                                               AS all_time_statements_outcome_known,
       (SELECT count(DISTINCT event_key) FROM w WHERE outcome_known)
                                                               AS all_time_fixtures_outcome_known,
       (SELECT count(DISTINCT event_key) FROM w WHERE outcome_known
          AND decided_at >= now() - interval '90 days')        AS window_fixtures_outcome_known,
       (SELECT count(DISTINCT event_key) FROM w WHERE outcome_known
          AND outcome_basis IN ('VENUE_SETTLEMENT_PRICE', 'VENUE_REPORTED_OUTCOME'))
                                                               AS all_time_fixtures_verified_basis,
       (SELECT count(DISTINCT (event_key, payout_event)) FROM w WHERE outcome_known
          AND decided_at >= now() - interval '90 days'
          AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
          AND sport_family IN ('baseball', 'soccer') AND market = 'h2h')
                                                               AS window_statements_in_scope_outcome_known,
       (SELECT min(decided_at) FROM w)                         AS first_valuation,
       (SELECT max(decided_at) FROM w)                         AS last_valuation;

\echo '== K3 · exclusions: out-of-scope reasons and outcome bases of known outcomes =='
SELECT 'scope' AS what, coalesce(version, '?') || ' / ' || coalesce(devig_method, '?')
         || ' / ' || coalesce(sport_family, '?') || ' / ' || coalesce(market, '?') AS key,
       count(*) AS rows, count(DISTINCT event_key) AS fixtures,
       count(DISTINCT event_key) FILTER (WHERE outcome_known) AS fixtures_outcome_known
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '90 days'
 GROUP BY 1, 2
UNION ALL
SELECT 'basis', coalesce(outcome_basis, 'NULL') || ' known=' || coalesce(outcome_known::text, 'NULL'),
       count(*), count(DISTINCT event_key), NULL
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at >= now() - interval '90 days'
 GROUP BY 1, 2
 ORDER BY 1, 3 DESC;

\echo '== P1 · every pair-observation attempt, in full =='
SELECT attempt_id, attempted_at, candidate_source, us_market_slug, side, fixture,
       outcome, refusal, detail::text AS detail, venue_reads::text AS venue_reads
  FROM bettor_pair_observation_attempts
 ORDER BY attempt_id;

\echo '== P2 · rotation: attempts per fixture =='
SELECT fixture, count(*) AS attempts, min(attempted_at) AS first, max(attempted_at) AS last,
       string_agg(DISTINCT pass_id, ',') AS passes
  FROM bettor_pair_observation_attempts
 GROUP BY fixture ORDER BY attempts DESC, fixture;

\echo '== P3 · catalogue window census: family x contract type, in fixtures and contracts =='
WITH win AS (
  SELECT * FROM us_premap
   WHERE game_start > now() + interval '600 seconds'
     AND game_start <= now() + interval '96 hours'
     AND updated_at > now() - interval '3900 seconds'
     AND event_slug IS NOT NULL AND market_slug IS NOT NULL)
SELECT split_part(coalesce(sports_type, ''), '_', 1) AS family,
       split_part(coalesce(sports_type, ''), '_', 1) IN ('baseball', 'soccer') AS family_captured,
       sports_type AS contract_type,
       coalesce(sports_type ~ '(_game_first_half_total_points|_game_second_half_total_points|_team_first_half_spread|_team_second_half_spread|_team_full_game_spread|_team_first_half_winner|_team_full_game_winner|_team_full_time_winner|_game_total_points|_match_winner|_fight_winner)$', false) AS graded,
       count(*) AS rows, count(DISTINCT event_slug) AS fixtures,
       count(DISTINCT market_slug) AS contracts
  FROM win
 GROUP BY 1, 2, 3, 4
 ORDER BY rows DESC
 LIMIT 80;

\echo '== P4 · census totals: rows vs fixtures vs contracts by family =='
WITH win AS (
  SELECT * FROM us_premap
   WHERE game_start > now() + interval '600 seconds'
     AND game_start <= now() + interval '96 hours'
     AND updated_at > now() - interval '3900 seconds'
     AND event_slug IS NOT NULL AND market_slug IS NOT NULL)
SELECT split_part(coalesce(sports_type, ''), '_', 1) AS family,
       count(*) AS rows, count(DISTINCT event_slug) AS fixtures,
       count(DISTINCT market_slug) AS contracts,
       count(DISTINCT sports_type) AS contract_types,
       count(DISTINCT event_slug) FILTER (WHERE sports_type ~ '(_team_full_game_winner|_team_full_time_winner|_match_winner|_fight_winner)$')
         AS fixtures_with_a_graded_winner
  FROM win GROUP BY 1 ORDER BY rows DESC;

\echo '== P5 · captured families: graded contract types per fixture =='
WITH win AS (
  SELECT * FROM us_premap
   WHERE game_start > now() + interval '600 seconds'
     AND game_start <= now() + interval '96 hours'
     AND updated_at > now() - interval '3900 seconds'
     AND event_slug IS NOT NULL AND market_slug IS NOT NULL
     AND split_part(coalesce(sports_type, ''), '_', 1) IN ('baseball', 'soccer')
     AND sports_type ~ '(_game_first_half_total_points|_game_second_half_total_points|_team_first_half_spread|_team_second_half_spread|_team_full_game_spread|_team_first_half_winner|_team_full_game_winner|_team_full_time_winner|_game_total_points|_match_winner|_fight_winner)$'),
fx AS (
  SELECT event_slug, split_part(sports_type, '_', 1) AS family,
         string_agg(DISTINCT regexp_replace(sports_type, '^[a-z]+_', ''), '+'
                    ORDER BY regexp_replace(sports_type, '^[a-z]+_', '')) AS kinds,
         count(DISTINCT market_slug) AS contracts
    FROM win GROUP BY 1, 2)
SELECT family, kinds, count(*) AS fixtures, sum(contracts) AS contracts,
       min(event_slug) AS example_fixture
  FROM fx GROUP BY 1, 2 ORDER BY fixtures DESC LIMIT 40;
