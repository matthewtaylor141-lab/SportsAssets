-- RC6 lane D1 (freshness measurement and delivery), read only.
-- The inputs the refresh simulation and the measurement design need, from
-- production's own records:
--   A  the plane's SNAPSHOT cadence over 24 h (gaps = slow passes / outages)
--   B  the instantaneous priority freshness series the snapshots carried
--   C  the priority members' stream update gaps (PRIORITY_PMX_BOOKS series):
--      how long a priority book stays quiet between two stream updates
--   D  the priority registry members now: venue, reason, family, period,
--      market type, phase
--   E  the paper REST observations that the coverage pass counts current
--      (any row inside 300 s) split by error / no error
--   F  open paper working orders (role, market, whether the plane holds the
--      market as a priority member)
--   G  the workers' institutional_md REST trail (bettor_l2_evidence DIRECT):
--      its symbols, cadence and overlap with the plane's priority members
--   H  Xavier reviews of open groups over 6 h: PinnAPI probability evidence
--      state per review (the held positions' probability inputs)
--   I  market_plane_events row counts by kind (the table has no kind index)
\echo === A. SNAPSHOT inter-arrival over 24 h (seconds) ===
WITH s AS (
  SELECT at, extract(epoch FROM at - lag(at) OVER (ORDER BY at)) AS gap
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours')
SELECT count(*) AS snapshots, min(at) AS first_at, max(at) AS last_at,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS p50,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS p90,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS p99,
       round(max(gap)::numeric, 1) AS max_gap,
       count(*) FILTER (WHERE gap > 120) AS gaps_over_120s,
       round(sum(gap) FILTER (WHERE gap > 120)::numeric, 1) AS seconds_in_gaps_over_120s
  FROM s;
\echo === A2. SNAPSHOT gaps over 180 s (24 h, newest 40) ===
WITH s AS (
  SELECT at, extract(epoch FROM at - lag(at) OVER (ORDER BY at)) AS gap
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours')
SELECT at, round(gap::numeric, 1) AS gap_s FROM s WHERE gap > 180
 ORDER BY at DESC LIMIT 40;
\echo === B. priority freshness per snapshot, hourly over 24 h ===
WITH s AS (
  SELECT at,
         (payload #>> '{freshness,priority_universe,denominator}')::int AS den,
         (payload #>> '{freshness,priority_universe,current_pmx_stream}')::int AS pmx,
         (payload #>> '{freshness,priority_universe,current_rest_fallback}')::int AS rest,
         (payload #>> '{freshness,priority_universe,external_unavailable}')::int AS ext,
         (payload #>> '{freshness,priority_universe,census,not_current}')::int AS census_nc
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '24 hours')
SELECT date_trunc('hour', at) AS hour, count(*) AS snaps,
       round(avg(den), 1) AS den_avg, min(den) AS den_min, max(den) AS den_max,
       round(avg(pmx), 1) AS pmx_avg, round(avg(rest), 1) AS rest_avg,
       round(avg((pmx + rest)::numeric / nullif(den - ext, 0)), 4) AS rate_avg,
       round(min((pmx + rest)::numeric / nullif(den - ext, 0)), 4) AS rate_min,
       round(max((pmx + rest)::numeric / nullif(den - ext, 0)), 4) AS rate_max,
       round(avg(census_nc), 1) AS census_not_current_avg
  FROM s GROUP BY 1 ORDER BY 1;
\echo === B2. census reasons over the last 6 h (summed over snapshots) ===
WITH s AS (
  SELECT payload #> '{freshness,priority_universe,census,by_tier_reason_rest_phase}' AS by
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > now() - interval '6 hours')
SELECT k AS tier_reason_rest_phase, sum(v::int) AS member_snapshots,
       count(*) AS snapshots_with_it
  FROM s, jsonb_each_text(coalesce(s.by, '{}'::jsonb)) AS e(k, v)
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;
\echo === C. priority stream updates (PRIORITY_PMX_BOOKS, 6 h): distinct receipts per symbol ===
WITH ev AS (
  SELECT at, payload -> 'books' AS books FROM market_plane_events
   WHERE kind = 'PRIORITY_PMX_BOOKS' AND at > now() - interval '6 hours'),
b AS (
  SELECT DISTINCT e.k AS sym, round((e.v ->> 'received_at')::numeric, 3) AS rcv
    FROM ev, jsonb_each(ev.books) AS e(k, v)
   WHERE e.v ->> 'received_at' IS NOT NULL),
g AS (
  SELECT sym, rcv, rcv - lag(rcv) OVER (PARTITION BY sym ORDER BY rcv) AS gap
    FROM b)
SELECT count(DISTINCT sym) AS symbols, count(*) AS updates_seen,
       count(gap) AS gaps,
       round(percentile_cont(0.25) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS gap_p25,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS gap_p50,
       round(percentile_cont(0.75) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS gap_p75,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS gap_p90,
       count(gap) FILTER (WHERE gap > 300) AS gaps_over_300s,
       round(sum(gap - 300) FILTER (WHERE gap > 300)::numeric, 0) AS quiet_seconds_past_300
  FROM g;
\echo === C2. stream gap histogram (seconds; 6 h) ===
WITH ev AS (
  SELECT at, payload -> 'books' AS books FROM market_plane_events
   WHERE kind = 'PRIORITY_PMX_BOOKS' AND at > now() - interval '6 hours'),
b AS (
  SELECT DISTINCT e.k AS sym, round((e.v ->> 'received_at')::numeric, 3) AS rcv
    FROM ev, jsonb_each(ev.books) AS e(k, v)
   WHERE e.v ->> 'received_at' IS NOT NULL),
g AS (
  SELECT sym, rcv - lag(rcv) OVER (PARTITION BY sym ORDER BY rcv) AS gap FROM b)
SELECT CASE WHEN gap <= 10 THEN 'a <=10' WHEN gap <= 30 THEN 'b 10-30'
            WHEN gap <= 60 THEN 'c 30-60' WHEN gap <= 120 THEN 'd 60-120'
            WHEN gap <= 300 THEN 'e 120-300' WHEN gap <= 600 THEN 'f 300-600'
            WHEN gap <= 1800 THEN 'g 600-1800' WHEN gap <= 3600 THEN 'h 1800-3600'
            ELSE 'i >3600' END AS bucket,
       count(*) AS gaps, round(sum(gap)::numeric, 0) AS seconds
  FROM g WHERE gap IS NOT NULL GROUP BY 1 ORDER BY 1;
\echo === C3. per-symbol update counts in 6 h (distribution) ===
WITH ev AS (
  SELECT at, payload -> 'books' AS books FROM market_plane_events
   WHERE kind = 'PRIORITY_PMX_BOOKS' AND at > now() - interval '6 hours'),
b AS (
  SELECT DISTINCT e.k AS sym, round((e.v ->> 'received_at')::numeric, 3) AS rcv
    FROM ev, jsonb_each(ev.books) AS e(k, v)
   WHERE e.v ->> 'received_at' IS NOT NULL),
c AS (SELECT sym, count(*) AS n FROM b GROUP BY 1)
SELECT CASE WHEN n = 1 THEN 'a 1' WHEN n <= 3 THEN 'b 2-3' WHEN n <= 10 THEN 'c 4-10'
            WHEN n <= 30 THEN 'd 11-30' WHEN n <= 100 THEN 'e 31-100'
            ELSE 'f >100' END AS updates_in_6h, count(*) AS symbols
  FROM c GROUP BY 1 ORDER BY 1;
\echo === C4. minute-by-minute stream-current priority books (PRIORITY_PMX_BOOKS, last 3 h, every 10th) ===
WITH ev AS (
  SELECT at, (SELECT count(*) FROM jsonb_object_keys(payload -> 'books')) AS n,
         row_number() OVER (ORDER BY at) AS rn
    FROM market_plane_events
   WHERE kind = 'PRIORITY_PMX_BOOKS' AND at > now() - interval '3 hours')
SELECT at, n FROM ev WHERE rn % 10 = 1 ORDER BY at;
\echo === D. priority registry members now (venue x reason x period x family) ===
SELECT venue, required_reason, coalesce(period, '?') AS period,
       coalesce(family, '?') AS family, count(*) AS n
  FROM market_plane_registry WHERE active AND priority <= 10
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40;
\echo === D2. priority members by market_type (top 40) ===
SELECT coalesce(market_type, '<null>') AS market_type, count(*) AS n,
       count(*) FILTER (WHERE event_start > now()) AS pregame,
       count(*) FILTER (WHERE event_start <= now()
                          AND event_start > now() - interval '4 hours') AS in_play_or_recent,
       count(*) FILTER (WHERE event_start <= now() - interval '4 hours') AS started_gt_4h,
       count(*) FILTER (WHERE event_start IS NULL) AS no_start
  FROM market_plane_registry WHERE active AND priority <= 10
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;
\echo === D3. the evaluated-candidate set over 6 h by hour (how the denominator moves) ===
SELECT date_trunc('hour', cycle_at) AS hour,
       count(DISTINCT us_market_slug) AS distinct_candidates
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '6 hours' AND us_market_slug IS NOT NULL
 GROUP BY 1 ORDER BY 1;
\echo === E. paper REST observations inside 300 s for priority members: error vs ok ===
SELECT count(DISTINCT o.us_market_slug) AS members_with_any_obs_300s,
       count(DISTINCT o.us_market_slug) FILTER (WHERE o.error IS NULL) AS with_ok_obs,
       count(DISTINCT o.us_market_slug) FILTER (WHERE o.error IS NOT NULL) AS with_error_obs,
       count(*) AS obs_rows,
       count(*) FILTER (WHERE o.error IS NOT NULL) AS error_rows
  FROM paper_book_observations o
  JOIN market_plane_registry r ON r.contract_id = o.us_market_slug
 WHERE o.observed_at > now() - interval '300 seconds'
   AND r.active AND r.priority <= 10;
\echo === E2. priority members whose NEWEST obs inside 300 s is an error ===
SELECT count(*) AS newest_is_error
  FROM (SELECT DISTINCT ON (o.us_market_slug) o.us_market_slug, o.error
          FROM paper_book_observations o
          JOIN market_plane_registry r ON r.contract_id = o.us_market_slug
         WHERE o.observed_at > now() - interval '300 seconds'
           AND r.active AND r.priority <= 10
         ORDER BY o.us_market_slug, o.observed_at DESC) x
 WHERE x.error IS NOT NULL;
\echo === F. open paper working orders ===
SELECT o.role, o.state, count(*) AS n, count(DISTINCT o.us_market_slug) AS markets,
       count(DISTINCT o.us_market_slug) FILTER (
           WHERE r.priority <= 10 AND r.active) AS markets_in_priority,
       count(DISTINCT o.us_market_slug) FILTER (WHERE r.contract_id IS NULL)
           AS markets_not_in_registry
  FROM paper_orders o
  LEFT JOIN market_plane_registry r ON r.contract_id = o.us_market_slug
 WHERE o.state IN ('PENDING_SIMULATION', 'RESTING', 'PARTIALLY_FILLED',
                   'CANCEL_PENDING')
 GROUP BY 1, 2 ORDER BY 3 DESC;
\echo === G. institutional_md DIRECT trail, 60 min: symbols and overlap ===
SELECT count(*) AS rows_60m, count(DISTINCT e.instrument_id) AS symbols,
       count(DISTINCT e.instrument_id) FILTER (
           WHERE r.active AND r.priority <= 10) AS symbols_in_priority,
       round(extract(epoch FROM now() - max(e.received_timestamp))::numeric, 1)
           AS newest_age_s
  FROM bettor_l2_evidence e
  LEFT JOIN market_plane_registry r ON r.contract_id = e.instrument_id
 WHERE e.received_timestamp > now() - interval '60 minutes'
   AND e.latency_regime <> 'BRIDGE';
\echo === G2. bettor_l2_evidence regimes over 60 min ===
SELECT latency_regime, count(*) AS n, count(DISTINCT instrument_id) AS symbols
  FROM bettor_l2_evidence
 WHERE received_timestamp > now() - interval '60 minutes'
 GROUP BY 1 ORDER BY 2 DESC;
\echo === H. Xavier reviews of open groups, 6 h: probability source per review ===
SELECT coalesce(measure ->> 'source', '<none>') AS source,
       coalesce(measure ->> 'stale', '?') AS stale, count(*) AS reviews,
       count(DISTINCT group_id) AS groups
  FROM paper_xavier_reviews
 WHERE reviewed_at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
\echo === H2. Xavier review cadence per group, 6 h ===
WITH r AS (
  SELECT group_id, reviewed_at,
         extract(epoch FROM reviewed_at - lag(reviewed_at)
                 OVER (PARTITION BY group_id ORDER BY reviewed_at)) AS gap
    FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '6 hours')
SELECT count(DISTINCT group_id) AS groups, count(*) AS reviews,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY gap)::numeric, 1) AS gap_p50,
       round(max(gap)::numeric, 1) AS gap_max
  FROM r;
\echo === I. market_plane_events rows by kind (all time) and payload bytes for the last day ===
SELECT kind, count(*) AS n,
       count(*) FILTER (WHERE at > now() - interval '24 hours') AS n_24h
  FROM market_plane_events GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
