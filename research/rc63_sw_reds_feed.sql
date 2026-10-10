-- READ-ONLY. RC6.3b software-reds audit part 3: the PinnAPI feed owner heartbeat
-- (ingestion_state key pinnapi_feed_last) -- cache size against its bounds, the
-- cumulative events_evicted counter pinnapi_names.absence reads, events held per
-- sport, the coverage census states, the feed scope. SELECT only.

\echo == F1 heartbeat identity and age
SELECT key, jsonb_typeof(value) AS t, length(value::text) AS bytes,
       value->>'state' AS state, value->>'runtime_id' AS runtime_id,
       value->>'enabled_env' AS enabled_env, value->>'last_owner_restart' AS last_owner_restart,
       value->>'beat_at' AS beat_at, value->>'at' AS at
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == F2 top-level keys of the heartbeat
SELECT k FROM ingestion_state, jsonb_object_keys(value) AS k WHERE key = 'pinnapi_feed_last' ORDER BY 1 LIMIT 80;

\echo == F3 cache size, bounds, evictions
SELECT value->'cache'->>'events' AS cached_events, value->'cache'->>'markets' AS cached_markets,
       value->'cache'->'bounds' AS bounds, value->'cache'->'counts'->>'events_evicted' AS events_evicted,
       value->'cache'->'events_by_sport' AS events_by_sport,
       value->'cache'->'authority' AS authority
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == F4 cache counts (all counters)
SELECT left((value->'cache'->'counts')::text, 3500) AS counts
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == F5 scope and coverage census (truncated)
SELECT left((value->'scope')::text, 600) AS scope,
       left((value->'coverage_census')::text, 3500) AS census
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == F6 native discovery digest (truncated)
SELECT left((value->'native_discovery')::text, 2500) AS native_discovery
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
