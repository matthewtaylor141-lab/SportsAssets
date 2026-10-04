-- ══════════════════════════════════════════════════════════════════════
-- 221 · THE BOUNDED SELF-IMPROVEMENT AND COLLABORATION PIPELINE:
--       ONE LEDGER FOR EVERY IMPROVEMENT ITEM, IN THE CANONICAL STAGES,
--       WITH NO DEPLOY, MERGE, CAPITAL OR APPROVAL AUTHORITY OF ITS OWN
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT EXISTS AND WHAT THIS ADDS. The agents already keep the pieces:
--   * agent_findings / agent_finding_stages (203): a finding's EVIDENCE ->
--     HYPOTHESIS -> PEER_CHALLENGE -> BOUNDED_EXPERIMENT -> CANDIDATE ->
--     INDEPENDENT_EVALUATION -> RELEASE_ELIGIBILITY, Derek / Xavier / Audrey
--     (+ Karen as challenger only, + Eddie / Scout never at eligibility);
--   * improvement_deficits (209): Audrey's measured deficits driven into
--     that loop as far as BOUNDED_EXPERIMENT (agents/improvement_driver.py);
--   * karen_challenges (207 / 212): Karen's grounded challenges, the
--     target's peer response and an independent evaluator's outcome;
--   * poslearn_promotion_steps / poslearn_human_approvals (218): the model
--     promotion ladder no agent can finish;
--   * pos_candidate_review_steps (217): the per-candidate review chain;
--   * improvement_candidates / improvement_releases (155): Audrey's bounded
--     policy-parameter workflow.
-- None of them carries the WHOLE path an improvement has to walk --
-- problem -> evidence -> hypothesis -> challenge -> owner response ->
-- experiment / candidate patch + tests -> independent review -> gate ->
-- controlled release -> forward monitoring -> rollback -- nor the human and
-- engineering steps at its end. This migration adds that ledger. It COPIES
-- NO ECONOMICS and REPLACES NOTHING: every stage row points at the record it
-- was taken from (source_ref), and the existing tables stay authoritative.
--
-- THE CANONICAL STAGES (stage_seq), forward only, none skipped:
--   1 EVIDENCE                RUNNER (seeded from a real signal) / OWNER
--   2 HYPOTHESIS              the OWNER agent only, once
--   3 PEER_CHALLENGE          KAREN (CHALLENGER) and a DIFFERENT agent
--                             (PEER_AGENT); repeatable
--   4 OWNER_RESPONSE          the OWNER agent (or a HUMAN owner) answers --
--                             only after BOTH a Karen challenge and a peer
--                             challenge are on the record; repeatable
--   5 EXPERIMENT              OWNER / ENGINEERING / HUMAN: the experiment
--                             ids and/or the candidate patch (branch, commit
--                             SHA, PR URL -- TEXT ONLY) and tests reference
--   6 INDEPENDENT_EVALUATION  an evaluator who is NOT the owner and NOT an
--                             author of the hypothesis or the experiment /
--                             patch: PASS / FAIL / INCONCLUSIVE; repeatable
--                             (one per reviewer counts)
--   7 ELIGIBLE_CHANGE         only with enough distinct independent PASSES
--                             (1, or 2 for a protected area) and no
--                             reviewer's latest outcome FAIL; never by the
--                             owner or an author; a protected area needs a
--                             HUMAN to record it; once
--   8 CONTROLLED_RELEASE      a HUMAN approver only (never an agent, system
--                             or bot name), with an EXACT-SHA gate receipt
--                             that equals the experiment's patch commit SHA,
--                             and a release reference; never the patch
--                             author; once
--   9 FORWARD_RESULT          the monitoring window (after the release) and
--                             its forward result: HELD / DEGRADED /
--                             INCONCLUSIVE; repeatable
--  98 ROLLED_BACK             HUMAN / ENGINEERING, from 8 or 9, with a
--                             rollback reference; terminal
--  99 CLOSED                  from any stage, with a reason; terminal
--
-- PROTECTED AREAS. An item touching risk, accounting, settlement,
-- execution authorization, financial controls, credentials or live capital
-- limits carries them in protected_areas, which forces
-- requires_human_review = true and required_independent_reviews >= 2
-- (CHECK). A classification can be ESCALATED, never relaxed (trigger).
--
-- DISAGREEMENTS ARE PRESERVED. improve_disagreements keeps every
-- party's position verbatim with the record it came from; the positions
-- never change; a resolution is recorded once and says HOW it ended
-- (DECIDED by a third party, CONCEDED by a party, WITHDRAWN by the raiser).
-- consensus can be true only on a concession -- a decision by a third party
-- is not agreement, and nothing here can say it was.
--
-- WHAT THIS LEDGER CANNOT DO, IN THE DATABASE:
--   * production_effect = 'NONE', label = 'SHADOW' on every row; no jsonb
--     carries a limit, capital, credential, approval, activation, promotion,
--     release, order, submit, merge, push or deploy key (CHECK);
--   * the patch / gate / release / rollback references are TEXT: nothing
--     here pushes, merges, deploys or runs anything -- a human and the
--     engineering process do, and record the reference afterwards;
--   * every agent-written stage cites the record it was taken from, and that
--     record must exist (trigger): the runner fabricates no text;
--   * HUMAN / ENGINEERING rows must name a non-machine actor who is also the
--     recorder, and are refused outright in a session that declared itself
--     the improvement runner (set_config('bettor.improvement_runner','on')).
--   * items, events, disagreements and runs are never deleted; events and
--     runs are append-only.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP-then-CREATE for triggers.
-- No BEGIN/COMMIT of its own. No foreign key outside this migration.

