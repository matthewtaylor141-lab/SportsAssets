-- R29 production closeout (read only): migration ledger 216-224, migration
-- 224 objects, the seven identities, opportunity-score writes since the
-- workers boot, and worker/runtime failure records since the deploy.
\echo == schema_migrations 216-224
SELECT v AS version, EXISTS (SELECT 1 FROM schema_migrations m WHERE m.version::text LIKE v || '%') AS applied
  FROM unnest(ARRAY['216','217','218','219','220','221','222','223','224']) v ORDER BY 1;
SELECT version::text FROM schema_migrations WHERE version::text >= '216' ORDER BY 1;
\echo == migration 224 objects
SELECT t AS object, to_regclass(t) IS NOT NULL AS present FROM unnest(ARRAY[
  'agent_identity_versions','agent_voice_profiles','agent_memory_events','agent_conversation_messages']) t;
SELECT count(*) FILTER (WHERE tgname LIKE 'agent_%') AS agent_triggers FROM pg_trigger;
SELECT conname FROM pg_constraint WHERE conrelid = 'agent_identity_versions'::regclass AND contype = 'c' ORDER BY 1;
\echo == identities
SELECT agent_id, identity_version, display_name, title, presentation, authority_status, approved_by, approved_at
  FROM agent_identity_versions ORDER BY agent_id, identity_version;
\echo == workers boot
SELECT left(value::text, 400) AS workers_boot FROM ingestion_state WHERE key = 'workers_boot';
\echo == opportunity scores: rows written per 15 minutes since 17:00Z
SELECT date_trunc('hour', computed_at) + floor(extract(minute FROM computed_at)/15)*interval '15 min' AS bucket, count(*)
  FROM lol_opportunity_scores WHERE computed_at >= date_trunc('day', now()) + interval '17 hours' GROUP BY 1 ORDER BY 1;
SELECT count(*) AS total, max(computed_at) AS newest, now() AS db_now FROM lol_opportunity_scores;
\echo == lost-opportunity component runs since the workers boot
SELECT component, status, count(*), min(started_at) AS first, max(started_at) AS last, max(left(error,160)) AS err
  FROM lol_runs WHERE started_at >= date_trunc('day', now()) + interval '17 hours 19 minutes' GROUP BY 1,2 ORDER BY 1,2;
