-- Freshness root-cause audit (group freshness), read only.
-- Every plane SNAPSHOT since the 732cc0c6 plane booted (2026-10-09 03:26:48Z)
-- and again since its redeploy (2026-10-09 15:59:44Z) split into what no
-- refresh can make current (the venue says the market is not open or has
-- ended: the census read outcome ACTIVE_REFRESH_MARKET_NOT_OPEN, or an event
-- started more than 4 h ago with the stream refusing), the members the
-- plane's books do not hold, and the open quiet members the refresh could
-- re-prove (the 12 reads a minute x 285 s = 57 members). Same model as
-- research/rc6_p-freshness_order_projection.sql (LIVE_FIRST), extended with
-- the venue-terminal members taken out of the denominator, and with a
-- snapshot-only call (250 symbols a call).
--   B1  all snapshots: measured rate, RC6.2 plan rate, and the extended rates
--   B2  per 3 h
--   B3  pass cadence (SNAPSHOT spacing) and book reads per hour
\echo === B1. all snapshots, 2026-10-09 03:26:48Z .. now, minus the 14:42-15:59Z interval of the freshness-task plane ===
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
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STREAM:MARKET_NOT_OPEN|%' AND k NOT LIKE '%|STARTED_GT_4H') AS stream_not_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND NOT (at >= timestamptz '2026-10-09 14:42:24+00' AND at < timestamptz '2026-10-09 16:00:30+00')
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (
  SELECT at, den, nc, not_held,
         least(nc, greatest(no_open, gt4h + stream_not_open)) AS t,
         greatest(q - no_open, 0) AS open_q
    FROM s),
r AS (
  SELECT at, den, nc, t, not_held, open_q,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc1,
         least(nc - t, not_held + greatest(open_q - 57, 0)) AS nc2,
         least(nc - t, not_held) AS nc3
    FROM p)
SELECT count(*) AS snaps, sum(den) AS member_snapshots,
       round(avg(t)::numeric, 1) AS avg_terminal, round(avg(not_held)::numeric, 2) AS avg_not_held,
       round(avg(open_q)::numeric, 1) AS avg_open_quiet, max(open_q) AS max_open_quiet,
       count(*) FILTER (WHERE open_q > 57) AS snaps_open_quiet_gt_57,
       round(sum(den - nc)::numeric / sum(den), 4) AS rate_measured,
       count(*) FILTER (WHERE (den - nc)::numeric / den >= 0.95) AS ge95_measured,
       round(sum(den - nc1)::numeric / sum(den), 4) AS rate_rc62_plan,
       count(*) FILTER (WHERE (den - nc1)::numeric / den >= 0.95) AS ge95_rc62_plan,
       round(sum(den - t - nc2)::numeric / sum(den - t), 4) AS rate_plan_excl_terminal,
       count(*) FILTER (WHERE den > t AND (den - t - nc2)::numeric / (den - t) >= 0.95) AS ge95_plan_excl_terminal,
       round(sum(den - t - nc3)::numeric / sum(den - t), 4) AS rate_snapshot_excl_terminal,
       count(*) FILTER (WHERE den > t AND (den - t - nc3)::numeric / (den - t) >= 0.95) AS ge95_snapshot_excl_terminal,
       round(sum(den - nc)::numeric / sum(den - t), 4) AS rate_measured_excl_terminal_only,
       count(*) FILTER (WHERE den > t AND (den - nc)::numeric / (den - t) >= 0.95) AS ge95_measured_excl_terminal_only
  FROM r;
\echo === B2. per 3 hours ===
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
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STREAM:MARKET_NOT_OPEN|%' AND k NOT LIKE '%|STARTED_GT_4H') AS stream_not_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND NOT (at >= timestamptz '2026-10-09 14:42:24+00' AND at < timestamptz '2026-10-09 16:00:30+00')
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (
  SELECT at, den, nc, not_held,
         least(nc, greatest(no_open, gt4h + stream_not_open)) AS t,
         greatest(q - no_open, 0) AS open_q
    FROM s),
r AS (
  SELECT at, den, nc, t, not_held, open_q,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc1,
         least(nc - t, not_held + greatest(open_q - 57, 0)) AS nc2,
         least(nc - t, not_held) AS nc3
    FROM p)
SELECT to_timestamp(floor(extract(epoch FROM at) / 10800) * 10800) AS bucket3h, count(*) AS snaps,
       round(avg(den)::numeric, 1) AS den, round(avg(t)::numeric, 1) AS terminal,
       round(avg(open_q)::numeric, 1) AS open_q, round(avg(not_held)::numeric, 2) AS not_held,
       round(sum(den - nc)::numeric / sum(den), 4) AS measured,
       round(sum(den - nc1)::numeric / sum(den), 4) AS rc62_plan,
       round(sum(den - t - nc2)::numeric / nullif(sum(den - t), 0), 4) AS plan_excl_term,
       round(sum(den - t - nc3)::numeric / nullif(sum(den - t), 0), 4) AS snap_excl_term,
       count(*) FILTER (WHERE (den - nc)::numeric / den >= 0.95) AS ge95_meas,
       count(*) FILTER (WHERE den > t AND (den - t - nc2)::numeric / (den - t) >= 0.95) AS ge95_plan_ex,
       count(*) FILTER (WHERE den > t AND (den - t - nc3)::numeric / (den - t) >= 0.95) AS ge95_snap_ex
  FROM r GROUP BY 1 ORDER BY 1;
\echo === B3. pass cadence on the 732cc0c6 plane (SNAPSHOT spacing, last 24 h) and book reads per hour ===
WITH e AS (
  SELECT at, lag(at) OVER (ORDER BY at) AS prev_at,
         (payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int AS reads
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > greatest(now() - interval '24 hours', timestamptz '2026-10-09 16:05:00+00'))
SELECT count(*) AS snapshots,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM at - prev_at))::numeric, 1) AS gap_p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM at - prev_at))::numeric, 1) AS gap_p90_s,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM at - prev_at))::numeric, 1) AS gap_p99_s,
       round(max(extract(epoch FROM at - prev_at))::numeric, 1) AS gap_max_s,
       max(reads) - min(reads) AS book_reads_24h,
       round(((max(reads) - min(reads)) / (extract(epoch FROM max(at) - min(at)) / 60.0))::numeric, 2) AS reads_per_min
  FROM e WHERE prev_at IS NOT NULL;
