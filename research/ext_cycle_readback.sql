-- The entry lane's last cycle (ext_pinnacle_last_cycle), read-only: why no
-- valuation was written. Counters and refusals only; no credentials.
\echo '== E0 · last writer cycle: timing, evaluated, written, refusals, credits =='
SELECT value->>'at' AS at_epoch, value->>'state' AS state, value->>'ran' AS ran,
       value->'step_timing_s' AS step_timing_s,
       value->>'evaluated' AS evaluated, value->>'written' AS written,
       left((value->'refusals')::text, 2500) AS refusals,
       left((value->'credits')::text, 800) AS credits,
       left((value->'venue_errors')::text, 800) AS venue_errors,
       left((value->'funnel')::text, 1500) AS funnel
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
\echo '== E1 · the summary keys present =='
SELECT jsonb_object_keys(value) AS k FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle' ORDER BY 1;
\echo '== E2 · venue cooldown rows =='
SELECT key, left(value::text, 600) FROM ingestion_state WHERE key LIKE 'venue_cooldown:%';
