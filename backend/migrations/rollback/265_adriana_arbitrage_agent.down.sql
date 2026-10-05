-- Refuses while Adriana has left any record: her scans, opportunities,
-- refusals, runs, tasks, conversations, findings, improvement items, work
-- requests, persona versions, chats and Slack deliveries are part of the
-- audit trail and are never dropped as cleanup. With none, removes her
-- tables, her version-1 identity and voice rows and her registry rows, and
-- restores every CHECK and function 265 widened to its pre-265 definition.
DO $$
DECLARE
    present boolean;
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['adriana_arb_scans', 'adriana_arb_opportunities',
                             'adriana_arb_refusals'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
                INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION '% holds records; rollback refused', t;
            END IF;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM agent_runs WHERE agent_id = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM agent_tasks WHERE assignee = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM agent_decisions WHERE agent_id = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM agent_persona_versions
                   WHERE agent_id = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM agent_chat_conversations
                   WHERE agent_id = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM agent_slack_delivery WHERE agent = 'adriana')
       OR EXISTS (SELECT 1 FROM agent_findings WHERE proposer = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM agent_finding_stages WHERE actor = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM agent_conversation_messages
                   WHERE 'ADRIANA' IN (from_agent, to_agent))
       OR EXISTS (SELECT 1 FROM agent_work_requests
                   WHERE agent_id = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM improve_items WHERE owner_agent = 'ADRIANA')
       OR EXISTS (SELECT 1 FROM agent_identity_versions
                   WHERE agent_id = 'ADRIANA' AND identity_version > 1)
       OR EXISTS (SELECT 1 FROM agent_voice_profiles
                   WHERE agent_id = 'ADRIANA' AND version > 1) THEN
        RAISE EXCEPTION 'Adriana has left records; rollback refused';
    END IF;
END $$;

DROP TABLE IF EXISTS adriana_arb_refusals;
DROP TABLE IF EXISTS adriana_arb_opportunities;
DROP TABLE IF EXISTS adriana_arb_scans;
DROP FUNCTION IF EXISTS adriana_records_append_only();

-- her version-1 rows are immutable by trigger; with no other record of hers
-- they are the migration's own seed and leave with it
ALTER TABLE agent_identity_versions
    DISABLE TRIGGER agent_identity_versions_immutable_trg;
DELETE FROM agent_identity_versions WHERE agent_id = 'ADRIANA';
ALTER TABLE agent_identity_versions
    ENABLE TRIGGER agent_identity_versions_immutable_trg;
ALTER TABLE agent_voice_profiles
    DISABLE TRIGGER agent_voice_profiles_immutable_trg;
DELETE FROM agent_voice_profiles WHERE agent_id = 'ADRIANA';
ALTER TABLE agent_voice_profiles
    ENABLE TRIGGER agent_voice_profiles_immutable_trg;
DELETE FROM agent_status WHERE agent_id = 'ADRIANA';
DELETE FROM agent_identities WHERE agent_id = 'ADRIANA';

ALTER TABLE agent_identities
    DROP CONSTRAINT IF EXISTS agent_identities_agent_id_check;
ALTER TABLE agent_identities
    ADD CONSTRAINT agent_identities_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT'));
ALTER TABLE agent_persona_versions
    DROP CONSTRAINT IF EXISTS agent_persona_versions_agent_id_check;
ALTER TABLE agent_persona_versions
    ADD CONSTRAINT agent_persona_versions_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT'));
ALTER TABLE agent_chat_conversations
    DROP CONSTRAINT IF EXISTS agent_chat_conversations_agent_id_check;
ALTER TABLE agent_chat_conversations
    ADD CONSTRAINT agent_chat_conversations_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT'));
ALTER TABLE agent_slack_delivery
    DROP CONSTRAINT IF EXISTS agent_slack_delivery_agent_check;
ALTER TABLE agent_slack_delivery
    ADD CONSTRAINT agent_slack_delivery_agent_check
    CHECK (agent IN ('derek', 'xavier', 'audrey', 'karen', 'eddie',
                     'scout'));
