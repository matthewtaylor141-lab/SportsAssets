-- 265: ADRIANA, HEAD OF ARBITRAGE -- THE EIGHTH AGENT (SHADOW ONLY).
--
-- PM directive 2026-10-05 ("Build ADRIANA now"): canonical identity ADRIANA
-- / adriana, a real SHADOW agent that proves or refuses fixed-payout
-- structures (cross-venue complements, YES / NO complements, middles across
-- lines, exhaustive outcome baskets). She records every opportunity and
-- every refusal; nothing executes on either.
--
-- WHAT THIS ADDS
--   1. ADRIANA in every agent-id CHECK an agent's own records pass through
--      (identities, personas, chat, Slack delivery, collaboration loop,
--      identity versions, voice profiles, conversations, work requests,
--      improvement items) -- widened by DROP-then-ADD, every existing agent
--      kept exactly as it was.
--   2. NO AUTHORITY, IN THE DATABASE: pos_agent_actor() now names ADRIANA,
--      so the aa_pos_agents_no_authority_trg trigger that 217 / 225 attach
--      to every approval, control, decision-of-record, order, intent and
--      fill table refuses her -- named in an actor column, or declared as
--      the session's acting agent (bettor.acting_agent, which her runner
--      sets on every transaction). The task-events guard refuses her moving
--      a task to APPROVAL_READY / APPROVED / RELEASED / ROLLED_BACK. The
--      learning and improvement pipelines classify her as a MACHINE actor,
--      never a human approver, reviewer or engineer.
--   3. HER OWN RECORDS, append-only, each production_effect = 'NONE' and
--      mode = 'SHADOW':
--        adriana_arb_scans          one row per census pass (counts by
--                                   verdict, kind and refusal code; per-venue
--                                   support; what was and was not recorded)
--        adriana_arb_opportunities  a structure proven GUARANTEED_AFTER_COSTS
--                                   on synchronized fresh books, with its
--                                   legs, size, costs and evidence
--        adriana_arb_refusals       a structure refused, with every code
--   4. Version 1 of her identity and voice profile, PENDING_OWNER_APPROVAL
--      (approved_at NULL: nothing here is an approval). No voice id exists
--      for her, so her voice is UNASSIGNED -- never another agent's.
--
-- WHAT DOES NOT CHANGE: no threshold, limit, cap, scale, policy, sleeve,
-- submission switch, SMALL LIVE / mirror control, credential or settlement
-- rule. No order, intent or fill table gains a writer.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP-then-ADD / WHERE NOT EXISTS.
-- No BEGIN/COMMIT of its own (the runner wraps the file in one transaction).

-- ── 1 · THE AGENT-ID CHECKS ──────────────────────────────────────────
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

-- ── 2 · NO AUTHORITY, IN THE DATABASE ────────────────────────────────
-- 217's acting-identity function with ADRIANA added. The agent id, or a
-- machine-style label (agent:adriana, slack:adriana, adriana-bot, adriana
-- arbitrage); a person called Adriana ("Adriana Ruiz") is NOT matched.
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

-- 218's learning-pipeline machine test, ADRIANA added (never a human
-- approver of a model, label or promotion).
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

-- 221's improvement-pipeline machine test, ADRIANA added (never a human
-- approver, reviewer or engineer of a change).
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