-- ── WHO IS A MACHINE (never a human approver, reviewer or engineer) ───
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

CREATE OR REPLACE FUNCTION improve_refs_grounded(refs jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN refs IS NULL OR jsonb_typeof(refs) <> 'array' THEN false
        WHEN jsonb_array_length(refs) NOT BETWEEN 1 AND 50 THEN false
        ELSE NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(refs) e
             WHERE jsonb_typeof(e) <> 'object'
                OR coalesce(btrim(e->>'kind'), '') = ''
                OR coalesce(btrim(e->>'id'), '') = '')
    END
$$;

CREATE OR REPLACE FUNCTION improve_ref_ok(ref jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT ref IS NOT NULL AND jsonb_typeof(ref) = 'object'
       AND coalesce(btrim(ref->>'kind'), '') <> ''
       AND coalesce(btrim(ref->>'id'), '') <> ''
$$;

-- No key, at the top level or one level down, that would make a row carry
-- a limit, an approval, an activation or a deploy / merge instruction.
CREATE OR REPLACE FUNCTION improve_no_authority(doc jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT doc IS NULL OR jsonb_typeof(doc) <> 'object' OR NOT EXISTS (
        SELECT 1 FROM (
            SELECT k FROM jsonb_object_keys(doc) k
            UNION ALL
            SELECT k2 FROM jsonb_each(doc) e,
                   LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(e.value)
                       = 'object' THEN e.value ELSE '{}'::jsonb END) k2
        ) keys(k)
         WHERE lower(k) IN ('risk_limits', 'limits', 'capital_usd',
                     'max_downside_usd', 'max_incremental_capital_usd',
                     'per_order_usd', 'max_order_usd', 'daily_loss_stop_usd',
                     'credentials', 'api_key', 'secret', 'token',
                     'account_authority', 'capital_authority',
                     'submission_enabled', 'approved', 'approved_by',
                     'approval', 'activate', 'activation', 'active',
                     'policy_activation', 'promote', 'promotion', 'order',
                     'submit', 'cancel', 'allowlist', 'merge', 'push',
                     'deploy', 'force_push', 'auto_merge', 'live_sizing',
                     'production_threshold'))
$$;

CREATE OR REPLACE FUNCTION improve_stage_seq(stage text)
RETURNS integer LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE stage
        WHEN 'EVIDENCE' THEN 1 WHEN 'HYPOTHESIS' THEN 2
        WHEN 'PEER_CHALLENGE' THEN 3 WHEN 'OWNER_RESPONSE' THEN 4
        WHEN 'EXPERIMENT' THEN 5 WHEN 'INDEPENDENT_EVALUATION' THEN 6
        WHEN 'ELIGIBLE_CHANGE' THEN 7 WHEN 'CONTROLLED_RELEASE' THEN 8
        WHEN 'FORWARD_RESULT' THEN 9 WHEN 'ROLLED_BACK' THEN 98
        WHEN 'CLOSED' THEN 99 ELSE NULL END
$$;

-- THE EVIDENCE A ROW MAY CITE: kind -> (table, key column). Fixed here,
-- never supplied by a caller. A kind whose table is absent on this database
-- resolves to false (the citation is refused, never assumed).
CREATE OR REPLACE FUNCTION improve_ref_target(kind text,
                                              OUT tbl text, OUT col text)
LANGUAGE sql IMMUTABLE AS $$
    SELECT t.tbl, t.col FROM (VALUES
        ('karen_challenges', 'karen_challenges', 'challenge_id'),
        ('karen_challenge_events', 'karen_challenge_events', 'event_id'),
        ('paper_audrey_findings', 'paper_audrey_findings', 'finding_id'),
        ('audrey_audit_reports', 'audrey_audit_reports', 'report_id'),
        ('coverage_collapse_alerts', 'coverage_collapse_alerts', 'alert_id'),
        ('eddie_execution_estimates', 'eddie_execution_estimates',
         'estimate_id'),
        ('lol_ledger', 'lol_ledger', 'ledger_id'),
        ('scout_feature_tournaments', 'scout_feature_tournaments',
         'tournament_id'),
        ('scout_features', 'scout_features', 'feature_id'),
        ('poslearn_registrations', 'poslearn_registrations',
         'registration_id'),
        ('poslearn_promotion_steps', 'poslearn_promotion_steps', 'step_id'),
        ('poslearn_experiments', 'poslearn_experiments', 'experiment_id'),
        ('agent_findings', 'agent_findings', 'finding_id'),
        ('agent_finding_stages', 'agent_finding_stages', 'stage_id'),
        ('improvement_deficits', 'improvement_deficits', 'deficit_id'),
        ('improvement_candidates', 'improvement_candidates', 'candidate_id'),
        ('paper_improvement_proposals', 'paper_improvement_proposals',
         'proposal_id'),
        ('agent_tasks', 'agent_tasks', 'task_id'),
        ('paper_decisions', 'paper_decisions', 'decision_id'),
        ('derek_entry_decisions', 'derek_entry_decisions', 'decision_id'),
        ('execution_intents', 'execution_intents', 'intent_id'),
        ('agent_decisions', 'agent_decisions', 'decision_ref'),
        ('agent_runs', 'agent_runs', 'run_id'),
        ('paper_agent_lessons', 'paper_agent_lessons', 'lesson_id'),
        ('paper_xavier_reviews', 'paper_xavier_reviews', 'review_id'),
        ('bettor_xavier_reviews', 'bettor_xavier_reviews', 'review_id'),
        ('smalllive_reviews', 'smalllive_reviews', 'review_id'),
        ('paper_orders', 'paper_orders', 'order_id'),
        ('paper_settlements', 'paper_settlements', 'settlement_id'),
        ('paper_recommendations', 'paper_recommendations',
         'recommendation_id'),
        ('external_valuations', 'external_valuations', 'id'),
        ('smalllive_reconciliations', 'smalllive_reconciliations', 'group_id'),
        ('kalshi_account_reconciliations', 'kalshi_account_reconciliations',
         'reconciliation_id'),
        ('bettor_account_reconciliation_reports',
         'bettor_account_reconciliation_reports', 'report_id'),
        ('improve_items', 'improve_items', 'item_id'),
        ('improve_events', 'improve_events', 'event_id')
    ) AS t(kind, tbl, col) WHERE t.kind = improve_ref_target.kind
$$;

CREATE OR REPLACE FUNCTION improve_ref_exists(ref jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE
    t   text;
    c   text;
    hit boolean;
BEGIN
    IF NOT improve_ref_ok(ref) THEN
        RETURN false;
    END IF;
    SELECT tbl, col INTO t, c FROM improve_ref_target(ref->>'kind');
    IF t IS NULL OR to_regclass(t) IS NULL THEN
        RETURN false;
    END IF;
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE %I::text = $1)',
                   t, c) INTO hit USING btrim(ref->>'id');
    RETURN coalesce(hit, false);
