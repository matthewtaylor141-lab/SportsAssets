-- READ-ONLY. THE ODDS SOURCE'S REQUEST BUDGET AS THE CYCLE HEARTBEAT RECORDS
-- IT, AND THE HEARTBEAT'S TOP-LEVEL SECTIONS, to explain valuation-free days.
-- No credential is selected (quota counters and refusal names only).

\echo '== O1 · cycle heartbeat: top-level keys =='
SELECT string_agg(k, ', ' ORDER BY k) AS keys
  FROM ingestion_state s, LATERAL jsonb_object_keys(s.value) AS k
 WHERE s.key = 'ext_pinnacle_last_cycle';

\echo '== O2 · credits, cooldown, state and why, from the cycle heartbeat =='
SELECT to_timestamp((value->>'at')::float8) AS written_at, value->>'state' AS state,
       left(value->>'why', 300) AS why,
       jsonb_pretty(value->'credits') AS credits,
       jsonb_pretty(value->'cooldown_resume') AS cooldown_resume
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
