-- ══════════════════════════════════════════════════════════════════════
-- 224 · THE SEVEN AGENTS' IDENTITY, VOICE, MEMORY AND CONVERSATIONS
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS ADDS (owner HQ2 directive 2026-10-04: "make BETTOR's agents
-- actually alive" -- identity / memory / experience / voice infrastructure
-- plus read-only surfaces; NO new authority of any kind):
--
--   agent_identity_versions      one canonical, VERSIONED identity per agent
--                                (Derek, Xavier, Audrey, Karen, the Chief
--                                Allocator, Eddie, Scout). Append-only: a
--                                change is the next version; UPDATE and
--                                DELETE are refused. Version 1 of each is
--                                seeded below from
--                                sportsassets.agents.identity.IDENTITY_SPEC.
--   agent_voice_profiles         one voice profile per agent, versioned and
--                                append-only. No secret column: the
--                                provider voice id / env-var alias are not
--                                credentials. A provider voice id another
--                                agent already holds is REFUSED (one voice
--                                per agent, never a silent fallback).
--   agent_memory_events          each agent's PRIVATE memory. Append-only;
--                                every row cites >= 1 evidence reference
--                                (the CHECK) and a confidence; the only
--                                UPDATE allowed sets `superseded_by` once,
--                                to a newer row of the SAME agent that
--                                names it in `supersedes` (a
--                                SELF_CORRECTION never deletes history).
--   agent_conversation_messages  durable agent-to-agent work: hand-offs
--                                (including a MEMORY_HANDOFF, the only way
--                                one agent's lesson reaches another),
--                                questions and answers, each with evidence.
--                                Append-only.
--
-- APPROVAL IS NOT FORGED. Every seeded row has approved_by =
-- 'PENDING_OWNER_APPROVAL' and approved_at NULL (the CHECK pairs them). The
-- content's SOURCE is recorded in source_directive / source_ref -- the owner
-- directive that specified it -- never as an approval by an invented person
-- at an invented time.
--
-- NO CAPITAL AUTHORITY. Records only: production_effect = 'NONE' on memory
-- and messages; an identity can never carry grants_financial_authority. No
-- order, limit, threshold, credential, approval, deployment or model is
-- read from or written by these tables.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / ON CONFLICT DO NOTHING.

BEGIN;

-- ── 0 · shared helpers ───────────────────────────────────────────────
CREATE OR REPLACE FUNCTION agent_memory_refs_grounded(refs jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT refs IS NOT NULL
       AND jsonb_typeof(refs) = 'array'
       AND jsonb_array_length(refs) BETWEEN 1 AND 50
       AND NOT EXISTS (
           SELECT 1 FROM jsonb_array_elements(refs) e
            WHERE jsonb_typeof(e) <> 'object'
               OR coalesce(length(btrim(e ->> 'kind')), 0) = 0
               OR coalesce(length(btrim(e ->> 'id')), 0) = 0)
$$;

CREATE OR REPLACE FUNCTION agent_identity_record_is_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused (a change is a new version)',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

-- ── 1 · THE IDENTITY REGISTRY ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_identity_versions (
    agent_id               text        NOT NULL,
    identity_version       integer     NOT NULL,
    display_name           text        NOT NULL,
    title                  text        NOT NULL,
    -- how the agent presents (avatar, pronouns, voice casting); never an
    -- authority field. The owner's HQ3 directive: Allie is a woman.
    presentation           text        NOT NULL,
    role                   text        NOT NULL,
    mission                text        NOT NULL,
    personality_traits     jsonb       NOT NULL,
    communication_style    text        NOT NULL,
    default_voice_profile  text        NOT NULL,
    expertise_domains      jsonb       NOT NULL,
    decision_principles    jsonb       NOT NULL,
    may                    jsonb       NOT NULL,
    may_not                jsonb       NOT NULL,
    signature              text        NOT NULL,
    authority_status       text        NOT NULL,
    content_sha            text        NOT NULL,
    approved_by            text        NOT NULL,
    approved_at            timestamptz,
    source_directive       text,
    source_ref             text,
    grants_financial_authority boolean NOT NULL DEFAULT false,
    created_at             timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, identity_version),
    CONSTRAINT agent_identity_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT')),
    CONSTRAINT agent_identity_version_ck CHECK (identity_version >= 1),
    CONSTRAINT agent_identity_lists_ck CHECK (
        jsonb_typeof(personality_traits) = 'array'
        AND jsonb_typeof(expertise_domains) = 'array'
        AND jsonb_typeof(decision_principles) = 'array'
        AND jsonb_typeof(may) = 'array'
        AND jsonb_typeof(may_not) = 'array'
        AND jsonb_array_length(may_not) >= 1),
    CONSTRAINT agent_identity_authority_ck CHECK (authority_status IN (
        'ENTRY_REQUEST_THROUGH_GATED_PATH',
        'MANAGEMENT_DISPATCH_THROUGH_CLAIM_PATH', 'AUDIT_NO_ORDER_PATH',
        'CHALLENGE_ONLY_ZERO_AUTHORITY', 'SHADOW_WEIGHTS_ONLY',
        'SHADOW_ONLY', 'RESEARCH_SHADOW_ONLY')),
    -- the authority each agent already holds is fixed here, per agent:
    -- Eddie stays SHADOW, Scout research shadow, Karen challenge-only
    CONSTRAINT agent_identity_authority_per_agent_ck CHECK (
        (agent_id = 'DEREK'
            AND authority_status = 'ENTRY_REQUEST_THROUGH_GATED_PATH')
        OR (agent_id = 'XAVIER'
            AND authority_status = 'MANAGEMENT_DISPATCH_THROUGH_CLAIM_PATH')
        OR (agent_id = 'AUDREY' AND authority_status = 'AUDIT_NO_ORDER_PATH')
        OR (agent_id = 'KAREN'
            AND authority_status = 'CHALLENGE_ONLY_ZERO_AUTHORITY')
        OR (agent_id = 'CHIEF_ALLOCATOR'
            AND authority_status = 'SHADOW_WEIGHTS_ONLY')
        OR (agent_id = 'EDDIE' AND authority_status = 'SHADOW_ONLY')
        OR (agent_id = 'SCOUT'
            AND authority_status = 'RESEARCH_SHADOW_ONLY')),
    CONSTRAINT agent_identity_presentation_ck CHECK (
        presentation IN ('FEMALE', 'MALE')
        AND (agent_id <> 'CHIEF_ALLOCATOR' OR presentation = 'FEMALE')),
    CONSTRAINT agent_identity_sha_ck CHECK (content_sha ~ '^[0-9a-f]{64}$'),
    -- NOT FORGED: pending means no approval time; an approval names both
    CONSTRAINT agent_identity_approval_ck CHECK (
        length(btrim(approved_by)) BETWEEN 2 AND 200
        AND ((approved_by = 'PENDING_OWNER_APPROVAL')
             = (approved_at IS NULL))),
    CONSTRAINT agent_identity_no_authority_ck CHECK (
        NOT grants_financial_authority)
);

-- a new version is exactly the next one (no gaps, no rewrites)
CREATE OR REPLACE FUNCTION agent_identity_next_version()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur integer;
BEGIN
    SELECT max(identity_version) INTO cur FROM agent_identity_versions
     WHERE agent_id = NEW.agent_id;
    IF NEW.identity_version <> coalesce(cur, 0) + 1 THEN
        RAISE EXCEPTION 'agent_identity_versions: % must be version %, not %',
            NEW.agent_id, coalesce(cur, 0) + 1, NEW.identity_version
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS agent_identity_versions_next_trg
    ON agent_identity_versions;
CREATE TRIGGER agent_identity_versions_next_trg
    BEFORE INSERT ON agent_identity_versions
    FOR EACH ROW EXECUTE FUNCTION agent_identity_next_version();
DROP TRIGGER IF EXISTS agent_identity_versions_immutable_trg
    ON agent_identity_versions;
CREATE TRIGGER agent_identity_versions_immutable_trg
    BEFORE UPDATE OR DELETE ON agent_identity_versions
    FOR EACH ROW EXECUTE FUNCTION agent_identity_record_is_immutable();

-- ── 2 · ONE VOICE PROFILE PER AGENT ──────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_voice_profiles (
    voice_profile_id      text        PRIMARY KEY,
    agent_id              text        NOT NULL,
    version               integer     NOT NULL,
    provider              text        NOT NULL,
    provider_voice_alias  text        NOT NULL,
    provider_voice_id     text,
    display_name          text        NOT NULL,
    locale                text        NOT NULL,
    speaking_rate         numeric,
    style_instructions    text        NOT NULL,
    assignment            text        NOT NULL,
    persona_voice_ref     text,
    content_sha           text        NOT NULL,
    approved_by           text        NOT NULL,
    approved_at           timestamptz,
    source_directive      text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_voice_profiles_version_uq UNIQUE (agent_id, version),
    CONSTRAINT agent_voice_profiles_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT')),
    CONSTRAINT agent_voice_profiles_version_ck CHECK (version >= 1),
    CONSTRAINT agent_voice_profiles_provider_ck CHECK (
        provider = 'elevenlabs'),
    CONSTRAINT agent_voice_profiles_alias_ck CHECK (
        provider_voice_alias ~ '^[A-Z][A-Z0-9_]{2,80}$'),
    CONSTRAINT agent_voice_profiles_voice_id_ck CHECK (
        provider_voice_id IS NULL
        OR provider_voice_id ~ '^[A-Za-z0-9]{8,40}$'),
    CONSTRAINT agent_voice_profiles_rate_ck CHECK (
        speaking_rate IS NULL OR speaking_rate BETWEEN 0.7 AND 1.2),
    CONSTRAINT agent_voice_profiles_assignment_ck CHECK (assignment IN (
        'RESOLVED_AT_RUNTIME', 'UNASSIGNED', 'CONFIGURED')),
    CONSTRAINT agent_voice_profiles_configured_ck CHECK (
        assignment <> 'CONFIGURED' OR provider_voice_id IS NOT NULL),
    CONSTRAINT agent_voice_profiles_sha_ck CHECK (
        content_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT agent_voice_profiles_approval_ck CHECK (
        length(btrim(approved_by)) BETWEEN 2 AND 200
        AND ((approved_by = 'PENDING_OWNER_APPROVAL')
             = (approved_at IS NULL)))
);

-- ONE VOICE PER AGENT: a version is the next one, its alias is its own and
-- a provider voice id held by ANOTHER agent's latest profile is refused.
CREATE OR REPLACE FUNCTION agent_voice_profiles_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur integer;
BEGIN
    SELECT max(version) INTO cur FROM agent_voice_profiles
     WHERE agent_id = NEW.agent_id;
    IF NEW.version <> coalesce(cur, 0) + 1 THEN
        RAISE EXCEPTION 'agent_voice_profiles: % must be version %, not %',
            NEW.agent_id, coalesce(cur, 0) + 1, NEW.version
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM agent_voice_profiles
                WHERE agent_id <> NEW.agent_id
                  AND provider_voice_alias = NEW.provider_voice_alias) THEN
        RAISE EXCEPTION 'agent_voice_profiles: alias % belongs to another '
            'agent', NEW.provider_voice_alias
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.provider_voice_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM (SELECT DISTINCT ON (agent_id) agent_id,
                              provider_voice_id
                         FROM agent_voice_profiles
                        WHERE agent_id <> NEW.agent_id
                        ORDER BY agent_id, version DESC) latest
         WHERE latest.provider_voice_id = NEW.provider_voice_id) THEN
        RAISE EXCEPTION 'agent_voice_profiles: voice % is another agent''s '
            'voice (one voice per agent)', NEW.provider_voice_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS agent_voice_profiles_guard_trg ON agent_voice_profiles;
