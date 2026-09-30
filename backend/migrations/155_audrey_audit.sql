-- ══════════════════════════════════════════════════════════════════════
-- 155 · AUDREY: THE DAILY AUDIT OF DEREK AND XAVIER, AND THE GOVERNED
--       SELF-IMPROVEMENT RECORD (candidates, trials, holdouts, releases)
-- ══════════════════════════════════════════════════════════════════════
--
-- Audrey READS the authoritative records (bettor_funded_intents / fills /
-- economics, bettor_xavier_decisions and their execution events,
-- external_valuations, ext_candidate_outcomes, settlement corrections, the
-- model registry) and writes only what is below. Nothing here is a second
-- book: every figure in a report is recomputed from the book and carries
-- the ids it came from.
--
-- WHAT IS HERE.
--  * audrey_audit_reports     one row per (report_id, version). The id is
--    deterministic per (timezone, local day); a rerun on the same evidence
--    writes nothing, a rerun on NEW evidence (a late settlement, a
--    correction, an outcome that became known) writes version + 1 naming
--    the version it supersedes. APPEND-ONLY by trigger.
--  * audrey_audit_watermarks  the last completed local day audited, per
--    scope, so the 15-minute hook does bounded work and a restart resumes.
--  * audrey_collection_samples  the pair collector's per-pass throughput
--    (candidates offered / attempted / left for LIMIT_PER_PASS / skipped as
--    recently refused), sampled from the cycle heartbeat, which keeps only
--    the LAST pass. Append-only evidence for the pass-limit replay.
--  * improvement_holdouts     a named fixture-level holdout and its TRIAL
--    BUDGET. The database refuses a trial past the budget, so variants are
--    not searched against one holdout until one of them wins.
--  * improvement_candidates   one proposed change. The DATABASE enforces the
--    separation of duties: the evaluator is not the proposer, the approver
--    is neither, approval requires an evaluation whose verdict is PASS, a
--    protected key can never be named, and an evaluation is write-once.
--  * improvement_trials       EVERY trial, the rejected ones included.
--  * improvement_releases     approval / canary / activation / rollback of a
--    released policy version (agent_policy_versions, migration 152).
--  * improvement_events       what happened to a candidate, append-only.
--
-- NO FOREIGN KEY TO agent_tasks (152): the task id is a soft reference so
-- this migration applies whether or not the core stream's tables exist.
--
-- ADDITIVE.

BEGIN;

CREATE OR REPLACE FUNCTION audrey_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; a new version or event is written '
                    'instead of rewriting a record', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

-- ── THE DAILY REPORTS ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audrey_audit_reports (
    report_id          text        NOT NULL,
    version            integer     NOT NULL,
    audit_day          date        NOT NULL,
    timezone           text        NOT NULL,
    day_boundary       text        NOT NULL,
    day_start          timestamptz NOT NULL,
    day_end            timestamptz NOT NULL,
    computed_at        timestamptz NOT NULL,
    evidence_sha       text        NOT NULL,
    supersedes_version integer,
    change_reason      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    report             jsonb       NOT NULL,
    summary            text        NOT NULL,
    audit_version      text        NOT NULL,
    code_version       text,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (report_id, version),
    CONSTRAINT audrey_report_version_ck CHECK (version >= 1),
    CONSTRAINT audrey_report_supersedes_ck CHECK (
        (version = 1 AND supersedes_version IS NULL)
        OR (version > 1 AND supersedes_version = version - 1)),
    CONSTRAINT audrey_report_window_ck CHECK (day_end > day_start),
    CONSTRAINT audrey_report_sha_ck CHECK (evidence_sha ~ '^[0-9a-f]{64}$'),
    -- A DAILY BOUNDARY SETTLES NOTHING: the report is the day's evidence as
    -- known when computed, and the id says which clock drew the day.
    CONSTRAINT audrey_report_id_ck CHECK (
        report_id = 'audrey:' || timezone || ':' || audit_day::text)
);
CREATE INDEX IF NOT EXISTS audrey_audit_reports_day_idx
    ON audrey_audit_reports (audit_day DESC, version DESC);

