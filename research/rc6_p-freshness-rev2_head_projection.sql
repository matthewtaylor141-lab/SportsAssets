-- RC6.2 lane p-freshness, independent review rev2, read only.
-- The lane's projection (rc6_p-freshness_rc62_projection.sql R1) gives the
-- RC6.2 REST-only cap as 57 holdable members (12 reads/min x 285 s). Its
-- second commit (c07830f2) also serves every frozen-window member that left
-- the live list, from the same 12/min, and those members are outside the
-- scorecard's instant denominator. Same formula, with the cap reduced by the
-- quiet frozen-only members of the hour:
--   fo(h)   = average frozen-only pregame candidates in hour h
--             (rc6_p-freshness-rev2_closed_state.sql C6's rule)
--   quiet   = the snapshot's own open-quiet share (open_q / den)
--   cap_head = 57 - fo(h) x quiet
\echo === H1. all snapshots since the 732cc0c6 boot: lane projection at 3a86235d vs at c07830f2 (REST only) ===
WITH e AS (
  SELECT us_market_slug AS s, date_trunc('minute', cycle_at) AS m
    FROM ext_candidate_outcomes
   WHERE cycle_at > timestamptz '2026-10-08 20:00:00+00'
     AND us_market_slug IS NOT NULL
   GROUP BY 1, 2),
hh AS (
  SELECT generate_series(timestamptz '2026-10-09 03:00:00+00',
                         date_trunc('hour', now()), interval '1 hour') AS hs),
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
  SELECT s.at, den, nc, q, no_open, not_held,
         greatest(no_open, gt4h + stream_not_open) AS t,
         greatest(q - no_open, 0) AS open_q,
         coalesce(fo.fo_pre, 0) AS fo_pre
    FROM s LEFT JOIN fo ON fo.hs = date_trunc('hour', s.at)),
r AS (
  SELECT at, den, nc, t, open_q, fo_pre,
         fo_pre * open_q::numeric / den AS fo_quiet,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc_3a86,
         least(nc, t + not_held + greatest(open_q - (57 - fo_pre * open_q::numeric / den), 0)) AS nc_head
    FROM p)
SELECT count(*) AS snaps, sum(den) AS member_snapshots,
       round(sum(den - nc_3a86), 1) AS fresh_3a86_rest,
       round(sum(den - nc_head), 1) AS fresh_head_rest,
       round(sum(den - nc_3a86)::numeric / sum(den), 4) AS rate_3a86_rest,
       round(sum(den - nc_head)::numeric / sum(den), 4) AS rate_head_rest,
       count(*) FILTER (WHERE (den - nc_3a86)::numeric / den >= 0.95) AS ge95_3a86,
       count(*) FILTER (WHERE (den - nc_head)::numeric / den >= 0.95) AS ge95_head,
       round(avg(fo_quiet), 2) AS avg_quiet_frozen_only_reads_slots
  FROM r;
\echo === H2. per hour ===
WITH e AS (
  SELECT us_market_slug AS s, date_trunc('minute', cycle_at) AS m
    FROM ext_candidate_outcomes
   WHERE cycle_at > timestamptz '2026-10-08 20:00:00+00'
     AND us_market_slug IS NOT NULL
   GROUP BY 1, 2),
hh AS (
  SELECT generate_series(timestamptz '2026-10-09 03:00:00+00',
                         date_trunc('hour', now()), interval '1 hour') AS hs),
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
  SELECT s.at, den, nc, q, no_open, not_held,
         greatest(no_open, gt4h + stream_not_open) AS t,
         greatest(q - no_open, 0) AS open_q,
         coalesce(fo.fo_pre, 0) AS fo_pre
    FROM s LEFT JOIN fo ON fo.hs = date_trunc('hour', s.at)),
r AS (
  SELECT at, den, nc, t, open_q, fo_pre,
         fo_pre * open_q::numeric / den AS fo_quiet,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc_3a86,
         least(nc, t + not_held + greatest(open_q - (57 - fo_pre * open_q::numeric / den), 0)) AS nc_head
    FROM p)
SELECT date_trunc('hour', at) AS hour, count(*) AS snaps, round(avg(den), 1) AS den,
       round(avg(open_q), 1) AS open_q, round(avg(fo_pre), 1) AS frozen_only_pregame,
       round(avg(fo_quiet), 2) AS quiet_frozen_only,
       round(avg((den - nc_3a86)::numeric / den), 4) AS rate_3a86_rest,
       round(avg((den - nc_head)::numeric / den), 4) AS rate_head_rest,
       count(*) FILTER (WHERE (den - nc_3a86)::numeric / den >= 0.95) AS ge95_3a86,
       count(*) FILTER (WHERE (den - nc_head)::numeric / den >= 0.95) AS ge95_head
  FROM r GROUP BY 1 ORDER BY 1;
