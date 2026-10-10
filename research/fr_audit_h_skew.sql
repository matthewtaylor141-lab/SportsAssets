-- Freshness root-cause audit (group freshness), read only.
--   H1  priority_universe.external_unavailable over every SNAPSHOT since the
--       732cc0c6 boot (is the venue-terminal state ever counted external)
--   H2  the measurement instants of one SNAPSHOT: the coverage pass that
--       counted the numerator vs the census taken at the snapshot, and the
--       difference between the two not-current counts
\echo === H1. external_unavailable in the priority tier, all snapshots since 2026-10-09 03:26:48Z ===
SELECT count(*) AS snapshots,
       max((payload #>> '{freshness,priority_universe,external_unavailable}')::int) AS max_external,
       sum((payload #>> '{freshness,priority_universe,external_unavailable}')::int) AS sum_external,
       max((payload #>> '{coverage,freshness_tiers,PRIORITY,EXTERNAL_DATA_UNAVAILABLE}')::int) AS max_external_in_coverage
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00';
\echo === H2. coverage instant vs snapshot instant, and pass not_current vs census not_current ===
WITH s AS (
  SELECT at,
         extract(epoch FROM at) - (payload #>> '{coverage,computed_at}')::float8 AS cov_age_s,
         extract(epoch FROM at) - (payload ->> 'computed_at')::float8 AS snap_lag_s,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         (payload #>> '{freshness,priority_universe,census,not_current}')::int AS cnc,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 16:05:00+00')
SELECT count(*) AS snapshots,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY cov_age_s)::numeric, 1) AS coverage_age_p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY cov_age_s)::numeric, 1) AS coverage_age_p90_s,
       round(max(cov_age_s)::numeric, 1) AS coverage_age_max_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY snap_lag_s)::numeric, 1) AS computed_to_event_p50_s,
       round(avg(cnc - nc)::numeric, 2) AS avg_census_minus_pass_nc,
       count(*) FILTER (WHERE cnc > nc) AS census_more_not_current,
       count(*) FILTER (WHERE cnc < nc) AS census_fewer_not_current,
       count(*) FILTER (WHERE cnc = nc) AS equal,
       round(avg(abs(cnc - nc))::numeric, 2) AS avg_abs_diff,
       max(abs(cnc - nc)) AS max_abs_diff
  FROM s WHERE cnc IS NOT NULL AND nc IS NOT NULL;