END $$;

CREATE OR REPLACE FUNCTION improve_refs_exist(refs jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE
    e jsonb;
BEGIN
    IF refs IS NULL OR jsonb_typeof(refs) <> 'array' THEN
        RETURN false;
    END IF;
    FOR e IN SELECT * FROM jsonb_array_elements(refs) LOOP
        IF NOT improve_ref_exists(e) THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
END $$;

CREATE OR REPLACE FUNCTION improve_runner_session()
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('bettor.improvement_runner', true), '')
           = 'on'
$$;

CREATE OR REPLACE FUNCTION improve_parties_distinct(parties text[])
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT parties IS NOT NULL AND cardinality(parties) >= 2
       AND cardinality(parties) = (SELECT count(DISTINCT upper(btrim(p)))
                                     FROM unnest(parties) p
                                    WHERE coalesce(btrim(p), '') <> '')
$$;

CREATE OR REPLACE FUNCTION improve_positions_ok(positions jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT positions IS NOT NULL AND jsonb_typeof(positions) = 'array'
       AND jsonb_array_length(positions) >= 2
       AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(positions) p
             WHERE jsonb_typeof(p) <> 'object'
                OR coalesce(btrim(p->>'party'), '') = ''
                OR coalesce(btrim(p->>'position'), '') = ''
                OR NOT improve_ref_ok(p->'source_ref'))
$$;

