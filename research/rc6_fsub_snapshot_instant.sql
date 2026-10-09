-- RC6 lane D1 (freshness), read only. How far the plane's SNAPSHOT is from
-- the instants it names: the payload's computed_at (the pass's instant
-- before coverage and certification) against the event's own insert time,
-- and the coverage pass's verification instant (payload coverage.computed_at)
-- against the snapshot's. Over 24 h, and the newest 20.
\echo === A. snapshot insert time minus payload computed_at, 24 h (seconds) ===
WITH s AS (
  SELECT at, extract(epoch FROM at) - (payload ->> 'computed_at')::float8 AS lag,
         (payload ->> 'computed_at')::float8
           - (payload #>> '{coverage,computed_at}')::float8 AS cov_lag
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours')
SELECT count(*) AS snapshots,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY lag)::numeric, 1) AS insert_lag_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY lag)::numeric, 1) AS insert_lag_p90,
       round(max(lag)::numeric, 1) AS insert_lag_max,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY cov_lag)::numeric, 1) AS coverage_to_snapshot_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY cov_lag)::numeric, 1) AS coverage_to_snapshot_p90,
       round(max(cov_lag)::numeric, 1) AS coverage_to_snapshot_max
  FROM s;
\echo === B. the newest 20 snapshots ===
SELECT at, round((extract(epoch FROM at) - (payload ->> 'computed_at')::float8)::numeric, 1) AS insert_lag_s,
       round(((payload ->> 'computed_at')::float8
              - (payload #>> '{coverage,computed_at}')::float8)::numeric, 1) AS coverage_age_at_snapshot_s,
       (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
       (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc,
       (payload #>> '{freshness,priority_universe,not_current}')::int AS tiers_nc
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT' ORDER BY at DESC LIMIT 20;
\echo === C. the plane heartbeats now ===
SELECT service, status, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail -> 'memory' -> 'by_step' ? 'coverage' AS has_coverage_step
  FROM service_heartbeats
 WHERE service IN ('universal_market_plane', 'market_plane', 'institutional_md');
