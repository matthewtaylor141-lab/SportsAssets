-- READ-ONLY. THE ODDS SOURCE'S REQUEST BUDGET AS THE CYCLE HEARTBEAT RECORDS
-- IT, AND THE HEARTBEAT'S TOP-LEVEL SECTIONS, to explain valuation-free days.
-- No credential is selected (quota counters and refusal names only).

\echo '== O1 · cycle heartbeat: top-level keys =='
SELECT string_agg(k, ', ' ORDER BY k) AS keys
  FROM ingestion_state s, LATERAL jsonb_object_keys(s.value) AS k
 WHERE s.key = 'ext_pinnacle_last_cycle';

\echo '== O2 · every heartbeat value mentioning credits / remaining / quota =='
SELECT s.key, p.path, left(p.val, 300) AS val
  FROM ingestion_state s,
       LATERAL (SELECT kv.key AS path, kv.value::text AS val
                  FROM jsonb_each(s.value) kv) p
 WHERE s.key LIKE 'ext_pinnacle%'
   AND (p.val ILIKE '%credit%' OR p.val ILIKE '%remaining%'
        OR p.val ILIKE '%quota%' OR p.path ILIKE '%odds%');

\echo '== O3 · ingestion_state keys that look like odds-source state =='
SELECT key, left(value::text, 400) AS value
  FROM ingestion_state
 WHERE key ILIKE '%odds%' OR key ILIKE '%credit%' OR key ILIKE '%quota%';
