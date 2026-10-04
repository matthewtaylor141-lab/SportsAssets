-- R29 production readback (read only): migration 224 objects, the seeded
-- identities (Allie), voice profiles, the workers boot, and the lost
-- opportunity SCORES component since the deploy.
\echo == boot
SELECT left(value::text, 400) AS workers_boot FROM ingestion_state WHERE key = 'workers_boot';
\echo == migration 224 tables
SELECT t AS object, to_regclass(t) IS NOT NULL AS present FROM unnest(ARRAY[
  'agent_identity_versions','agent_voice_profiles','agent_memory_events',
  'agent_conversation_messages']) AS t;
\echo == migration 224 triggers and functions
SELECT tgname AS trigger FROM pg_trigger WHERE tgname IN ('agent_conv_guard_trg','agent_conv_immutable_trg',
  'agent_identity_versions_immutable_trg','agent_identity_versions_next_trg','agent_memory_append_only_trg',
  'agent_voice_profiles_guard_trg','agent_voice_profiles_immutable_trg') ORDER BY 1;
SELECT proname AS function FROM pg_proc WHERE proname IN ('agent_conv_guard','agent_identity_next_version',
  'agent_identity_record_is_immutable','agent_memory_append_only','agent_memory_refs_grounded',
  'agent_voice_profiles_guard') ORDER BY 1;
\echo == identities (v1)
SELECT agent_id, identity_version, display_name, title, presentation, authority_status, approved_by, approved_at, left(content_sha,12) AS sha
  FROM agent_identity_versions ORDER BY agent_id, identity_version;
\echo == voice profiles
SELECT agent_id, voice_profile_id, display_name, assignment, provider_voice_id IS NULL AS no_voice_id, approved_by
  FROM agent_voice_profiles ORDER BY agent_id;
\echo == agent memory / conversations so far
SELECT (SELECT count(*) FROM agent_memory_events) AS memory_events,
       (SELECT count(*) FROM agent_conversation_messages) AS conversation_messages;
\echo == lost opportunity components since the API boot (SCORES was FAILED every cycle on R28b)
SELECT component, status, count(*), max(started_at) AS last, max(left(error,140)) AS err
  FROM lol_runs WHERE started_at >= now() - interval '90 minutes' GROUP BY 1,2 ORDER BY 1,2;
SELECT count(*) AS opportunity_scores, max(computed_at) AS newest FROM lol_opportunity_scores;
