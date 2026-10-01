-- ══════════════════════════════════════════════════════════════════════
-- 185 · PAPER ONLY: THE LEARNING RECORD -- DECISION PROVENANCE, THE AGENTS'
--       LESSONS (PERSISTENT MEMORY) AND IMPROVEMENT PROPOSALS WITH A
--       TRAINING PERIOD STRICTLY BEFORE A LATER EVALUATION PERIOD
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER-AUTHORIZED, PAPER ONLY. Real-money execution stays disabled; nothing
-- here can place, cancel or modify an order, and nothing here is read by a
-- funded module (`agents/paper_learning.py` is the only writer).
--
-- 1 · paper_decisions.provenance (NULLABLE, NO DEFAULT). Written GOING
--     FORWARD by every paper decision (Derek's two-model strategy and both
--     benchmark policies) in the same INSERT as the decision: the code /
--     runtime / policy / model / simulator versions, a snapshot of the
--     valuation inputs AS READ at the decision instant with their SHA-256,
--     the session config SHA, the prices, fees and expected figures, the
--     alternatives considered and a plain explanation. Existing rows are NOT
--     rewritten (the table is append-only; adding a nullable column without
--     a default fires no row trigger): a row without it reads
--     NOT_RECORDED_BEFORE_MIGRATION_185, never a reconstruction.
--
-- 2 · paper_agent_lessons: EACH AGENT'S PERSISTENT MEMORY of what the paper
--     book's FORWARD records taught it (settled positions, refusal funnels,
--     simulated fills against the optimistic bound, management outcomes,
--     audit coverage). APPEND-ONLY; one series per (account, agent, kind,
--     strategy), versioned: a lesson whose content is unchanged writes
--     nothing, a changed one is a new version that supersedes the last.
--     Every lesson carries its PROVENANCE: the selection (window, strategy,
--     query), the record count, the SHA-256 of every contributing record id
--     and a sample of the ids -- re-running the selection reproduces it.
--
-- 3 · paper_improvement_proposals (+ its append-only events). A proposed
--     change, its evaluation protocol and its result. THE DATABASE ENFORCES
--     THE PROTOCOL:
--         training_start < training_end <= proposed_at <= evaluation_start
--                                                     < evaluation_end
--     so the training period is strictly before the evaluation period, and
--     the evaluation period begins no earlier than the proposal: it is
--     scored on FORWARD outcomes only, never on data seen when proposing.
--     The evaluation is WRITE-ONCE (a trigger refuses re-scoring); the
--     evaluator is never the proposer; INSUFFICIENT_FORWARD_DATA is a
--     status with its counts, never a verdict.
--     ACTIVATION IS NEVER AUTOMATIC: `active` needs a PASS, a named
--     approver and the time (CHECK), the scope is PAPER_ONLY (CHECK: no
--     funded scope exists), and the only writer refuses unless the explicit
--     control row PAPER_LEARNING_PROPOSAL_ACTIVATION is enabled -- it is
--     inserted DISABLED below.
--
-- 4 · An index on paper_audrey_findings (account_id, kind, subject): Audrey
--     audits meaningful paper events AS THEY HAPPEN, one finding per event
--     (deterministic finding id), and looks up the unaudited events of the
--     account by it (so a resumed session never audits an event twice).
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction together with its schema_migrations row (as 181-184 do).

ALTER TABLE paper_decisions ADD COLUMN IF NOT EXISTS provenance jsonb;

COMMENT ON COLUMN paper_decisions.provenance IS
    'Going forward from migration 185: versions (code, runtime, policy, '
    'model, simulator), the valuation inputs as read at the decision instant '
    'with their SHA-256, session config SHA, prices, fees, expected figures, '
    'alternatives considered and a plain explanation. NULL on rows recorded '
    'before 185 (never back-filled).';

CREATE INDEX IF NOT EXISTS paper_audrey_findings_kind_subject_idx
    ON paper_audrey_findings (account_id, kind, subject);

