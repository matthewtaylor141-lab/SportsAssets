-- 266: THE EXECUTION AGENT IS RENAMED -- EDDIE -> ARCHER.
--
-- PM directive 2026-10-05: canonical identity ARCHER / archer, display name
-- Archer, title "Head of Execution". Same mandate, same SHADOW_ONLY
-- authority, same records: a NAME change, not a new agent and not a new
-- power. Exactly one execution agent is active (ARCHER); "EDDIE" survives
-- ONLY as a historical alias for audit.
--
-- HISTORY IS NOT REWRITTEN. Every row written as 'EDDIE' before this
-- migration stays exactly as written: no UPDATE and no DELETE of any of
-- them, here or in code. Readers that show one name ARCHER and carry an
-- explicit historical_alias: "EDDIE" (sportsassets.agents.registry.
-- label_aliases) -- never a silent relabel. The physical names migration
-- 217 gave his records (eddie_execution_estimates / eddie_execution_outcomes,
-- pos_iface_eddie_execution and their constraints, triggers and indexes)
-- are storage names and stay; so do the persisted record codes that key
-- history (estimator version EDDIE_EXECUTION_ESTIMATOR_V1, the twin world
-- EDDIE_EXECUTION, the improvement source kind EDDIE_SKIP_EXECUTION).
--
-- WHAT THIS ADDS
--   1. agent_canonical_id(v): the agent id `v` names NOW ('EDDIE' ->
--      'ARCHER', any case; everything else upper-cased as is).
--   2. ARCHER in every agent-id CHECK an agent's records pass through
--      (identities, personas, chat, Slack delivery, the collaboration loop,
--      identity versions -- authority fixed SHADOW_ONLY exactly as EDDIE's --
--      voice profiles, conversations, work requests, improvement items,
--      219's twin scorecards and 217's candidate-review step 4, now
--      (4, ARCHER_EXECUTION_ESTIMATE, ARCHER)) -- widened by DROP-then-ADD.
--      'EDDIE' STAYS in each, so every historical row remains valid.
--   3. NO AUTHORITY, IN THE DATABASE, EXACTLY AS BEFORE: pos_agent_actor()
--      names ARCHER -- the id or a machine-style label (agent:archer,
--      archer-bot, archer execution); a person called Archer is NOT
--      matched -- and resolves every EDDIE label 217 matched to ARCHER, so
--      217's aa_pos_agents_no_authority_trg refuses either name on every
--      approval, control, decision-of-record, order, intent and fill table,
--      and the task-events guard refuses either moving a task to an
--      approval / release / rollback state. The learning and improvement
--      pipelines classify ARCHER as a MACHINE actor; 225's named-human rule
--      refuses 'archer' as a LIVE approver / cutover recorder, as it
--      refused 'eddie'.
--   4. 221's improvement stage guard admits ARCHER as a peer agent.
--   5. NEW WRITES USE ARCHER; HISTORY IS FROZEN: agent_historical_alias_
--      guard() refuses a NEW row naming the alias in any agent-id column of
--      the tables listed in agent_historical_alias_guarded_tables(). On the
--      identity record (agent_identities, agent_status) and on the rows a
--      later stage would otherwise update (agent_findings, improve_items,
--      pos_candidate_review_steps) it also refuses UPDATE / DELETE of an
--      alias row: the EDDIE identity can never be re-activated beside
--      ARCHER, nor erased, and a historical finding / item / review step
--      stays exactly as recorded (the code refuses to advance one first:
--      collaboration_loop / improvement_stages R_HISTORICAL_ALIAS).
--   6. Version 1 of ARCHER's identity and voice profile, generated from the
--      code, PENDING_OWNER_APPROVAL (approved_at NULL: nothing here is an
--      approval). EDDIE's version 1 (224) stays recorded, unchanged.
--
-- WHAT DOES NOT CHANGE: no threshold, limit, cap, scale, policy, sleeve,
-- submission switch, SMALL LIVE / mirror control, credential, allowlist or
-- settlement rule. No order, intent or fill table gains a writer.
--
-- IDEMPOTENT: CREATE OR REPLACE / DROP-then-ADD / DROP TRIGGER IF EXISTS /
-- WHERE NOT EXISTS. No BEGIN/COMMIT of its own (the runner wraps the file in
-- one transaction). Rollback: rollback/266_archer_execution_agent_rename.down.sql (refuses while ARCHER
-- has left any record).

