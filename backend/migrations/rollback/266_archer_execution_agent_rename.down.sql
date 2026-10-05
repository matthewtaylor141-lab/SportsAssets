-- Rollback of 266 (EDDIE -> ARCHER).
--
-- REFUSES while ARCHER has left any record: his runs, tasks, task events,
-- decisions, persona versions, chats, Slack deliveries, findings and stages,
-- conversations, work requests, memories, voice resolutions, improvement
-- items, twin scorecards, candidate-review steps, identity / voice versions
-- beyond the seeded version 1, and any execution estimate or outcome
-- recorded since 266 was applied (his, under the 217 storage name) are part
-- of the audit trail and are never dropped as cleanup. Nothing written as
-- 'EDDIE' is touched, in either direction. With none, removes the alias
-- guard, ARCHER's version-1 identity / voice rows and his registry rows, and
-- restores every CHECK and function 266 changed to its previous definition.
DO $$
DECLARE
    applied timestamptz;
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runs WHERE agent_id = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_tasks
                   WHERE 'ARCHER' IN (assignee, upper(created_by)))
       OR EXISTS (SELECT 1 FROM agent_task_events
                   WHERE upper(actor) = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_decisions WHERE agent_id = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_persona_versions
                   WHERE agent_id = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_chat_conversations
                   WHERE agent_id = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_slack_delivery WHERE agent = 'archer')
       OR EXISTS (SELECT 1 FROM agent_findings WHERE proposer = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_finding_stages WHERE actor = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_conversation_messages
                   WHERE 'ARCHER' IN (from_agent, to_agent))
       OR EXISTS (SELECT 1 FROM agent_work_requests
                   WHERE agent_id = 'ARCHER')
       OR EXISTS (SELECT 1 FROM improve_items WHERE owner_agent = 'ARCHER')
       OR EXISTS (SELECT 1 FROM twin_agent_scorecards WHERE agent = 'ARCHER')
       OR EXISTS (SELECT 1 FROM pos_candidate_review_steps
                   WHERE agent = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_identity_versions
                   WHERE agent_id = 'ARCHER' AND identity_version > 1)
       OR EXISTS (SELECT 1 FROM agent_voice_profiles
                   WHERE agent_id = 'ARCHER' AND version > 1) THEN
        RAISE EXCEPTION 'ARCHER has left records; rollback refused';
    END IF;
    IF to_regclass('agent_memory_events') IS NOT NULL
       AND EXISTS (SELECT 1 FROM agent_memory_events
                    WHERE agent_id = 'ARCHER') THEN
        RAISE EXCEPTION 'ARCHER has left memories; rollback refused';
    END IF;
    IF to_regclass('agent_voice_resolutions') IS NOT NULL
       AND EXISTS (SELECT 1 FROM agent_voice_resolutions
                    WHERE agent_id = 'ARCHER') THEN
        RAISE EXCEPTION 'ARCHER has left voice resolutions; rollback '
                        'refused';
    END IF;
    -- his estimates and outcomes keep 217's storage names: any recorded
    -- since 266 was applied are his
    IF to_regclass('schema_migrations') IS NOT NULL THEN
        SELECT applied_at INTO applied FROM schema_migrations
         WHERE version = '266_archer_execution_agent_rename.sql';
    END IF;
    IF applied IS NOT NULL AND (
           EXISTS (SELECT 1 FROM eddie_execution_estimates
                    WHERE created_at >= applied)
        OR EXISTS (SELECT 1 FROM eddie_execution_outcomes
                    WHERE created_at >= applied)) THEN
        RAISE EXCEPTION 'ARCHER has recorded execution estimates since 266; '
                        'rollback refused';
    END IF;
END $$;

-- the alias guard (idempotent: a second run finds it gone)
DO $$
DECLARE
    r record;