DROP TRIGGER IF EXISTS audrey_audit_reports_append_only_trg
    ON audrey_audit_reports;
CREATE TRIGGER audrey_audit_reports_append_only_trg
    BEFORE UPDATE OR DELETE ON audrey_audit_reports
    FOR EACH ROW EXECUTE FUNCTION audrey_is_append_only();

CREATE TABLE IF NOT EXISTS audrey_audit_watermarks (
    scope              text        PRIMARY KEY,
    timezone           text        NOT NULL,
    day_boundary       text        NOT NULL,
    last_completed_day date,
    last_report_id     text,
    last_version       integer,
    last_run_at        timestamptz,
    last_reaudit_at    timestamptz,
    detail             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- ── THE COLLECTOR'S THROUGHPUT, PER PASS ─────────────────────────────
CREATE TABLE IF NOT EXISTS audrey_collection_samples (
    sample_id            text        PRIMARY KEY,
    source               text        NOT NULL,
    pass_id              text,
    pass_at              timestamptz NOT NULL,
    sampled_at           timestamptz NOT NULL,
    configured_per_pass  integer,
    offered              integer,
    attempted            integer,
    not_attempted        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    limit_per_pass_left  integer,
    skipped_recently_refused integer,
    stopped_for_deadline boolean,
    elapsed_s            numeric,
    budget_s             numeric,
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT audrey_sample_counts_ck CHECK (
        (offered IS NULL OR offered >= 0)
        AND (attempted IS NULL OR attempted >= 0)
        AND (limit_per_pass_left IS NULL OR limit_per_pass_left >= 0))
);
CREATE INDEX IF NOT EXISTS audrey_collection_samples_at_idx
    ON audrey_collection_samples (pass_at DESC);

DROP TRIGGER IF EXISTS audrey_collection_samples_append_only_trg
    ON audrey_collection_samples;
CREATE TRIGGER audrey_collection_samples_append_only_trg
    BEFORE UPDATE OR DELETE ON audrey_collection_samples
    FOR EACH ROW EXECUTE FUNCTION audrey_is_append_only();

-- ── HOLDOUTS AND THEIR BUDGET ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS improvement_holdouts (
    holdout_id    text        PRIMARY KEY,
    description   text        NOT NULL,
    -- how fixtures are assigned (hash rule, percentage, boundaries)
    fixture_rule  jsonb       NOT NULL,
    trial_budget  integer     NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT improvement_holdout_budget_ck CHECK (trial_budget >= 1)
);

-- ── CANDIDATES ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS improvement_candidates (
    candidate_id        text        PRIMARY KEY,
    task_id             text        NOT NULL,
    assigned_agent      text        NOT NULL,
    change_class        text        NOT NULL,
    change_kind         text        NOT NULL,
    hypothesis          text        NOT NULL,
    evidence            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    affected_behavior   text        NOT NULL,
    training_boundary   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    evaluation_boundary jsonb       NOT NULL DEFAULT '{}'::jsonb,
    success_metrics     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    harm_metrics        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    policy_key          text,
    params              jsonb,
    touched_keys        text[]      NOT NULL DEFAULT '{}'::text[],
    diff                text,
    base_commit         text,
    artifact_ref        text,
    test_results        jsonb,
    evaluation          jsonb,
    release_scope       text        NOT NULL,
    rollback_procedure  text        NOT NULL,
    state               text        NOT NULL DEFAULT 'PROPOSED',
    proposed_by         text        NOT NULL,
    evaluated_by        text,
    evaluated_at        timestamptz,
    approved_by         text,
    approved_at         timestamptz,
    approval_statement  text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT improvement_candidate_kind_ck CHECK (change_kind IN (
        'POLICY_PARAMETER', 'CODE', 'MODEL', 'REPORT_THRESHOLD')),
    CONSTRAINT improvement_candidate_state_ck CHECK (state IN (
        'PROPOSED', 'EVALUATING', 'REJECTED', 'APPROVAL_READY', 'APPROVED',
        'CANARY', 'RELEASED', 'ROLLED_BACK', 'WITHDRAWN')),
    CONSTRAINT improvement_candidate_scope_ck CHECK (release_scope IN (
        'PRE_AUTHORIZED_UNATTENDED', 'REQUIRES_APPROVAL')),
    CONSTRAINT improvement_candidate_rollback_ck CHECK (
        length(btrim(rollback_procedure)) > 0),
    CONSTRAINT improvement_candidate_proposer_ck CHECK (
        length(btrim(proposed_by)) > 0),
    -- ── SEPARATION OF DUTIES ─────────────────────────────────────────
    CONSTRAINT improvement_evaluator_is_not_proposer_ck CHECK (
        evaluated_by IS NULL OR evaluated_by <> proposed_by),
    CONSTRAINT improvement_approver_is_neither_ck CHECK (
        approved_by IS NULL
        OR (approved_by <> proposed_by
            AND approved_by <> coalesce(evaluated_by, ''))),
    CONSTRAINT improvement_evaluated_has_evaluation_ck CHECK (
        (evaluated_by IS NULL) = (evaluation IS NULL)),
    -- APPROVAL ONLY AFTER AN EVALUATION THAT PASSED
    CONSTRAINT improvement_approval_needs_a_pass_ck CHECK (
        approved_by IS NULL
        OR (evaluated_by IS NOT NULL
            AND evaluation ->> 'verdict' = 'PASS'
            AND approved_at IS NOT NULL)),
    CONSTRAINT improvement_released_was_approved_ck CHECK (
        state NOT IN ('APPROVED', 'CANARY', 'RELEASED', 'ROLLED_BACK')
        OR approved_by IS NOT NULL),
    CONSTRAINT improvement_rejected_did_not_pass_ck CHECK (
        state <> 'REJECTED' OR coalesce(evaluation ->> 'verdict', '')
                               <> 'PASS'),
    -- AN UNATTENDED RELEASE IS ONLY EVER A PRE-AUTHORIZED CLASS'S RULE
    CONSTRAINT improvement_preauth_approver_ck CHECK (
        approved_by IS NULL OR approved_by NOT LIKE 'PRE_AUTHORIZED:%'
        OR (release_scope = 'PRE_AUTHORIZED_UNATTENDED'
            AND approved_by = 'PRE_AUTHORIZED:' || change_class)),
    -- ── PROTECTED KEYS: NO CANDIDATE MAY NAME ONE ────────────────────
    CONSTRAINT improvement_no_protected_key_ck CHECK (NOT (touched_keys && ARRAY[
        'risk_limits', 'max_position_usd', 'max_exposure_usd',
        'account_exposure_limit', 'daily_loss_limit', 'credentials',
        'api_key', 'admin_token', 'operator_password',
        'funded_resolution_key', 'account_authority', 'owner_authorization',
        'approval_controls', 'approved_by', 'FUNDED_SUBMISSION_ENABLED',
        'REAL_ORDER_SUBMISSION_ENABLED', 'FUNDED_EXIT_SUBMISSION_ENABLED',
        'evaluation_criteria', 'acceptance_criteria', 'harm_metrics',
        'success_metrics', 'holdout_budget', 'change_class_registry',
        'protected_keys', 'capital_critical_tests']::text[]))
);
CREATE INDEX IF NOT EXISTS improvement_candidates_task_idx
    ON improvement_candidates (task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS improvement_candidates_state_idx
    ON improvement_candidates (state, updated_at DESC);

CREATE OR REPLACE FUNCTION improvement_candidate_guard()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'improvement candidate % is a record; it is never '
                        'deleted', OLD.candidate_id;
    END IF;
    IF NEW.candidate_id <> OLD.candidate_id
       OR NEW.task_id <> OLD.task_id
       OR NEW.proposed_by <> OLD.proposed_by
       OR NEW.change_class <> OLD.change_class
       OR NEW.change_kind <> OLD.change_kind
       OR NEW.hypothesis <> OLD.hypothesis
       OR NEW.release_scope <> OLD.release_scope
       OR NEW.touched_keys <> OLD.touched_keys
       OR NEW.success_metrics <> OLD.success_metrics
       OR NEW.harm_metrics <> OLD.harm_metrics
       OR NEW.params IS DISTINCT FROM OLD.params THEN
        RAISE EXCEPTION 'improvement candidate %: what was proposed and the '
                        'criteria it is judged by are fixed', OLD.candidate_id;
    END IF;
    IF OLD.diff IS NOT NULL AND NEW.diff IS DISTINCT FROM OLD.diff THEN
        RAISE EXCEPTION 'improvement candidate %: the diff is fixed once '
                        'recorded', OLD.candidate_id;
    END IF;
    IF OLD.artifact_ref IS NOT NULL
       AND NEW.artifact_ref IS DISTINCT FROM OLD.artifact_ref THEN
        RAISE EXCEPTION 'improvement candidate %: the artifact is fixed once '
                        'recorded', OLD.candidate_id;
    END IF;
    -- AN EVALUATION IS WRITE-ONCE: a failed candidate is not re-scored
    -- until it passes.
    IF OLD.evaluation IS NOT NULL AND (
           NEW.evaluation IS DISTINCT FROM OLD.evaluation
           OR NEW.evaluated_by IS DISTINCT FROM OLD.evaluated_by) THEN
        RAISE EXCEPTION 'improvement candidate %: the evaluation is written '
                        'once', OLD.candidate_id;
    END IF;
    IF OLD.approved_by IS NOT NULL
       AND NEW.approved_by IS DISTINCT FROM OLD.approved_by THEN
        RAISE EXCEPTION 'improvement candidate %: the approval is written '
                        'once', OLD.candidate_id;
    END IF;
    IF OLD.state IN ('REJECTED', 'WITHDRAWN') AND NEW.state <> OLD.state THEN
        RAISE EXCEPTION 'improvement candidate % was %; it does not come '
                        'back', OLD.candidate_id, OLD.state;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS improvement_candidate_guard_trg
    ON improvement_candidates;
CREATE TRIGGER improvement_candidate_guard_trg
    BEFORE UPDATE OR DELETE ON improvement_candidates
    FOR EACH ROW EXECUTE FUNCTION improvement_candidate_guard();

-- ── TRIALS ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS improvement_trials (
    trial_id            text        PRIMARY KEY,
    candidate_id        text        NOT NULL
        REFERENCES improvement_candidates (candidate_id),
    task_id             text        NOT NULL,
    segment             text        NOT NULL,
    holdout_id          text        REFERENCES improvement_holdouts (holdout_id),
    variant             jsonb       NOT NULL,
    training_boundary   timestamptz,
    evaluation_boundary timestamptz,
    evidence_category   text        NOT NULL,
    metrics             jsonb       NOT NULL,
    verdict             text        NOT NULL,
    evaluated_by        text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT improvement_trial_segment_ck CHECK (segment IN (
        'TRAINING', 'HOLDOUT', 'SANDBOX_TESTS', 'CANARY')),
    CONSTRAINT improvement_trial_holdout_ck CHECK (
        (segment = 'HOLDOUT') = (holdout_id IS NOT NULL)),
    CONSTRAINT improvement_trial_category_ck CHECK (evidence_category IN (
        'ACTUAL_EXECUTED_PNL', 'KNOWN_SETTLEMENT_HYPOTHETICAL_EXECUTION',
        'SIMULATED_WITH_DISCLOSED_ASSUMPTIONS',
        'NOT_OBSERVED_OR_UNEVALUABLE')),
    CONSTRAINT improvement_trial_verdict_ck CHECK (verdict IN (
        'PASS', 'FAIL_HARM', 'FAIL_NO_IMPROVEMENT', 'INSUFFICIENT_EVIDENCE',
        'SELECTED', 'NOT_SELECTED')),
    CONSTRAINT improvement_trial_boundaries_ck CHECK (
        training_boundary IS NULL OR evaluation_boundary IS NULL
        OR evaluation_boundary > training_boundary)
);
CREATE INDEX IF NOT EXISTS improvement_trials_holdout_idx
    ON improvement_trials (holdout_id) WHERE holdout_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS improvement_trials_candidate_idx
    ON improvement_trials (candidate_id, created_at);

CREATE OR REPLACE FUNCTION improvement_trial_within_budget()
RETURNS trigger AS $$
DECLARE
    used   integer;
    budget integer;
BEGIN
    IF NEW.holdout_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT trial_budget INTO budget FROM improvement_holdouts
     WHERE holdout_id = NEW.holdout_id FOR UPDATE;
    SELECT count(*) INTO used FROM improvement_trials
     WHERE holdout_id = NEW.holdout_id;
    IF used >= budget THEN
        RAISE EXCEPTION 'HOLDOUT_BUDGET_EXHAUSTED: holdout % has been used % '
                        'time(s) of a budget of %; a new holdout is needed',
                        NEW.holdout_id, used, budget;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS improvement_trial_within_budget_trg
    ON improvement_trials;
CREATE TRIGGER improvement_trial_within_budget_trg
    BEFORE INSERT ON improvement_trials
    FOR EACH ROW EXECUTE FUNCTION improvement_trial_within_budget();

DROP TRIGGER IF EXISTS improvement_trials_append_only_trg
    ON improvement_trials;
CREATE TRIGGER improvement_trials_append_only_trg
    BEFORE UPDATE OR DELETE ON improvement_trials
    FOR EACH ROW EXECUTE FUNCTION audrey_is_append_only();

-- ── RELEASES ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS improvement_releases (
    release_id       text        PRIMARY KEY,
    candidate_id     text        NOT NULL
        REFERENCES improvement_candidates (candidate_id),
    agent_id         text        NOT NULL,
    policy_key       text        NOT NULL,
    from_version     text,
    to_version       text        NOT NULL,
    state            text        NOT NULL,
    acceptance       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    canary           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    released_by      text        NOT NULL,
    released_at      timestamptz NOT NULL,
    confirmed_at     timestamptz,
    rolled_back_at   timestamptz,
    rollback_reason  text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT improvement_release_state_ck CHECK (state IN (
        'CANARY', 'ACTIVE', 'ROLLED_BACK')),
    CONSTRAINT improvement_release_rollback_ck CHECK (
        (state = 'ROLLED_BACK') = (rolled_back_at IS NOT NULL
                                   AND rollback_reason IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS improvement_one_live_release
    ON improvement_releases (agent_id, policy_key)
    WHERE state IN ('CANARY', 'ACTIVE');

CREATE TABLE IF NOT EXISTS improvement_events (
    event_id     bigserial   PRIMARY KEY,
    candidate_id text,
    task_id      text,
    at           timestamptz NOT NULL,
    kind         text        NOT NULL,
    actor        text        NOT NULL,
    detail       jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS improvement_events_candidate_idx
    ON improvement_events (candidate_id, event_id);

DROP TRIGGER IF EXISTS improvement_events_append_only_trg
    ON improvement_events;
CREATE TRIGGER improvement_events_append_only_trg
    BEFORE UPDATE OR DELETE ON improvement_events
    FOR EACH ROW EXECUTE FUNCTION audrey_is_append_only();

COMMIT;