-- ── 1 · THE CANONICAL ID ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION agent_canonical_id(v text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN v IS NULL THEN NULL
        WHEN upper(btrim(v)) = 'EDDIE' THEN 'ARCHER'
        ELSE upper(btrim(v)) END
$$;

-- ── 2 · THE AGENT-ID CHECKS ──────────────────────────────────────────
ALTER TABLE agent_identities
    DROP CONSTRAINT IF EXISTS agent_identities_agent_id_check;
ALTER TABLE agent_identities
    ADD CONSTRAINT agent_identities_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'ARCHER', 'SCOUT', 'ADRIANA'));

ALTER TABLE agent_persona_versions
    DROP CONSTRAINT IF EXISTS agent_persona_versions_agent_id_check;
ALTER TABLE agent_persona_versions
    ADD CONSTRAINT agent_persona_versions_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'ARCHER', 'SCOUT', 'ADRIANA'));
ALTER TABLE agent_chat_conversations
    DROP CONSTRAINT IF EXISTS agent_chat_conversations_agent_id_check;
ALTER TABLE agent_chat_conversations
    ADD CONSTRAINT agent_chat_conversations_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                        'ARCHER', 'SCOUT', 'ADRIANA'));

ALTER TABLE agent_slack_delivery
    DROP CONSTRAINT IF EXISTS agent_slack_delivery_agent_check;
ALTER TABLE agent_slack_delivery
    ADD CONSTRAINT agent_slack_delivery_agent_check
    CHECK (agent IN ('derek', 'xavier', 'audrey', 'karen', 'eddie',
                     'archer', 'scout', 'adriana'));

ALTER TABLE agent_findings DROP CONSTRAINT IF EXISTS agent_findings_proposer_ck;
ALTER TABLE agent_findings
    ADD CONSTRAINT agent_findings_proposer_ck CHECK (
        proposer IN ('DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'ARCHER', 'SCOUT',
                     'ADRIANA'));
ALTER TABLE agent_finding_stages
    DROP CONSTRAINT IF EXISTS agent_finding_stages_actor_ck;
ALTER TABLE agent_finding_stages
    ADD CONSTRAINT agent_finding_stages_actor_ck CHECK (
        actor IN ('DEREK', 'XAVIER', 'AUDREY')
        OR (actor IN ('EDDIE', 'ARCHER', 'SCOUT', 'ADRIANA')
            AND stage <> 'RELEASE_ELIGIBILITY')
        OR (actor = 'KAREN' AND stage = 'PEER_CHALLENGE'));

ALTER TABLE agent_identity_versions
    DROP CONSTRAINT IF EXISTS agent_identity_agent_ck;
ALTER TABLE agent_identity_versions
    ADD CONSTRAINT agent_identity_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'ARCHER', 'SCOUT', 'ADRIANA'));
-- his authority is fixed here exactly as EDDIE's was: SHADOW_ONLY
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
        OR (agent_id = 'ARCHER' AND authority_status = 'SHADOW_ONLY')
        OR (agent_id = 'SCOUT'
            AND authority_status = 'RESEARCH_SHADOW_ONLY')
        OR (agent_id = 'ADRIANA' AND authority_status = 'SHADOW_ONLY'));

ALTER TABLE agent_voice_profiles
    DROP CONSTRAINT IF EXISTS agent_voice_profiles_agent_ck;
ALTER TABLE agent_voice_profiles
    ADD CONSTRAINT agent_voice_profiles_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'ARCHER', 'SCOUT', 'ADRIANA'));

ALTER TABLE agent_conversation_messages
    DROP CONSTRAINT IF EXISTS agent_conv_agents_ck;
ALTER TABLE agent_conversation_messages
    ADD CONSTRAINT agent_conv_agents_ck CHECK (
        from_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                       'CHIEF_ALLOCATOR', 'EDDIE', 'ARCHER', 'SCOUT',
                       'ADRIANA')
        AND to_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                         'CHIEF_ALLOCATOR', 'EDDIE', 'ARCHER', 'SCOUT',
                         'ADRIANA')
        AND from_agent <> to_agent);

ALTER TABLE agent_work_requests
    DROP CONSTRAINT IF EXISTS agent_work_requests_agent_ck;
ALTER TABLE agent_work_requests
    ADD CONSTRAINT agent_work_requests_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'ARCHER', 'SCOUT', 'ADRIANA'));

