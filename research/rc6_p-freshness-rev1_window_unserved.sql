-- RC6.2 lane p-freshness, independent review (rev1). Read only.
-- The frozen freshness window of 2026-10-09 15:00Z (RC6.1 plane 3d5af039,
-- booted 14:42:24Z) keeps 1-5 members coded N with reason
-- STREAM:SNAPSHOT_OLDER_THAN_THE_BOUND (no REFRESH suffix) at every sample
-- from 15:05Z, while book reads ran below 12 a minute. Question: were those
-- members live priority members (the plane's census names every live member
-- not current), or members frozen at 15:00Z that have since left the live
-- set the plane refresh reads?
--   W1  per sample: N members, how many the nearest snapshot census names
--   W2  per N member: samples N, census presence, live registry row now
\echo === W1. per frozen-window sample: N members and census presence ===
WITH w AS (
  SELECT payload FROM market_plane_events WHERE event_key = 'fwin:1791558000'),
m AS (
  SELECT (e.ord - 1)::int AS idx, e.r ->> 0 AS contract_id, e.r ->> 1 AS tier
    FROM w, jsonb_array_elements(w.payload -> 'members') WITH ORDINALITY AS e(r, ord)),
s AS (
  SELECT at, payload ->> 'codes' AS codes
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_SAMPLE'
     AND at >= timestamptz '2026-10-09 15:05:00+00'
     AND at <  timestamptz '2026-10-09 16:00:00+00'
     AND (payload ->> 'window_start')::float8 = 1791558000),
snap AS (
  SELECT at, payload #> '{freshness,priority_universe,census,sample}' AS sample
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT'
     AND at >= timestamptz '2026-10-09 14:55:00+00'
     AND at <  timestamptz '2026-10-09 16:05:00+00'),
n AS (
  SELECT s.at, m.contract_id, m.tier,
         (SELECT sn.sample FROM snap sn ORDER BY abs(extract(epoch FROM sn.at - s.at)) LIMIT 1) AS near_sample
    FROM s JOIN m ON substr(s.codes, m.idx + 1, 1) = 'N')
SELECT at, count(*) AS n_members,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(near_sample, '[]'::jsonb)) x
                                       WHERE x ->> 'contract_id' = n.contract_id)) AS named_by_census,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(near_sample, '[]'::jsonb)) x
                                           WHERE x ->> 'contract_id' = n.contract_id)) AS not_named_by_census
  FROM n GROUP BY at ORDER BY at;
\echo === W2. per N member of the window: census presence and the live registry row now ===
WITH w AS (
  SELECT payload FROM market_plane_events WHERE event_key = 'fwin:1791558000'),
m AS (
  SELECT (e.ord - 1)::int AS idx, e.r ->> 0 AS contract_id, e.r ->> 1 AS tier
    FROM w, jsonb_array_elements(w.payload -> 'members') WITH ORDINALITY AS e(r, ord)),
s AS (
  SELECT at, payload ->> 'codes' AS codes
    FROM market_plane_events
   WHERE kind = 'FRESHNESS_SAMPLE'
     AND at >= timestamptz '2026-10-09 15:05:00+00'
     AND at <  timestamptz '2026-10-09 16:00:00+00'
     AND (payload ->> 'window_start')::float8 = 1791558000),
snap AS (
  SELECT at, payload #> '{freshness,priority_universe,census,sample}' AS sample
    FROM market_plane_events
   WHERE kind = 'SNAPSHOT'
     AND at >= timestamptz '2026-10-09 14:55:00+00'
     AND at <  timestamptz '2026-10-09 16:05:00+00'),
n AS (
  SELECT m.contract_id, m.tier, count(*) AS samples_n, min(s.at) AS first_n, max(s.at) AS last_n
    FROM s JOIN m ON substr(s.codes, m.idx + 1, 1) = 'N'
   GROUP BY 1, 2),
c AS (
  SELECT n.contract_id,
         count(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(sn.sample, '[]'::jsonb)) x
                                         WHERE x ->> 'contract_id' = n.contract_id)) AS census_named,
         count(*) AS census_snapshots,
         max(x2.why) AS census_why, max(x2.rf) AS census_read_outcome
    FROM n JOIN snap sn ON sn.at BETWEEN n.first_n - interval '2 minutes' AND n.last_n + interval '2 minutes'
    LEFT JOIN LATERAL (SELECT x ->> 'why' AS why, x ->> ('re' || 'fresh') AS rf
                         FROM jsonb_array_elements(coalesce(sn.sample, '[]'::jsonb)) x
                        WHERE x ->> 'contract_id' = n.contract_id LIMIT 1) x2 ON true
   GROUP BY 1)
SELECT n.contract_id, n.tier, n.samples_n, n.first_n, n.last_n,
       c.census_named, c.census_snapshots, c.census_why, c.census_read_outcome,
       r.active AS active_now, r.priority AS priority_now,
       r.required_reason AS reason_now, r.event_start,
       r.updated_at AS registry_row_at
  FROM n LEFT JOIN c USING (contract_id)
  LEFT JOIN market_plane_registry r ON r.contract_id = n.contract_id
 ORDER BY n.samples_n DESC, n.contract_id;