-- ── 2 · LESSONS: EACH AGENT'S PERSISTENT MEMORY ────────────────────────
CREATE TABLE IF NOT EXISTS paper_agent_lessons (
    lesson_id           text PRIMARY KEY,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    session_id          text        REFERENCES paper_sessions,
    agent_id            text        NOT NULL,
    kind                text        NOT NULL,
    strategy            text,
    series_key          text        NOT NULL,
    version             integer     NOT NULL,
    supersedes          text,
    learned_at          timestamptz NOT NULL,
    window_start        timestamptz,
    window_end          timestamptz NOT NULL,
    statement           text        NOT NULL,
    metrics             jsonb       NOT NULL,
    provenance          jsonb       NOT NULL,
    evidence_category   text        NOT NULL,
    basis               text        NOT NULL,
    improvement_task_id text,
    digest              text        NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_agent_lessons_paper_id_ck CHECK (
        lesson_id LIKE 'paper%'),
    CONSTRAINT paper_agent_lessons_agent_ck CHECK (
        agent_id IN ('DEREK', 'XAVIER', 'AUDREY')),
    CONSTRAINT paper_agent_lessons_forward_ck CHECK (
        basis = 'FORWARD_RECORDS_ONLY'),
    CONSTRAINT paper_agent_lessons_provenance_ck CHECK (
        jsonb_typeof(provenance) = 'object'
        AND provenance ? 'record_count' AND provenance ? 'ids_sha256'),
    CONSTRAINT paper_agent_lessons_version_ck UNIQUE (
        account_id, series_key, version)
);
CREATE INDEX IF NOT EXISTS paper_agent_lessons_agent_idx
    ON paper_agent_lessons (account_id, agent_id, learned_at DESC);

DROP TRIGGER IF EXISTS paper_agent_lessons_append_only_trg
    ON paper_agent_lessons;
CREATE TRIGGER paper_agent_lessons_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_agent_lessons
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();

-- ── 3 · IMPROVEMENT PROPOSALS ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS paper_improvement_proposals (
    proposal_id         text PRIMARY KEY,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    agent_id            text        NOT NULL,
    strategy            text,
    change_class        text        NOT NULL,
    proposed_change     jsonb       NOT NULL,
    rationale           text        NOT NULL,
    source_lesson_ids   text[]      NOT NULL DEFAULT '{}',
    proposed_by         text        NOT NULL,
    proposed_at         timestamptz NOT NULL,
    training_start      timestamptz NOT NULL,
    training_end        timestamptz NOT NULL,
    evaluation_start    timestamptz NOT NULL,
    evaluation_end      timestamptz NOT NULL,
    protocol            jsonb       NOT NULL,
    status              text        NOT NULL
                        DEFAULT 'AWAITING_FORWARD_DATA',
    last_attempt        jsonb,
    evaluation          jsonb,
    verdict             text,
    evaluated_at        timestamptz,
    evaluated_by        text,
    scope               text        NOT NULL DEFAULT 'PAPER_ONLY',
    activation_control_key text     NOT NULL
                        DEFAULT 'PAPER_LEARNING_PROPOSAL_ACTIVATION',
    active              boolean     NOT NULL DEFAULT FALSE,
    activated_by        text,
    activated_at        timestamptz,
    deactivated_at      timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_improvement_proposals_paper_id_ck CHECK (
        proposal_id LIKE 'paper%'),
    CONSTRAINT paper_improvement_proposals_agent_ck CHECK (
        agent_id IN ('DEREK', 'XAVIER', 'AUDREY')),
    -- THE TRAINING PERIOD IS STRICTLY BEFORE THE LATER EVALUATION PERIOD.
    CONSTRAINT paper_improvement_proposals_split_ck CHECK (
        training_start < training_end
        AND training_end <= evaluation_start
        AND evaluation_start < evaluation_end),
    -- NO PEEKING: training ends by the proposal; evaluation starts after it.
    CONSTRAINT paper_improvement_proposals_forward_ck CHECK (
        training_end <= proposed_at AND proposed_at <= evaluation_start),
    CONSTRAINT paper_improvement_proposals_status_ck CHECK (status IN (
        'AWAITING_FORWARD_DATA', 'INSUFFICIENT_FORWARD_DATA', 'EVALUATED',
        'WITHDRAWN')),
    CONSTRAINT paper_improvement_proposals_verdict_ck CHECK (
        verdict IS NULL OR verdict IN ('PASS', 'FAIL_NO_IMPROVEMENT',
                                       'FAIL_HARM')),
    CONSTRAINT paper_improvement_proposals_evaluated_ck CHECK (
        (status = 'EVALUATED') = (verdict IS NOT NULL
                                  AND evaluation IS NOT NULL
                                  AND evaluated_at IS NOT NULL
                                  AND evaluated_by IS NOT NULL)),
    CONSTRAINT paper_improvement_proposals_evaluator_ck CHECK (
        evaluated_by IS NULL OR evaluated_by <> proposed_by),
    -- NO FUNDED SCOPE EXISTS: a proposal can only ever be a paper change.
    CONSTRAINT paper_improvement_proposals_scope_ck CHECK (
        scope = 'PAPER_ONLY'),
    -- ACTIVE ONLY ON A PASS, WITH A NAMED APPROVER AND THE TIME.
    CONSTRAINT paper_improvement_proposals_active_ck CHECK (
        NOT active OR (status = 'EVALUATED' AND verdict = 'PASS'
                       AND activated_by IS NOT NULL
                       AND activated_at IS NOT NULL))
);
-- One open (not yet evaluated) proposal per agent / change class / strategy.
CREATE UNIQUE INDEX IF NOT EXISTS paper_improvement_proposals_one_open_idx
    ON paper_improvement_proposals (account_id, agent_id, change_class,
                                    coalesce(strategy, ''))
    WHERE status IN ('AWAITING_FORWARD_DATA', 'INSUFFICIENT_FORWARD_DATA');

