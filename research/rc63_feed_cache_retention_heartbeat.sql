-- FEED-1 (rc6/feed-retention), READ ONLY: the PinnAPI feed cache's eviction
-- state on the live heartbeat -- events and markets held against the caps,
-- the eviction counters, the owner state and the heartbeat age -- to size the
-- tombstone ring (how many evictions a retention window must account for)
-- and to state the before-figure the lane's readback compares against; then
-- the last 24 h of NO_EXACT / NOT_YET_POSTED ledger rows per hour and sport.
\echo === pinnapi_feed_last: cache bounds, holdings and eviction counters ===
SELECT to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->>'state' AS state, value->>'runtime_id' AS runtime_id,
       value->'cache'->>'events' AS events,
       value->'cache'->>'markets' AS markets,
       value->'cache'->'bounds' AS bounds,
       value->'cache'->'counts'->>'events_evicted' AS events_evicted,
       value->'cache'->'counts'->>'events_deleted' AS events_deleted,
       value->'cache'->'counts'->>'epochs' AS epochs,
       value->'cache'->'counts'->>'frames' AS frames,
       value->'cache'->'counts'->>'snapshot_builds_swapped_in' AS builds,
       value->'cache'->'events_by_sport' AS events_by_sport,
       value->'cache'->'markets_by_sport' AS markets_by_sport,
       value->'cache'->'child_records' AS child_records,
       value->'cache'->'authority' AS authority,
       value->'last_owner_restart' AS last_owner_restart
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
\echo === the feed's venue census: matched events and states ===
SELECT value->'coverage_census'->>'matched_events' AS matched_events,
       value->'coverage_census'->'events_by_state' AS events_by_state,
       value->'coverage_census'->>'total_contracts' AS total_contracts,
       value->'coverage_census'->>'subscribed_rows' AS subscribed_rows,
       value->'coverage_census'->>'took_ms' AS took_ms
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
\echo === held priority targets and the held-review scheduler ===
SELECT value->'held_priority_targets' AS held,
       value->'held_review_scheduler' AS sched
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
\echo === NO_EXACT rows in the last 24 h by hour and sport (first or beside) ===
SELECT date_trunc('hour', cycle_at) AS hour, sport_key,
       count(*) AS rows_, count(DISTINCT provider_event_id) AS events,
       sum((first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE')::int) AS first_no_exact
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
   AND codes @> '["PINNAPI_PRIMARY_NO_EXACT_FIXTURE"]'::jsonb
 GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 150;
\echo === NOT_YET_POSTED rows in the last 24 h by hour and sport ===
SELECT date_trunc('hour', cycle_at) AS hour, sport_key,
       count(*) AS rows_, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
   AND first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED'
 GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 150;
