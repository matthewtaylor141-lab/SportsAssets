-- RC6.2 lane p-freshness (data freshness), read only. Follows
-- rc6_p-freshness_projection.sql (P2 / P2b: what the RC6.1 REST refresh and
-- the snapshot-only read would make current at every snapshot since the
-- 732cc0c6 plane booted).
--   A   which plane build runs now (boot record / heartbeat): has the RC6.1
--       freshness task started (freshness_task in the heartbeat)?
--   R1  the same projection with the RC6.2 read plan: a market the venue
--       says is not open is read only after every member that may be
--       current (and one whose read states a TERMINAL state is not re-read),
--       so its 900 s re-reads no longer come out of the 57 members the
--       budget holds while it binds. RC6.1: cap = 57 - 0.317 x NOT_OPEN
--       (12 reads a minute x 285 s, less the 900 s re-reads of not-open
--       markets); RC6.2: cap = 57. Everything else is P2's formula:
--         nc = least(nc_actual, T + not_held + max(0, open_q - cap))
--       with T the members the venue's own word keeps not current.
--   R2  per hour, the same three columns.
\echo === A. the plane's heartbeat now (which build runs) ===
SELECT service, status,
       round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail ->> 'commit' AS commit_sha,
       to_timestamp((detail ->> 'started_at')::float8) AS started_at,
       detail ? 'freshness_task' AS has_freshness_task,
       detail #>> '{freshness_task,ticks}' AS task_ticks,
       detail #>> '{freshness_task,reads}' AS task_reads,
       detail #>> '{freshness_task,errors}' AS task_errors
  FROM service_heartbeats
 WHERE service IN ('market_plane', 'universal_market_plane')
 ORDER BY 1;
\echo === R1. all snapshots since the 732cc0c6 boot: RC6.1 vs RC6.2 read plan (REST only) and with the snapshot read ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS e(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS e(k, v)
           WHERE k LIKE '%|STREAM:MARKET_NOT_OPEN|%' AND k NOT LIKE '%|STARTED_GT_4H') AS stream_not_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (
  SELECT at, den, nc, q, no_open, not_held,
         greatest(no_open, gt4h + stream_not_open) AS t,
         greatest(q - no_open, 0) AS open_q
    FROM s),
r AS (
  SELECT at, den, nc, t, open_q, no_open,
         least(nc, t + not_held + greatest(open_q - (57 - 0.317 * no_open), 0)) AS nc_rc61,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc_rc62,
         least(nc, t + not_held) AS nc_snap
    FROM p)
SELECT count(*) AS snaps, max(at) AS last_snapshot, sum(den) AS member_snapshots,
       sum(den - nc) AS fresh_actual,
       round(sum(den - nc_rc61), 1) AS fresh_rc61_rest,
       round(sum(den - nc_rc62), 1) AS fresh_rc62_rest,
       sum(den - nc_snap) AS fresh_with_snapshot_read,
       round(sum(den - nc)::numeric / sum(den), 4) AS rate_actual,
       round(sum(den - nc_rc61)::numeric / sum(den), 4) AS rate_rc61_rest,
       round(sum(den - nc_rc62)::numeric / sum(den), 4) AS rate_rc62_rest,
       round(sum(den - nc_snap)::numeric / sum(den), 4) AS rate_with_snapshot_read,
       count(*) FILTER (WHERE (den - nc)::numeric / den >= 0.95) AS ge95_actual,
       count(*) FILTER (WHERE (den - nc_rc61)::numeric / den >= 0.95) AS ge95_rc61_rest,
       count(*) FILTER (WHERE (den - nc_rc62)::numeric / den >= 0.95) AS ge95_rc62_rest,
       count(*) FILTER (WHERE (den - nc_snap)::numeric / den >= 0.95) AS ge95_with_snapshot_read,
       count(*) FILTER (WHERE open_q > 57 - 0.317 * no_open) AS snaps_budget_binds_rc61,
       count(*) FILTER (WHERE open_q > 57) AS snaps_budget_binds_rc62,
       round(sum(t)::numeric / sum(den), 4) AS share_venue_not_open
  FROM r;
\echo === R2. per hour ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS e(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS e(k, v)
           WHERE k LIKE '%|STREAM:MARKET_NOT_OPEN|%' AND k NOT LIKE '%|STARTED_GT_4H') AS stream_not_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (
  SELECT at, den, nc, q, no_open, not_held,
         greatest(no_open, gt4h + stream_not_open) AS t,
         greatest(q - no_open, 0) AS open_q
    FROM s),
r AS (
  SELECT at, den, nc, t, open_q, no_open,
         least(nc, t + not_held + greatest(open_q - (57 - 0.317 * no_open), 0)) AS nc_rc61,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc_rc62,
         least(nc, t + not_held) AS nc_snap
    FROM p)
SELECT to_timestamp(floor(extract(epoch FROM at) / 3600) * 3600) AS hour, count(*) AS snaps,
       round(avg(den), 1) AS den, round(avg(no_open), 1) AS not_open_reads,
       round(avg(open_q), 1) AS open_q,
       round(avg((den - nc)::numeric / den), 4) AS rate_actual,
       round(avg((den - nc_rc61)::numeric / den), 4) AS rate_rc61_rest,
       round(avg((den - nc_rc62)::numeric / den), 4) AS rate_rc62_rest,
       round(avg((den - nc_snap)::numeric / den), 4) AS rate_with_snapshot_read,
       count(*) FILTER (WHERE (den - nc_rc61)::numeric / den >= 0.95) AS ge95_rc61,
       count(*) FILTER (WHERE (den - nc_rc62)::numeric / den >= 0.95) AS ge95_rc62,
       count(*) FILTER (WHERE (den - nc_snap)::numeric / den >= 0.95) AS ge95_snap
  FROM r GROUP BY 1 ORDER BY 1;