BEGIN
    -- every table carrying the guard (a later migration, e.g. 302, may
    -- have attached it to more tables than 266 listed)
    FOR r IN SELECT tgrelid::regclass::text AS tbl FROM pg_trigger
              WHERE tgname = 'agent_historical_alias_trg' LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS agent_historical_alias_trg'
                       ' ON %s', r.tbl);
    END LOOP;
END $$;
DROP FUNCTION IF EXISTS agent_historical_alias_guarded_tables();
DROP FUNCTION IF EXISTS agent_historical_alias_guard();

-- his version-1 rows are immutable by trigger; with no other record of his
-- they are the migration's own seed and leave with it
ALTER TABLE agent_identity_versions
    DISABLE TRIGGER agent_identity_versions_immutable_trg;
DELETE FROM agent_identity_versions WHERE agent_id = 'ARCHER';
ALTER TABLE agent_identity_versions
    ENABLE TRIGGER agent_identity_versions_immutable_trg;
ALTER TABLE agent_voice_profiles
    DISABLE TRIGGER agent_voice_profiles_immutable_trg;
DELETE FROM agent_voice_profiles WHERE agent_id = 'ARCHER';
ALTER TABLE agent_voice_profiles
    ENABLE TRIGGER agent_voice_profiles_immutable_trg;
DELETE FROM agent_status WHERE agent_id = 'ARCHER';
DELETE FROM agent_identities WHERE agent_id = 'ARCHER';

-- every CHECK as 265 (219 / 217) left it
ALTER TABLE agent_identities
    DROP CONSTRAINT IF EXISTS agent_identities_agent_id_check;
ALTER TABLE agent_identities
    ADD CONSTRAINT agent_identities_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT', 'ADRIANA'));

ALTER TABLE agent_persona_versions
    DROP CONSTRAINT IF EXISTS agent_persona_versions_agent_id_check;
ALTER TABLE agent_persona_versions
    ADD CONSTRAINT agent_persona_versions_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT', 'ADRIANA'));
ALTER TABLE agent_chat_conversations
    DROP CONSTRAINT IF EXISTS agent_chat_conversations_agent_id_check;
ALTER TABLE agent_chat_conversations
    ADD CONSTRAINT agent_chat_conversations_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'SCOUT', 'ADRIANA'));

ALTER TABLE agent_slack_delivery
    DROP CONSTRAINT IF EXISTS agent_slack_delivery_agent_check;
ALTER TABLE agent_slack_delivery
    ADD CONSTRAINT agent_slack_delivery_agent_check
    CHECK (agent IN ('derek', 'xavier', 'audrey', 'karen', 'eddie',
                     'scout', 'adriana'));

ALTER TABLE agent_findings DROP CONSTRAINT IF EXISTS agent_findings_proposer_ck;
ALTER TABLE agent_findings
    ADD CONSTRAINT agent_findings_proposer_ck CHECK (
        proposer IN ('DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'SCOUT',
                     'ADRIANA'));
ALTER TABLE agent_finding_stages
    DROP CONSTRAINT IF EXISTS agent_finding_stages_actor_ck;
ALTER TABLE agent_finding_stages
    ADD CONSTRAINT agent_finding_stages_actor_ck CHECK (
        actor IN ('DEREK', 'XAVIER', 'AUDREY')
        OR (actor IN ('EDDIE', 'SCOUT', 'ADRIANA')
            AND stage <> 'RELEASE_ELIGIBILITY')
        OR (actor = 'KAREN' AND stage = 'PEER_CHALLENGE'));

ALTER TABLE agent_identity_versions
    DROP CONSTRAINT IF EXISTS agent_identity_agent_ck;
ALTER TABLE agent_identity_versions
    ADD CONSTRAINT agent_identity_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT', 'ADRIANA'));
-- her authority is fixed here like everyone else's: SHADOW_ONLY
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
            AND authority_status = 'RESEARCH_SHADOW_ONLY')
        OR (agent_id = 'ADRIANA' AND authority_status = 'SHADOW_ONLY'));
