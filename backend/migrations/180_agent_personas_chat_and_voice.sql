-- ══════════════════════════════════════════════════════════════════════
-- 180 · THE THREE AGENTS' PERSONAS, THEIR GROUNDED CONVERSATIONS AND VOICE
-- ══════════════════════════════════════════════════════════════════════
--
-- `sportsassets.agents.personas`, `persona_chat`, `persona_speech`.
--
-- agent_persona_versions   Derek's, Xavier's and Audrey's persona and voice
--                          profiles. APPEND-ONLY: a change is the next
--                          version; the ACTIVE profile is the highest version
--                          of the agent. Rolling back appends a copy.
-- agent_voice_resolutions  which ElevenLabs voice (id + name) the server-side
--                          resolver chose for an (agent, persona version),
--                          and how. APPEND-ONLY. Never a credential.
-- agent_chat_conversations one conversation with ONE agent.
-- agent_chat_messages      every turn. APPEND-ONLY. An assistant message
--                          stores BOTH the visible transcript (`body`) and the
--                          pronunciation-normalised text the voice speaks
--                          (`spoken_text`), the facts it cited, and whether it
--                          completed or was INTERRUPTED (a partial answer a
--                          newer user message cancelled).
-- agent_chat_turns         one row per answer in flight; IN_FLIGHT moves once
--                          to COMPLETED / INTERRUPTED / FAILED.
--
-- NOTHING HERE HAS AUTHORITY. No table is read to size, price, authorise or
-- send anything; a persona cannot grant itself a capability.
--
-- ADDITIVE. Five new tables; nothing existing is altered.

BEGIN;

CREATE TABLE IF NOT EXISTS agent_persona_versions (
    agent_id          text        NOT NULL
        CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY')),
    version           integer     NOT NULL CHECK (version >= 1),
    display_name      text        NOT NULL,
    role_title        text        NOT NULL,
    perspective       text        NOT NULL,
    perspective_text  text,
    persona_text      text        NOT NULL,
    style_rules       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    avoid             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    answer_order      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- {"provider": "elevenlabs", "voice_id": null|text, "preferred_names":
    --  [...], "preferred_labels": {...}, "settings": {"stability",
    --  "similarity_boost", "style", "use_speaker_boost", "speed"},
    --  "speaking_rate_hint", "browser_fallback": {...}}
    voice_profile     jsonb       NOT NULL,
    content_sha       text        NOT NULL,
    created_by        text        NOT NULL,
    reason            text        NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, version),
    CONSTRAINT agent_persona_voice_provider_ck CHECK (
        voice_profile->>'provider' = 'elevenlabs')
);

CREATE TABLE IF NOT EXISTS agent_voice_resolutions (
    resolution_id     bigserial   PRIMARY KEY,
    agent_id          text        NOT NULL,
    persona_version   integer     NOT NULL,
    status            text        NOT NULL
        CHECK (status IN ('RESOLVED', 'UNRESOLVED')),
    method            text,
    voice_id          text,
    voice_name        text,
    category          text,
    labels            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    reason            text,
    detail            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    resolved_at       timestamptz NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_voice_resolved_has_id_ck CHECK (
        status <> 'RESOLVED' OR voice_id IS NOT NULL),
    -- never a cloned voice
    CONSTRAINT agent_voice_not_cloned_ck CHECK (
        category IS NULL OR category NOT IN ('cloned', 'famous'))
);
CREATE INDEX IF NOT EXISTS agent_voice_resolutions_agent_idx
    ON agent_voice_resolutions (agent_id, persona_version, resolution_id DESC);

