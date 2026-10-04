-- P0 INCIDENT (2026-10-04) · segment: SPORT/LEAGUE NORMALIZATION + EVENT IDENTITY
-- RESOLUTION. READ-ONLY. Every statement is bounded by a time window and/or a
-- LIMIT; statement_timeout is set by the workflow.
--
-- Answers: per provider sport, how many provider events end MATCHED /
-- AMBIGUOUS / NO_VENUE_COUNTERPART / NORMALIZATION_FAILURE / PARTICIPANT_MISMATCH
-- / DUPLICATE_CANDIDATES (raw codes shown beside the class), what the venue
-- board offers per league, which canonical ids (venue team_id) exist, and the
-- raw provider-vs-venue names needed to replay the matcher offline.

\echo '== A0 · read instant, last collector cycle: requested keys, budget drops, fixture confirmation =='
SELECT now() AS read_at,
       to_timestamp((value->>'at')::float8) AS cycle_at,
       value->>'state' AS state,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'metered_budget' AS budget,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'sports_selection'->'rejected' AS rejected,
       left((value->'sports_selection'->'confirmed_by_provider')::text, 2500) AS confirmed,
       left((value->'sports_selection'->'venue_football_board')::text, 600) AS football_board,
       left((value->'sports_selection'->'venue_board')::text, 600) AS soccer_board
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== A1 · last cycle funnel_by_provider_sport (bounded) =='
SELECT k AS sport_key,
       v->>'provider_events' AS provider_events,
       v->>'with_pinnacle_h2h' AS with_pinn,
       v->>'mapped_to_a_venue_contract' AS mapped,
       v->>'mapped_by_venue_native' AS mapped_vn,
       v->>'identity_resolved' AS identity_ok,
       v->>'evaluated' AS evaluated,
       left((v->'refusals')::text, 700) AS refusals,
       left((v->'mapping_confirmation')::text, 500) AS confirmation
  FROM ingestion_state, jsonb_each(coalesce(value->'funnel_by_provider_sport', '{}'::jsonb)) AS f(k, v)
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== A2 · ledger volume per provider sport: 24 h and 72 h (rows, cycles, distinct provider events, outcomes) =='
SELECT sport_key,
       count(*) FILTER (WHERE cycle_at > now() - interval '24 hours') AS rows_24h,
       count(DISTINCT cycle_id) FILTER (WHERE cycle_at > now() - interval '24 hours') AS cycles_24h,
       count(DISTINCT provider_event_id) FILTER (WHERE cycle_at > now() - interval '24 hours') AS events_24h,
       count(*) AS rows_72h,
       count(DISTINCT cycle_id) AS cycles_72h,
       count(DISTINCT provider_event_id) AS events_72h,
       count(*) FILTER (WHERE outcome = 'ADMITTED') AS admitted,
       count(*) FILTER (WHERE outcome = 'REFUSED') AS refused,
       count(*) FILTER (WHERE outcome = 'DEFERRED') AS deferred,
       count(*) FILTER (WHERE outcome = 'ALREADY_RECORDED') AS already,
       count(*) FILTER (WHERE outcome = 'UNCLASSIFIED') AS unclassified,
       count(*) FILTER (WHERE mapped_by IS NOT NULL) AS rows_mapped,
       count(DISTINCT provider_event_id) FILTER (WHERE mapped_by IS NOT NULL) AS events_ever_mapped,
       min(cycle_at) AS first_row, max(cycle_at) AS last_row
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '72 hours'
 GROUP BY 1 ORDER BY rows_72h DESC;

\echo '== A3 · raw codes per sport (72 h): first_refusal, 2nd code, mapped_by -> rows / distinct events =='
SELECT sport_key, outcome, coalesce(first_refusal, '-') AS first_refusal,
       coalesce(codes->>1, '-') AS second_code, coalesce(mapped_by, '-') AS mapped_by,
       count(*) AS rows, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '72 hours'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 6 DESC LIMIT 160;

