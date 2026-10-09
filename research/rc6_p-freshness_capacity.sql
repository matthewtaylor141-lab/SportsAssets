-- RC6.2 lane p-freshness (data freshness), read only.
-- Is the plane's book-read budget (12 GetOrderBook a minute, 300 s bound:
-- at most 12 x 285 / 60 = 57 quiet members kept current at once, re-read
-- 15 s before lapse) what leaves priority members not current, or how the
-- reads are scheduled? From every plane SNAPSHOT since the 732cc0c6 plane
-- booted (2026-10-09 03:26:48Z):
--   S1  per 20 min: priority denominator, not current, members quiet on the
--       stream and refreshable (Q), of them waiting a retry (mostly a
--       market the venue said is not open), due, read, current via reads
--   S2  the book reads actually made per minute (totals delta between
--       consecutive snapshots of one boot) against the 12 a minute budget
--   S3  Q against the 57 the budget can hold: how many snapshots exceed it
--   S4  the census reasons of the not-current members, summed over the
--       snapshots (stream refusal x paper REST age x phase)
--   S5  the census read outcome of the not-current members, summed
--   S6  the frozen-window samples, when a build writing them runs
-- Q = members the stream refuses for snapshot currency only = the read
-- pass's REFRESH_CURRENT + WAITING_TO_RETRY + READ_IN_FLIGHT + due (due
-- includes REFRESH_CURRENT_DUE_FOR_RE_READ); the pass's other buckets are
-- STREAM_CURRENT, STREAM_REFUSAL_NOT_REFRESHABLE, NOT_HELD_BY_THE_PLANE_BOOKS.
\echo === S1. per 20 min since boot ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,STREAM_CURRENT}')::int, 0) AS stream_cur,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0) AS read_cur,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0) AS retry_wait,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0) AS inflight,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,STREAM_REFUSAL_NOT_REFRESHABLE}')::int, 0) AS not_refreshable,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS due,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,current_via_refresh}')::int, 0) AS via_read,
         (payload #> '{freshness,priority_universe}') ? 'snapshot_refresh' AS rc61_build
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00')
SELECT to_timestamp(floor(extract(epoch FROM at) / 1200) * 1200) AS bucket,
       bool_or(rc61_build) AS rc61, count(*) AS snaps,
       round(avg(den), 1) AS den, min(den) AS den_min, max(den) AS den_max,
       round(avg(nc), 1) AS nc, min(nc) AS nc_min, max(nc) AS nc_max,
       round(avg((den - nc)::numeric / nullif(den, 0)), 4) AS rate,
       round(min((den - nc)::numeric / nullif(den, 0)), 4) AS rate_min,
       round(avg(census_nc), 1) AS census_nc,
       round(avg(read_cur + retry_wait + inflight + due), 1) AS q,
       max(read_cur + retry_wait + inflight + due) AS q_max,
       round(avg(retry_wait), 1) AS retry_wait,
       round(avg(due), 1) AS due, max(due) AS due_max,
       round(avg(via_read), 1) AS via_read,
       round(avg(not_refreshable), 1) AS not_refr,
       round(avg(not_held), 1) AS not_held
  FROM s GROUP BY 1 ORDER BY 1;
\echo === S2. book reads made per minute, between consecutive snapshots of one boot ===
WITH s AS (
  SELECT at, (payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int AS reads,
         (payload #> '{freshness,priority_universe}') ? 'snapshot_refresh' AS rc61_build
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'),
d AS (
  SELECT at, rc61_build, reads - lag(reads) OVER (ORDER BY at) AS dr,
         extract(epoch FROM at - lag(at) OVER (ORDER BY at)) AS dt
    FROM s)
SELECT to_timestamp(floor(extract(epoch FROM at) / 3600) * 3600) AS hour,
       bool_or(rc61_build) AS rc61, count(*) AS pairs,
       sum(dr) AS reads, round(sum(dt)::numeric, 0) AS seconds,
       round((60 * sum(dr) / nullif(sum(dt), 0))::numeric, 2) AS reads_per_min,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dt)::numeric, 0) AS snap_gap_p50_s,
       round(max(dt)::numeric, 0) AS snap_gap_max_s
  FROM d WHERE dr >= 0 AND dt > 0 GROUP BY 1 ORDER BY 1;
\echo === S3. quiet refreshable members Q against the 57 the budget holds (12 x 285 s / 60) ===
WITH s AS (
  SELECT (payload #> '{freshness,priority_universe}') ? 'snapshot_refresh' AS rc61_build,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0) AS w,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL)
SELECT rc61_build AS rc61, count(*) AS snaps,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY q)::numeric, 1) AS q_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY q)::numeric, 1) AS q_p90,
       max(q) AS q_max,
       count(*) FILTER (WHERE q > 57) AS snaps_q_over_57,
       count(*) FILTER (WHERE q - w > 57) AS snaps_open_q_over_57,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY nc)::numeric, 1) AS nc_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY nc)::numeric, 1) AS nc_p90,
       count(*) FILTER (WHERE nc <= 9) AS snaps_nc_le_9
  FROM s GROUP BY 1 ORDER BY 1;
