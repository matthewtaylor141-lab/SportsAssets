-- RC6.2 lane p-freshness, read only. The plane's own cadence on
-- 2026-10-09 14:40-16:15Z: SNAPSHOT events (the pass; the scorecard's
-- instant and the census) and FRESHNESS_SAMPLE events (the freshness task)
-- per 5 minutes, with the census completeness of each snapshot.
\echo === C1. events per 5 minutes ===
SELECT to_timestamp(floor(extract(epoch FROM at) / 300) * 300) AS bucket,
       count(*) FILTER (WHERE kind = 'SNAPSHOT') AS snapshots,
       count(*) FILTER (WHERE kind = 'FRESHNESS_SAMPLE') AS samples,
       max(at) FILTER (WHERE kind = 'SNAPSHOT') AS newest_snapshot
  FROM market_plane_events
 WHERE kind IN ('SNAPSHOT', 'FRESHNESS_SAMPLE')
   AND at >= timestamptz '2026-10-09 14:40:00+00' AND at < timestamptz '2026-10-09 16:15:00+00'
 GROUP BY 1 ORDER BY 1;
\echo === C2. snapshots 15:15-16:10Z: census size and completeness ===
SELECT at,
       (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
       (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
       (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc,
       jsonb_array_length(coalesce(payload #> '{freshness,priority_universe,census,sample}', '[]'::jsonb)) AS census_named
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT'
   AND at >= timestamptz '2026-10-09 15:15:00+00' AND at < timestamptz '2026-10-09 16:10:00+00'
 ORDER BY at;