ALTER TABLE agent_identity_versions
    DROP CONSTRAINT IF EXISTS agent_identity_presentation_ck;
ALTER TABLE agent_identity_versions
    ADD CONSTRAINT agent_identity_presentation_ck CHECK (
        presentation IN ('FEMALE', 'MALE')
        AND (agent_id <> 'CHIEF_ALLOCATOR' OR presentation = 'FEMALE')
        AND (agent_id <> 'ADRIANA' OR presentation = 'FEMALE'));

ALTER TABLE agent_voice_profiles
    DROP CONSTRAINT IF EXISTS agent_voice_profiles_agent_ck;
ALTER TABLE agent_voice_profiles
    ADD CONSTRAINT agent_voice_profiles_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT', 'ADRIANA'));

ALTER TABLE agent_conversation_messages
    DROP CONSTRAINT IF EXISTS agent_conv_agents_ck;
ALTER TABLE agent_conversation_messages
    ADD CONSTRAINT agent_conv_agents_ck CHECK (
        from_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                       'CHIEF_ALLOCATOR', 'EDDIE', 'SCOUT', 'ADRIANA')
        AND to_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                         'CHIEF_ALLOCATOR', 'EDDIE', 'SCOUT', 'ADRIANA')
        AND from_agent <> to_agent);

ALTER TABLE agent_work_requests
    DROP CONSTRAINT IF EXISTS agent_work_requests_agent_ck;
ALTER TABLE agent_work_requests
    ADD CONSTRAINT agent_work_requests_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT', 'ADRIANA'));

ALTER TABLE improve_items DROP CONSTRAINT IF EXISTS improve_items_owner_ck;
ALTER TABLE improve_items
    ADD CONSTRAINT improve_items_owner_ck CHECK (owner_agent IN (
        'DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'SCOUT', 'CHIEF_ALLOCATOR',
        'ADRIANA'));

ALTER TABLE twin_agent_scorecards
    DROP CONSTRAINT IF EXISTS twin_scorecards_agent_ck;
ALTER TABLE twin_agent_scorecards
    ADD CONSTRAINT twin_scorecards_agent_ck CHECK (agent IN (
        'DEREK', 'XAVIER', 'EDDIE', 'SCOUT', 'KAREN', 'ALLOCATOR',
        'AUDREY'));

ALTER TABLE pos_candidate_review_steps
    DROP CONSTRAINT IF EXISTS pos_steps_order_ck;
ALTER TABLE pos_candidate_review_steps
    ADD CONSTRAINT pos_steps_order_ck CHECK ((seq, step, agent) IN (
        (1, 'DEREK_CANDIDATE', 'DEREK'),
        (2, 'KAREN_CHALLENGE', 'KAREN'),
        (3, 'SCOUT_EVIDENCE', 'SCOUT'),
        (4, 'EDDIE_EXECUTION_ESTIMATE', 'EDDIE'),
        (5, 'ALLOCATOR_RANKING', 'CHIEF_ALLOCATOR'),
        (6, 'AUDREY_RISK_CHECK', 'AUDREY'),
        (7, 'XAVIER_MANAGEMENT_PLAN', 'XAVIER')));

-- every function as it was before 266
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
        WHEN upper(btrim(v)) = 'ADRIANA'
          OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+ADRIANA([^A-Z]|$)'
          OR upper(btrim(v)) ~ 'ADRIANA[ ._:-]*(AGENT|BOT|ARB|ARBITRAGE)'
            THEN 'ADRIANA'
        ELSE NULL END
$$;

