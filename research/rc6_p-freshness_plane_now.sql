-- RC6.2 lane p-freshness, read only. The market plane was deployed again at
-- 2026-10-09 15:58:22-15:59:44Z (Render deploy dep-db4gu7jbc2fs73boqcu0)
-- and the frozen window of 16:00Z holds only 2 samples (research-sql
-- 37965994147). What runs on the plane now, and is the window measure
-- still written?
--   N1  the running commit per loop (runtime_loop_health)
--   N2  the newest event per kind the freshness measure reads
--   N3  the snapshots since 15:55Z: the priority counts and the census
\echo === N1. running commit per loop ===
SELECT process, commit_sha, count(*) AS loops, max(last_success_at) AS newest_success
  FROM runtime_loop_health GROUP BY 1, 2 ORDER BY 1, 2;
\echo === N2. newest freshness events by kind ===
SELECT kind, count(*) FILTER (WHERE at > now() - interval '90 minutes') AS last_90m,
       max(at) AS newest
  FROM market_plane_events
 WHERE kind IN ('FRESHNESS_WINDOW', 'FRESHNESS_SAMPLE', 'SNAPSHOT')
   AND at > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 1;
\echo === N3. snapshots since 15:55Z ===
SELECT at,
       (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
       (payload #>> '{freshness,priority_universe,current_pmx_stream}')::int AS pmx,
       (payload #>> '{freshness,priority_universe,current_rest_fallback}')::int AS rest,
       (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
       payload #>> '{freshness,priority_universe,rate}' AS rate,
       (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc,
       payload #>> '{freshness,priority_universe,census,by_refresh_outcome}' AS census_read_outcomes,
       (payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int AS reads_total,
       payload #> '{freshness_task}' IS NOT NULL AS has_task_digest
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 15:55:00+00'
 ORDER BY at;
