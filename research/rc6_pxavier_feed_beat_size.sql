-- READ-ONLY. RC6.2 lane p-xavier (fix stage). The held-review scheduler's
-- counters are to be carried on the PinnAPI feed heartbeat, whose writer
-- caps the serialized value at 65,536 bytes (pinnapi_feed_runtime.
-- HEARTBEAT_MAX_BYTES; past it the census and discovery detail is cut).
-- Here: the size of the persisted heartbeat now and of each top-level
-- block, to show the headroom. Every statement is a SELECT.

\echo B1 the persisted feed heartbeat: total serialized size, truncated flag, beat instant
SELECT length(value::text) AS chars, value->>'heartbeat_truncated' AS truncated,
       value->>'beat_at' AS beat_at
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo B2 each top-level block of the feed heartbeat by serialized size
SELECT e.key, length(e.value::text) AS chars
  FROM ingestion_state i, jsonb_each(i.value) e
 WHERE i.key = 'pinnapi_feed_last'
 ORDER BY 2 DESC LIMIT 25;