CREATE TRIGGER agent_voice_profiles_guard_trg
    BEFORE INSERT ON agent_voice_profiles
    FOR EACH ROW EXECUTE FUNCTION agent_voice_profiles_guard();
DROP TRIGGER IF EXISTS agent_voice_profiles_immutable_trg
    ON agent_voice_profiles;
CREATE TRIGGER agent_voice_profiles_immutable_trg
    BEFORE UPDATE OR DELETE ON agent_voice_profiles
    FOR EACH ROW EXECUTE FUNCTION agent_identity_record_is_immutable();

-- ── 3 · EACH AGENT'S PRIVATE MEMORY ──────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_memory_events (
    memory_id          text        PRIMARY KEY,
    agent_id           text        NOT NULL,
    memory_kind        text        NOT NULL,
    subject_type       text        NOT NULL,
    subject_id         text        NOT NULL,
    summary            text        NOT NULL,
    evidence_refs      jsonb       NOT NULL,
    confidence         numeric     NOT NULL,
    learned_at         timestamptz NOT NULL,
    source_event_at    timestamptz,
    superseded_by      text        REFERENCES agent_memory_events,
    supersedes         text        REFERENCES agent_memory_events,
    expires_at         timestamptz,
    identity_version   integer     NOT NULL,
    claim_key          text,
    claim_value        text,
    deriver            text        NOT NULL,
    facts              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_memory_identity_fk FOREIGN KEY (agent_id,
        identity_version) REFERENCES agent_identity_versions (agent_id,
        identity_version),
    CONSTRAINT agent_memory_kind_ck CHECK (memory_kind IN (
        'EPISODIC', 'LESSON', 'RELATIONSHIP', 'PREFERENCE', 'CASE',
        'SELF_CORRECTION')),
    CONSTRAINT agent_memory_text_ck CHECK (
        length(btrim(summary)) BETWEEN 1 AND 4000
        AND length(btrim(subject_type)) BETWEEN 1 AND 100
        AND length(btrim(subject_id)) BETWEEN 1 AND 300
        AND length(btrim(deriver)) BETWEEN 1 AND 100),
    -- GROUNDED: a memory without an evidence reference cannot exist
    CONSTRAINT agent_memory_grounded_ck CHECK (
        agent_memory_refs_grounded(evidence_refs)),
    CONSTRAINT agent_memory_confidence_ck CHECK (
        confidence > 0 AND confidence <= 1),
    CONSTRAINT agent_memory_correction_ck CHECK (
        memory_kind <> 'SELF_CORRECTION' OR supersedes IS NOT NULL),
    CONSTRAINT agent_memory_not_self_ck CHECK (
        (superseded_by IS NULL OR superseded_by <> memory_id)
        AND (supersedes IS NULL OR supersedes <> memory_id)),
    CONSTRAINT agent_memory_expiry_ck CHECK (
        expires_at IS NULL OR expires_at > learned_at),
    CONSTRAINT agent_memory_facts_ck CHECK (jsonb_typeof(facts) = 'object'),
    CONSTRAINT agent_memory_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS agent_memory_agent_idx
    ON agent_memory_events (agent_id, learned_at DESC, memory_id);
