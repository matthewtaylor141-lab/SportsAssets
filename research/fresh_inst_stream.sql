-- Institutional market-data stream state in production (read-only).
SELECT k FROM ingestion_state, jsonb_object_keys(value) AS k
 WHERE key = 'ext_pinnacle_last_cycle' ORDER BY k;

SELECT key, k, left(v::text, 1500) AS v
  FROM ingestion_state, jsonb_each(value) AS e(k, v)
 WHERE key IN ('ext_pinnacle_last_cycle')
   AND (k ILIKE '%institution%' OR k ILIKE '%p5%' OR k ILIKE '%md%' OR k ILIKE '%stream%');

SELECT key, left(value::text, 1500) FROM ingestion_state
 WHERE key ILIKE '%institution%' OR key ILIKE '%p5%' OR key ILIKE '%loop_health%' ORDER BY key;

SELECT table_name FROM information_schema.tables
 WHERE table_schema = 'public' AND (table_name ILIKE '%institution%' OR table_name ILIKE '%p5%');