ALTER TABLE agent_findings DROP CONSTRAINT IF EXISTS agent_findings_proposer_ck;
ALTER TABLE agent_findings
    ADD CONSTRAINT agent_findings_proposer_ck CHECK (
        proposer IN ('DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'SCOUT'));
ALTER TABLE agent_finding_stages
    DROP CONSTRAINT IF EXISTS agent_finding_stages_actor_ck;
ALTER TABLE agent_finding_stages
    ADD CONSTRAINT agent_finding_stages_actor_ck CHECK (
        actor IN ('DEREK', 'XAVIER', 'AUDREY')
        OR (actor IN ('EDDIE', 'SCOUT') AND stage <> 'RELEASE_ELIGIBILITY')
        OR (actor = 'KAREN' AND stage = 'PEER_CHALLENGE'));
ALTER TABLE agent_identity_versions
    DROP CONSTRAINT IF EXISTS agent_identity_agent_ck;
ALTER TABLE agent_identity_versions
    ADD CONSTRAINT agent_identity_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT'));
ALTER TABLE agent_identity_versions
    DROP CONSTRAINT IF EXISTS agent_identity_authority_per_agent_ck;
ALTER TABLE agent_identity_versions
    ADD CONSTRAINT agent_identity_authority_per_agent_ck CHECK (
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
            AND authority_status = 'RESEARCH_SHADOW_ONLY'));
ALTER TABLE agent_identity_versions
    DROP CONSTRAINT IF EXISTS agent_identity_presentation_ck;
ALTER TABLE agent_identity_versions
    ADD CONSTRAINT agent_identity_presentation_ck CHECK (
        presentation IN ('FEMALE', 'MALE')
        AND (agent_id <> 'CHIEF_ALLOCATOR' OR presentation = 'FEMALE'));
ALTER TABLE agent_voice_profiles
    DROP CONSTRAINT IF EXISTS agent_voice_profiles_agent_ck;
ALTER TABLE agent_voice_profiles
    ADD CONSTRAINT agent_voice_profiles_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT'));
ALTER TABLE agent_conversation_messages
    DROP CONSTRAINT IF EXISTS agent_conv_agents_ck;
ALTER TABLE agent_conversation_messages
    ADD CONSTRAINT agent_conv_agents_ck CHECK (
        from_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                       'CHIEF_ALLOCATOR', 'EDDIE', 'SCOUT')
        AND to_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                         'CHIEF_ALLOCATOR', 'EDDIE', 'SCOUT')
        AND from_agent <> to_agent);
ALTER TABLE agent_work_requests
    DROP CONSTRAINT IF EXISTS agent_work_requests_agent_ck;
ALTER TABLE agent_work_requests
    ADD CONSTRAINT agent_work_requests_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT'));
ALTER TABLE improve_items DROP CONSTRAINT IF EXISTS improve_items_owner_ck;
ALTER TABLE improve_items
    ADD CONSTRAINT improve_items_owner_ck CHECK (owner_agent IN (
        'DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'SCOUT', 'CHIEF_ALLOCATOR'));

-- the three functions, exactly as 217 / 218 / 221 define them
CREATE OR REPLACE FUNCTION pos_agent_actor(v text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN v IS NULL THEN NULL
        WHEN upper(btrim(v)) = 'EDDIE'
          OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+EDDIE([^A-Z]|$)'
          OR upper(btrim(v)) ~ 'EDDIE[ ._:-]*(AGENT|BOT|EXECUTION)'
            THEN 'EDDIE'
        WHEN upper(btrim(v)) = 'SCOUT'
          OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+SCOUT([^A-Z]|$)'
          OR upper(btrim(v)) ~ 'SCOUT[ ._:-]*(AGENT|BOT|INTEL|RESEARCH)'
            THEN 'SCOUT'
        ELSE NULL END
$$;
CREATE OR REPLACE FUNCTION poslearn_is_agent_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT v IS NULL
        OR btrim(v) = ''
        OR upper(btrim(v)) IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                               'SCOUT', 'ALLOCATOR', 'CLAUDE', 'SYSTEM',
                               'RUNNER', 'POS_LEARN_RUNNER', 'MIGRATION',
                               'ROOT', 'POSTGRES', 'BOT', 'AGENT')
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN|RUNNER|INTEL|AUTOMATION|SERVICE)([:/ ._-]|$)'
        OR upper(btrim(v)) ~ '(DEREK|XAVIER|AUDREY|KAREN|EDDIE|SCOUT|ALLOCATOR)[ ._:-]*(AGENT|BOT|V[0-9]|CHALLENGER|RED[ ._-]*TEAM)'
$$;
CREATE OR REPLACE FUNCTION improve_is_machine_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT v IS NULL
        OR btrim(v) = ''
        OR upper(btrim(v)) IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                               'SCOUT', 'ALLOCATOR', 'CHIEF_ALLOCATOR',
                               'CALIBRATION_ENGINE', 'MODEL_TOURNAMENT',
                               'CLAUDE', 'SYSTEM', 'RUNNER', 'MIGRATION',
                               'ROOT', 'POSTGRES', 'BOT', 'AGENT', 'CI',
                               'GITHUB', 'GITHUB_ACTIONS', 'RENDER', 'NETLIFY',
                               'DEPENDABOT', 'IMPROVEMENT_PIPELINE',
                               'POS_LEARN_RUNNER', 'POS_WORKFLOW', 'UNKNOWN',
                               'ANONYMOUS', 'SERVICE', 'AUTOMATION', 'CRON')
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN|RUNNER|INTEL|AUTOMATION|SERVICE|IMPROVEMENT|PIPELINE|GITHUB|CI|CRON|WORKER|DEPLOY)([:/ ._-]|$)'
        OR upper(btrim(v)) ~ '(DEREK|XAVIER|AUDREY|KAREN|EDDIE|SCOUT|ALLOCATOR)[ ._:-]*(AGENT|BOT|V[0-9]|CHALLENGER|RED[ ._-]*TEAM|EXECUTION|RESEARCH|INTEL)'
        OR lower(btrim(v)) ~ '\[bot\]'
        OR lower(btrim(v)) ~ '(^|[^a-z])(bot|runner|daemon|scheduler)$'
$$;