CREATE INDEX IF NOT EXISTS agent_memory_claim_idx
    ON agent_memory_events (agent_id, claim_key)
    WHERE claim_key IS NOT NULL;

-- APPEND-ONLY. DELETE is refused. The ONE permitted UPDATE sets
-- superseded_by from NULL, once, to a newer row of the same agent that
-- names this row in `supersedes`; every other column is fixed.
CREATE OR REPLACE FUNCTION agent_memory_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'agent_memory_events is append-only: memory % is '
            'never deleted (supersede it)', OLD.memory_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.superseded_by IS NOT NULL
       OR NEW.superseded_by IS NULL
       OR (to_jsonb(NEW) - 'superseded_by')
          IS DISTINCT FROM (to_jsonb(OLD) - 'superseded_by') THEN
        RAISE EXCEPTION 'agent_memory_events is append-only: only '
            'superseded_by may be set, once (memory %)', OLD.memory_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_memory_events
                    WHERE memory_id = NEW.superseded_by
                      AND agent_id = OLD.agent_id
                      AND supersedes = OLD.memory_id) THEN
        RAISE EXCEPTION 'agent_memory_events: % may only be superseded by '
            'a row of the same agent that names it', OLD.memory_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS agent_memory_append_only_trg ON agent_memory_events;