CREATE OR REPLACE FUNCTION poslearn_is_agent_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT v IS NULL
        OR btrim(v) = ''
        OR upper(btrim(v)) IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                               'SCOUT', 'ALLOCATOR', 'ADRIANA', 'CLAUDE',
                               'SYSTEM', 'RUNNER', 'POS_LEARN_RUNNER',
                               'MIGRATION', 'ROOT', 'POSTGRES', 'BOT', 'AGENT')
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN|RUNNER|INTEL|AUTOMATION|SERVICE)([:/ ._-]|$)'
        OR upper(btrim(v)) ~ '(DEREK|XAVIER|AUDREY|KAREN|EDDIE|SCOUT|ALLOCATOR|ADRIANA)[ ._:-]*(AGENT|BOT|V[0-9]|CHALLENGER|RED[ ._-]*TEAM|ARB|ARBITRAGE)'
$$;

CREATE OR REPLACE FUNCTION improve_is_machine_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT v IS NULL
        OR btrim(v) = ''
        OR upper(btrim(v)) IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                               'SCOUT', 'ALLOCATOR', 'CHIEF_ALLOCATOR',
                               'ADRIANA',
                               'CALIBRATION_ENGINE', 'MODEL_TOURNAMENT',
                               'CLAUDE', 'SYSTEM', 'RUNNER', 'MIGRATION',
                               'ROOT', 'POSTGRES', 'BOT', 'AGENT', 'CI',
                               'GITHUB', 'GITHUB_ACTIONS', 'RENDER', 'NETLIFY',
                               'DEPENDABOT', 'IMPROVEMENT_PIPELINE',
                               'POS_LEARN_RUNNER', 'POS_WORKFLOW', 'UNKNOWN',
                               'ANONYMOUS', 'SERVICE', 'AUTOMATION', 'CRON')
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN|RUNNER|INTEL|AUTOMATION|SERVICE|IMPROVEMENT|PIPELINE|GITHUB|CI|CRON|WORKER|DEPLOY)([:/ ._-]|$)'
        OR upper(btrim(v)) ~ '(DEREK|XAVIER|AUDREY|KAREN|EDDIE|SCOUT|ALLOCATOR|ADRIANA)[ ._:-]*(AGENT|BOT|V[0-9]|CHALLENGER|RED[ ._-]*TEAM|EXECUTION|RESEARCH|INTEL|ARB|ARBITRAGE)'
        OR lower(btrim(v)) ~ '\[bot\]'
        OR lower(btrim(v)) ~ '(^|[^a-z])(bot|runner|daemon|scheduler)$'
$$;

