-- RC6.2 lane p-freshness (data freshness), read only. Independent review
-- rev1 found the plane's book reads serve only the LIVE registry list
-- while the frozen freshness window measures the hour's frozen membership:
-- a member that leaves the live list mid-hour is never read again and is
-- coded N for the rest of the hour. This sizes that gap over EVERY frozen
-- window since the RC6.1 plane boot (2026-10-09 14:42:24Z, release
-- 3d5af039), from the plane's own persisted samples and census snapshots.
-- A member N in a sample is LEFT_LIVE_LIST when no census snapshot of its
-- window (taken while it was N, +-2 min, and complete: census not-current
-- <= its 40-name sample) names it, LIVE_NOT_CURRENT when one does, and
-- NO_CENSUS when no complete census overlaps its N samples.
--   V1  per window: member-samples by code, the window measure, the N
--       member-samples by class (pregame or not), the upper bound if every
--       pregame LEFT_LIVE_LIST member-sample were current, book reads/min
--   V2  per window and member: the LEFT_LIVE_LIST and NO_CENSUS members
--   V3  the whole span: totals of V1
\echo === V1. per frozen window since the RC6.1 plane boot ===
WITH w AS (
  SELECT (payload ->> 'window_start')::float8 AS ws, payload
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_WINDOW'
     AND (payload ->> 'window_start')::float8 >= 1791558000),
m AS (
  SELECT w.ws, (e.ord - 1)::int AS idx, e.r ->> 0 AS contract_id,
         (e.r ->> 5)::float8 AS event_start
    FROM w, jsonb_array_elements(w.payload -> 'members') WITH ORDINALITY AS e(r, ord)),
s AS (
  SELECT at, extract(epoch FROM at) AS t, (payload ->> 'window_start')::float8 AS ws,
         payload ->> 'codes' AS codes
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_SAMPLE' AND at > timestamptz '2026-10-09 14:42:24+00'
     AND (payload ->> 'window_start')::float8 >= 1791558000),
