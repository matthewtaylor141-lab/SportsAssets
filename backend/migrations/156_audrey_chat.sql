-- ══════════════════════════════════════════════════════════════════════
-- 156 · AUDREY'S MANAGEMENT CHAT AND THE DIRECTIVES MANAGEMENT GIVES
-- ══════════════════════════════════════════════════════════════════════
--
-- Management talks to Audrey (`sportsassets.agents.audrey_chat`). Every turn
-- is kept: the question as asked (secrets redacted before storage), each
-- typed tool Audrey ran to answer it, and the answer with the identifiers of
-- the records it was composed from. `provider` says HOW it was answered: by
-- the language model (mode LLM, with the model id) or directly from records
-- (mode DETERMINISTIC, with the reason the model was not used). The provider
-- credential is never stored here.
--
-- A DIRECTIVE is management's instruction translated into a structured,
-- durable record (`sportsassets.agents.directives`): who asked (the role the
-- route authenticated, never a name typed in the message), the objective,
-- scope, numerical constraints, acceptance criteria, review/expiry date,
-- change class, required approval, the agent it was assigned to and the
-- improvement tasks created for it. A draft that lacks an essential stores
-- the exact clarifying question.
--
-- A DIRECTIVE HAS NO AUTHORITY OVER RISK. Nothing reads these tables to size,
-- price, authorise or send anything. A directive cannot grant credentials,
-- change approved limits, authorise a live release, flip a submission switch
-- or change approval controls: such content is REFUSED
-- (PROHIBITED_SELF_AUTHORIZATION) and, when the owner asked, recorded as a
-- request for the existing owner approval process -- never executed.
--
-- ADDITIVE. Messages and directive events are APPEND-ONLY.

BEGIN;

CREATE TABLE IF NOT EXISTS audrey_conversations (
    conversation_id  text        PRIMARY KEY,
    requester_role   text        NOT NULL,
    requester_label  text,
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS audrey_messages (
    message_id       text        PRIMARY KEY,
    conversation_id  text        NOT NULL
        REFERENCES audrey_conversations (conversation_id),
    seq              integer     NOT NULL CHECK (seq >= 0),
    at               timestamptz NOT NULL,
    role             text        NOT NULL
        CHECK (role IN ('MANAGEMENT', 'AUDREY', 'TOOL')),
    -- the authenticated role of the caller on whose turn this was written
    requester_role   text,
    body             text        NOT NULL,
    intent           text,
    -- [{"kind": <table/record kind>, "id": <primary key>, "href": ...}]
    citations        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- {"mode": "LLM"|"DETERMINISTIC", "model": ..., "failure": ...,
    --  "disclosure": ...}; never a credential
    provider         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    tool_calls       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audrey_messages_seq_uq UNIQUE (conversation_id, seq)
);

CREATE INDEX IF NOT EXISTS audrey_messages_conversation_idx
    ON audrey_messages (conversation_id, seq);
CREATE INDEX IF NOT EXISTS audrey_conversations_updated_idx
    ON audrey_conversations (updated_at DESC);

CREATE OR REPLACE FUNCTION audrey_message_is_a_record()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audrey message % is a record; it is never rewritten or '
                    'deleted', OLD.message_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audrey_message_is_a_record_trg ON audrey_messages;
CREATE TRIGGER audrey_message_is_a_record_trg
    BEFORE UPDATE OR DELETE ON audrey_messages
    FOR EACH ROW EXECUTE FUNCTION audrey_message_is_a_record();


CREATE TABLE IF NOT EXISTS management_directives (
    directive_id          text        PRIMARY KEY,
    requested_by_role     text        NOT NULL,
    requested_by_label    text,
    conversation_id       text,
    message_id            text,
    instruction           text        NOT NULL,
    objective             text,
    objective_kind        text,
    -- {"accounts": [...], "agents": [...], "markets": [...]}
    scope                 jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- numerical constraints as supplied, plus the standing rules
    constraints           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    acceptance_criteria   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    review_at             timestamptz,
    expires_at            timestamptz,
    change_class          text        NOT NULL,
    required_approval     text        NOT NULL,
    assigned_agent        text,
    status                text        NOT NULL,
    clarifying_question   text,
    refusal               text,
    evidence              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    task_ids              text[]      NOT NULL DEFAULT '{}'::text[],
    created_at            timestamptz NOT NULL,
    updated_at            timestamptz NOT NULL,
    CONSTRAINT management_directive_status_ck CHECK (status IN (
        'DRAFT_NEEDS_CLARIFICATION', 'ACTIVE', 'IN_PROGRESS', 'COMPLETED',
        'REFUSED', 'EXPIRED', 'CANCELLED')),
    CONSTRAINT management_directive_change_class_ck CHECK (change_class IN (
        'PRIORITY_ONLY', 'POLICY_CANDIDATE', 'AUTHORITY_CHANGE')),
    CONSTRAINT management_directive_agent_ck CHECK (
        assigned_agent IS NULL
        OR assigned_agent IN ('DEREK', 'XAVIER', 'DEREK_AND_XAVIER')),
    -- a draft says what it is waiting for; a refusal says why
    CONSTRAINT management_directive_draft_asks_ck CHECK (
        status <> 'DRAFT_NEEDS_CLARIFICATION'
        OR clarifying_question IS NOT NULL),
    CONSTRAINT management_directive_refusal_named_ck CHECK (
        status <> 'REFUSED' OR refusal IS NOT NULL),
    -- an authority change is never anything but refused
    CONSTRAINT management_directive_no_authority_ck CHECK (
        change_class <> 'AUTHORITY_CHANGE' OR status = 'REFUSED')
);

CREATE INDEX IF NOT EXISTS management_directives_created_idx
    ON management_directives (created_at DESC);
CREATE INDEX IF NOT EXISTS management_directives_status_idx
    ON management_directives (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS management_directives_conversation_idx
    ON management_directives (conversation_id)
    WHERE conversation_id IS NOT NULL;

-- A directive's STATUS moves (every move is an event below); the directive
-- itself is never deleted -- a withdrawn one is CANCELLED.
CREATE OR REPLACE FUNCTION management_directive_is_kept()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'directive % is kept; cancel it instead of deleting it',
        OLD.directive_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS management_directive_is_kept_trg
    ON management_directives;
CREATE TRIGGER management_directive_is_kept_trg
    BEFORE DELETE ON management_directives
    FOR EACH ROW EXECUTE FUNCTION management_directive_is_kept();

CREATE TABLE IF NOT EXISTS management_directive_events (
    event_id      bigserial   PRIMARY KEY,
    directive_id  text        NOT NULL
        REFERENCES management_directives (directive_id),
    at            timestamptz NOT NULL,
    kind          text        NOT NULL,
    actor_role    text        NOT NULL,
    actor_label   text,
    from_status   text,
    to_status     text,
    detail        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS management_directive_events_directive_idx
    ON management_directive_events (directive_id, event_id);

CREATE OR REPLACE FUNCTION management_directive_event_is_a_record()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'directive event % is a record; it is never rewritten or '
                    'deleted', OLD.event_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS management_directive_event_is_a_record_trg
    ON management_directive_events;
CREATE TRIGGER management_directive_event_is_a_record_trg
    BEFORE UPDATE OR DELETE ON management_directive_events
    FOR EACH ROW EXECUTE FUNCTION management_directive_event_is_a_record();

COMMIT;
