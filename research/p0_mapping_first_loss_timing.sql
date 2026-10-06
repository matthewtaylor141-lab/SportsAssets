-- P0 coverage (2026-10-06): WHEN each remaining mapping refusal was last
-- written, which build served it, and the feed's own renderings of the two
-- primary-fixture refusals where a heartbeat recorded them. Read-only.

\echo '== T1: first / last write of each refusal, last 48 h'
SELECT first_refusal, home, away, count(*) AS rows,
       min(cycle_at) AS first_at, max(cycle_at) AS last_at,
       string_agg(DISTINCT coalesce(writer, '?'), ',') AS writers
  FROM ext_candidate_outcomes
 WHERE (first_refusal IN ('VENUE_NATIVE_NICKNAME_DOES_NOT_CONFIRM_THE_TEAM',
                          'VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM',
                          'PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS')
        OR (first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE'
            AND home LIKE 'Bosnia%'))
   AND cycle_at >= now() - interval '48 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2;

\echo '== T2: heartbeat services, their build fields'
SELECT service, beat_at,
       detail->>'build' AS build, detail->>'release_sha' AS release_sha,
       detail->>'git_sha' AS git_sha, detail->>'version' AS version
  FROM service_heartbeats
 ORDER BY beat_at DESC LIMIT 20;

\echo '== T3: heartbeat text naming Riestra / Bosnia (feed renderings)'
SELECT service, beat_at,
       substring(detail::text FROM position('Riestra' IN detail::text) - 300
                 FOR 700) AS riestra,
       substring(detail::text FROM position('Bosnia' IN detail::text) - 300
                 FOR 700) AS bosnia
  FROM service_heartbeats
 WHERE detail::text LIKE '%Riestra%' OR detail::text LIKE '%Bosnia%'
 LIMIT 6;

\echo '== T4: reactive attempts naming Riestra / Bosnia'
SELECT event_id, created_at, state,
       substring(detail::text FOR 900) AS detail
  FROM pinnapi_reactive_attempts
 WHERE created_at >= now() - interval '72 hours'
   AND (detail::text LIKE '%Riestra%' OR detail::text LIKE '%Bosnia%'
        OR event_id IN ('pinnapi:1637743785'))
 ORDER BY created_at DESC LIMIT 8;