-- THE PROTOCOL IS FIXED AT PROPOSAL; THE EVALUATION IS WRITE-ONCE.
CREATE OR REPLACE FUNCTION paper_improvement_proposals_guard()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'paper_improvement_proposals: a proposal is never '
                        'deleted (it is part of the learning record)';
    END IF;
    IF NEW.proposal_id IS DISTINCT FROM OLD.proposal_id
       OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.agent_id IS DISTINCT FROM OLD.agent_id
       OR NEW.strategy IS DISTINCT FROM OLD.strategy
       OR NEW.change_class IS DISTINCT FROM OLD.change_class
       OR NEW.proposed_change IS DISTINCT FROM OLD.proposed_change
       OR NEW.proposed_by IS DISTINCT FROM OLD.proposed_by
       OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at
       OR NEW.training_start IS DISTINCT FROM OLD.training_start
       OR NEW.training_end IS DISTINCT FROM OLD.training_end
       OR NEW.evaluation_start IS DISTINCT FROM OLD.evaluation_start
       OR NEW.evaluation_end IS DISTINCT FROM OLD.evaluation_end
       OR NEW.protocol IS DISTINCT FROM OLD.protocol THEN
        RAISE EXCEPTION 'paper_improvement_proposals: the proposed change '
                        'and its protocol are fixed at proposal (%)',
                        OLD.proposal_id;
    END IF;
    IF OLD.status = 'EVALUATED' AND (
           NEW.status IS DISTINCT FROM OLD.status
           OR NEW.evaluation IS DISTINCT FROM OLD.evaluation
           OR NEW.verdict IS DISTINCT FROM OLD.verdict
           OR NEW.evaluated_at IS DISTINCT FROM OLD.evaluated_at
           OR NEW.evaluated_by IS DISTINCT FROM OLD.evaluated_by) THEN
        RAISE EXCEPTION 'paper_improvement_proposals: the evaluation of % '
                        'is write-once', OLD.proposal_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS paper_improvement_proposals_guard_trg
    ON paper_improvement_proposals;
CREATE TRIGGER paper_improvement_proposals_guard_trg
    BEFORE UPDATE OR DELETE ON paper_improvement_proposals
    FOR EACH ROW EXECUTE FUNCTION paper_improvement_proposals_guard();

CREATE TABLE IF NOT EXISTS paper_improvement_proposal_events (
    event_id            bigserial PRIMARY KEY,
    proposal_id         text        NOT NULL
                        REFERENCES paper_improvement_proposals,
    at                  timestamptz NOT NULL,
    kind                text        NOT NULL,
    actor               text        NOT NULL,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS paper_improvement_proposal_events_idx
    ON paper_improvement_proposal_events (proposal_id, event_id);

DROP TRIGGER IF EXISTS paper_improvement_proposal_events_append_only_trg
    ON paper_improvement_proposal_events;
CREATE TRIGGER paper_improvement_proposal_events_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_improvement_proposal_events
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();

-- ── THE EXPLICIT ACTIVATION CONTROL: INSERTED DISABLED ─────────────────
-- Activating a PASSED paper proposal also needs a named approver; with this
-- row off (or absent) every activation is refused by name. Nothing turns it
-- on automatically:
--     UPDATE paper_control SET enabled = TRUE, updated_by = '<who>',
--            why = '<why>', updated_at = now()
--      WHERE control_key = 'PAPER_LEARNING_PROPOSAL_ACTIVATION';
INSERT INTO paper_control (control_key, enabled, why, updated_by)
VALUES ('PAPER_LEARNING_PROPOSAL_ACTIVATION', FALSE,
        'inserted DISABLED at migration 185: activating a passed paper '
        'improvement proposal needs this row on AND a named approver. PAPER '
        'ONLY: no proposal can reach funded execution (scope CHECK)',
        'migration 185')
ON CONFLICT DO NOTHING;
