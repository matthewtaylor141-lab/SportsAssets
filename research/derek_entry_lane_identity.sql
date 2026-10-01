-- READ-ONLY. Venue-native names beside the provider names for the events refused
-- VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM (follow-up to derek_entry_lane_investigation.sql).
-- I1 venue soccer winner events within 90 min of each refused provider fixture, with every participant name

\echo '== I1 venue events within 90 min of each identity-refused fixture =='
WITH r AS (
  SELECT DISTINCT sport_key, home, away, commence_time::timestamptz AS ct
    FROM ext_candidate_outcomes
   WHERE codes::text LIKE '%VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM%')
SELECT r.sport_key, r.home, r.away, r.ct, p.event_slug,
       string_agg(DISTINCT p.team_name, ' | ') AS venue_team_names,
       string_agg(DISTINCT p.team_abbr, ',') AS venue_abbrs,
       min(p.game_start) AS venue_start
  FROM r JOIN us_premap p
    ON p.sports_type = 'soccer_team_full_time_winner'
   AND p.game_start BETWEEN r.ct - interval '90 minutes' AND r.ct + interval '90 minutes'
 GROUP BY 1, 2, 3, 4, 5
HAVING bool_or(lower(p.team_name) LIKE '%' || lower(split_part(r.home, ' ', 1)) || '%'
            OR lower(p.team_name) LIKE '%' || lower(split_part(r.away, ' ', 1)) || '%'
            OR lower(p.team_name) LIKE '%' || lower(split_part(r.home, ' ', 2)) || '%' AND split_part(r.home, ' ', 2) <> ''
            OR lower(p.team_name) LIKE '%' || lower(split_part(r.away, ' ', 2)) || '%' AND split_part(r.away, ' ', 2) <> '')
 ORDER BY r.ct, r.home LIMIT 60;