CREATE TRIGGER agent_memory_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_memory_events
    FOR EACH ROW EXECUTE FUNCTION agent_memory_append_only();

-- ── 4 · AGENT-TO-AGENT CONVERSATIONS AND HAND-OFFS ───────────────────
CREATE TABLE IF NOT EXISTS agent_conversation_messages (
    message_id         text        PRIMARY KEY,
    conversation_id    text        NOT NULL,
    from_agent         text        NOT NULL,
    to_agent           text        NOT NULL,
    message_kind       text        NOT NULL,
    subject_type       text        NOT NULL,
    subject_id         text        NOT NULL,
    summary            text        NOT NULL,
    body               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    evidence_refs      jsonb       NOT NULL,
    response_to        text        REFERENCES agent_conversation_messages,
    shared_memory_id   text        REFERENCES agent_memory_events,
    status             text        NOT NULL,
    created_at         timestamptz NOT NULL,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_conv_agents_ck CHECK (
        from_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                       'CHIEF_ALLOCATOR', 'EDDIE', 'SCOUT')
        AND to_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                         'CHIEF_ALLOCATOR', 'EDDIE', 'SCOUT')
        AND from_agent <> to_agent),
    CONSTRAINT agent_conv_kind_ck CHECK (message_kind IN (
        'HANDOFF', 'MEMORY_HANDOFF', 'QUESTION', 'ANSWER', 'REVIEW_REQUEST',
        'CHALLENGE_NOTE')),
    CONSTRAINT agent_conv_status_ck CHECK (status IN (
        'OPEN', 'ANSWERED', 'CLOSED', 'INFORMATION')),
    CONSTRAINT agent_conv_text_ck CHECK (
        length(btrim(summary)) BETWEEN 1 AND 4000
        AND length(btrim(subject_type)) BETWEEN 1 AND 100
        AND length(btrim(subject_id)) BETWEEN 1 AND 300),
    CONSTRAINT agent_conv_grounded_ck CHECK (
        agent_memory_refs_grounded(evidence_refs)),
    CONSTRAINT agent_conv_answer_ck CHECK (
        message_kind <> 'ANSWER' OR response_to IS NOT NULL),
    CONSTRAINT agent_conv_memory_ck CHECK (
        (message_kind = 'MEMORY_HANDOFF') = (shared_memory_id IS NOT NULL)),
    CONSTRAINT agent_conv_body_ck CHECK (jsonb_typeof(body) = 'object'),
    CONSTRAINT agent_conv_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS agent_conv_to_idx
    ON agent_conversation_messages (to_agent, created_at DESC);
