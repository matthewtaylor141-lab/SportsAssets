-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 9:
-- why the exact-fixture misses are not classified NOT_YET_POSTED: the feed cache eviction counter
-- and the near-start evidence the heartbeat records.
\echo == A top-level keys of the PinnAPI feed heartbeat
SELECT jsonb_object_keys(value) AS heartbeat_key FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == B any heartbeat field whose name mentions evict, cache or capacity (path and value)
WITH RECURSIVE t(path, v) AS (
  SELECT k, val FROM ingestion_state, jsonb_each(value) AS e(k, val) WHERE key = 'pinnapi_feed_last'
  UNION ALL
  SELECT t.path || '.' || e.k, e.val FROM t, jsonb_each(CASE WHEN jsonb_typeof(t.v) = 'object' THEN t.v ELSE '{}'::jsonb END) AS e(k, val)
   WHERE length(t.path) < 80)
SELECT path, left(v::text, 160) AS val FROM t
 WHERE (path ILIKE '%evict%' OR path ILIKE '%capacity%' OR path ILIKE '%cache%' OR path ILIKE '%events_held%' OR path ILIKE '%not_yet%')
   AND jsonb_typeof(v) <> 'object'
 ORDER BY path LIMIT 40;

\echo == C ledger rows carrying the NOT_YET_POSTED code or the other-start code in the last 3 days (is the classifier ever firing)
SELECT sport_key, count(DISTINCT provider_event_id) AS events,
       count(DISTINCT provider_event_id) FILTER (WHERE codes::text LIKE '%PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED%') AS not_yet_posted,
       count(DISTINCT provider_event_id) FILTER (WHERE codes::text LIKE '%PINNAPI_FEED_NAMES_THE_TEAMS_ONLY_AT_OTHER_START_TIMES%') AS teams_only_at_other_starts,
       count(DISTINCT provider_event_id) FILTER (WHERE codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%') AS no_exact
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '3 days'
 GROUP BY 1 HAVING count(DISTINCT provider_event_id) FILTER (WHERE codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%') > 0
 ORDER BY 5 DESC LIMIT 20;

\echo == D by day: NOT_YET_POSTED against NO_EXACT events
SELECT date_trunc('day', cycle_at) AS day_utc,
       count(DISTINCT provider_event_id) FILTER (WHERE codes::text LIKE '%PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED%') AS not_yet_posted,
       count(DISTINCT provider_event_id) FILTER (WHERE codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%') AS no_exact
  FROM ext_candidate_outcomes WHERE cycle_at >= now() - interval '6 days'
 GROUP BY 1 ORDER BY 1;