ALTER TABLE improve_items DROP CONSTRAINT IF EXISTS improve_items_owner_ck;
ALTER TABLE improve_items
    ADD CONSTRAINT improve_items_owner_ck CHECK (owner_agent IN (
        'DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'ARCHER', 'SCOUT',
        'CHIEF_ALLOCATOR', 'ADRIANA'));

-- 219's twin scorecards: one row per agent per metric -- ARCHER's from now
ALTER TABLE twin_agent_scorecards
    DROP CONSTRAINT IF EXISTS twin_scorecards_agent_ck;
ALTER TABLE twin_agent_scorecards
    ADD CONSTRAINT twin_scorecards_agent_ck CHECK (agent IN (
        'DEREK', 'XAVIER', 'EDDIE', 'ARCHER', 'SCOUT', 'KAREN', 'ALLOCATOR',
        'AUDREY'));

-- 217's candidate-review workflow: step 4 is ARCHER's from now; the
-- historical (4, EDDIE_EXECUTION_ESTIMATE, EDDIE) rows stay valid
ALTER TABLE pos_candidate_review_steps
    DROP CONSTRAINT IF EXISTS pos_steps_order_ck;
ALTER TABLE pos_candidate_review_steps
    ADD CONSTRAINT pos_steps_order_ck CHECK ((seq, step, agent) IN (
        (1, 'DEREK_CANDIDATE', 'DEREK'),
        (2, 'KAREN_CHALLENGE', 'KAREN'),
        (3, 'SCOUT_EVIDENCE', 'SCOUT'),
        (4, 'EDDIE_EXECUTION_ESTIMATE', 'EDDIE'),
        (4, 'ARCHER_EXECUTION_ESTIMATE', 'ARCHER'),
        (5, 'ALLOCATOR_RANKING', 'CHIEF_ALLOCATOR'),
        (6, 'AUDREY_RISK_CHECK', 'AUDREY'),
        (7, 'XAVIER_MANAGEMENT_PLAN', 'XAVIER')));

-- ── 3 · NO AUTHORITY, IN THE DATABASE ────────────────────────────────
-- 265's acting-identity function: every EDDIE label now resolves to
-- ARCHER, and ARCHER's own labels are added.
CREATE OR REPLACE FUNCTION pos_agent_actor(v text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN v IS NULL THEN NULL
        WHEN upper(btrim(v)) = 'EDDIE'
          OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+EDDIE([^A-Z]|$)'
          OR upper(btrim(v)) ~ 'EDDIE[ ._:-]*(AGENT|BOT|EXECUTION)'
            THEN 'ARCHER'
        WHEN upper(btrim(v)) = 'ARCHER'
          OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+ARCHER([^A-Z]|$)'
          OR upper(btrim(v)) ~ 'ARCHER[ ._:-]*(AGENT|BOT|EXECUTION)'
            THEN 'ARCHER'
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

-- 265's learning-pipeline machine test, ARCHER added.
CREATE OR REPLACE FUNCTION poslearn_is_agent_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT v IS NULL
        OR btrim(v) = ''
        OR upper(btrim(v)) IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                               'SCOUT', 'ALLOCATOR', 'ADRIANA', 'ARCHER',
                               'CLAUDE',
                               'SYSTEM', 'RUNNER', 'POS_LEARN_RUNNER',
                               'MIGRATION', 'ROOT', 'POSTGRES', 'BOT', 'AGENT')
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN|RUNNER|INTEL|AUTOMATION|SERVICE)([:/ ._-]|$)'
        OR upper(btrim(v)) ~ '(DEREK|XAVIER|AUDREY|KAREN|EDDIE|ARCHER|SCOUT|ALLOCATOR|ADRIANA)[ ._:-]*(AGENT|BOT|V[0-9]|CHALLENGER|RED[ ._-]*TEAM|ARB|ARBITRAGE)'
$$;