-- ── 1 · THE ITEMS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS improve_items (
    item_id                       text        PRIMARY KEY,
    source_kind                   text        NOT NULL,
    source_key                    text        NOT NULL,
    source_ref                    jsonb       NOT NULL,
    title                         text        NOT NULL,
    problem_statement             text        NOT NULL,
    statement_basis               text        NOT NULL,
    owner_agent                   text        NOT NULL,
    evidence_refs                 jsonb       NOT NULL,
    protected_areas               text[]      NOT NULL DEFAULT '{}',
    protected_basis               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    requires_human_review         boolean     NOT NULL,
    required_independent_reviews  integer     NOT NULL,
    stage                         text,
    stage_seq                     integer     NOT NULL DEFAULT 0,
    created_by                    text        NOT NULL,
    created_at                    timestamptz NOT NULL,
    updated_at                    timestamptz NOT NULL,
    production_effect             text        NOT NULL DEFAULT 'NONE',
    label                         text        NOT NULL DEFAULT 'SHADOW',
    authority                     text        NOT NULL
                                  DEFAULT 'NO_DEPLOY_NO_MERGE_NO_CAPITAL',
    CONSTRAINT improve_items_id_ck CHECK (
        item_id ~ '^impr:[0-9a-f]{24}$'),
    CONSTRAINT improve_items_source_ck CHECK (source_kind IN (
        'KAREN_UPHELD_CHALLENGE', 'AUDREY_FINDING', 'COVERAGE_INCIDENT',
        'EDDIE_SKIP_EXECUTION', 'FALSE_REFUSAL', 'TOURNAMENT_VERDICT',
        'AGENT_FINDING', 'HUMAN_REPORTED')),
    CONSTRAINT improve_items_once UNIQUE (source_kind, source_key),
    CONSTRAINT improve_items_source_ref_ck CHECK (
        improve_ref_ok(source_ref)
        AND length(btrim(source_key)) BETWEEN 1 AND 300),
    CONSTRAINT improve_items_text_ck CHECK (
        length(btrim(title)) BETWEEN 1 AND 300
        AND length(btrim(problem_statement)) BETWEEN 1 AND 4000),
    CONSTRAINT improve_items_basis_ck CHECK (statement_basis IN (
        'RUNNER_SUMMARY_OF_CITED_RECORDS', 'AGENT_RECORD', 'HUMAN')),
    CONSTRAINT improve_items_owner_ck CHECK (owner_agent IN (
        'DEREK', 'XAVIER', 'AUDREY', 'EDDIE', 'SCOUT', 'CHIEF_ALLOCATOR')),
    CONSTRAINT improve_items_grounded_ck CHECK (
        improve_refs_grounded(evidence_refs)),
    CONSTRAINT improve_items_areas_ck CHECK (
        protected_areas <@ ARRAY['RISK', 'ACCOUNTING', 'SETTLEMENT',
                                 'EXECUTION_AUTHORIZATION',
                                 'FINANCIAL_CONTROLS', 'CREDENTIALS',
                                 'LIVE_CAPITAL_LIMITS']::text[]),
    -- A PROTECTED AREA NEEDS A HUMAN AND TWO INDEPENDENT REVIEWS
    CONSTRAINT improve_items_protected_ck CHECK (
        required_independent_reviews BETWEEN 1 AND 5
        AND (cardinality(protected_areas) = 0
             OR (requires_human_review AND required_independent_reviews >= 2))),
    CONSTRAINT improve_items_stage_ck CHECK (
        (stage IS NULL) = (stage_seq = 0)
        AND (stage IS NULL OR improve_stage_seq(stage) = stage_seq)),
    CONSTRAINT improve_items_effect_ck CHECK (production_effect = 'NONE'),
    CONSTRAINT improve_items_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT improve_items_authority_ck CHECK (
        authority = 'NO_DEPLOY_NO_MERGE_NO_CAPITAL'),
    CONSTRAINT improve_items_no_authority_ck CHECK (
        improve_no_authority(protected_basis)),
    CONSTRAINT improve_items_human_source_ck CHECK (
        source_kind <> 'HUMAN_REPORTED' OR NOT improve_is_machine_actor(
            created_by)),
    CONSTRAINT improve_items_window_ck CHECK (created_at <= updated_at)
);
CREATE INDEX IF NOT EXISTS improve_items_stage_idx
    ON improve_items (stage_seq, updated_at DESC);

