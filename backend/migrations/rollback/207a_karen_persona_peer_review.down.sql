-- Refuses while Karen has a persona version or a conversation, or any
-- challenge carries what 207a added (a CHIEF_ALLOCATOR target or an
-- evaluation's evidence): those records are never dropped as cleanup.
DO $$
DECLARE
    present boolean;
BEGIN
    IF EXISTS (SELECT 1 FROM agent_persona_versions WHERE agent_id = 'KAREN')
       OR EXISTS (SELECT 1 FROM agent_chat_conversations
                   WHERE agent_id = 'KAREN') THEN
        RAISE EXCEPTION 'Karen persona records exist; rollback refused';
    END IF;
    IF to_regclass('karen_challenges') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM karen_challenges WHERE '
                ' target_agent = ''CHIEF_ALLOCATOR'' OR '
                ' resolution_evidence_refs IS NOT NULL)' INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'karen_challenges hold 207a records; rollback '
                            'refused';
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('karen_challenges') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS karen_challenges_207a_guard_trg
            ON karen_challenges;
        ALTER TABLE karen_challenges
            DROP CONSTRAINT IF EXISTS karen_challenges_dispute_independent_ck;
        ALTER TABLE karen_challenges
            DROP CONSTRAINT IF EXISTS karen_challenges_resolution_refs_ck;
        ALTER TABLE karen_challenges
            DROP CONSTRAINT IF EXISTS karen_challenges_category_ck;
        ALTER TABLE karen_challenges
            DROP CONSTRAINT IF EXISTS karen_challenges_target_ck;
        ALTER TABLE karen_challenges
            ADD CONSTRAINT karen_challenges_target_ck CHECK (
                target_agent IN ('DEREK', 'XAVIER', 'AUDREY'));
        ALTER TABLE karen_challenges DROP COLUMN IF EXISTS category;
        ALTER TABLE karen_challenges
            DROP COLUMN IF EXISTS resolution_evidence_refs;
    END IF;
END $$;
DROP FUNCTION IF EXISTS karen_challenges_207a_guard();

ALTER TABLE agent_persona_versions
    DROP CONSTRAINT IF EXISTS agent_persona_versions_agent_id_check;
ALTER TABLE agent_persona_versions
    ADD CONSTRAINT agent_persona_versions_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY'));
ALTER TABLE agent_chat_conversations
    DROP CONSTRAINT IF EXISTS agent_chat_conversations_agent_id_check;
ALTER TABLE agent_chat_conversations
    ADD CONSTRAINT agent_chat_conversations_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY'));