\echo '== A4 · RECEIPT CLASS per provider event: the LAST pre-start receipt of every event that has started in the last 72 h =='
WITH ev AS (
  SELECT DISTINCT ON (sport_key, provider_event_id)
         sport_key, provider_event_id, home, away, commence_time, cycle_at,
         outcome, first_refusal, codes, mapped_by, us_market_slug,
         (SELECT c FROM jsonb_array_elements_text(codes) WITH ORDINALITY t(c, i)
           WHERE c LIKE 'VENUE_NATIVE%' OR c = 'NO_VENUE_NATIVE_EVENT_FOR_FIXTURE'
           ORDER BY i LIMIT 1) AS vn_code
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '96 hours'
     AND (CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
               THEN commence_time::timestamptz END) BETWEEN now() - interval '72 hours' AND now()
     AND cycle_at <= (CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
               THEN commence_time::timestamptz END)
   ORDER BY sport_key, provider_event_id, cycle_at DESC
), cls AS (
  SELECT *, CASE
     WHEN outcome = 'DEFERRED' THEN 'Z_DEFERRED_PAST_CYCLE_CAP'
     WHEN first_refusal IN ('NO_PINNACLE_ON_EVENT', 'PROVIDER_REQUEST_FAILED') THEN 'P_PRE_IDENTITY_NO_PROVIDER_PRICE'
     WHEN first_refusal IN ('THE_PROVIDER_COMPETITION_FIXTURES_DO_NOT_MATCH_THE_VENUE_COMPETITION',
                            'THE_MAPPING_COULD_NOT_BE_CONFIRMED_AGAINST_ANY_FIXTURE') THEN 'NORMALIZATION_FAILURE(competition)'
     WHEN mapped_by IS NOT NULL AND (first_refusal IS NULL OR first_refusal NOT IN (
            'NO_VENUE_NATIVE_CONTRACT_IN_PREMAP', 'VENUE_CONTRACT_IS_NOT_LONG_ON_THE_PRICED_OUTCOME')) THEN 'MATCHED'
     WHEN vn_code IN ('VENUE_NATIVE_EVENT_AMBIGUOUS') THEN 'DUPLICATE_CANDIDATES'
     WHEN vn_code IN ('VENUE_NATIVE_TEAM_ASSIGNMENT_AMBIGUOUS',
                      'VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_AMBIGUOUS') THEN 'AMBIGUOUS'
     WHEN vn_code IN ('VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM',
                      'VENUE_NATIVE_NICKNAME_DOES_NOT_CONFIRM_THE_TEAM') THEN 'PARTICIPANT_MISMATCH'
     WHEN vn_code IN ('VENUE_NATIVE_PROVIDER_EVENT_NOT_MATCHABLE', 'VENUE_NATIVE_FAMILY_NOT_SUPPORTED',
                      'VENUE_NATIVE_COMPETITION_NOT_ESTABLISHED') THEN 'NORMALIZATION_FAILURE'
     WHEN vn_code IN ('NO_VENUE_NATIVE_EVENT_FOR_FIXTURE',
                      'VENUE_NATIVE_CONTRACT_FOR_THE_PRICED_TEAM_NOT_FOUND') THEN 'NO_VENUE_COUNTERPART'
     WHEN vn_code IS NOT NULL THEN 'VN_OTHER:' || vn_code
     WHEN first_refusal = 'VENUE_MAPPING_AMBIGUOUS' THEN 'DUPLICATE_CANDIDATES(global)'
     WHEN first_refusal IN ('TEAM_NAMES_COLLIDE_AFTER_NORMALISATION') THEN 'PARTICIPANT_MISMATCH(global)'
     WHEN first_refusal IN ('EVENT_DOES_NOT_NAME_TWO_TEAMS') THEN 'NORMALIZATION_FAILURE(global)'
     WHEN first_refusal IN ('NO_VENUE_CONTRACT_FOR_EVENT', 'NO_VENUE_NATIVE_CONTRACT_IN_PREMAP') THEN 'NO_VENUE_COUNTERPART(global only)'
     WHEN first_refusal IN ('VENUE_MARKET_CLOSED_OR_RESOLVED', 'VENUE_CONTRACT_IS_A_SEGMENT_NOT_FULL_GAME',
                            'VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE') THEN 'GLOBAL_ROW_UNUSABLE_VN_NOT_TRIED:' || first_refusal
     ELSE 'OTHER:' || coalesce(first_refusal, outcome) END AS receipt
    FROM ev)
SELECT sport_key, receipt, coalesce(vn_code, first_refusal, '-') AS raw_code,
       count(*) AS events,
       (array_agg(home || ' v ' || away ORDER BY commence_time))[1:3] AS examples
  FROM cls GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 200;