CREATE OR REPLACE FUNCTION live_parity_named_human(actor text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT actor IS NOT NULL AND length(btrim(actor)) > 0
       AND btrim(actor) !~* '^(system|derek|xavier|audrey|karen|allie|chief_allocator|eddie|scout|bettor|claude|agent|migration|test_harness_system)|(^|[^a-z0-9])(bots?|ci|cron|codex|assistant|openai|gpt|chatgpt|anthropic|llm|copilot|automation|automated|service|svc|root|admin|administrator|scheduler|deploy|deployer|render|github|actions|worker|daemon|robot|script|pipeline|webhook|unknown|anonymous|none|null)([^a-z0-9]|$)|\[bot\]'
$$;

CREATE OR REPLACE FUNCTION improve_events_guard() RETURNS trigger
AS $$
DECLARE
    it          improve_items%ROWTYPE;
    last_at     timestamptz;
    rel         improve_events%ROWTYPE;
    patch_sha   text;
    passes      integer;
    fails       integer;
    agent_evaluators CONSTANT text[] := ARRAY['DEREK', 'XAVIER', 'AUDREY'];
    agents_all  CONSTANT text[] := ARRAY['DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                                         'EDDIE', 'SCOUT', 'CHIEF_ALLOCATOR'];
    who         text := upper(btrim(NEW.actor));
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'improve_events: append-only, % refused', TG_OP
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO it FROM improve_items WHERE item_id = NEW.item_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'improve_events: no item %', NEW.item_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF it.stage_seq IN (98, 99) THEN
        RAISE EXCEPTION 'improve_events: item % is % (terminal)',
            NEW.item_id, it.stage USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.at < it.created_at THEN
        RAISE EXCEPTION 'improve_events: an event cannot predate its item'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT max(at) INTO last_at FROM improve_events
     WHERE item_id = NEW.item_id;
    IF last_at IS NOT NULL AND NEW.at < last_at THEN
        RAISE EXCEPTION 'improve_events: an event cannot predate the '
                        'event before it' USING ERRCODE = 'check_violation';
    END IF;

    -- THE RUNNER NEVER WRITES A HUMAN OR ENGINEERING STEP
    IF NEW.actor_class IN ('HUMAN', 'ENGINEERING') AND improve_runner_session()
    THEN
        RAISE EXCEPTION 'improve_events: the improvement runner cannot '
                        'record a % step', NEW.actor_class
            USING ERRCODE = 'check_violation';
    END IF;
    -- A HUMAN / ENGINEERING STEP IS A PERSON'S, RECORDED BY THAT PERSON
    IF NEW.actor_class IN ('HUMAN', 'ENGINEERING')
       AND (improve_is_machine_actor(NEW.actor)
            OR upper(btrim(NEW.recorded_by)) <> upper(btrim(NEW.actor))) THEN
        RAISE EXCEPTION 'improvement_events: % is not a human recording their '
                        'own % step', NEW.actor, NEW.actor_class
            USING ERRCODE = 'check_violation';
    END IF;
    -- PROVENANCE: the cited records exist
    IF NEW.source_ref IS NOT NULL AND NOT improve_ref_exists(NEW.source_ref)
    THEN
        RAISE EXCEPTION 'improve_events: source record % % does not '
                        'exist', NEW.source_ref->>'kind', NEW.source_ref->>'id'
            USING ERRCODE = 'check_violation';
    END IF;
    IF jsonb_array_length(NEW.evidence_refs) > 0
       AND NOT improve_refs_exist(NEW.evidence_refs) THEN
        RAISE EXCEPTION 'improve_events: a cited evidence record does '
                        'not exist' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.experiment_refs IS NOT NULL
       AND NOT improve_refs_exist(NEW.experiment_refs) THEN
        RAISE EXCEPTION 'improve_events: a cited experiment record does '
                        'not exist' USING ERRCODE = 'check_violation';
    END IF;

    -- ACTOR CLASS IS WHO THE ACTOR IS
    IF NEW.actor_class = 'OWNER_AGENT' AND who <> it.owner_agent THEN
        RAISE EXCEPTION 'improve_events: % is not the owner (%) of %',
            who, it.owner_agent, NEW.item_id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.actor_class = 'CHALLENGER' AND who <> 'KAREN' THEN
        RAISE EXCEPTION 'improve_events: CHALLENGER is Karen'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.actor_class = 'PEER_AGENT' AND (NOT who = ANY (agents_all)
           OR who IN ('KAREN', it.owner_agent)) THEN
        RAISE EXCEPTION 'improve_events: a PEER_AGENT is another agent '
                        '(not the owner, not Karen): %', who
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.actor_class = 'INDEPENDENT_EVALUATOR' AND (
           NOT who = ANY (agent_evaluators) OR who = it.owner_agent) THEN
        RAISE EXCEPTION 'improve_events: % cannot evaluate % (owner %)',
            who, NEW.item_id, it.owner_agent USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.actor_class = 'RUNNER' AND who <> 'IMPROVEMENT_PIPELINE' THEN
        RAISE EXCEPTION 'improve_events: RUNNER is IMPROVEMENT_PIPELINE'
            USING ERRCODE = 'check_violation';
    END IF;

    -- ── ORDER: forward only, never skipped, terminal is terminal ──────
    IF NEW.stage = 'CLOSED' THEN
        IF it.stage_seq = 0 THEN
            RAISE EXCEPTION 'improve_events: record EVIDENCE first'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.stage = 'ROLLED_BACK' THEN
        IF it.stage_seq NOT IN (8, 9) THEN
            RAISE EXCEPTION 'improve_events: only a released item can be '
                            'rolled back' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.actor_class NOT IN ('HUMAN', 'ENGINEERING') THEN
            RAISE EXCEPTION 'improve_events: a rollback is a human / '
                            'engineering step' USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.stage_seq < it.stage_seq THEN
        RAISE EXCEPTION 'improve_events: % cannot follow % (stages only '
                        'move forward)', NEW.stage, it.stage
            USING ERRCODE = 'check_violation';
    ELSIF NEW.stage_seq = it.stage_seq THEN
        IF NEW.stage NOT IN ('EVIDENCE', 'PEER_CHALLENGE', 'OWNER_RESPONSE',
                             'EXPERIMENT', 'INDEPENDENT_EVALUATION',
                             'FORWARD_RESULT') THEN
            RAISE EXCEPTION 'improve_events: % is recorded once', NEW.stage
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.stage_seq <> it.stage_seq + 1 THEN
        RAISE EXCEPTION 'improve_events: % cannot follow % (no stage is '
                        'skipped)', NEW.stage, coalesce(it.stage, 'NONE')
            USING ERRCODE = 'check_violation';
    END IF;

    -- ── WHO MAY WRITE EACH STAGE ───────────────────────────────────
    IF NEW.stage = 'EVIDENCE'
       AND NEW.actor_class NOT IN ('RUNNER', 'OWNER_AGENT') THEN
        RAISE EXCEPTION 'improve_events: EVIDENCE is the runner''s or '
                        'the owner''s' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.stage = 'HYPOTHESIS' AND NEW.actor_class <> 'OWNER_AGENT' THEN
        RAISE EXCEPTION 'improve_events: HYPOTHESIS is the owner''s (%)',
            it.owner_agent USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.stage = 'PEER_CHALLENGE'
       AND NEW.actor_class NOT IN ('CHALLENGER', 'PEER_AGENT') THEN
        RAISE EXCEPTION 'improve_events: PEER_CHALLENGE is Karen''s or '
                        'another agent''s, never the owner''s'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.stage = 'OWNER_RESPONSE' THEN
        IF NEW.actor_class NOT IN ('OWNER_AGENT', 'HUMAN') THEN
            RAISE EXCEPTION 'improve_events: OWNER_RESPONSE is the '
                            'owner''s' USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM improve_events
                        WHERE item_id = NEW.item_id
                          AND stage = 'PEER_CHALLENGE'
                          AND actor_class = 'CHALLENGER')
           OR NOT EXISTS (SELECT 1 FROM improve_events
                           WHERE item_id = NEW.item_id
                             AND stage = 'PEER_CHALLENGE'
                             AND actor_class = 'PEER_AGENT') THEN
            RAISE EXCEPTION 'improve_events: the owner responds after '
                            'BOTH Karen and a peer challenged'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.stage = 'EXPERIMENT'
       AND NEW.actor_class NOT IN ('OWNER_AGENT', 'ENGINEERING', 'HUMAN') THEN
        RAISE EXCEPTION 'improve_events: EXPERIMENT is registered by the '
                        'owner or engineering' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.stage IN ('INDEPENDENT_EVALUATION', 'ELIGIBLE_CHANGE') THEN
        IF NEW.actor_class NOT IN ('INDEPENDENT_EVALUATOR', 'HUMAN') THEN
            RAISE EXCEPTION 'improve_events: % is an independent '
                            'evaluator''s or a human''s', NEW.stage
                USING ERRCODE = 'check_violation';
        END IF;
        -- NO SELF-APPROVAL: never the owner, never an author of the
        -- hypothesis, the experiment or the candidate patch
        IF who = it.owner_agent OR EXISTS (
               SELECT 1 FROM improve_events
                WHERE item_id = NEW.item_id
                  AND stage IN ('HYPOTHESIS', 'EXPERIMENT')
                  AND upper(btrim(actor)) = who) THEN
            RAISE EXCEPTION 'improve_events: % proposed or authored % and '
                            'cannot record %', who, NEW.item_id, NEW.stage
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.stage = 'ELIGIBLE_CHANGE' THEN
        -- each reviewer's LATEST outcome counts once
        WITH latest AS (
            SELECT DISTINCT ON (upper(btrim(actor))) upper(btrim(actor)) a,
                   outcome
              FROM improve_events
             WHERE item_id = NEW.item_id AND stage = 'INDEPENDENT_EVALUATION'
             ORDER BY upper(btrim(actor)), event_id DESC)
        SELECT count(*) FILTER (WHERE outcome = 'PASS'),
               count(*) FILTER (WHERE outcome = 'FAIL')
          INTO passes, fails FROM latest;
        IF coalesce(passes, 0) < it.required_independent_reviews THEN
            RAISE EXCEPTION 'improve_events: ELIGIBLE_CHANGE needs % '
                            'independent PASS review(s); % recorded',
                            it.required_independent_reviews, coalesce(passes, 0)
                USING ERRCODE = 'check_violation';
        END IF;
        IF coalesce(fails, 0) > 0 THEN
            RAISE EXCEPTION 'improve_events: a reviewer''s latest outcome '
                            'is FAIL' USING ERRCODE = 'check_violation';
        END IF;
        IF it.requires_human_review AND NEW.actor_class <> 'HUMAN' THEN
            RAISE EXCEPTION 'improve_events: % touches a protected area '
                            '(%); a HUMAN records its eligibility',
                            NEW.item_id, array_to_string(it.protected_areas,
                                                         ', ')
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.stage = 'CONTROLLED_RELEASE' THEN
        IF NEW.actor_class <> 'HUMAN' THEN
            RAISE EXCEPTION 'improve_events: CONTROLLED_RELEASE needs a '
                            'recorded HUMAN approver'
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT patch_commit_sha INTO patch_sha FROM improve_events
         WHERE item_id = NEW.item_id AND stage = 'EXPERIMENT'
           AND patch_commit_sha IS NOT NULL
         ORDER BY event_id DESC LIMIT 1;
        IF patch_sha IS NULL THEN
            RAISE EXCEPTION 'improve_events: no candidate patch commit '
                            'SHA was recorded at EXPERIMENT; nothing to '
                            'release' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.gate_receipt_sha IS DISTINCT FROM patch_sha THEN
            RAISE EXCEPTION 'improve_events: the gate receipt is for % '
                            'but the candidate patch is % (exact SHA '
                            'required)', NEW.gate_receipt_sha, patch_sha
                USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM improve_events
                    WHERE item_id = NEW.item_id AND stage = 'EXPERIMENT'
                      AND upper(btrim(actor)) = who) THEN
            RAISE EXCEPTION 'improve_events: the patch author % cannot '
                            'approve its release', who
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.stage = 'FORWARD_RESULT' THEN
        IF NEW.actor_class NOT IN ('INDEPENDENT_EVALUATOR', 'HUMAN',
                                   'ENGINEERING') THEN
            RAISE EXCEPTION 'improve_events: FORWARD_RESULT is measured by '
                            'an independent evaluator, a human or engineering'
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT * INTO rel FROM improve_events
         WHERE item_id = NEW.item_id AND stage = 'CONTROLLED_RELEASE'
         ORDER BY event_id DESC LIMIT 1;
        IF NEW.monitoring_start < rel.at THEN
            RAISE EXCEPTION 'improve_events: the monitoring window starts '
                            'at or after the release'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- the transition marker (one Slack line per transition, never more)
    NEW.is_transition := (NEW.stage_seq <> it.stage_seq);
    NEW.from_stage := it.stage;
    NEW.recorded_at := now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP FUNCTION IF EXISTS agent_canonical_id(text);
