-- RC6.2 lane p-freshness, rework after independent review rev2, read only.
-- The members only the frozen window holds (c07830f2) share the plane's 12
-- book reads a minute with the scorecard's instant population (the coverage
-- pass's PRIORITY tier, registry priority <= 10). Same snapshots, same cap
-- (57 holdable members: 12 reads/min x 285 s) and the same frozen-only rule
-- as research/rc6_p-freshness-rev2_head_projection.sql (fo_pre: average
-- frozen-only pregame candidates of the hour; quiet share open_q / den):
--   fq      = fo_pre x open_q / den   (quiet frozen-only members needing reads)
--   d       = open_q + fq             (members the budget must hold)
-- three read orders, REST only:
--   LIVE_FIRST  (the lane's default now)  live cap 57; frozen-only get
--               least(fq, greatest(57 - open_q, 0))
--   BY_LAPSE worst (the reviewer's model)  every unserved place charged to
--               the live list: live cap 57 - fq; frozen-only get fq
--   BY_LAPSE proportional (round robin)    each list loses its share:
--               live unserved open_q x (d - 57) / d when d > 57
-- O3/O4: what the plane runs now (the RC6.1 freshness task's events).
\echo === O1. all snapshots since the 732cc0c6 boot, three read orders (REST only) ===
WITH e AS (
  SELECT us_market_slug AS s, date_trunc('minute', cycle_at) AS m
    FROM ext_candidate_outcomes
   WHERE cycle_at > timestamptz '2026-10-08 20:00:00+00'
     AND us_market_slug IS NOT NULL
   GROUP BY 1, 2),
hh AS (
  SELECT generate_series(timestamptz '2026-10-09 03:00:00+00',
                         timestamptz '2026-10-09 14:00:00+00', interval '1 hour') AS hs),
a AS (
  SELECT hh.hs, e.s, max(e.m) AS last_eval
    FROM hh JOIN e ON e.m > hh.hs - interval '6 hours'
                  AND e.m <= hh.hs + interval '1 hour'
   GROUP BY 1, 2
  HAVING bool_or(e.m <= hh.hs)),
fo AS (
  SELECT a.hs,
         coalesce(sum(greatest(0, extract(epoch FROM (a.hs + interval '1 hour')
                     - greatest(a.hs, a.last_eval + interval '6 hours'))))
                  FILTER (WHERE r.event_start > a.last_eval + interval '6 hours'), 0) / 3600.0 AS fo_pre
    FROM a LEFT JOIN market_plane_registry r ON r.contract_id = a.s
   GROUP BY 1),
s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STREAM:MARKET_NOT_OPEN|%' AND k NOT LIKE '%|STARTED_GT_4H') AS stream_not_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND at < timestamptz '2026-10-09 14:42:24+00'
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (
  SELECT s.at, den, nc, not_held,
         greatest(no_open, gt4h + stream_not_open) AS t,
         greatest(q - no_open, 0) AS open_q,
         coalesce(fo.fo_pre, 0) AS fo_pre
    FROM s LEFT JOIN fo ON fo.hs = date_trunc('hour', s.at)),
q AS (
  SELECT p.*, fo_pre * open_q::numeric / den AS fq,
         open_q + fo_pre * open_q::numeric / den AS d
    FROM p),
r AS (
  SELECT at, den, fq,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc_live_first,
         least(nc, t + not_held + greatest(d - 57, 0)) AS nc_lapse_worst,
         least(nc, t + not_held + CASE WHEN d > 57 THEN open_q * (d - 57) / d ELSE 0 END) AS nc_lapse_prop,
         least(fq, greatest(57 - open_q, 0)) AS fo_live_first,
         fq AS fo_lapse_worst,
         CASE WHEN d > 57 THEN fq * 57 / d ELSE fq END AS fo_lapse_prop
    FROM q)
SELECT count(*) AS snaps, sum(den) AS member_snapshots,
       round(sum(den - nc_live_first)::numeric / sum(den), 4) AS rate_live_first,
       round(sum(den - nc_lapse_prop)::numeric / sum(den), 4) AS rate_lapse_prop,
       round(sum(den - nc_lapse_worst)::numeric / sum(den), 4) AS rate_lapse_worst,
       count(*) FILTER (WHERE (den - nc_live_first)::numeric / den >= 0.95) AS ge95_live_first,
       count(*) FILTER (WHERE (den - nc_lapse_prop)::numeric / den >= 0.95) AS ge95_lapse_prop,
       count(*) FILTER (WHERE (den - nc_lapse_worst)::numeric / den >= 0.95) AS ge95_lapse_worst,
       round(sum(fq), 1) AS fo_quiet_member_snapshots,
       round(sum(fo_live_first), 1) AS fo_served_live_first,
       round(sum(fo_lapse_prop), 1) AS fo_served_lapse_prop,
       round(sum(fo_lapse_worst), 1) AS fo_served_lapse_worst
  FROM r;
\echo === O2. per hour ===
WITH e AS (
  SELECT us_market_slug AS s, date_trunc('minute', cycle_at) AS m
    FROM ext_candidate_outcomes
   WHERE cycle_at > timestamptz '2026-10-08 20:00:00+00'
     AND us_market_slug IS NOT NULL
   GROUP BY 1, 2),
hh AS (
  SELECT generate_series(timestamptz '2026-10-09 03:00:00+00',
                         timestamptz '2026-10-09 14:00:00+00', interval '1 hour') AS hs),
a AS (
  SELECT hh.hs, e.s, max(e.m) AS last_eval
    FROM hh JOIN e ON e.m > hh.hs - interval '6 hours'
                  AND e.m <= hh.hs + interval '1 hour'
   GROUP BY 1, 2
  HAVING bool_or(e.m <= hh.hs)),
fo AS (
  SELECT a.hs,
         coalesce(sum(greatest(0, extract(epoch FROM (a.hs + interval '1 hour')
                     - greatest(a.hs, a.last_eval + interval '6 hours'))))
                  FILTER (WHERE r.event_start > a.last_eval + interval '6 hours'), 0) / 3600.0 AS fo_pre
    FROM a LEFT JOIN market_plane_registry r ON r.contract_id = a.s
   GROUP BY 1),
s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STREAM:MARKET_NOT_OPEN|%' AND k NOT LIKE '%|STARTED_GT_4H') AS stream_not_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND at < timestamptz '2026-10-09 14:42:24+00'
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (
  SELECT s.at, den, nc, not_held,
         greatest(no_open, gt4h + stream_not_open) AS t,
         greatest(q - no_open, 0) AS open_q,
         coalesce(fo.fo_pre, 0) AS fo_pre
    FROM s LEFT JOIN fo ON fo.hs = date_trunc('hour', s.at)),
q AS (
  SELECT p.*, fo_pre * open_q::numeric / den AS fq,
         open_q + fo_pre * open_q::numeric / den AS d
    FROM p),
r AS (
  SELECT at, den, open_q, fo_pre, fq,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc_live_first,
         least(nc, t + not_held + greatest(d - 57, 0)) AS nc_lapse_worst,
         least(nc, t + not_held + CASE WHEN d > 57 THEN open_q * (d - 57) / d ELSE 0 END) AS nc_lapse_prop,
         least(fq, greatest(57 - open_q, 0)) AS fo_live_first,
         CASE WHEN d > 57 THEN fq * 57 / d ELSE fq END AS fo_lapse_prop
    FROM q)
SELECT date_trunc('hour', at) AS hour, count(*) AS snaps, round(avg(den), 1) AS den,
       round(avg(open_q), 1) AS open_q, round(avg(fo_pre), 1) AS fo_pregame,
       round(avg(fq), 2) AS fo_quiet,
       round(avg((den - nc_live_first)::numeric / den), 4) AS rate_live_first,
       round(avg((den - nc_lapse_prop)::numeric / den), 4) AS rate_lapse_prop,
       round(avg((den - nc_lapse_worst)::numeric / den), 4) AS rate_lapse_worst,
       count(*) FILTER (WHERE (den - nc_live_first)::numeric / den >= 0.95) AS ge95_live_first,
       count(*) FILTER (WHERE (den - nc_lapse_prop)::numeric / den >= 0.95) AS ge95_lapse_prop,
       count(*) FILTER (WHERE (den - nc_lapse_worst)::numeric / den >= 0.95) AS ge95_lapse_worst,
       round(avg(fo_live_first), 2) AS fo_served_live_first,
       round(avg(fo_lapse_prop), 2) AS fo_served_lapse_prop,
       round(avg(fq), 2) AS fo_served_lapse_worst
  FROM r GROUP BY 1 ORDER BY 1;
\echo === O3. what the plane runs now: running commit per loop ===
SELECT process, commit_sha, count(*) AS loops, max(last_success_at) AS newest_success
  FROM runtime_loop_health GROUP BY 1, 2 ORDER BY 1, 2;
\echo === O4. newest freshness events by kind, and book reads since 16:00Z ===
SELECT kind, count(*) FILTER (WHERE at > now() - interval '90 minutes') AS last_90m,
       max(at) AS newest
  FROM market_plane_events
 WHERE kind IN ('FRESHNESS_WINDOW', 'FRESHNESS_SAMPLE', 'SNAPSHOT')
   AND at > now() - interval '6 hours'
 GROUP BY 1 ORDER BY 1;
SELECT date_trunc('hour', at) AS hour, count(*) AS snapshots,
       min((payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int) AS reads_total_min,
       max((payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int) AS reads_total_max,
       round(avg((payload #>> '{freshness,priority_universe,rate}')::numeric), 4) AS avg_rate,
       bool_or(payload #> '{freshness_task}' IS NOT NULL) AS any_task_digest
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 16:00:00+00'
 GROUP BY 1 ORDER BY 1;
