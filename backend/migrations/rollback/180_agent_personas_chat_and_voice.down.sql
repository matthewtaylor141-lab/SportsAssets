-- down for 180. Refuses while any conversation message or any persona
-- version beyond the seeded defaults is recorded: they are the record of
-- what each agent said, from which facts, in which persona and voice.
DO $$
BEGIN
    IF to_regclass('agent_chat_messages') IS NOT NULL
       AND EXISTS (SELECT 1 FROM agent_chat_messages) THEN
        RAISE EXCEPTION 'agent conversations are recorded; 180 is not '
                        'rolled back over them';
    END IF;
    IF to_regclass('agent_persona_versions') IS NOT NULL
       AND EXISTS (SELECT 1 FROM agent_persona_versions
                   WHERE version > 1) THEN
        RAISE EXCEPTION 'persona versions beyond the defaults are recorded; '
                        '180 is not rolled back over them';
    END IF;
END $$;
DROP TABLE IF EXISTS agent_chat_turns;
DROP TABLE IF EXISTS agent_chat_messages;
DROP TABLE IF EXISTS agent_chat_conversations;
DROP TABLE IF EXISTS agent_voice_resolutions;
DROP TABLE IF EXISTS agent_persona_versions;
DROP FUNCTION IF EXISTS agent_chat_turn_moves_once();
DROP FUNCTION IF EXISTS agent_persona_record_is_kept();