-- ── 3 · HER RECORDS (append-only, SHADOW, no production effect) ──────
CREATE TABLE IF NOT EXISTS adriana_arb_scans (
    scan_id               text        PRIMARY KEY,
    started_at            timestamptz NOT NULL,
    finished_at           timestamptz NOT NULL,
    status                text        NOT NULL,
    why                   text,
    engine_version        text        NOT NULL,
    venues                jsonb       NOT NULL,
    markets_read          integer     NOT NULL,
    books_fresh           integer     NOT NULL,
    structures_considered integer     NOT NULL,
    opportunities         integer     NOT NULL,
    refusals_total        integer     NOT NULL,
    refusals_recorded     integer     NOT NULL,
    by_verdict            jsonb       NOT NULL,
    by_kind               jsonb       NOT NULL,
    by_code               jsonb       NOT NULL,
    limits                jsonb       NOT NULL,
    authority             jsonb       NOT NULL,
    mode                  text        NOT NULL DEFAULT 'SHADOW',
    production_effect     text        NOT NULL DEFAULT 'NONE',
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT adriana_scans_status_ck CHECK (status IN (
        'OK', 'NO_EVIDENCE', 'PARTIAL', 'FAILED')),
    CONSTRAINT adriana_scans_counts_ck CHECK (
        markets_read >= 0 AND books_fresh >= 0
        AND structures_considered >= 0 AND opportunities >= 0
        AND refusals_total >= 0
        AND refusals_recorded BETWEEN 0 AND refusals_total
        AND opportunities + refusals_total = structures_considered),
    CONSTRAINT adriana_scans_order_ck CHECK (finished_at >= started_at),
    CONSTRAINT adriana_scans_why_ck CHECK (
        status = 'OK' OR length(btrim(coalesce(why, ''))) > 0),
    -- she declares, on every pass, that she holds no authority
    CONSTRAINT adriana_scans_authority_ck CHECK (
        authority->>'submit' = 'false' AND authority->>'cancel' = 'false'
        AND authority->>'credentials' = 'false'
        AND authority->>'capital' = 'false'),
    CONSTRAINT adriana_scans_mode_ck CHECK (mode = 'SHADOW'),
    CONSTRAINT adriana_scans_effect_ck CHECK (production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS adriana_arb_scans_at_idx
    ON adriana_arb_scans (finished_at DESC);

CREATE TABLE IF NOT EXISTS adriana_arb_opportunities (
    opportunity_id      text        PRIMARY KEY,
    scan_id             text        NOT NULL REFERENCES adriana_arb_scans,
    structure_kind      text        NOT NULL,
    event_key           text        NOT NULL,
    venues              text[]      NOT NULL,
    legs                jsonb       NOT NULL,
    verdict             text        NOT NULL,
    max_qty             integer     NOT NULL,
    min_payout_usd      numeric     NOT NULL,
    total_cost_usd      numeric     NOT NULL,
    net_profit_usd      numeric     NOT NULL,
    edge_per_set_usd    numeric     NOT NULL,
    books               jsonb       NOT NULL,
    economics           jsonb       NOT NULL,
    leg_plan            jsonb       NOT NULL,
    evidence_refs       jsonb       NOT NULL,
    decided_at          timestamptz NOT NULL,
    mode                text        NOT NULL DEFAULT 'SHADOW',
    production_effect   text        NOT NULL DEFAULT 'NONE',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT adriana_opp_kind_ck CHECK (structure_kind IN (
        'COMPLEMENT', 'MIDDLE_FLOOR', 'YES_BASKET', 'NO_BASKET')),
    -- an opportunity row exists ONLY for a structure proven after costs
    CONSTRAINT adriana_opp_verdict_ck CHECK (
        verdict = 'GUARANTEED_AFTER_COSTS'),
    CONSTRAINT adriana_opp_math_ck CHECK (
        max_qty >= 1 AND net_profit_usd > 0 AND edge_per_set_usd > 0
        AND total_cost_usd > 0 AND min_payout_usd > 0),
    CONSTRAINT adriana_opp_legs_ck CHECK (
        jsonb_typeof(legs) = 'array' AND jsonb_array_length(legs) >= 2),
    -- her own grounding check (not 217's pos_refs_grounded, so 217 can
    -- still be rolled back and re-applied on its own)
    CONSTRAINT adriana_opp_refs_ck CHECK (
        jsonb_typeof(evidence_refs) = 'array'
        AND jsonb_array_length(evidence_refs) BETWEEN 1 AND 50),
    CONSTRAINT adriana_opp_mode_ck CHECK (mode = 'SHADOW'),
    CONSTRAINT adriana_opp_effect_ck CHECK (production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS adriana_arb_opportunities_at_idx
    ON adriana_arb_opportunities (decided_at DESC);

CREATE TABLE IF NOT EXISTS adriana_arb_refusals (
    refusal_id          text        PRIMARY KEY,
    scan_id             text        NOT NULL REFERENCES adriana_arb_scans,
    structure_kind      text        NOT NULL,
    event_key           text,
    venues              text[]      NOT NULL,
    legs                jsonb       NOT NULL,
    codes               text[]      NOT NULL,
    primary_code        text        NOT NULL,
    detail              jsonb       NOT NULL,
    decided_at          timestamptz NOT NULL,
    mode                text        NOT NULL DEFAULT 'SHADOW',
    production_effect   text        NOT NULL DEFAULT 'NONE',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT adriana_ref_kind_ck CHECK (structure_kind IN (
        'COMPLEMENT', 'MIDDLE_FLOOR', 'YES_BASKET', 'NO_BASKET', 'PAIR',
        'UNCLASSIFIED')),
    CONSTRAINT adriana_ref_codes_ck CHECK (
        cardinality(codes) >= 1 AND primary_code = codes[1]
        AND primary_code ~ '^[A-Z][A-Z0-9_]{2,80}$'),
    CONSTRAINT adriana_ref_mode_ck CHECK (mode = 'SHADOW'),
    CONSTRAINT adriana_ref_effect_ck CHECK (production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS adriana_arb_refusals_at_idx
    ON adriana_arb_refusals (decided_at DESC);
CREATE INDEX IF NOT EXISTS adriana_arb_refusals_scan_idx
    ON adriana_arb_refusals (scan_id);

CREATE OR REPLACE FUNCTION adriana_records_append_only() RETURNS trigger
AS $$
BEGIN
    RAISE EXCEPTION '%: append-only, % refused', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'check_violation';
END $$ LANGUAGE plpgsql;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['adriana_arb_scans', 'adriana_arb_opportunities',
                             'adriana_arb_refusals'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION '
                       'adriana_records_append_only()',
                       t || '_append_only_trg', t);
    END LOOP;
END $$;

-- ── 4 · VERSION 1 OF HER IDENTITY AND VOICE PROFILE ──────────────────
-- Generated from sportsassets.agents.identity (IDENTITY_SPEC, VOICE_SPEC,
-- SOURCES); tests/test_adriana_agent.py proves the rows equal the code.
-- PENDING_OWNER_APPROVAL, approved_at NULL: nothing here is an approval.
-- ══ SEED BEGIN (generated) ══
INSERT INTO agent_identity_versions (agent_id, identity_version, display_name, title, presentation, role, mission, personality_traits, communication_style, default_voice_profile, expertise_domains, decision_principles, may, may_not, signature, authority_status, content_sha, approved_by, approved_at, source_directive, source_ref)
SELECT $q$ADRIANA$q$, 1, $q$Adriana$q$, $q$Head of Arbitrage$q$, $q$FEMALE$q$, $q$HEAD_OF_ARBITRAGE$q$, $q$Find structures whose payout is fixed in every outcome -- cross-venue complements, YES / NO complements, middles and exhaustive outcome baskets -- and prove or refuse each one: identical settlement and payoff in every outcome, synchronized fresh books, executable depth on every leg and a positive worst case after every fee, slippage allowance and cost. Records every opportunity and refusal in SHADOW; nothing executes on it.$q$,
  $q$["exacting", "calm", "settlement-literate", "hardest on her own numbers"]$q$::jsonb,
  $q$Precise and unhurried. States the structure, the worst-case payout, the size and the cost, then every condition that would break it.$q$, $q$vp-adriana-v1$q$,
  $q$["cross-venue arbitrage", "settlement and payoff equivalence", "complement and basket pricing", "order-book depth, fees and leg risk"]$q$::jsonb,
  $q$["If one outcome can lose, it is not an arbitrage.", "Settlement first, prices second.", "A stale or unsynchronized book proves nothing.", "An opportunity is SHADOW; nothing executes on it."]$q$::jsonb,
  $q$["Read recorded venue books, the catalogue and settlement terms", "Record arbitrage opportunities and refusals with their evidence (SHADOW)", "Hand an opportunity to Eddie for an execution review and ask Karen to challenge it"]$q$::jsonb,
  $q$["Place, cancel or route any order on any venue", "Hold or read any venue credential", "Allocate, reserve or approve capital", "Change a size, limit, threshold, fee or freshness rule", "Activate itself, change a limit, credential or threshold, deploy code, approve its own work or promote its own model"]$q$::jsonb,
  $q$If one outcome can lose, it is not an arbitrage.$q$, $q$SHADOW_ONLY$q$,
  $q$0bce787c0a944d8ac155ac3ffc9f109a474d2ff884199e67009053a46caad2f1$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$PM_DIRECTIVE_2026-10-05_ADRIANA$q$,
  $q$CURRENT BETTOR PM DIRECTIVE (2026-10-05) section 3 'Build ADRIANA now': canonical identity ADRIANA / adriana, eighth BETTOR employee, SHADOW/PAPER arbitrage agent$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_identity_versions WHERE agent_id = $q$ADRIANA$q$);

INSERT INTO agent_voice_profiles (voice_profile_id, agent_id, version, provider, provider_voice_alias, provider_voice_id, display_name, locale, speaking_rate, style_instructions, assignment, persona_voice_ref, content_sha, approved_by, approved_at, source_directive)
SELECT $q$vp-adriana-v1$q$, $q$ADRIANA$q$, 1, $q$elevenlabs$q$, $q$ELEVENLABS_VOICE_ID_ADRIANA$q$, NULL, $q$Adriana voice (unassigned)$q$, $q$en-US$q$, NULL,
  $q$Precise and unhurried; worst case first. adult woman; her own voice, never another agent's.$q$,
  $q$UNASSIGNED$q$, NULL, $q$baae06e270124d44d71f4af4b83e415c5ae5a4442903c3a0bd92a3f70531db51$q$, 'PENDING_OWNER_APPROVAL', NULL, $q$PM_DIRECTIVE_2026-10-05_ADRIANA$q$
WHERE NOT EXISTS (SELECT 1 FROM agent_voice_profiles WHERE agent_id = $q$ADRIANA$q$);
-- ══ SEED END ══
