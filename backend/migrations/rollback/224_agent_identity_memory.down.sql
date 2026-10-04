-- Rollback of 224 (the seven agents' identity, voice, memory and
-- conversations). Refuses while any identity or voice profile beyond the
-- seeded version 1 exists, or any row carries an owner approval: those are
-- records of people's decisions and are never dropped as cleanup. Memory
-- and conversation rows are DERIVED from durable records (the learner
-- re-derives them) and do not block it. Touches only 224's objects.
DO $$
BEGIN
    IF to_regclass('agent_identity_versions') IS NOT NULL
       AND EXISTS (SELECT 1 FROM agent_identity_versions
                    WHERE identity_version > 1
                       OR approved_by <> 'PENDING_OWNER_APPROVAL') THEN
        RAISE EXCEPTION 'agent_identity_versions holds a later version or an '
                        'approval; rollback refused';
    END IF;
    IF to_regclass('agent_voice_profiles') IS NOT NULL
       AND EXISTS (SELECT 1 FROM agent_voice_profiles
                    WHERE version > 1
                       OR approved_by <> 'PENDING_OWNER_APPROVAL') THEN
        RAISE EXCEPTION 'agent_voice_profiles holds a later version or an '
                        'approval; rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS agent_conversation_messages;
DROP TABLE IF EXISTS agent_memory_events;
DROP TABLE IF EXISTS agent_voice_profiles;
DROP TABLE IF EXISTS agent_identity_versions;
DROP FUNCTION IF EXISTS agent_conv_guard();
DROP FUNCTION IF EXISTS agent_memory_append_only();
DROP FUNCTION IF EXISTS agent_voice_profiles_guard();
DROP FUNCTION IF EXISTS agent_identity_next_version();
DROP FUNCTION IF EXISTS agent_identity_record_is_immutable();
DROP FUNCTION IF EXISTS agent_memory_refs_grounded(jsonb);