-- 265's improvement-pipeline machine test, ARCHER added.
CREATE OR REPLACE FUNCTION improve_is_machine_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT v IS NULL
        OR btrim(v) = ''
        OR upper(btrim(v)) IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                               'SCOUT', 'ALLOCATOR', 'CHIEF_ALLOCATOR',
                               'ADRIANA', 'ARCHER',
                               'CALIBRATION_ENGINE', 'MODEL_TOURNAMENT',
                               'CLAUDE', 'SYSTEM', 'RUNNER', 'MIGRATION',
                               'ROOT', 'POSTGRES', 'BOT', 'AGENT', 'CI',
                               'GITHUB', 'GITHUB_ACTIONS', 'RENDER', 'NETLIFY',
                               'DEPENDABOT', 'IMPROVEMENT_PIPELINE',
                               'POS_LEARN_RUNNER', 'POS_WORKFLOW', 'UNKNOWN',
                               'ANONYMOUS', 'SERVICE', 'AUTOMATION', 'CRON')
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN|RUNNER|INTEL|AUTOMATION|SERVICE|IMPROVEMENT|PIPELINE|GITHUB|CI|CRON|WORKER|DEPLOY)([:/ ._-]|$)'
        OR upper(btrim(v)) ~ '(DEREK|XAVIER|AUDREY|KAREN|EDDIE|ARCHER|SCOUT|ALLOCATOR|ADRIANA)[ ._:-]*(AGENT|BOT|V[0-9]|CHALLENGER|RED[ ._-]*TEAM|EXECUTION|RESEARCH|INTEL|ARB|ARBITRAGE)'
        OR lower(btrim(v)) ~ '\[bot\]'
        OR lower(btrim(v)) ~ '(^|[^a-z])(bot|runner|daemon|scheduler)$'
$$;

-- 225's named-human rule, 'archer' added beside 'eddie' (character for
-- character canonical_intent.NON_HUMAN_ACTOR_PATTERN; a test pins them).
CREATE OR REPLACE FUNCTION live_parity_named_human(actor text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT actor IS NOT NULL AND length(btrim(actor)) > 0
       AND btrim(actor) !~* '^(system|derek|xavier|audrey|karen|allie|chief_allocator|eddie|archer|scout|bettor|claude|agent|migration|test_harness_system)|(^|[^a-z0-9])(bots?|ci|cron|codex|assistant|openai|gpt|chatgpt|anthropic|llm|copilot|automation|automated|service|svc|root|admin|administrator|scheduler|deploy|deployer|render|github|actions|worker|daemon|robot|script|pipeline|webhook|unknown|anonymous|none|null)([^a-z0-9]|$)|\[bot\]'
$$;

-- ── 4 · THE IMPROVEMENT STAGE GUARD ──────────────────────────────────
-- 221's guard, ARCHER a peer agent (EDDIE, the alias, writes no new row).
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
                                         'ARCHER', 'SCOUT', 'CHIEF_ALLOCATOR'];
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

-- ── 5 · NEW WRITES USE ARCHER; THE ALIAS'S IDENTITY ROW IS FROZEN ────
CREATE OR REPLACE FUNCTION agent_historical_alias_guard() RETURNS trigger
AS $$
DECLARE
    col  text;
    doc  jsonb;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        doc := to_jsonb(OLD);
        FOREACH col IN ARRAY TG_ARGV LOOP
            IF upper(btrim(doc->>col)) = 'EDDIE' THEN
                RAISE EXCEPTION 'EDDIE_IS_A_HISTORICAL_ALIAS: %.% = % is a '
                                'historical record of ARCHER (append-only); '
                                '% refused', TG_TABLE_NAME, col, doc->>col,
                                TG_OP USING ERRCODE = 'check_violation';
            END IF;
        END LOOP;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
    END IF;
    doc := to_jsonb(NEW);
    FOREACH col IN ARRAY TG_ARGV LOOP
        IF upper(btrim(doc->>col)) = 'EDDIE' THEN
            RAISE EXCEPTION 'EDDIE_IS_A_HISTORICAL_ALIAS: %.% cannot be '
                            'written as %; new records name ARCHER',
                            TG_TABLE_NAME, col, doc->>col
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

-- (table, agent-id columns, frozen): the identity tables are frozen for
-- the alias (no UPDATE / DELETE of its row); every table refuses a NEW
-- alias row. A table absent in a given database is skipped.
CREATE OR REPLACE FUNCTION agent_historical_alias_guarded_tables()
RETURNS TABLE (tbl text, cols text[], frozen boolean)
LANGUAGE sql IMMUTABLE AS $$
    SELECT * FROM (VALUES
        ($q$agent_identities$q$, ARRAY[$q$agent_id$q$], true),
        ($q$agent_status$q$, ARRAY[$q$agent_id$q$], true),
        ($q$agent_runs$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_tasks$q$, ARRAY[$q$assignee$q$], false),
        ($q$agent_task_events$q$, ARRAY[$q$actor$q$], false),
        ($q$agent_decisions$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_persona_versions$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_chat_conversations$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_chat_messages$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_chat_turns$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_slack_delivery$q$, ARRAY[$q$agent$q$], false),
        ($q$agent_findings$q$, ARRAY[$q$proposer$q$], true),
        ($q$agent_finding_stages$q$, ARRAY[$q$actor$q$], false),
        ($q$agent_identity_versions$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_voice_profiles$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_voice_resolutions$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_conversation_messages$q$, ARRAY[$q$from_agent$q$, $q$to_agent$q$], false),
        ($q$agent_work_requests$q$, ARRAY[$q$agent_id$q$], false),
        ($q$agent_memory_events$q$, ARRAY[$q$agent_id$q$], false),
        ($q$improve_items$q$, ARRAY[$q$owner_agent$q$], true),
        ($q$twin_agent_scorecards$q$, ARRAY[$q$agent$q$], false),
        ($q$pos_candidate_review_steps$q$, ARRAY[$q$agent$q$], true)
    ) AS t(tbl, cols, frozen)