\echo '== A5 · per provider sport and ET day of commence (last 7 days): unique events seen / ever mapped / ever admitted =='
SELECT sport_key,
       to_char((CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN commence_time::timestamptz END)
               AT TIME ZONE 'America/New_York', 'YYYY-MM-DD') AS et_day,
       count(DISTINCT provider_event_id) AS events_seen,
       count(DISTINCT provider_event_id) FILTER (WHERE mapped_by IS NOT NULL) AS ever_mapped,
       count(DISTINCT provider_event_id) FILTER (WHERE outcome = 'ADMITTED') AS ever_admitted,
       count(DISTINCT provider_event_id) FILTER (WHERE codes ? 'NO_PINNACLE_ON_EVENT') AS ever_no_pinnacle
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '7 days'
   AND (CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN commence_time::timestamptz END)
       BETWEEN now() - interval '7 days' AND now() + interval '2 days'
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 120;

\echo '== A6 · VENUE UNIVERSE (real families), events starting now-6h .. now+7d, by league token and sports_type (winner types first) =='
SELECT split_part(event_slug, '-', 1) AS league, sports_type,
       count(DISTINCT event_slug) AS events, count(*) AS rows,
       count(*) FILTER (WHERE team_id IS NOT NULL) AS rows_with_team_id,
       count(*) FILTER (WHERE team_name IS NOT NULL AND team_name <> '') AS rows_with_team_name,
       count(*) FILTER (WHERE updated_at < now() - interval '5400 seconds') AS rows_not_reseen_90m,
       min(game_start) AS first_start, max(game_start) AS last_start
  FROM us_premap
 WHERE game_start BETWEEN now() - interval '6 hours' AND now() + interval '7 days'
   AND sports_type LIKE '%winner%'
   AND sports_type NOT LIKE 'efootball%' AND sports_type NOT LIKE 'esports%'
   AND sports_type NOT LIKE 'ebasketball%' AND sports_type NOT LIKE 'esoccer%'
 GROUP BY 1, 2 ORDER BY events DESC LIMIT 120;

\echo '== A7 · VENUE canonical team identity per league (winner rows, now-30h .. now+8d): team_id coverage and stability =='
WITH r AS (
  SELECT split_part(event_slug, '-', 1) AS league, team_id, team_name, team_safe_name, side_norm, team_abbr, team_league
    FROM us_premap
   WHERE game_start BETWEEN now() - interval '30 hours' AND now() + interval '8 days'
     AND sports_type IN ('football_team_full_game_winner', 'baseball_team_full_game_winner',
                         'soccer_team_full_time_winner', 'basketball_team_full_game_winner',
                         'hockey_team_full_game_winner', 'tennis_match_winner')
)
SELECT league, count(*) AS rows,
       count(*) FILTER (WHERE team_id IS NOT NULL) AS with_id,
       count(DISTINCT team_id) AS distinct_ids,
       count(DISTINCT team_name) AS distinct_names,
       (SELECT count(*) FROM (SELECT team_id FROM r r2 WHERE r2.league = r.league AND team_id IS NOT NULL
                               GROUP BY team_id HAVING count(DISTINCT team_name) > 1) x) AS ids_with_2plus_names,
       (SELECT count(*) FROM (SELECT team_name FROM r r2 WHERE r2.league = r.league AND team_id IS NOT NULL
                               GROUP BY team_name HAVING count(DISTINCT team_id) > 1) x) AS names_with_2plus_ids,
       count(DISTINCT team_league) AS distinct_team_league,
       (array_agg(DISTINCT team_league))[1:4] AS team_leagues
  FROM r GROUP BY league ORDER BY rows DESC LIMIT 40;

\echo '== A8 · PinnAPI feed heartbeat: scope, census states, unmatched venue events, feed sample =='
SELECT (SELECT value::text FROM ingestion_state WHERE key = 'pinnapi_feed_scope') AS scope,
       value->>'state' AS state,
       left((value->'coverage_census'->'events_by_state')::text, 600) AS events_by_state,
       left((value->'coverage_census'->'states')::text, 600) AS contract_states,
       value->'coverage_census'->'matched_events' AS matched_events,
       left((value->'coverage_census'->'unmatched_event_sample')::text, 2500) AS unmatched_sample,
       left((value->'coverage_census'->'feed_event_sample')::text, 2000) AS feed_sample
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