\echo === S4. census reasons of not-current members, summed over snapshots since boot ===
WITH s AS (
  SELECT (payload #> '{freshness,priority_universe}') ? 'snapshot_refresh' AS rc61_build,
         payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}' AS by
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00')
SELECT rc61_build AS rc61, k AS tier_reason_rest_phase, sum(v::int) AS member_snapshots,
       count(*) AS snapshots_with_it
  FROM s, jsonb_each_text(coalesce(s.by, '{}'::jsonb)) AS e(k, v)
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 40;
\echo === S5. census read outcomes of not-current members, summed over snapshots since boot ===
WITH s AS (
  SELECT (payload #> '{freshness,priority_universe}') ? 'snapshot_refresh' AS rc61_build,
         payload #> '{freshness,priority_universe,census,by_refresh_outcome}' AS by
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00')
SELECT rc61_build AS rc61, k AS read_outcome, sum(v::int) AS member_snapshots,
       count(*) AS snapshots_with_it
  FROM s, jsonb_each_text(coalesce(s.by, '{}'::jsonb)) AS e(k, v)
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;
\echo === S6. frozen-window samples (FRESHNESS_SAMPLE), per 20 min, when written ===
WITH s AS (
  SELECT at, (payload ->> 'n')::int AS n, payload -> 'counts' AS c,
         payload -> 'reasons' AS r
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_SAMPLE' AND at > timestamptz '2026-10-09 03:26:48+00')
SELECT to_timestamp(floor(extract(epoch FROM at) / 1200) * 1200) AS bucket,
       count(*) AS samples, round(avg(n), 1) AS n,
       round(avg(coalesce((c ->> 'CURRENT_PMX_STREAM')::int, 0)), 1) AS s,
       round(avg(coalesce((c ->> 'CURRENT_PLANE_REFRESH')::int, 0)), 1) AS r,
       round(avg(coalesce((c ->> 'CURRENT_PLANE_SNAPSHOT')::int, 0)), 1) AS g,
       round(avg(coalesce((c ->> 'CURRENT_PAPER_REST')::int, 0)), 1) AS p,
       round(avg(coalesce((c ->> 'NOT_CURRENT')::int, 0)), 1) AS nc,
       round(avg(coalesce((c ->> 'NOT_HELD_BY_THE_PLANE')::int, 0)), 1) AS u,
       round(avg(coalesce((c ->> 'EXTERNAL_UNAVAILABLE')::int, 0)), 1) AS x
  FROM s GROUP BY 1 ORDER BY 1;
\echo === S6b. frozen-window sample reasons, summed (newest 3 h) ===
SELECT k AS reason, sum(v::int) AS member_samples, count(*) AS samples
  FROM market_plane_events ev, jsonb_each_text(coalesce(ev.payload -> 'reasons', '{}'::jsonb)) AS e(k, v)
 WHERE ev.kind = 'FRESHNESS_SAMPLE' AND ev.at > now() - interval '3 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 25;
