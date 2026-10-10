-- Freshness root-cause audit (group freshness), read only.
-- The dedicated market plane (release plane 732cc0c6) and the scorecard's
-- priority_members_fresh 218/265 (0.8226, completion snapshot computed
-- 2026-10-10 09:52:12Z; the plane snapshot at 10:01:45Z reads 223/265).
--   A  which build runs on the plane now and which freshness task exists
--   B  the plane events by kind in the last 36 h (is the window measure written)
--   C  the priority rate per hour since the plane booted (2026-10-09 16:00Z)
--   D  the newest snapshots: counts, sources, read pass, census totals
\echo === A. plane boot records and heartbeats now ===
SELECT service, status,
       round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail ->> 'commit' AS commit_sha,
       to_timestamp((detail ->> 'started_at')::float8) AS started_at,
       detail ? 'freshness_task' AS has_freshness_task
  FROM service_heartbeats
 WHERE service IN ('market_plane', 'universal_market_plane')
 ORDER BY 1;
\echo === B. plane events by kind, last 36 h ===
SELECT kind, count(*) AS n, min(at) AS first_at, max(at) AS last_at
  FROM market_plane_events
 WHERE at > now() - interval '36 hours'
 GROUP BY 1 ORDER BY 2 DESC;
\echo === C. priority rate per hour since 2026-10-09 16:00Z (SNAPSHOT events) ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,current_pmx_stream}')::int AS pmx,
         (payload #>> '{freshness,priority_universe,current_rest_fallback}')::int AS rest,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         (payload #>> '{freshness,priority_universe,rate}')::float8 AS rate
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at >= timestamptz '2026-10-09 16:00:00+00')
SELECT date_trunc('hour', at) AS hr, count(*) AS snaps,
       round(avg(den)::numeric, 1) AS avg_den,
       round(avg(pmx)::numeric, 1) AS avg_pmx,
       round(avg(rest)::numeric, 1) AS avg_rest,
       round(avg(nc)::numeric, 1) AS avg_nc,
       round(min(rate)::numeric, 4) AS min_rate,
       round(avg(rate)::numeric, 4) AS avg_rate,
       round(max(rate)::numeric, 4) AS max_rate,
       count(*) FILTER (WHERE rate >= 0.95) AS snaps_ge_95
  FROM s GROUP BY 1 ORDER BY 1;
\echo === D. newest 3 snapshots: counts, read pass and census totals ===
SELECT at,
       to_timestamp((payload ->> 'computed_at')::float8) AS computed_at,
       (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
       (payload #>> '{freshness,priority_universe,current_pmx_stream}')::int AS pmx,
       (payload #>> '{freshness,priority_universe,current_rest_fallback}')::int AS rest,
       (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
       payload #>> '{freshness,priority_universe,rate}' AS rate,
       payload #> '{freshness,priority_universe,census,by_refresh_outcome}' AS read_outcomes,
       payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}' AS by_reason,
       payload #> '{freshness,priority_universe,active_refresh,last_pass}' AS last_pass,
       payload #> '{freshness,priority_universe,active_refresh,totals}' AS read_totals,
       payload #> '{freshness,priority_universe,active_refresh,budget}' AS budget
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT'
 ORDER BY at DESC LIMIT 3;