snap AS (
  SELECT at, payload #> '{freshness,priority_universe,census,sample}' AS sample
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 14:42:24+00'
     AND (payload #>> '{freshness,priority_universe,census,not_current}')::int <= 40),
sm AS (
  SELECT s.ws, s.at, s.t, m.contract_id, m.event_start,
         substr(s.codes, m.idx + 1, 1) AS code
    FROM s JOIN m ON m.ws = s.ws),
nspan AS (
  SELECT ws, contract_id, min(at) AS first_n, max(at) AS last_n
    FROM sm WHERE code = 'N' GROUP BY 1, 2),
cls AS (
  SELECT n.ws, n.contract_id,
         CASE WHEN count(sn.at) = 0 THEN 'NO_CENSUS'
              WHEN count(*) FILTER (WHERE EXISTS (
                     SELECT 1 FROM jsonb_array_elements(coalesce(sn.sample, '[]'::jsonb)) x
                      WHERE x ->> 'contract_id' = n.contract_id)) > 0 THEN 'LIVE_NOT_CURRENT'
              ELSE 'LEFT_LIVE_LIST' END AS cls
    FROM nspan n
    LEFT JOIN snap sn ON sn.at BETWEEN n.first_n - interval '2 minutes'
                                   AND n.last_n + interval '2 minutes'
   GROUP BY 1, 2),
agg AS (
  SELECT sm.ws, count(DISTINCT sm.at) AS samples, count(*) AS member_samples,
         count(*) FILTER (WHERE sm.code IN ('S', 'R', 'G', 'P', 'K')) AS cur,
         count(*) FILTER (WHERE sm.code = 'X') AS ext,
         count(*) FILTER (WHERE sm.code = 'U') AS unheld,
         count(*) FILTER (WHERE sm.code = 'N') AS n_all,
         count(*) FILTER (WHERE sm.code = 'N' AND c.cls = 'LEFT_LIVE_LIST') AS n_left,
         count(*) FILTER (WHERE sm.code = 'N' AND c.cls = 'LEFT_LIVE_LIST'
                          AND sm.event_start > sm.t) AS n_left_pregame,
         count(*) FILTER (WHERE sm.code = 'N' AND c.cls = 'LIVE_NOT_CURRENT') AS n_live,
         count(*) FILTER (WHERE sm.code = 'N' AND c.cls = 'NO_CENSUS') AS n_no_census
    FROM sm LEFT JOIN cls c ON c.ws = sm.ws AND c.contract_id = sm.contract_id
   GROUP BY 1),
rd AS (
  SELECT at, (payload #>> '{freshness,priority_universe,active_refresh,totals,reads}')::int AS reads
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 14:42:24+00'),
rdd AS (
  SELECT at, reads - lag(reads) OVER (ORDER BY at) AS dr,
         extract(epoch FROM at - lag(at) OVER (ORDER BY at)) AS ds
    FROM rd),
rpw AS (
  SELECT floor(extract(epoch FROM at) / 3600) * 3600 AS ws,
         round((sum(dr) FILTER (WHERE dr >= 0) / nullif(sum(ds) FILTER (WHERE dr >= 0), 0) * 60)::numeric, 2) AS reads_per_min
    FROM rdd GROUP BY 1)
SELECT to_timestamp(a.ws) AS window_start, a.samples, a.member_samples, a.cur, a.ext,
       a.unheld, a.n_all, a.n_live, a.n_left, a.n_left_pregame, a.n_no_census,
       round(a.cur::numeric / nullif(a.member_samples - a.ext, 0), 4) AS window_rate,
       round((a.cur + a.n_left_pregame)::numeric / nullif(a.member_samples - a.ext, 0), 4)
         AS bound_if_left_pregame_current,
       rpw.reads_per_min
  FROM agg a LEFT JOIN rpw ON rpw.ws = a.ws
 ORDER BY a.ws;
\echo === V2. per window and member: the N members no complete census names ===
WITH w AS (
  SELECT (payload ->> 'window_start')::float8 AS ws, payload
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_WINDOW'
     AND (payload ->> 'window_start')::float8 >= 1791558000),
m AS (
  SELECT w.ws, (e.ord - 1)::int AS idx, e.r ->> 0 AS contract_id,
         (e.r ->> 5)::float8 AS event_start
    FROM w, jsonb_array_elements(w.payload -> 'members') WITH ORDINALITY AS e(r, ord)),
s AS (
  SELECT at, (payload ->> 'window_start')::float8 AS ws, payload ->> 'codes' AS codes
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_SAMPLE' AND at > timestamptz '2026-10-09 14:42:24+00'
     AND (payload ->> 'window_start')::float8 >= 1791558000),
snap AS (
  SELECT at, payload #> '{freshness,priority_universe,census,sample}' AS sample
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 14:42:24+00'
     AND (payload #>> '{freshness,priority_universe,census,not_current}')::int <= 40),
nspan AS (
  SELECT m.ws, m.contract_id, max(m.event_start) AS event_start, count(*) AS samples_n,
         min(s.at) AS first_n, max(s.at) AS last_n
    FROM s JOIN m ON m.ws = s.ws AND substr(s.codes, m.idx + 1, 1) = 'N'
   GROUP BY 1, 2),
cls AS (
  SELECT n.ws, n.contract_id, n.event_start, n.samples_n, n.first_n, n.last_n,
         count(sn.at) AS census_snapshots,
         count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM jsonb_array_elements(coalesce(sn.sample, '[]'::jsonb)) x
            WHERE x ->> 'contract_id' = n.contract_id)) AS census_named
    FROM nspan n
    LEFT JOIN snap sn ON sn.at BETWEEN n.first_n - interval '2 minutes'
                                   AND n.last_n + interval '2 minutes'
   GROUP BY 1, 2, 3, 4, 5, 6)
SELECT to_timestamp(c.ws) AS window_start, c.contract_id, c.samples_n,
       c.census_snapshots, c.census_named, to_timestamp(c.event_start) AS event_start,
       c.first_n, c.last_n, r.active AS active_now, r.priority AS priority_now,
       r.required_reason AS reason_now
  FROM cls c LEFT JOIN market_plane_registry r ON r.contract_id = c.contract_id
 WHERE c.census_named = 0
 ORDER BY c.ws, c.samples_n DESC, c.contract_id;
\echo === V3. the whole span since the RC6.1 boot ===
WITH w AS (
  SELECT (payload ->> 'window_start')::float8 AS ws, payload
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_WINDOW'
     AND (payload ->> 'window_start')::float8 >= 1791558000),
m AS (
  SELECT w.ws, (e.ord - 1)::int AS idx, e.r ->> 0 AS contract_id,
         (e.r ->> 5)::float8 AS event_start
    FROM w, jsonb_array_elements(w.payload -> 'members') WITH ORDINALITY AS e(r, ord)),
s AS (
  SELECT at, extract(epoch FROM at) AS t, (payload ->> 'window_start')::float8 AS ws,
         payload ->> 'codes' AS codes
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_SAMPLE' AND at > timestamptz '2026-10-09 14:42:24+00'
     AND (payload ->> 'window_start')::float8 >= 1791558000),
snap AS (
  SELECT at, payload #> '{freshness,priority_universe,census,sample}' AS sample
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT' AND at > timestamptz '2026-10-09 14:42:24+00'
     AND (payload #>> '{freshness,priority_universe,census,not_current}')::int <= 40),
sm AS (
  SELECT s.ws, s.at, s.t, m.contract_id, m.event_start,
         substr(s.codes, m.idx + 1, 1) AS code
    FROM s JOIN m ON m.ws = s.ws),
nspan AS (
  SELECT ws, contract_id, min(at) AS first_n, max(at) AS last_n
    FROM sm WHERE code = 'N' GROUP BY 1, 2),
cls AS (
  SELECT n.ws, n.contract_id,
         CASE WHEN count(sn.at) = 0 THEN 'NO_CENSUS'
              WHEN count(*) FILTER (WHERE EXISTS (
                     SELECT 1 FROM jsonb_array_elements(coalesce(sn.sample, '[]'::jsonb)) x
                      WHERE x ->> 'contract_id' = n.contract_id)) > 0 THEN 'LIVE_NOT_CURRENT'
              ELSE 'LEFT_LIVE_LIST' END AS cls
    FROM nspan n
    LEFT JOIN snap sn ON sn.at BETWEEN n.first_n - interval '2 minutes'
                                   AND n.last_n + interval '2 minutes'
   GROUP BY 1, 2)
SELECT count(DISTINCT sm.at) AS samples, count(*) AS member_samples,
       count(*) FILTER (WHERE sm.code IN ('S', 'R', 'G', 'P', 'K')) AS cur,
       count(*) FILTER (WHERE sm.code = 'X') AS ext,
       count(*) FILTER (WHERE sm.code = 'N') AS n_all,
       count(*) FILTER (WHERE sm.code = 'N' AND c.cls = 'LIVE_NOT_CURRENT') AS n_live,
       count(*) FILTER (WHERE sm.code = 'N' AND c.cls = 'LEFT_LIVE_LIST') AS n_left,
       count(*) FILTER (WHERE sm.code = 'N' AND c.cls = 'LEFT_LIVE_LIST'
                        AND sm.event_start > sm.t) AS n_left_pregame,
       count(*) FILTER (WHERE sm.code = 'N' AND c.cls = 'NO_CENSUS') AS n_no_census,
       count(*) FILTER (WHERE sm.code = 'U') AS unheld
  FROM sm LEFT JOIN cls c ON c.ws = sm.ws AND c.contract_id = sm.contract_id;
