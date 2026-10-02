-- PinnAPI feed C1 readback (read-only). Never reads or prints the key.
\echo '== F0 · lease 7723901544120036 and ext writer 7723901544120034 holders (the lease is a DEDICATED connection beside the writer: same host, distinct pid) =='
SELECT l.objid::bigint + (l.classid::bigint << 32) AS lock_key, l.pid, a.backend_start,
       a.application_name, a.state, a.client_addr
  FROM pg_locks l JOIN pg_stat_activity a USING (pid)
 WHERE l.locktype = 'advisory' AND l.granted
   AND (l.objid::bigint + (l.classid::bigint << 32)) IN (7723901544120034, 7723901544120036)
 ORDER BY 1;

\echo '== F1 · control rows (arm must be exactly true; scope defaults to baseball) =='
SELECT key, value, jsonb_typeof(value) AS type
  FROM ingestion_state WHERE key IN ('pinnapi_feed', 'pinnapi_feed_scope');

\echo '== F2 · heartbeat: state, refusal, enabled_env, beat age, size =='
SELECT value->>'state' AS state, value->>'refused' AS refused,
       value->>'enabled_env' AS enabled_env, value->>'c1_decision_effect' AS decision_effect,
       round((extract(epoch FROM now()) - (value->>'beat_at')::float)::numeric, 1) AS beat_age_s,
       octet_length(value::text) AS bytes, value->>'heartbeat_truncated' AS truncated
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== F3 · heartbeat detail: transitions, cache census, coverage census =='
SELECT jsonb_pretty(jsonb_build_object(
         'transitions', value->'transitions',
         'sport_ids', value->'sport_ids', 'streams', value->'streams',
         'cache', value->'cache',
         'coverage_census', value->'coverage_census'))
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
