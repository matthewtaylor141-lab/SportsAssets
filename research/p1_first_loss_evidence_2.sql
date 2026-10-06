-- READ-ONLY. P1 first-loss evidence, second read.
--
--   1. every metered NCAAF / MLS / Serie B / NFL / MLB name of the last 14 d
--      next to every PinnAPI-native seed name of the same sport key (the
--      native names ARE the feed fixture names), so a closed alias table is
--      built from the two sources' own renderings
--   2. the last cycle heartbeat's freshness / step-timing blocks (where our
--      own ~17 s before queue position 2 goes)
--   3. a sample of pinnapi_reactive_attempts detail (what a refused reactive
--      registration names)
--
-- No writes. Every statement is a SELECT.

\echo == 1a. metered names by sport key (14 d) ==
SELECT sport_key, name, count(*) AS rows
  FROM (SELECT sport_key, home AS name FROM ext_candidate_outcomes
         WHERE cycle_at >= now() - interval '14 days'
           AND provider_event_id NOT LIKE 'pinnapi:%'
           AND sport_key IN ('americanfootball_ncaaf', 'soccer_usa_mls',
                             'soccer_brazil_serie_b', 'americanfootball_nfl',
                             'basketball_wnba')
        UNION ALL
        SELECT sport_key, away FROM ext_candidate_outcomes
         WHERE cycle_at >= now() - interval '14 days'
           AND provider_event_id NOT LIKE 'pinnapi:%'
           AND sport_key IN ('americanfootball_ncaaf', 'soccer_usa_mls',
                             'soccer_brazil_serie_b', 'americanfootball_nfl',
                             'basketball_wnba')) t
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 900;

\echo == 1b. PinnAPI-native (feed) names by sport key (14 d) ==
SELECT sport_key, name, count(*) AS rows
  FROM (SELECT sport_key, home AS name FROM ext_candidate_outcomes
         WHERE cycle_at >= now() - interval '14 days'
           AND provider_event_id LIKE 'pinnapi:%'
        UNION ALL
        SELECT sport_key, away FROM ext_candidate_outcomes
         WHERE cycle_at >= now() - interval '14 days'
           AND provider_event_id LIKE 'pinnapi:%') t
 WHERE sport_key IN ('americanfootball_ncaaf', 'soccer_usa_mls',
                     'soccer_brazil_serie_b', 'americanfootball_nfl',
                     'basketball_wnba', 'pinnapi_football',
                     'pinnapi_basketball')
 GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 900;

\echo == 1c. metered vs native pairs at the same start (14 d) ==
WITH m AS (
    SELECT DISTINCT sport_key, home, away, commence_time
      FROM ext_candidate_outcomes
     WHERE cycle_at >= now() - interval '14 days'
       AND provider_event_id NOT LIKE 'pinnapi:%'),
n AS (
    SELECT DISTINCT sport_key, home, away, commence_time
      FROM ext_candidate_outcomes
     WHERE cycle_at >= now() - interval '14 days'
       AND provider_event_id LIKE 'pinnapi:%')
SELECT m.sport_key, m.commence_time, m.home AS m_home, m.away AS m_away,
       n.home AS feed_home, n.away AS feed_away
  FROM m JOIN n ON n.sport_key = m.sport_key
   AND n.commence_time = m.commence_time
   AND (lower(m.home) <> lower(n.home) OR lower(m.away) <> lower(n.away))
   AND (lower(m.home) LIKE lower(split_part(n.home, ' ', 1)) || '%'
        OR lower(m.away) LIKE lower(split_part(n.away, ' ', 1)) || '%'
        OR lower(m.home) LIKE lower(split_part(n.away, ' ', 1)) || '%')
 ORDER BY 1, 2, 3 LIMIT 400;

\echo == 2. last cycle heartbeat: freshness, odds_freshness, step timings ==
SELECT key,
       jsonb_pretty(value -> 'freshness') AS freshness
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

SELECT key,
       value -> 'odds_freshness' AS odds_freshness,
       value -> 'step_rss' AS step_rss,
       value -> 'steps' AS steps,
       value -> 'elapsed_s' AS elapsed_s,
       value -> 'step_seconds' AS step_seconds,
       value -> 'timing' AS timing
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

SELECT jsonb_object_keys(value) AS heartbeat_key
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

SELECT left((value -> 'venue_errors')::text, 3000) AS venue_errors
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo == 3. pinnapi_reactive_attempts sample (24 h) ==
SELECT state, count(*) AS n FROM pinnapi_reactive_attempts
 WHERE created_at >= now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;

SELECT created_at, state, left(detail::text, 900) AS detail
  FROM pinnapi_reactive_attempts
 WHERE created_at >= now() - interval '24 hours'
 ORDER BY created_at DESC LIMIT 12;
