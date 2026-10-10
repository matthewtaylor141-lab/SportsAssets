-- Freshness root-cause audit (group freshness), read only.
-- Sensitivity of research/fr_audit_b_model.sql (run 38056227039) to what is
-- called a venue-terminal member. B used greatest(no_open, started > 4 h
-- ago + stream says not open); here the STRICT reading: only the members
-- whose own last book read said the market is not open
-- (ACTIVE_REFRESH_MARKET_NOT_OPEN, the venue's word), and a LOOSE reading
-- (every not-current member whose event started more than 4 h ago).
--   G1  strict terminal: rates of the four stages
--   G2  loose terminal: rates of the four stages
--   G3  the open quiet members the 12 reads a minute cannot hold (open_q - 57)
\echo === G1. strict terminal = last book read said not open ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND NOT (at >= timestamptz '2026-10-09 14:42:24+00' AND at < timestamptz '2026-10-09 16:00:30+00')
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (SELECT at, den, nc, not_held, least(nc, no_open) AS t, greatest(q - no_open, 0) AS open_q FROM s),
r AS (
  SELECT at, den, nc, t, not_held, open_q,
         least(nc, t + not_held + greatest(open_q - 57, 0)) AS nc1,
         least(nc - t, not_held + greatest(open_q - 57, 0)) AS nc2,
         least(nc - t, not_held) AS nc3
    FROM p)
SELECT count(*) AS snaps, round(avg(t)::numeric, 1) AS avg_terminal,
       round(sum(den - nc)::numeric / sum(den), 4) AS measured,
       count(*) FILTER (WHERE (den - nc)::numeric / den >= 0.95) AS ge95_measured,
       round(sum(den - nc1)::numeric / sum(den), 4) AS rc62_plan,
       count(*) FILTER (WHERE (den - nc1)::numeric / den >= 0.95) AS ge95_rc62_plan,
       round(sum(den - t - nc2)::numeric / sum(den - t), 4) AS plan_excl_terminal,
       count(*) FILTER (WHERE den > t AND (den - t - nc2)::numeric / (den - t) >= 0.95) AS ge95_plan_ex,
       round(sum(den - t - nc3)::numeric / sum(den - t), 4) AS snapshot_excl_terminal,
       count(*) FILTER (WHERE den > t AND (den - t - nc3)::numeric / (den - t) >= 0.95) AS ge95_snap_ex
  FROM r;
\echo === G2. loose terminal = every not-current member whose event started more than 4 h ago ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,not_current}')::int AS nc,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,NOT_HELD_BY_THE_PLANE_BOOKS}')::int, 0) AS not_held,
         (SELECT coalesce(sum(v::int), 0) FROM jsonb_each_text(coalesce(payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}', '{}'::jsonb)) AS x(k, v)
           WHERE k LIKE '%|STARTED_GT_4H') AS gt4h,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND NOT (at >= timestamptz '2026-10-09 14:42:24+00' AND at < timestamptz '2026-10-09 16:00:30+00')
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (SELECT at, den, nc, not_held, least(nc, gt4h) AS t, greatest(q - no_open, 0) AS open_q FROM s),
r AS (
  SELECT at, den, nc, t, not_held, open_q,
         least(nc - t, not_held + greatest(open_q - 57, 0)) AS nc2,
         least(nc - t, not_held) AS nc3
    FROM p)
SELECT count(*) AS snaps, round(avg(t)::numeric, 1) AS avg_terminal,
       round(sum(den - t - nc2)::numeric / sum(den - t), 4) AS plan_excl_terminal,
       count(*) FILTER (WHERE den > t AND (den - t - nc2)::numeric / (den - t) >= 0.95) AS ge95_plan_ex,
       round(sum(den - t - nc3)::numeric / sum(den - t), 4) AS snapshot_excl_terminal,
       count(*) FILTER (WHERE den > t AND (den - t - nc3)::numeric / (den - t) >= 0.95) AS ge95_snap_ex
  FROM r;
\echo === G3. the open quiet members beyond the 57 the 12 reads a minute can hold ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         coalesce((payload #>> '{freshness,priority_universe,census,by_refresh_outcome,ACTIVE_REFRESH_MARKET_NOT_OPEN}')::int, 0) AS no_open,
         coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,REFRESH_CURRENT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,ACTIVE_REFRESH_WAITING_TO_RETRY_A_FAILED_READ}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,by_reason,READ_IN_FLIGHT}')::int, 0)
       + coalesce((payload #>> '{freshness,priority_universe,active_refresh,last_pass,due}')::int, 0) AS q
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 03:26:48+00'
     AND NOT (at >= timestamptz '2026-10-09 14:42:24+00' AND at < timestamptz '2026-10-09 16:00:30+00')
     AND payload #> '{freshness,priority_universe,active_refresh,last_pass}' IS NOT NULL
     AND (payload #>> '{freshness,priority_universe,denominator}')::int > 0),
p AS (SELECT at, den, greatest(q - no_open, 0) AS open_q, no_open FROM s)
SELECT count(*) AS snaps,
       count(*) FILTER (WHERE open_q <= 57) AS open_q_le_57,
       count(*) FILTER (WHERE open_q > 57 AND open_q <= 70) AS open_q_58_70,
       count(*) FILTER (WHERE open_q > 70 AND open_q <= 100) AS open_q_71_100,
       count(*) FILTER (WHERE open_q > 100) AS open_q_gt_100,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY open_q)::numeric, 1) AS open_q_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY open_q)::numeric, 1) AS open_q_p90,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY open_q)::numeric, 1) AS open_q_p99,
       max(open_q) AS open_q_max,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY no_open)::numeric, 1) AS not_open_p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY no_open)::numeric, 1) AS not_open_p90,
       max(no_open) AS not_open_max,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY den)::numeric, 1) AS den_p50,
       max(den) AS den_max, min(den) AS den_min
  FROM p;