CREATE TABLE IF NOT EXISTS agent_chat_conversations (
    conversation_id   text        PRIMARY KEY,
    agent_id          text        NOT NULL
        CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY')),
    requester_role    text        NOT NULL,
    requester_label   text,
    created_at        timestamptz NOT NULL,
    updated_at        timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_chat_conversations_agent_idx
    ON agent_chat_conversations (agent_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_chat_messages (
    message_id        text        PRIMARY KEY,
    conversation_id   text        NOT NULL
        REFERENCES agent_chat_conversations (conversation_id),
    agent_id          text        NOT NULL,
    seq               integer     NOT NULL CHECK (seq >= 0),
    at                timestamptz NOT NULL,
    role              text        NOT NULL CHECK (role IN ('USER',
                                                           'ASSISTANT')),
    requester_role    text,
    -- THE VISIBLE TRANSCRIPT, exactly as shown
    body              text        NOT NULL,
    -- THE TEXT THE VOICE SPEAKS: the pronunciation-normalised form of body
    spoken_text       text,
    status            text        NOT NULL DEFAULT 'COMPLETE'
        CHECK (status IN ('COMPLETE', 'INTERRUPTED', 'REFUSED')),
    in_reply_to       text,
    turn_id           text,
    interrupted_by    text,
    -- the page's selected position / decision, as sent
    context           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    intent            text,
    depth             text,
    outcome           text,
    -- [{"fact_id", "source", "record_id", "field", "value", "text"}]
    facts             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    missing_evidence  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- {"mode": "LLM"|"RECORDS_ONLY", "model", "failure", "disclosure"};
    -- never a credential
    provider          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    persona_version   integer,
    request_id        text,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_chat_messages_seq_uq UNIQUE (conversation_id, seq),
    CONSTRAINT agent_chat_assistant_spoken_ck CHECK (
        role <> 'ASSISTANT' OR spoken_text IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS agent_chat_messages_conversation_idx
    ON agent_chat_messages (conversation_id, seq);

CREATE TABLE IF NOT EXISTS agent_chat_turns (
    turn_id              text        PRIMARY KEY,
    conversation_id      text        NOT NULL
        REFERENCES agent_chat_conversations (conversation_id),
    agent_id             text        NOT NULL,
    user_message_id      text        NOT NULL,
    state                text        NOT NULL
        CHECK (state IN ('IN_FLIGHT', 'COMPLETED', 'INTERRUPTED', 'FAILED')),
    started_at           timestamptz NOT NULL,
    finished_at          timestamptz,
    interrupted_by       text,
    assistant_message_id text
);
CREATE INDEX IF NOT EXISTS agent_chat_turns_inflight_idx
    ON agent_chat_turns (conversation_id) WHERE state = 'IN_FLIGHT';

-- ── records are never rewritten ─────────────────────────────────────
CREATE OR REPLACE FUNCTION agent_persona_record_is_kept()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: a new version / row is written '
                    'instead', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_persona_versions_kept_trg
    ON agent_persona_versions;
CREATE TRIGGER agent_persona_versions_kept_trg
    BEFORE UPDATE OR DELETE ON agent_persona_versions
    FOR EACH ROW EXECUTE FUNCTION agent_persona_record_is_kept();
DROP TRIGGER IF EXISTS agent_voice_resolutions_kept_trg
    ON agent_voice_resolutions;
CREATE TRIGGER agent_voice_resolutions_kept_trg
    BEFORE UPDATE OR DELETE ON agent_voice_resolutions
    FOR EACH ROW EXECUTE FUNCTION agent_persona_record_is_kept();
DROP TRIGGER IF EXISTS agent_chat_messages_kept_trg ON agent_chat_messages;
CREATE TRIGGER agent_chat_messages_kept_trg
    BEFORE UPDATE OR DELETE ON agent_chat_messages
    FOR EACH ROW EXECUTE FUNCTION agent_persona_record_is_kept();

-- A TURN MOVES ONCE: IN_FLIGHT -> COMPLETED | INTERRUPTED | FAILED. After
-- that only its assistant message may be bound, once. Never deleted.
CREATE OR REPLACE FUNCTION agent_chat_turn_moves_once()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'chat turn % is a record; it is never deleted',
            OLD.turn_id;
    END IF;
    IF (to_jsonb(NEW) - 'state' - 'finished_at' - 'interrupted_by'
                      - 'assistant_message_id')
       IS DISTINCT FROM (to_jsonb(OLD) - 'state' - 'finished_at'
                         - 'interrupted_by' - 'assistant_message_id') THEN
        RAISE EXCEPTION 'chat turn %: only its state moves', OLD.turn_id;
    END IF;
    IF OLD.state = 'IN_FLIGHT' THEN
        RETURN NEW;
    END IF;
    IF NEW.state = OLD.state
       AND NEW.finished_at IS NOT DISTINCT FROM OLD.finished_at
       AND NEW.interrupted_by IS NOT DISTINCT FROM OLD.interrupted_by
       AND OLD.assistant_message_id IS NULL
       AND NEW.assistant_message_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'chat turn % is % and does not move again',
        OLD.turn_id, OLD.state;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_chat_turn_moves_once_trg ON agent_chat_turns;
CREATE TRIGGER agent_chat_turn_moves_once_trg
    BEFORE UPDATE OR DELETE ON agent_chat_turns
    FOR EACH ROW EXECUTE FUNCTION agent_chat_turn_moves_once();

COMMIT;