-- ── 2 · THE STAGE EVENTS (append-only) ──────────────────────────────
CREATE TABLE IF NOT EXISTS improve_events (
    event_id            bigserial   PRIMARY KEY,
    item_id             text        NOT NULL REFERENCES improve_items,
    stage               text        NOT NULL,
    stage_seq           integer     NOT NULL,
    is_transition       boolean     NOT NULL DEFAULT false,
    from_stage          text,
    actor               text        NOT NULL,
    actor_class         text        NOT NULL,
    at                  timestamptz NOT NULL,
    body                jsonb       NOT NULL DEFAULT '{}'::jsonb,
    source_ref          jsonb,
    evidence_refs       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    stance              text,
    outcome             text,
    experiment_refs     jsonb,
    patch_branch        text,
    patch_commit_sha    text,
    patch_pr_url        text,
    tests_ref           text,
    gate_receipt_ref    text,
    gate_receipt_sha    text,
    release_ref         text,
    monitoring_start    timestamptz,
    monitoring_end      timestamptz,
    forward_result      jsonb,
    rollback_ref        text,
    recorded_by         text        NOT NULL,
    production_effect   text        NOT NULL DEFAULT 'NONE',
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT improve_events_stage_ck CHECK (
        improve_stage_seq(stage) IS NOT NULL
        AND improve_stage_seq(stage) = stage_seq),
    CONSTRAINT improve_events_class_ck CHECK (actor_class IN (
        'RUNNER', 'OWNER_AGENT', 'PEER_AGENT', 'CHALLENGER',
        'INDEPENDENT_EVALUATOR', 'HUMAN', 'ENGINEERING')),
    CONSTRAINT improve_events_actor_ck CHECK (
        length(btrim(actor)) BETWEEN 2 AND 100
        AND length(btrim(recorded_by)) BETWEEN 2 AND 100),
    CONSTRAINT improve_events_effect_ck CHECK (production_effect = 'NONE'),
    CONSTRAINT improve_events_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT improve_events_body_ck CHECK (
        jsonb_typeof(body) = 'object' AND jsonb_typeof(evidence_refs) = 'array'
        AND improve_no_authority(body)
        AND improve_no_authority(forward_result)
        AND (forward_result IS NULL OR jsonb_typeof(forward_result) = 'object')),
    -- AN AGENT-CLASS ROW CITES THE RECORD IT WAS TAKEN FROM (no invented
    -- text); a HUMAN / ENGINEERING row names a person who recorded it.
    CONSTRAINT improve_events_provenance_ck CHECK (
        (actor_class IN ('HUMAN', 'ENGINEERING')
         AND NOT improve_is_machine_actor(actor)
         AND upper(btrim(recorded_by)) = upper(btrim(actor)))
        OR (actor_class NOT IN ('HUMAN', 'ENGINEERING')
            AND improve_ref_ok(source_ref))),
    -- THE CANDIDATE PATCH IS A REFERENCE, NEVER AN INSTRUCTION
    CONSTRAINT improve_events_patch_ck CHECK (
        (patch_branch IS NULL
         OR patch_branch ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$')
        AND (patch_commit_sha IS NULL OR patch_commit_sha ~ '^[0-9a-f]{40}$')
        AND (patch_pr_url IS NULL OR patch_pr_url ~
             '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]{1,7}$')
        AND (gate_receipt_sha IS NULL OR gate_receipt_sha ~ '^[0-9a-f]{40}$')
        AND (tests_ref IS NULL OR length(btrim(tests_ref)) BETWEEN 1 AND 500)
        AND (gate_receipt_ref IS NULL
             OR length(btrim(gate_receipt_ref)) BETWEEN 1 AND 500)
        AND (release_ref IS NULL OR length(btrim(release_ref)) BETWEEN 1 AND 500)
        AND (rollback_ref IS NULL
             OR length(btrim(rollback_ref)) BETWEEN 1 AND 500)),
    -- EACH REFERENCE BELONGS TO ITS STAGE
    CONSTRAINT improve_events_fields_by_stage_ck CHECK (
        (stage = 'EXPERIMENT' OR (patch_branch IS NULL
            AND patch_commit_sha IS NULL AND patch_pr_url IS NULL
            AND tests_ref IS NULL AND experiment_refs IS NULL))
        AND (stage = 'CONTROLLED_RELEASE' OR (gate_receipt_ref IS NULL
            AND gate_receipt_sha IS NULL AND release_ref IS NULL))
        AND (stage = 'FORWARD_RESULT' OR (monitoring_start IS NULL
            AND monitoring_end IS NULL AND forward_result IS NULL))
        AND (stage = 'ROLLED_BACK' OR rollback_ref IS NULL)),
    CONSTRAINT improve_events_stance_ck CHECK (
        (stage = 'PEER_CHALLENGE'
         AND stance IN ('SUSTAINED', 'REFUTED', 'CHALLENGES'))
        OR (stage = 'OWNER_RESPONSE'
            AND stance IN ('CONCEDE', 'DISPUTE', 'REVISE'))
        OR (stage NOT IN ('PEER_CHALLENGE', 'OWNER_RESPONSE')
            AND stance IS NULL)),
    CONSTRAINT improve_events_outcome_ck CHECK (
        (stage = 'INDEPENDENT_EVALUATION'
         AND outcome IN ('PASS', 'FAIL', 'INCONCLUSIVE'))
        OR (stage = 'FORWARD_RESULT'
            AND outcome IN ('HELD', 'DEGRADED', 'INCONCLUSIVE'))
        OR (stage NOT IN ('INDEPENDENT_EVALUATION', 'FORWARD_RESULT')
            AND outcome IS NULL)),
    CONSTRAINT improve_events_text_ck CHECK (
        (stage <> 'HYPOTHESIS'
         OR coalesce(length(btrim(body->>'hypothesis')) BETWEEN 1 AND 4000,
                     false))
        AND (stage <> 'PEER_CHALLENGE'
             OR coalesce(length(btrim(body->>'challenge')) BETWEEN 1 AND 4000,
                         false))
        AND (stage <> 'OWNER_RESPONSE'
             OR coalesce(length(btrim(body->>'response')) BETWEEN 1 AND 4000,
                         false))
        AND (stage NOT IN ('CLOSED', 'ROLLED_BACK')
             OR coalesce(length(btrim(body->>'reason')) BETWEEN 1 AND 2000,
                         false))),
    CONSTRAINT improve_events_experiment_ck CHECK (
        stage <> 'EXPERIMENT'
        OR improve_refs_grounded(experiment_refs)
        OR tests_ref IS NOT NULL
        OR patch_commit_sha IS NOT NULL),
    CONSTRAINT improve_events_release_ck CHECK (
        stage <> 'CONTROLLED_RELEASE'
        OR (gate_receipt_ref IS NOT NULL AND gate_receipt_sha IS NOT NULL
            AND release_ref IS NOT NULL)),
    CONSTRAINT improve_events_forward_ck CHECK (
        stage <> 'FORWARD_RESULT'
        OR coalesce(monitoring_start < monitoring_end
                    AND monitoring_end <= at
                    AND forward_result IS NOT NULL, false)),
    CONSTRAINT improve_events_rollback_ck CHECK (
        stage <> 'ROLLED_BACK' OR rollback_ref IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS improve_events_item_idx
    ON improve_events (item_id, event_id);
CREATE INDEX IF NOT EXISTS improve_events_transition_idx
    ON improve_events (at DESC) WHERE is_transition;
CREATE UNIQUE INDEX IF NOT EXISTS improve_events_source_once_idx
    ON improve_events (item_id, stage, (source_ref->>'kind'),
                           (source_ref->>'id'))
    WHERE source_ref IS NOT NULL;

-- ── THE STAGE GUARD: ORDER, ACTOR CLASS, INDEPENDENCE, PROVENANCE ────
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

DROP TRIGGER IF EXISTS improve_events_guard_trg ON improve_events;
CREATE TRIGGER improve_events_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON improve_events
    FOR EACH ROW EXECUTE FUNCTION improve_events_guard();

-- the item's stage follows its events, and nothing else
CREATE OR REPLACE FUNCTION improve_events_advance() RETURNS trigger
AS $$
BEGIN
    IF NEW.is_transition THEN
        UPDATE improve_items SET stage = NEW.stage,
               stage_seq = NEW.stage_seq,
               updated_at = greatest(updated_at, NEW.at)
         WHERE item_id = NEW.item_id;
    ELSE
        UPDATE improve_items SET updated_at = greatest(updated_at, NEW.at)
         WHERE item_id = NEW.item_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS improve_events_advance_trg ON improve_events;
CREATE TRIGGER improve_events_advance_trg
    AFTER INSERT ON improve_events
    FOR EACH ROW EXECUTE FUNCTION improve_events_advance();

CREATE OR REPLACE FUNCTION improve_items_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'improve_items: an item is never deleted (%)',
            OLD.item_id USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.stage IS NOT NULL OR NEW.stage_seq <> 0 THEN
            RAISE EXCEPTION 'improve_items: an item starts with no stage; '
                            'stages are improve_events'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NOT improve_ref_exists(NEW.source_ref) THEN
            RAISE EXCEPTION 'improve_items: the source record % % does '
                            'not exist', NEW.source_ref->>'kind',
                            NEW.source_ref->>'id'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NOT improve_refs_exist(NEW.evidence_refs) THEN
            RAISE EXCEPTION 'improve_items: a cited evidence record does '
                            'not exist' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.item_id IS DISTINCT FROM OLD.item_id
       OR NEW.source_kind IS DISTINCT FROM OLD.source_kind
       OR NEW.source_key IS DISTINCT FROM OLD.source_key
       OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.problem_statement IS DISTINCT FROM OLD.problem_statement
       OR NEW.statement_basis IS DISTINCT FROM OLD.statement_basis
       OR NEW.owner_agent IS DISTINCT FROM OLD.owner_agent
       OR NEW.evidence_refs IS DISTINCT FROM OLD.evidence_refs
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'improve_items: % is fixed at creation',
            OLD.item_id USING ERRCODE = 'check_violation';
    END IF;
    -- A PROTECTED CLASSIFICATION IS ESCALATED, NEVER RELAXED
    IF NOT (OLD.protected_areas <@ NEW.protected_areas)
       OR (OLD.requires_human_review AND NOT NEW.requires_human_review)
       OR NEW.required_independent_reviews < OLD.required_independent_reviews
    THEN
        RAISE EXCEPTION 'improve_items: the protected classification of '
                        '% can only be escalated', OLD.item_id
            USING ERRCODE = 'check_violation';
    END IF;
    -- THE STAGE IS ALWAYS THE LATEST TRANSITION EVENT (set by the advance
    -- trigger); it can neither jump ahead nor move back
    IF (NEW.stage, NEW.stage_seq) IS DISTINCT FROM (OLD.stage, OLD.stage_seq)
       AND NOT EXISTS (
           SELECT 1 FROM improve_events e
            WHERE e.item_id = NEW.item_id AND e.is_transition
              AND e.stage = NEW.stage AND e.stage_seq = NEW.stage_seq
              AND e.event_id = (SELECT max(event_id) FROM improve_events m
                                 WHERE m.item_id = NEW.item_id
                                   AND m.is_transition)) THEN
        RAISE EXCEPTION 'improve_items: stage % of % has no transition '
                        'event', NEW.stage, NEW.item_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS improve_items_guard_trg ON improve_items;
CREATE TRIGGER improve_items_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON improve_items
    FOR EACH ROW EXECUTE FUNCTION improve_items_guard();

-- ── 3 · DISAGREEMENTS, PRESERVED ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS improve_disagreements (
    disagreement_id     text        PRIMARY KEY,
    item_id             text        NOT NULL REFERENCES improve_items,
    stage               text        NOT NULL,
    parties             text[]      NOT NULL,
    positions           jsonb       NOT NULL,
    raised_event_id     bigint      REFERENCES improve_events,
    opened_at           timestamptz NOT NULL,
    state               text        NOT NULL DEFAULT 'OPEN',
    resolved_by         text,
    resolution          text,
    resolution_ref      jsonb,
    resolved_at         timestamptz,
    consensus           boolean     NOT NULL DEFAULT false,
    recorded_by         text        NOT NULL,
    production_effect   text        NOT NULL DEFAULT 'NONE',
    label               text        NOT NULL DEFAULT 'SHADOW',
    CONSTRAINT improve_disagreements_stage_ck CHECK (stage IN (
        'ORIGIN', 'PEER_CHALLENGE', 'OWNER_RESPONSE',
        'INDEPENDENT_EVALUATION', 'FORWARD_RESULT')),
    CONSTRAINT improve_disagreements_parties_ck CHECK (
        improve_parties_distinct(parties)),
    -- EVERY POSITION IS VERBATIM AND CITES ITS RECORD
    CONSTRAINT improve_disagreements_positions_ck CHECK (
        improve_positions_ok(positions)),
    CONSTRAINT improve_disagreements_state_ck CHECK (state IN (
        'OPEN', 'DECIDED', 'CONCEDED', 'WITHDRAWN')),
    CONSTRAINT improve_disagreements_resolution_ck CHECK (
        (state = 'OPEN' AND resolved_by IS NULL AND resolution IS NULL
         AND resolved_at IS NULL AND resolution_ref IS NULL)
        OR (state <> 'OPEN' AND resolved_by IS NOT NULL
            AND length(btrim(resolution)) BETWEEN 1 AND 4000
            AND resolved_at IS NOT NULL AND resolved_at >= opened_at)),
    -- A DECISION BY A THIRD PARTY IS NOT CONSENSUS
    CONSTRAINT improve_disagreements_consensus_ck CHECK (
        NOT consensus OR state = 'CONCEDED'),
    CONSTRAINT improve_disagreements_effect_ck CHECK (
        production_effect = 'NONE' AND label = 'SHADOW')
);
CREATE INDEX IF NOT EXISTS improve_disagreements_item_idx
    ON improve_disagreements (item_id, opened_at);

CREATE OR REPLACE FUNCTION improve_disagreements_guard() RETURNS trigger
AS $$
DECLARE
    p jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'improve_disagreements: a disagreement is never '
                        'deleted' USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'OPEN' AND NEW.resolution_ref IS NULL
           AND improve_is_machine_actor(NEW.resolved_by) THEN
            RAISE EXCEPTION 'improve_disagreements: an agent resolution '
                            'cites its record' USING ERRCODE = 'check_violation';
        END IF;
        FOR p IN SELECT * FROM jsonb_array_elements(NEW.positions) LOOP
            IF NOT improve_ref_exists(p->'source_ref') THEN
                RAISE EXCEPTION 'improve_disagreements: the position of % '
                                'cites no existing record', p->>'party'
                    USING ERRCODE = 'check_violation';
            END IF;
        END LOOP;
    ELSE
        -- the positions are preserved forever; a resolution is recorded once
        IF NEW.disagreement_id IS DISTINCT FROM OLD.disagreement_id
           OR NEW.item_id IS DISTINCT FROM OLD.item_id
           OR NEW.stage IS DISTINCT FROM OLD.stage
           OR NEW.parties IS DISTINCT FROM OLD.parties
           OR NEW.positions IS DISTINCT FROM OLD.positions
           OR NEW.raised_event_id IS DISTINCT FROM OLD.raised_event_id
           OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
           OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by THEN
            RAISE EXCEPTION 'improve_disagreements: the positions of % are '
                            'preserved', OLD.disagreement_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF OLD.state <> 'OPEN' THEN
            RAISE EXCEPTION 'improve_disagreements: % is already %',
                OLD.disagreement_id, OLD.state
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.resolution_ref IS NOT NULL
           AND NOT improve_ref_exists(NEW.resolution_ref) THEN
            RAISE EXCEPTION 'improve_disagreements: the resolution record '
                            'does not exist' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.state <> 'OPEN' AND NEW.resolution_ref IS NULL
           AND improve_is_machine_actor(NEW.resolved_by) THEN
            RAISE EXCEPTION 'improve_disagreements: an agent resolution '
                            'cites its record' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.state = 'DECIDED' AND upper(btrim(NEW.resolved_by)) IN (
           SELECT upper(btrim(x)) FROM unnest(NEW.parties) x) THEN
        RAISE EXCEPTION 'improve_disagreements: a party (%) cannot decide '
                        'its own disagreement; it may concede', NEW.resolved_by
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.state = 'CONCEDED' AND upper(btrim(NEW.resolved_by)) NOT IN (
           SELECT upper(btrim(x)) FROM unnest(NEW.parties) x) THEN
        RAISE EXCEPTION 'improve_disagreements: only a party concedes'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS improve_disagreements_guard_trg
    ON improve_disagreements;
CREATE TRIGGER improve_disagreements_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON improve_disagreements
    FOR EACH ROW EXECUTE FUNCTION improve_disagreements_guard();

-- ── 4 · THE RUNNER'S PASSES (append-only heartbeat record) ──────────
CREATE TABLE IF NOT EXISTS improve_runs (
    run_id              text        PRIMARY KEY,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz NOT NULL,
    status              text        NOT NULL,
    summary             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    version             text        NOT NULL,
    production_effect   text        NOT NULL DEFAULT 'NONE',
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT improve_runs_status_ck CHECK (status IN (
        'OK', 'PARTIAL', 'FAILED', 'DISABLED', 'NO_SCHEMA')),
    CONSTRAINT improve_runs_window_ck CHECK (started_at <= finished_at),
    CONSTRAINT improve_runs_effect_ck CHECK (
        production_effect = 'NONE' AND label = 'SHADOW'),
    CONSTRAINT improve_runs_no_authority_ck CHECK (
        improve_no_authority(summary))
);
CREATE INDEX IF NOT EXISTS improve_runs_at_idx
    ON improve_runs (started_at DESC);

CREATE OR REPLACE FUNCTION improve_runs_append_only() RETURNS trigger
AS $$
BEGIN
    RAISE EXCEPTION 'improve_runs: append-only, % refused', TG_OP
        USING ERRCODE = 'check_violation';
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS improve_runs_append_only_trg ON improve_runs;
CREATE TRIGGER improve_runs_append_only_trg
    BEFORE UPDATE OR DELETE ON improve_runs
    FOR EACH ROW EXECUTE FUNCTION improve_runs_append_only();

-- TRUNCATE would bypass the row triggers: refused on every ledger table.
CREATE OR REPLACE FUNCTION improve_no_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%: TRUNCATE refused (append-only ledger)', TG_TABLE_NAME
        USING ERRCODE = 'check_violation';
END $$ LANGUAGE plpgsql;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['improve_items', 'improve_events',
                             'improve_disagreements', 'improve_runs']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION '
                       'improve_no_truncate()', t || '_no_truncate_trg', t);
    END LOOP;
END $$;
