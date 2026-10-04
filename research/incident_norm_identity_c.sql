-- P0 INCIDENT (2026-10-04) · segment: NORMALIZATION + EVENT IDENTITY, third read.
-- READ-ONLY, bounded. (1) which Pinnacle source each valuation used and why the
-- PinnAPI event identity fell back; (2) the venue's full college-football team
-- identity table (team_id is the canonical key); (3) the provider events whose
-- global-catalogue refusal blocked the venue-native path; (4) every distinct
-- provider participant name the collector has seen, per sport.

\echo '== C0 · read instant =='
SELECT now() AS read_at;

\echo '== C1 · VALUATIONS 72 h: Pinnacle source and PinnAPI identity fallback reason, per family =='
SELECT sport_family,
       coalesce(settlement_comparison->'reference_input'->>'provider', provider) AS ref_provider,
       coalesce(settlement_comparison->'reference_input'->>'fallback_reason', '-') AS fallback_reason,
       count(*) AS rows, count(DISTINCT event_key) AS events,
       count(DISTINCT contract_selection) AS contracts,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM external_valuations
 WHERE decided_at > now() - interval '72 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 60;

\echo '== C2 · VENUE college-football team identity table (every us_premap row with team_league cfb, any market type) =='
SELECT team_id, team_name, coalesce(team_safe_name, '') AS safe_name,
       string_agg(DISTINCT coalesce(side_norm, ''), '/') AS side_norms,
       string_agg(DISTINCT coalesce(team_abbr, ''), '/') AS abbrs,
       count(*) AS rows, max(game_start) AS last_game
  FROM us_premap
 WHERE team_league = 'cfb' AND team_id IS NOT NULL
 GROUP BY team_id, team_name, team_safe_name
 ORDER BY team_name LIMIT 700;

\echo '== C3 · events whose GLOBAL-catalogue refusal is not one the venue-native path may replace (72 h, distinct events) =='
SELECT sport_key, home, away, commence_time, first_refusal, codes::text AS codes,
       count(*) AS rows, min(cycle_at) AS first_row, max(cycle_at) AS last_row
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '72 hours'
   AND first_refusal IN ('VENUE_CONTRACT_IS_A_SEGMENT_NOT_FULL_GAME', 'VENUE_MARKET_CLOSED_OR_RESOLVED',
                         'TEAM_NAMES_COLLIDE_AFTER_NORMALISATION', 'EVENT_DOES_NOT_NAME_TWO_TEAMS')
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY rows DESC LIMIT 40;

\echo '== C4 · every distinct provider participant name per sport (ledger, all retained rows, bounded) =='
SELECT sport_key, name, count(*) AS rows, min(cycle_at) AS first_seen, max(cycle_at) AS last_seen,
       bool_or(mapped_by IS NOT NULL) AS ever_mapped
  FROM (SELECT sport_key, home AS name, cycle_at, mapped_by FROM ext_candidate_outcomes
         WHERE cycle_at > now() - interval '21 days'
        UNION ALL
        SELECT sport_key, away, cycle_at, mapped_by FROM ext_candidate_outcomes
         WHERE cycle_at > now() - interval '21 days') x
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 900;

\echo '== C5 · venue-native identity refusals over 21 days, per sport and precise code (distinct events) =='
SELECT sport_key,
       (SELECT c FROM jsonb_array_elements_text(codes) WITH ORDINALITY t(c, i)
         WHERE c LIKE 'VENUE_NATIVE%' OR c = 'NO_VENUE_NATIVE_EVENT_FOR_FIXTURE'
         ORDER BY i LIMIT 1) AS vn_code,
       count(*) AS rows, count(DISTINCT provider_event_id) AS events,
       (array_agg(DISTINCT home || ' v ' || away))[1:8] AS examples
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '21 days'
   AND (codes::text LIKE '%VENUE_NATIVE%')
 GROUP BY 1, 2 ORDER BY 1, 4 DESC LIMIT 80;

\echo '== C6 · per sport over 21 days: distinct events seen / ever priced / ever mapped (the identity success rate on priced events) =='
SELECT sport_key,
       count(DISTINCT provider_event_id) AS events_seen,
       count(DISTINCT provider_event_id) FILTER (WHERE NOT (codes ? 'NO_PINNACLE_ON_EVENT')
                                                   AND outcome <> 'DEFERRED') AS events_ever_priced,
       count(DISTINCT provider_event_id) FILTER (WHERE mapped_by IS NOT NULL) AS events_ever_mapped,
       count(DISTINCT provider_event_id) FILTER (WHERE mapped_by = 'GLOBAL_CATALOGUE') AS by_global,
       count(DISTINCT provider_event_id) FILTER (WHERE mapped_by = 'VENUE_NATIVE') AS by_venue_native,
       min(cycle_at) AS first_row
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '21 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;
