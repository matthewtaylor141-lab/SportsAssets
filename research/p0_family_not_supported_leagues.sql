-- P0 coverage (2026-10-06): WHICH VENUE LEAGUES are the PinnAPI-native
-- basketball / hockey seeds refused VENUE_NATIVE_FAMILY_NOT_SUPPORTED in the
-- last 48 h? Read-only. Each refused event is listed with every venue
-- basketball / hockey full-game winner event starting within 90 minutes of
-- it (the resolver's own tolerance), so the event's league token is read
-- off the venue's own slug and title -- never inferred from the names.

\echo '== F1: refused events (VENUE_NATIVE_FAMILY_NOT_SUPPORTED, last 48 h)'
SELECT DISTINCT ON (provider_event_id)
       provider_event_id, sport_key, family, home, away, commence_time
  FROM ext_candidate_outcomes
 WHERE first_refusal = 'VENUE_NATIVE_FAMILY_NOT_SUPPORTED'
   AND cycle_at >= now() - interval '48 hours'
 ORDER BY provider_event_id, cycle_at DESC;

\echo '== F2: each refused event against the venue winner events within 90 min'
WITH refused AS (
  SELECT DISTINCT ON (provider_event_id)
         provider_event_id, family, home, away,
         (commence_time)::timestamptz AS t0
    FROM ext_candidate_outcomes
   WHERE first_refusal = 'VENUE_NATIVE_FAMILY_NOT_SUPPORTED'
     AND cycle_at >= now() - interval '48 hours'
     AND commence_time IS NOT NULL
   ORDER BY provider_event_id, cycle_at DESC
), venue AS (
  SELECT event_slug, min(event_title) AS event_title,
         min(sports_type) AS sports_type, min(game_start) AS game_start,
         string_agg(DISTINCT coalesce(team_league, '?'), ',') AS leagues,
         string_agg(DISTINCT coalesce(team_name, '?'), ' | ') AS teams,
         string_agg(DISTINCT coalesce(team_safe_name, '?'), ' | ') AS safe
    FROM us_premap
   WHERE sports_type IN ('basketball_team_full_game_winner',
                         'hockey_team_full_game_winner')
     AND game_start >= now() - interval '72 hours'
   GROUP BY event_slug
)
SELECT r.provider_event_id, r.family, r.home, r.away, r.t0,
       split_part(v.event_slug, '-', 1) AS venue_league, v.event_slug,
       v.event_title, v.leagues, v.teams, v.safe
  FROM refused r
  JOIN venue v
    ON abs(extract(epoch FROM v.game_start - r.t0)) <= 5400
   AND v.sports_type = (CASE r.family WHEN 'basketball'
                        THEN 'basketball_team_full_game_winner'
                        ELSE 'hockey_team_full_game_winner' END)
   AND (lower(v.teams) LIKE '%' || lower(split_part(r.home, ' ', 1)) || '%'
        OR lower(v.teams) LIKE '%' || lower(split_part(r.away, ' ', 1)) || '%'
        OR lower(v.safe) LIKE '%' || lower(split_part(r.home, ' ', 1)) || '%'
        OR lower(v.safe) LIKE '%' || lower(split_part(r.away, ' ', 1)) || '%'
        OR lower(v.event_title) LIKE '%' || lower(split_part(r.home, ' ', 1)) || '%')
 ORDER BY r.family, venue_league, r.t0;

\echo '== F3: venue basketball / hockey winner events by league, last 72 h .. next 7 d'
SELECT split_part(event_slug, '-', 1) AS venue_league, sports_type,
       count(DISTINCT event_slug) AS events, min(event_title) AS sample_title,
       min(event_slug) AS sample_slug
  FROM us_premap
 WHERE sports_type IN ('basketball_team_full_game_winner',
                       'hockey_team_full_game_winner')
   AND game_start >= now() - interval '72 hours'
   AND game_start <= now() + interval '7 days'
 GROUP BY 1, 2
 ORDER BY 2, 3 DESC;