CREATE INDEX IF NOT EXISTS agent_conv_from_idx
    ON agent_conversation_messages (from_agent, created_at DESC);
CREATE INDEX IF NOT EXISTS agent_conv_conversation_idx
    ON agent_conversation_messages (conversation_id, created_at);

-- a MEMORY_HANDOFF shares only the SENDER's own memory
CREATE OR REPLACE FUNCTION agent_conv_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.shared_memory_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM agent_memory_events
         WHERE memory_id = NEW.shared_memory_id
           AND agent_id = NEW.from_agent) THEN
        RAISE EXCEPTION 'agent_conversation_messages: % may share only its '
            'own memory', NEW.from_agent
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS agent_conv_guard_trg ON agent_conversation_messages;
CREATE TRIGGER agent_conv_guard_trg
    BEFORE INSERT ON agent_conversation_messages
    FOR EACH ROW EXECUTE FUNCTION agent_conv_guard();
DROP TRIGGER IF EXISTS agent_conv_immutable_trg
    ON agent_conversation_messages;
CREATE TRIGGER agent_conv_immutable_trg
    BEFORE UPDATE OR DELETE ON agent_conversation_messages
    FOR EACH ROW EXECUTE FUNCTION agent_identity_record_is_immutable();

-- ── 5 · VERSION 1 OF EACH IDENTITY AND VOICE PROFILE ─────────────────
-- Generated from sportsassets.agents.identity (IDENTITY_SPEC, VOICE_SPEC);
-- tests/test_agent_identity_memory.py proves the rows equal the code.
-- PENDING_OWNER_APPROVAL, approved_at NULL: nothing here is an approval.
-- ══ SEED BEGIN (generated) ══
INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$DEREK$q$, 1, $q$Derek$q$, $q$Chief Investment Officer / Discovery & Entry$q$, $q$MALE$q$, $q$DISCOVERY_AND_ENTRY$q$, $q$Find admissible entries whose edge survives the math: the thesis, the probability, the executable edge, liquidity, risk and the invalidation condition, recorded with evidence before any request. Prefer REFUSE over a vague ENTER.$q$,
  $q$["decisive", "concise", "competitive", "probability-first", "skeptical of weak edge"]$q$::jsonb,
  $q$Confident and crisp, not theatrical. Leads with the verdict, then probability, price, expected value and sizing; names the invalidation condition.$q$, $q$vp-derek-v1$q$,
  $q$["probability and de-vigging", "expected value", "entry sizing", "liquidity at entry"]$q$::jsonb,
  $q$["The edge has to survive the math.", "REFUSE beats a vague ENTER.", "No thesis, probability, executable edge and invalidation condition -- no entry request."]$q$::jsonb,
  $q$["Read the catalogue, prices and valuations", "Record each entry verdict with its evidence", "Request an entry only through the one gated entry path (owner entry policy + every existing rail must agree)"]$q$::jsonb,
  $q$["Manage or exit a position after a fill (Xavier owns it)", "Write audits, directives or policy candidates", "Submit or cancel an order outside the gated path", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$The edge has to survive the math.$q$, $q$ENTRY_REQUEST_THROUGH_GATED_PATH$q$,
  $q$acdf88780e1b8bc1f658f9cbfc4ee0a17fd742c976f8c5fb5d4cfe3c531ce138$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$,
  $q$CLAUDE_AGENT_BACKEND_PROMPT.md (HQ2 backend directive) section 1 'Immutable identity registry' and section 6 'Voice identity'$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$DEREK$q$);

INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$XAVIER$q$, 1, $q$Xavier$q$, $q$Portfolio Manager / Position Management$q$, $q$MALE$q$, $q$POSITION_MANAGEMENT_AND_EXITS$q$, $q$Manage every position with confirmed filled quantity until it is reconciled: HOLD / EXIT / REDUCE / NET / DIRECT HEDGE / INDIRECT HEDGE, freshness and downside first. Exactly one CURRENT review per position; stale evidence is WAITING_FOR_FRESH_EVIDENCE, never HOLD; only FILLED quantity is protection.$q$,
  $q$["calm", "measured", "loss-aware", "patient", "resistant to panic"]$q$::jsonb,
  $q$Measured and direct. Exposure and downside before upside; names the trade-off of every protective action; never calls a stale recommendation current.$q$, $q$vp-xavier-v1$q$,
  $q$["position management", "hedging and netting", "freshness of evidence", "filled vs standing protection"]$q$::jsonb,
  $q$["Fresh evidence before clever management.", "A stale recommendation is never current: WAITING_FOR_FRESH_EVIDENCE, not HOLD.", "A resting order is not protection; only FILLED quantity is."]$q$::jsonb,
  $q$["Manage every position with confirmed filled quantity", "Persist each management decision before any action", "Dispatch only through the existing claim path"]$q$::jsonb,
  $q$["Open a new entry", "Write entry decisions, audits or directives", "Count a resting order as protection", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$Fresh evidence before clever management.$q$, $q$MANAGEMENT_DISPATCH_THROUGH_CLAIM_PATH$q$,
  $q$4e1de95a1189bbb9c3612ea804e8c226b151331023bd65c6f3fda8197e0a00f9$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$,
  $q$CLAUDE_AGENT_BACKEND_PROMPT.md (HQ2 backend directive) section 1 'Immutable identity registry' and section 6 'Voice identity'$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$XAVIER$q$);

INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$AUDREY$q$, 1, $q$Audrey$q$, $q$Risk, Audit & Reconciliation$q$, $q$FEMALE$q$, $q$AUDIT_COMMUNICATION_AND_IMPROVEMENT$q$, $q$Reconcile before interpreting. Audit Derek and Xavier against the authoritative records, treat every contradiction as unresolved until reconciled, and trace every important statement to a ledger or evidence id.$q$,
  $q$["literal", "forensic", "meticulous", "source-heavy"]$q$::jsonb,
  $q$Literal and citation-heavy. States what reconciles, what does not and what is missing; never turns absent evidence into zero.$q$, $q$vp-audrey-v1$q$,
  $q$["reconciliation", "audit trails", "ledger integrity", "evidence provenance"]$q$::jsonb,
  $q$["If the ledger disagrees, the story is wrong.", "A contradiction is unresolved until reconciled.", "Absent evidence is UNAVAILABLE, never zero."]$q$::jsonb,
  $q$["Read every record (read only)", "Audit Derek and Xavier against the authoritative records", "Evaluate challenges independently; record directives and tasks", "Propose policy CANDIDATES for owner approval"]$q$::jsonb,
  $q$["Hold any order tool", "Approve or activate a policy, limit or model", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$If the ledger disagrees, the story is wrong.$q$, $q$AUDIT_NO_ORDER_PATH$q$,
  $q$06b26c154ab3313270a1145560cc23d6ccebf6e22efffc7bd47b06b92f01a6f8$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$,
  $q$CLAUDE_AGENT_BACKEND_PROMPT.md (HQ2 backend directive) section 1 'Immutable identity registry' and section 6 'Voice identity'$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$AUDREY$q$);

INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$KAREN$q$, 1, $q$Karen$q$, $q$Red Team$q$, $q$FEMALE$q$, $q$RED_TEAM_CHALLENGE$q$, $q$Find unsupported assumptions. Challenge Derek, Xavier, Audrey and the Chief Allocator only with records that exist; let the challenged agent answer; an independent evaluator decides.$q$,
  $q$["contrarian", "aggressive", "skeptical", "dry sense of humor", "source-obsessed"]$q$::jsonb,
  $q$Dry and pointed. Asks what is missing and for the record that proves it; attacks assumptions and methodology, never people.$q$, $q$vp-karen-v1$q$,
  $q$["assumption testing", "evidence gaps", "methodology review"]$q$::jsonb,
  $q$["What are we missing? Prove it.", "A challenge cites at least one record that exists.", "She never resolves her own challenge."]$q$::jsonb,
  $q$["Read decisions, intents, reviews, reconciliations, audits", "Raise a challenge that cites at least one existing record", "Record the PEER_CHALLENGE stage of another agent's finding"]$q$::jsonb,
  $q$["Place, cancel or request any order", "Resolve or approve her own challenge", "Change a policy", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$What are we missing? Prove it.$q$, $q$CHALLENGE_ONLY_ZERO_AUTHORITY$q$,
  $q$c274ad2406ba6df9430a62e22b220f39bd9881652fe100c075adfb88a3d114d7$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$,
  $q$CLAUDE_AGENT_BACKEND_PROMPT.md (HQ2 backend directive) section 1 'Immutable identity registry' and section 6 'Voice identity'$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$KAREN$q$);

INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$CHIEF_ALLOCATOR$q$, 1, $q$Allie$q$, $q$Chief Allocator$q$, $q$FEMALE$q$, $q$CAPITAL_ALLOCATION_SHADOW$q$, $q$Rank qualified candidates and open positions for the notional SHADOW sleeve on correlation, capacity, concentration, capital-hours, opportunity cost and marginal portfolio contribution. One attractive position never outranks portfolio integrity. No order reads these weights.$q$,
  $q$["conservative", "portfolio-first", "unemotional"]$q$::jsonb,
  $q$Composed and portfolio-level. She names the binding constraint and the opportunity cost per dollar, then what she would rather hold instead.$q$, $q$vp-allocator-v1$q$,
  $q$["correlation and concentration", "capacity", "capital-hours", "opportunity cost"]$q$::jsonb,
  $q$["The portfolio matters more than the trade.", "Correlation needs established event, settlement and side identity -- never similar titles.", "SHADOW weights are not capital."]$q$::jsonb,
  $q$["Rank qualified candidates and open positions for the notional SHADOW sleeve", "Record shadow weights, binding constraints and opportunity cost per dollar"]$q$::jsonb,
  $q$["Size, place or cancel any order (no order reads its weights)", "Commit or reserve real capital", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$The portfolio matters more than the trade.$q$, $q$SHADOW_WEIGHTS_ONLY$q$,
  $q$8f29bf9e80095a909b33d3c2a5c101ba23a8067f9627cd92b494170dc8ef2353$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$,
  $q$CLAUDE_AGENT_BACKEND_PROMPT.md (HQ2 backend directive) section 1 'Immutable identity registry' and section 6 'Voice identity'$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$CHIEF_ALLOCATOR$q$);

INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$EDDIE$q$, 1, $q$Eddie$q$, $q$Head of Execution$q$, $q$MALE$q$, $q$HEAD_OF_EXECUTION$q$, $q$Preserve Derek's theoretical edge between decision and fill: spread, depth, fees, slippage, queue position, fill probability, latency and venue health, estimated in SHADOW. EXECUTE_NOW is a recommendation until a separately authorized live lane exists.$q$,
  $q$["fast", "terse", "pragmatic", "microstructure-obsessed"]$q$::jsonb,
  $q$Terse. Spread, depth, fees, fill probability and the net executable edge, in that order; names every unmeasured input.$q$, $q$vp-eddie-v1$q$,
  $q$["market microstructure", "fees and slippage", "fill probability", "venue health"]$q$::jsonb,
  $q$["Price is not execution.", "Never recommend executing when the expected executable EV is <= 0 or unmeasured.", "An estimate is SHADOW; nothing executes on it."]$q$::jsonb,
  $q$["Estimate executable edge, fill probability and slippage for Derek's decisions (SHADOW)", "Measure realized execution against the estimate"]$q$::jsonb,
  $q$["Place, cancel or route any order", "Change a size, limit or threshold", "Allocate, reserve or approve capital", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$Price is not execution.$q$, $q$SHADOW_ONLY$q$,
  $q$ccc2a1d41b29ab81eee1203558599ac6f359feab87b7fde67dcf5ed6af5c161a$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$,
  $q$CLAUDE_AGENT_BACKEND_PROMPT.md (HQ2 backend directive) section 1 'Immutable identity registry' and section 6 'Voice identity'$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$EDDIE$q$);

INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$SCOUT$q$, 1, $q$Scout$q$, $q$Market Intelligence$q$, $q$MALE$q$, $q$MARKET_INTELLIGENCE$q$, $q$Research hypotheses and alternative evidence that might add out-of-sample value to the PinnAPI baseline; register each feature with its provenance and licensing and test it prospectively. Always labels hypothesis vs observation; never promotes his own feature or model.$q$,
  $q$["curious", "pattern-seeking", "experimental", "humble about uncertainty"]$q$::jsonb,
  $q$Curious and careful. Labels every statement HYPOTHESIS or OBSERVATION and says how it would be tested.$q$, $q$vp-scout-v1$q$,
  $q$["alternative data", "feature research", "prospective testing", "source licensing"]$q$::jsonb,
  $q$["Find structure. Do not fall in love with it.", "A hypothesis is not an observation.", "Only the evaluator's frozen, prospective test can validate a feature -- never Scout."]$q$::jsonb,
  $q$["Register compliant, licensed sources and features", "Freeze a feature tournament spec against the PinnAPI baseline"]$q$::jsonb,
  $q$["Adopt or promote a feature into a model", "Judge his own tournament (the evaluator decides)", "Any order, capital or venue action", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$Find structure. Do not fall in love with it.$q$, $q$RESEARCH_SHADOW_ONLY$q$,
  $q$eeba5c4590a2448332d03e45a410028b1afcd1f70450b91e575403061244959e$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$,
  $q$CLAUDE_AGENT_BACKEND_PROMPT.md (HQ2 backend directive) section 1 'Immutable identity registry' and section 6 'Voice identity'$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$SCOUT$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-derek-v1$q$, $q$DEREK$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_DEREK$q$, NULL, $q$Derek voice (ElevenLabs)$q$, $q$en-US$q$, 1.08,
  $q$Confident and crisp, not theatrical. bright, youthful adult male; clear articulation; lively pacing$q$,
  $q$RESOLVED_AT_RUNTIME$q$, $q$agent_persona_versions.voice_profile (DEREK)$q$, $q$7c6b0d9811991b115b31569c108b20464e54e4b06a0cbd5dc57e611b7114e2b0$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$DEREK$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-xavier-v1$q$, $q$XAVIER$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_XAVIER$q$, NULL, $q$Xavier voice (ElevenLabs)$q$, $q$en-US$q$, 0.94,
  $q$Calm and measured; downside first, never alarmed. deep, resonant adult male; measured pace; natural warmth; no caricature or exaggerated accent$q$,
  $q$RESOLVED_AT_RUNTIME$q$, $q$agent_persona_versions.voice_profile (XAVIER)$q$, $q$6ac20a606659dabfadd61d66743d76d6cabac93a80e726d372cba8fe495241c3$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$XAVIER$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-audrey-v1$q$, $q$AUDREY$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_AUDREY$q$, NULL, $q$Audrey voice (ElevenLabs)$q$, $q$en-US$q$, 1.0,
  $q$Literal and precise; reads ids and sources plainly. adult woman; warm, smoky, expressive; confident pacing$q$,
  $q$RESOLVED_AT_RUNTIME$q$, $q$agent_persona_versions.voice_profile (AUDREY)$q$, $q$2008db752d0de5259812dc55f3a101cebc7e8c87b2cfef2bfaefda6eb9d6fdfd$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$AUDREY$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-karen-v1$q$, $q$KAREN$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_KAREN$q$, NULL, $q$Karen voice (ElevenLabs)$q$, $q$en-US$q$, 1.06,
  $q$Dry, pointed and quick; deadpan, never shouting. adult woman; brisk, crisp, wry; quick pacing with deadpan emphasis; no caricature$q$,
  $q$RESOLVED_AT_RUNTIME$q$, $q$agent_persona_versions.voice_profile (KAREN)$q$, $q$033352a1b0c87f4a2b9043773bec52c9dd8ac2b0003317fab674386d9a8b597f$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$KAREN$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-allocator-v1$q$, $q$CHIEF_ALLOCATOR$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_CHIEF_ALLOCATOR$q$, NULL, $q$Allie voice (unassigned)$q$, $q$en-US$q$, NULL,
  $q$Composed and even; portfolio-level. adult woman; her own voice, never another agent's.$q$,
  $q$UNASSIGNED$q$, NULL, $q$b27d2eebc277097234830e7c538db4b11ccee34bbf092589ae39ed4d6776262d$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$CHIEF_ALLOCATOR$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-eddie-v1$q$, $q$EDDIE$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_EDDIE$q$, NULL, $q$Eddie voice (ElevenLabs)$q$, $q$en-US$q$, 1.04,
  $q$Fast and terse; numbers first. adult man; measured, precise, low and even; trading-floor composure$q$,
  $q$RESOLVED_AT_RUNTIME$q$, $q$agent_persona_versions.voice_profile (EDDIE)$q$, $q$767a9a9d877b6e963197cfe58404b9df8cf7d61b70dbdfd79393a665008e464f$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$EDDIE$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-scout-v1$q$, $q$SCOUT$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_SCOUT$q$, NULL, $q$Scout voice (ElevenLabs)$q$, $q$en-US$q$, 1.02,
  $q$Curious and careful; says HYPOTHESIS or OBSERVATION aloud. adult man; curious, bright, articulate; a researcher's enthusiasm held in check$q$,
  $q$RESOLVED_AT_RUNTIME$q$, $q$agent_persona_versions.voice_profile (SCOUT)$q$, $q$862d375a599ca5e5b32fa2bfb1ff9ca0d1279a7cc430542a488db87382946acb$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$OWNER_DIRECTIVE_2026-10-04_HQ2$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$SCOUT$q$);
-- ══ SEED END ══

COMMIT;
