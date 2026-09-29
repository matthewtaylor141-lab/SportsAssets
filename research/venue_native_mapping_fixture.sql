-- READ-ONLY. A VALIDATION FIXTURE FOR A VENUE-NATIVE ENTRY MAPPING.
--
-- The entry lane maps an odds-provider event through the GLOBAL catalogue.
-- A repair that maps it straight to the US venue's own catalogue must be
-- validated against the fixtures that actually occur: the provider events
-- the lane saw, and the venue's winner contracts for the same families and
-- dates. Both sets are printed one JSON object per line so they can be read
-- back verbatim; nothing is summarised or inferred here.
--
-- No balance, cash, credential or account row is selected.

\echo '== PROVIDER_EVENTS_BEGIN =='
SELECT row_to_json(e) FROM (
  SELECT DISTINCT ON (provider_event_id)
         provider_event_id, sport_key, family, home, away, commence_time,
         first_refusal, us_market_slug, global_slug
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '30 hours'
   ORDER BY provider_event_id, cycle_at DESC
) e;
\echo '== PROVIDER_EVENTS_END =='

\echo '== VENUE_WINNER_ROWS_BEGIN =='
SELECT row_to_json(p) FROM (
  SELECT market_slug, intent, event_slug, event_title, question, kind,
         sports_type, team_abbr, team_name, side_norm, line, signed,
         game_start, updated_at
    FROM us_premap
   WHERE game_start > now() - interval '12 hours'
     AND game_start <= now() + interval '120 hours'
     AND updated_at > now() - interval '65 minutes'
     AND (sports_type LIKE 'baseball\_%' OR sports_type LIKE 'soccer\_%')
     AND sports_type ~ '(_full_game_winner|_full_time_winner)$'
   ORDER BY game_start, event_slug, market_slug, intent
) p;
\echo '== VENUE_WINNER_ROWS_END =='
