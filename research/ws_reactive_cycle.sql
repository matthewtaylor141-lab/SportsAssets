-- Latest collector cycle (read-only): what it opened, how many events carried
-- a primary PinnAPI quote, its refusals, and when it ran; plus feed cache
-- counters. Context for whether WS-reactive seeds can exist and change.
\echo '== C0 · last cycle summary =='
SELECT value->'writer'->>'build' AS build, to_timestamp((value->>'at')::float8) AS at,
       value->>'state' AS state, value->>'cycle_label' AS label,
       value->>'elapsed_s' AS elapsed_s, value->>'evaluated' AS evaluated,
       value->>'written' AS written, value->'refusals' AS refusals
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
\echo '== C1 · last cycle top-level keys =='
SELECT jsonb_object_keys(value) AS k FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle' ORDER BY 1;
\echo '== C2 · step / funnel counters if present =='
SELECT left(coalesce(value->'step', value->'steps', value->'funnel', value->'step_counts')::text, 1500) AS step
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
\echo '== C3 · feed cache counters now =='
SELECT value->'cache'->'counts' AS counts, value->'cache'->>'markets_age_unknown' AS age_unknown,
       value->'cache'->>'markets' AS markets, value->'cache'->>'events' AS events,
       value->'cache'->'markets_by_sport_type_phase' AS by_sport, value->>'state' AS state,
       to_timestamp((value->>'beat_at')::float8) AS beat_at
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
