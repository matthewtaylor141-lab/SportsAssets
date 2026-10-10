-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 10:
-- the PinnAPI feed cache occupancy against its two bounds, and the eviction age profile.
\echo == A every scalar field of the cache section of the heartbeat that is not an events_by_sport or frames counter
WITH RECURSIVE t(path, v) AS (
  SELECT k, val FROM ingestion_state, jsonb_each(value -> 'cache') AS e(k, val) WHERE key = 'pinnapi_feed_last'
  UNION ALL
  SELECT t.path || '.' || e.k, e.val FROM t, jsonb_each(CASE WHEN jsonb_typeof(t.v) = 'object' THEN t.v ELSE '{}'::jsonb END) AS e(k, val)
   WHERE length(t.path) < 80)
SELECT path, left(v::text, 120) AS val FROM t
 WHERE jsonb_typeof(v) <> 'object' AND path NOT LIKE 'frames_by_sport_type%' AND path NOT LIKE 'child_records%'
   AND path NOT LIKE 'confirmations%' AND path NOT LIKE 'counts.frames%'
 ORDER BY path LIMIT 120;

\echo == B venue events the census could not place because the feed holds none, by sport family
SELECT left((value -> 'coverage_census' -> 'by_sport_family_phase_state')::text, 3500) AS by_sport_family_phase_state
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == C scope and the sports the owner subscribes
SELECT left((value -> 'scope')::text, 1500) AS scope, left((value -> 'sport_ids')::text, 200) AS sport_ids,
       left((value -> 'state')::text, 200) AS state, left((value -> 'transitions')::text, 600) AS transitions
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
