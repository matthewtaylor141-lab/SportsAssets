-- P0 INCIDENT (2026-10-04) · segment: NORMALIZATION + EVENT IDENTITY. READ-ONLY.
-- Raw provider-vs-venue participant dumps so the production matcher
-- (bettor_venue_native_identity.match_event) can be replayed offline, refusal by
-- refusal, and each proposed fix measured on the same rows. Bounded by windows
-- and LIMITs.

\echo '== B0 · read instant =='
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et;

\echo '== B1 · VENUE football winner rows (cfb + nfl), game_start 2026-10-03 12:00Z .. now+8d, one line per row =='
SELECT event_slug,
       to_char(game_start AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS gs,
       market_slug, replace(coalesce(intent, ''), 'ORDER_INTENT_BUY_', '') AS it,
       coalesce(team_name, '') AS tn, coalesce(team_safe_name, '') AS tsn,
       coalesce(side_norm, '') AS sn, coalesce(team_abbr, '') AS ab,
       coalesce(team_id::text, '') AS tid, coalesce(team_league, '') AS tl,
       coalesce(kind, '') AS kind, sports_type,
       round(extract(epoch FROM now() - updated_at))::int AS age_s,
       left(coalesce(event_title, ''), 70) AS title
  FROM us_premap
 WHERE sports_type = 'football_team_full_game_winner'
   AND split_part(event_slug, '-', 1) IN ('cfb', 'nfl')
   AND game_start >= timestamptz '2026-10-03 12:00Z'
   AND game_start <= now() + interval '8 days'
 ORDER BY game_start, event_slug, intent LIMIT 900;

\echo '== B2 · PROVIDER football events (ncaaf + nfl) since 2026-10-03 06:00Z: last pre-start receipt per event =='
WITH r AS (
  SELECT o.*, CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
                   THEN commence_time::timestamptz END AS ct
    FROM ext_candidate_outcomes o
   WHERE cycle_at > timestamptz '2026-10-03 06:00Z'
     AND sport_key IN ('americanfootball_ncaaf', 'americanfootball_nfl')
), n AS (
  SELECT sport_key, provider_event_id, count(*) AS n_rows,
         count(*) FILTER (WHERE mapped_by IS NOT NULL) AS n_mapped,
         min(cycle_at) AS first_seen
    FROM r GROUP BY 1, 2
), last AS (
  SELECT DISTINCT ON (sport_key, provider_event_id) *
    FROM r WHERE ct IS NULL OR cycle_at <= ct
   ORDER BY sport_key, provider_event_id, cycle_at DESC
)
SELECT l.sport_key, l.provider_event_id AS pid, l.home, l.away,
       to_char(l.ct AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') AS ct,
       to_char(l.cycle_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS last_cycle,
       n.n_rows, n.n_mapped, l.outcome, coalesce(l.first_refusal, '-') AS first_refusal,
       left(l.codes::text, 260) AS codes, coalesce(l.mapped_by, '-') AS mapped_by,
       coalesce(l.us_market_slug, '-') AS us_slug
  FROM last l JOIN n USING (sport_key, provider_event_id)
 ORDER BY l.sport_key, l.ct, l.home LIMIT 700;

\echo '== B3 · PROVIDER baseball + soccer events, last 36 h: last pre-start receipt per event =='
WITH r AS (
  SELECT o.*, CASE WHEN commence_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
                   THEN commence_time::timestamptz END AS ct
    FROM ext_candidate_outcomes o
   WHERE cycle_at > now() - interval '36 hours'
     AND sport_key NOT LIKE 'americanfootball%'
), n AS (
  SELECT sport_key, provider_event_id, count(*) AS n_rows,
         count(*) FILTER (WHERE mapped_by IS NOT NULL) AS n_mapped
    FROM r GROUP BY 1, 2
), last AS (
  SELECT DISTINCT ON (sport_key, provider_event_id) *
    FROM r WHERE ct IS NULL OR cycle_at <= ct
   ORDER BY sport_key, provider_event_id, cycle_at DESC
)
SELECT l.sport_key, l.provider_event_id AS pid, l.home, l.away,
       to_char(l.ct AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') AS ct,
       n.n_rows, n.n_mapped, l.outcome, coalesce(l.first_refusal, '-') AS first_refusal,
       left(l.codes::text, 220) AS codes, coalesce(l.mapped_by, '-') AS mapped_by,
       coalesce(l.us_market_slug, '-') AS us_slug
  FROM last l JOIN n USING (sport_key, provider_event_id)
 ORDER BY l.sport_key, l.ct, l.home LIMIT 400;

\echo '== B4 · VENUE baseball + soccer winner events in the lane-mapped leagues, game_start now-36h .. now+8d (one line per event) =='
SELECT event_slug,
       to_char(min(game_start) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') AS gs,
       count(DISTINCT game_start) AS n_starts, min(sports_type) AS sports_type,
       string_agg(replace(coalesce(intent, ''), 'ORDER_INTENT_BUY_', '') || '~' || coalesce(team_name, '')
                  || '~' || coalesce(side_norm, '') || '~' || coalesce(team_abbr, '') || '~'
                  || coalesce(team_id::text, ''), ' ; ' ORDER BY market_slug, intent) AS sides,
       max(round(extract(epoch FROM now() - updated_at)))::int AS max_age_s,
       left(min(event_title), 60) AS title
  FROM us_premap
 WHERE sports_type IN ('baseball_team_full_game_winner', 'soccer_team_full_time_winner')
   AND split_part(event_slug, '-', 1) IN ('mlb', 'unl', 'mls', 'lmx', 'uwcl', 'cnl', 'uslc',
                                          'arg2', 'brb', 'lco', 'uru1', 'nwsl')
   AND game_start BETWEEN now() - interval '36 hours' AND now() + interval '8 days'
 GROUP BY event_slug ORDER BY min(game_start) LIMIT 400;

\echo '== B5 · VENUE winner events in sports the lane never requests (basketball / hockey / tennis), sample with structured names =='
SELECT split_part(event_slug, '-', 1) AS league, event_slug, sports_type,
       to_char(min(game_start) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"') AS gs,
       string_agg(replace(coalesce(intent, ''), 'ORDER_INTENT_BUY_', '') || '~' || coalesce(team_name, '')
                  || '~' || coalesce(side_norm, '') || '~' || coalesce(team_id::text, ''), ' ; '
                  ORDER BY intent) AS sides
  FROM us_premap
 WHERE (sports_type LIKE 'basketball%winner' OR sports_type LIKE 'hockey%winner'
        OR sports_type LIKE 'tennis%winner')
   AND game_start BETWEEN now() - interval '2 hours' AND now() + interval '3 days'
 GROUP BY 1, 2, 3 ORDER BY min(game_start) LIMIT 40;

\echo '== B6 · football winner rows the venue-native read cannot see: future start, not re-seen within 5400 s =='
SELECT split_part(event_slug, '-', 1) AS league,
       count(*) AS future_rows,
       count(*) FILTER (WHERE updated_at < now() - interval '5400 seconds') AS stale_rows,
       count(DISTINCT event_slug) FILTER (WHERE updated_at < now() - interval '5400 seconds') AS stale_events,
       max(round(extract(epoch FROM now() - updated_at)))::int AS max_age_s,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY round(extract(epoch FROM now() - updated_at))) AS p50_age_s
  FROM us_premap
 WHERE sports_type IN ('football_team_full_game_winner', 'baseball_team_full_game_winner',
                       'soccer_team_full_time_winner')
   AND game_start > now()
 GROUP BY 1 ORDER BY future_rows DESC LIMIT 30;