$$;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT * FROM agent_historical_alias_guarded_tables() LOOP
        IF to_regclass(r.tbl) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('DROP TRIGGER IF EXISTS agent_historical_alias_trg '
                       'ON %I', r.tbl);
        EXECUTE format(
            'CREATE TRIGGER agent_historical_alias_trg BEFORE %s ON %I '
            'FOR EACH ROW EXECUTE FUNCTION agent_historical_alias_guard(%s)',
            CASE WHEN r.frozen THEN 'INSERT OR UPDATE OR DELETE'
                 ELSE 'INSERT' END,
            r.tbl,
            (SELECT string_agg(quote_literal(c), ', ') FROM unnest(r.cols) c));
    END LOOP;
END $$;

-- ── 6 · VERSION 1 OF ARCHER'S IDENTITY AND VOICE PROFILE ─────────────
-- Generated from sportsassets.agents.identity (IDENTITY_SPEC, VOICE_SPEC,
-- SOURCES); tests/test_archer_rename.py proves the rows equal the code.
-- PENDING_OWNER_APPROVAL, approved_at NULL: nothing here is an approval.
-- ══ SEED BEGIN (generated) ══
INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$ARCHER$q$, 1, $q$Archer$q$, $q$Head of Execution$q$, $q$MALE$q$, $q$HEAD_OF_EXECUTION$q$, $q$Preserve Derek's theoretical edge between decision and fill: spread, depth, fees, slippage, queue position, fill probability, latency and venue health, estimated in SHADOW. EXECUTE_NOW is a recommendation until a separately authorized live lane exists.$q$,
  $q$["fast", "terse", "pragmatic", "microstructure-obsessed"]$q$::jsonb,
  $q$Terse. Spread, depth, fees, fill probability and the net executable edge, in that order; names every unmeasured input.$q$, $q$vp-archer-v1$q$,
  $q$["market microstructure", "fees and slippage", "fill probability", "venue health"]$q$::jsonb,
  $q$["Price is not execution.", "Never recommend executing when the expected executable EV is <= 0 or unmeasured.", "An estimate is SHADOW; nothing executes on it."]$q$::jsonb,
  $q$["Estimate executable edge, fill probability and slippage for Derek's decisions (SHADOW)", "Measure realized execution against the estimate"]$q$::jsonb,
  $q$["Place, cancel or route any order", "Change a size, limit or threshold", "Allocate, reserve or approve capital", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$Price is not execution.$q$, $q$SHADOW_ONLY$q$,
  $q$c8088e39d16915b6259b2b31a5105ab8a3979d16eae965d734cd0598093e8a26$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$PM_DIRECTIVE_2026-10-05_ARCHER$q$,
  $q$CURRENT BETTOR PM DIRECTIVE (2026-10-05): the execution agent EDDIE is renamed ARCHER / archer, Head of Execution; same SHADOW_ONLY mandate and authority; EDDIE kept only as a historical alias for audit (migration 266)$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$ARCHER$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-archer-v1$q$, $q$ARCHER$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_ARCHER$q$, NULL, $q$Archer voice (ElevenLabs)$q$, $q$en-US$q$, 1.04,
  $q$Fast and terse; numbers first. adult man; measured, precise, low and even; trading-floor composure$q$,
  $q$RESOLVED_AT_RUNTIME$q$, $q$agent_persona_versions.voice_profile (ARCHER)$q$, $q$355f28e78493d8e351e83cec980a39bfdd233d965a63b42dd36165d8cb2b4266$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$PM_DIRECTIVE_2026-10-05_ARCHER$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$ARCHER$q$);
-- ══ SEED END ══
