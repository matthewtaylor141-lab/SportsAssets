-- ══════════════════════════════════════════════════════════════════════
-- 203 · THE AGENTS' COLLABORATION LOOP: A FINDING MOVES THROUGH EXPLICIT,
--       PERSISTED, PEER-CHECKED STAGES -- AND NEVER CHANGES PRODUCTION
-- ══════════════════════════════════════════════════════════════════════
--
-- Derek, Xavier and Audrey already write evidence (paper_agent_lessons,
-- agent_decisions, paper_xavier_reviews, paper_audrey_findings, ...) and
-- forward-evaluated proposals (paper_improvement_proposals, migration 185).
-- What was missing is the path BETWEEN them: who challenged a hypothesis,
-- what experiment was pre-registered, who evaluated it independently and on
-- which data. This migration EXTENDS those records (it references them; it
-- copies no economics) with two tables:
--
--   agent_findings        one finding: its proposer, its evidence
--                         references (GROUNDED: at least one {kind, id}),
--                         the end of the data window it was formed on, and
--                         its current stage (maintained from the stages).
--   agent_finding_stages  APPEND-ONLY, one row per stage, in this order:
--        1 EVIDENCE                 the proposer's grounded evidence
--        2 HYPOTHESIS               the proposer's hypothesis, grounded
--        3 PEER_CHALLENGE           a DIFFERENT agent's challenge, grounded;
--                                   SUSTAINED to go on, REFUTED to close
--        4 BOUNDED_EXPERIMENT       PAPER_ONLY, a pre-registered metric
--                                   (name, direction, threshold) and a
--                                   stopping rule (max_duration_s and/or
--                                   max_samples)
--        5 CANDIDATE_IMPROVEMENT    the candidate change (optionally the
--                                   paper_improvement_proposals row)
--        6 INDEPENDENT_EVALUATION   by an agent that is neither the proposer
--                                   nor the candidate's author, on data
--                                   that starts at/after BOTH the finding's
--                                   evidence window and the experiment's
--                                   registration, cites none of the
--                                   evidence the hypothesis was formed on;
--                                   PASS / FAIL / INCONCLUSIVE
--        7 RELEASE_ELIGIBILITY      only after a PASS, by a non-proposer:
--                                   ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW. It is
--                                   a MARK for a person; it changes nothing.
--       99 CLOSED                   from any stage; nothing follows it.
--
-- WHAT THE DATABASE ENFORCES (the trigger and CHECKs below; the module
-- agents/collaboration_loop.py refuses the same things first, by name):
--   * no stage can be skipped (seq = current + 1) or repeated (UNIQUE);
--   * no agent challenges or evaluates its own finding;
--   * an ungrounded finding cannot exist or advance;
--   * production_effect is 'NONE' on every row (CHECK), and no row carries
--     a risk limit, capital, credential, approval or activation key: no
--     stage of this loop can activate a policy or change a limit. There is
--     no foreign key or trigger from these tables into agent_policy_versions,
--     paper_control or any limit table.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE. No BEGIN/COMMIT of its own.

CREATE OR REPLACE FUNCTION agent_loop_refs_grounded(refs jsonb)
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

CREATE OR REPLACE FUNCTION agent_loop_no_authority(doc jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT doc IS NULL OR jsonb_typeof(doc) <> 'object' OR NOT (
        doc ?| ARRAY['risk_limits', 'limits', 'capital_usd',
                     'max_downside_usd', 'max_incremental_capital_usd',
                     'per_order_usd', 'max_order_usd', 'daily_loss_stop_usd',
                     'credentials', 'api_key', 'account_authority',
                     'submission_enabled', 'approved', 'approved_by',
                     'approval', 'activate', 'activation', 'active',
                     'policy_activation'])
$$;

CREATE TABLE IF NOT EXISTS agent_findings (
    finding_id           text PRIMARY KEY,
    proposer             text        NOT NULL,
    title                text        NOT NULL,
    statement            text        NOT NULL,
    evidence_refs        jsonb       NOT NULL,
    evidence_window_end  timestamptz NOT NULL,
    source_lesson_id     text        REFERENCES paper_agent_lessons,
    stage                text,
    stage_seq            integer     NOT NULL DEFAULT 0,
    production_effect    text        NOT NULL DEFAULT 'NONE',
    created_at           timestamptz NOT NULL,
    updated_at           timestamptz NOT NULL,
    CONSTRAINT agent_findings_proposer_ck CHECK (
        proposer IN ('DEREK', 'XAVIER', 'AUDREY')),
    CONSTRAINT agent_findings_title_ck CHECK (
        length(btrim(title)) BETWEEN 1 AND 300
        AND length(btrim(statement)) BETWEEN 1 AND 4000),
    -- GROUNDED: a finding without an evidence reference cannot exist.
    CONSTRAINT agent_findings_grounded_ck CHECK (
        agent_loop_refs_grounded(evidence_refs)),
    CONSTRAINT agent_findings_window_ck CHECK (
        evidence_window_end <= created_at),
    CONSTRAINT agent_findings_stage_ck CHECK (
        (stage IS NULL) = (stage_seq = 0)
        AND (stage IS NULL OR (stage, stage_seq) IN (
            ('EVIDENCE', 1), ('HYPOTHESIS', 2), ('PEER_CHALLENGE', 3),
            ('BOUNDED_EXPERIMENT', 4), ('CANDIDATE_IMPROVEMENT', 5),
            ('INDEPENDENT_EVALUATION', 6), ('RELEASE_ELIGIBILITY', 7),
            ('CLOSED', 99)))),
    CONSTRAINT agent_findings_no_production_effect_ck CHECK (
        production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS agent_findings_stage_idx
    ON agent_findings (stage, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_finding_stages (
    stage_id           bigserial PRIMARY KEY,
    finding_id         text        NOT NULL REFERENCES agent_findings,
    seq                integer     NOT NULL,
    stage              text        NOT NULL,
    actor              text        NOT NULL,
    at                 timestamptz NOT NULL,
    body               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    evidence_refs      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    outcome            text,
    scope              text,
    metric             jsonb,
    stopping_rule      jsonb,
    data_start         timestamptz,
    data_end           timestamptz,
    proposal_id        text        REFERENCES paper_improvement_proposals,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_finding_stages_once_ck UNIQUE (finding_id, seq),
    CONSTRAINT agent_finding_stages_actor_ck CHECK (
        actor IN ('DEREK', 'XAVIER', 'AUDREY')),
    CONSTRAINT agent_finding_stages_seq_ck CHECK ((stage, seq) IN (
        ('EVIDENCE', 1), ('HYPOTHESIS', 2), ('PEER_CHALLENGE', 3),
        ('BOUNDED_EXPERIMENT', 4), ('CANDIDATE_IMPROVEMENT', 5),
        ('INDEPENDENT_EVALUATION', 6), ('RELEASE_ELIGIBILITY', 7),
        ('CLOSED', 99))),
    CONSTRAINT agent_finding_stages_body_ck CHECK (
        jsonb_typeof(body) = 'object' AND jsonb_typeof(evidence_refs) =
        'array'),
    -- NOTHING HERE ACTIVATES A POLICY OR CHANGES A LIMIT.
    CONSTRAINT agent_finding_stages_no_production_effect_ck CHECK (
        production_effect = 'NONE'),
    CONSTRAINT agent_finding_stages_no_authority_ck CHECK (
        agent_loop_no_authority(body)
        AND agent_loop_no_authority(body->'change')),
    -- GROUNDED STAGES CITE EVIDENCE.
    CONSTRAINT agent_finding_stages_grounded_ck CHECK (
        stage NOT IN ('EVIDENCE', 'HYPOTHESIS', 'PEER_CHALLENGE',
                      'INDEPENDENT_EVALUATION')
        OR agent_loop_refs_grounded(evidence_refs)),
    CONSTRAINT agent_finding_stages_hypothesis_ck CHECK (
        stage <> 'HYPOTHESIS'
        OR coalesce(length(btrim(body->>'hypothesis')) > 0, false)),
    CONSTRAINT agent_finding_stages_challenge_ck CHECK (
        stage <> 'PEER_CHALLENGE'
        OR coalesce(outcome IN ('SUSTAINED', 'REFUTED')
                    AND length(btrim(body->>'challenge')) > 0, false)),
    -- PAPER ONLY, WITH A PRE-REGISTERED METRIC AND A BOUNDED STOPPING RULE.
    -- (Every stage CHECK is coalesced: a NULL never passes.)
    CONSTRAINT agent_finding_stages_experiment_ck CHECK (
        stage <> 'BOUNDED_EXPERIMENT'
        OR coalesce(
            scope = 'PAPER_ONLY'
            AND jsonb_typeof(metric) = 'object'
            AND metric ? 'name' AND metric ? 'direction'
            AND metric ? 'threshold'
            AND metric->>'direction' IN ('INCREASE', 'DECREASE')
            AND jsonb_typeof(metric->'threshold') = 'number'
            AND jsonb_typeof(stopping_rule) = 'object'
            AND (stopping_rule ? 'max_duration_s'
                 OR stopping_rule ? 'max_samples')
            AND (NOT stopping_rule ? 'max_duration_s'
                 OR (jsonb_typeof(stopping_rule->'max_duration_s') = 'number'
                     AND (stopping_rule->>'max_duration_s')::numeric
                         BETWEEN 1 AND 7776000))
            AND (NOT stopping_rule ? 'max_samples'
                 OR (jsonb_typeof(stopping_rule->'max_samples') = 'number'
                     AND (stopping_rule->>'max_samples')::numeric
                         BETWEEN 1 AND 100000)), false)),
    CONSTRAINT agent_finding_stages_scope_ck CHECK (
        scope IS NULL OR scope = 'PAPER_ONLY'),
    CONSTRAINT agent_finding_stages_candidate_ck CHECK (
        stage <> 'CANDIDATE_IMPROVEMENT'
        OR coalesce(jsonb_typeof(body->'change') = 'object'
                    AND length(btrim(body->>'description')) > 0, false)),
    CONSTRAINT agent_finding_stages_evaluation_ck CHECK (
        stage <> 'INDEPENDENT_EVALUATION'
        OR coalesce(outcome IN ('PASS', 'FAIL', 'INCONCLUSIVE')
                    AND data_start < data_end AND data_end <= at, false)),
    CONSTRAINT agent_finding_stages_release_ck CHECK (
        stage <> 'RELEASE_ELIGIBILITY'
        OR coalesce(outcome = 'ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW', false)),
    CONSTRAINT agent_finding_stages_closed_ck CHECK (
        stage <> 'CLOSED'
        OR coalesce(length(btrim(body->>'reason')) > 0, false)),
    CONSTRAINT agent_finding_stages_outcome_ck CHECK (
        outcome IS NULL OR stage IN ('PEER_CHALLENGE',
                                     'INDEPENDENT_EVALUATION',
                                     'RELEASE_ELIGIBILITY', 'CLOSED'))
);
CREATE INDEX IF NOT EXISTS agent_finding_stages_finding_idx
    ON agent_finding_stages (finding_id, seq);

-- ── THE STAGE GUARD: ORDER, INDEPENDENCE, GROUNDING ───────────────────
CREATE OR REPLACE FUNCTION agent_finding_stages_guard() RETURNS trigger
AS $$
DECLARE
    f      agent_findings%ROWTYPE;
    prev   agent_finding_stages%ROWTYPE;
    exp    agent_finding_stages%ROWTYPE;
    cand   agent_finding_stages%ROWTYPE;
    hyp    agent_finding_stages%ROWTYPE;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'agent_finding_stages is append-only: % refused',
            TG_OP;
    END IF;
    SELECT * INTO f FROM agent_findings WHERE finding_id = NEW.finding_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'agent_finding_stages: no finding %', NEW.finding_id;
    END IF;
    IF f.stage = 'CLOSED' THEN
        RAISE EXCEPTION 'agent_finding_stages: finding % is CLOSED',
            NEW.finding_id;
    END IF;
    IF NEW.at < f.created_at THEN
        RAISE EXCEPTION 'agent_finding_stages: a stage cannot predate its '
                        'finding';
    END IF;
    IF NEW.stage = 'CLOSED' THEN
        IF f.stage_seq = 0 THEN
            RAISE EXCEPTION 'agent_finding_stages: record EVIDENCE first';
        END IF;
        RETURN NEW;
    END IF;
    -- NO STAGE CAN BE SKIPPED
    IF NEW.seq <> f.stage_seq + 1 THEN
        RAISE EXCEPTION 'agent_finding_stages: % cannot follow stage % of '
                        'finding % (no stage is skipped or repeated)',
                        NEW.stage,
                        coalesce(f.stage, 'NONE'), NEW.finding_id;
    END IF;
    -- AN UNGROUNDED FINDING CANNOT ADVANCE
    IF NOT agent_loop_refs_grounded(f.evidence_refs) THEN
        RAISE EXCEPTION 'agent_finding_stages: finding % is not grounded',
            NEW.finding_id;
    END IF;
    IF NEW.seq > 1 THEN
        SELECT * INTO prev FROM agent_finding_stages
         WHERE finding_id = NEW.finding_id AND seq = NEW.seq - 1;
        IF NEW.at < prev.at THEN
            RAISE EXCEPTION 'agent_finding_stages: a stage cannot predate '
                            'the stage before it';
        END IF;
    END IF;
    IF NEW.stage IN ('EVIDENCE', 'HYPOTHESIS') AND NEW.actor <> f.proposer
    THEN
        RAISE EXCEPTION 'agent_finding_stages: % is recorded by the '
                        'proposer %', NEW.stage, f.proposer;
    END IF;
    IF NEW.stage = 'PEER_CHALLENGE' AND NEW.actor = f.proposer THEN
        RAISE EXCEPTION 'agent_finding_stages: % cannot challenge its own '
                        'finding %', NEW.actor, NEW.finding_id;
    END IF;
    IF NEW.stage = 'BOUNDED_EXPERIMENT'
       AND prev.outcome IS DISTINCT FROM 'SUSTAINED' THEN
        RAISE EXCEPTION 'agent_finding_stages: a REFUTED hypothesis is '
                        'closed, not experimented on';
    END IF;
    IF NEW.stage = 'INDEPENDENT_EVALUATION' THEN
        SELECT * INTO hyp FROM agent_finding_stages
         WHERE finding_id = NEW.finding_id AND seq = 2;
        SELECT * INTO exp FROM agent_finding_stages
         WHERE finding_id = NEW.finding_id AND seq = 4;
        SELECT * INTO cand FROM agent_finding_stages
         WHERE finding_id = NEW.finding_id AND seq = 5;
        IF NEW.actor = f.proposer OR NEW.actor = cand.actor THEN
            RAISE EXCEPTION 'agent_finding_stages: % cannot evaluate its own '
                            'finding or candidate (%)', NEW.actor,
                            NEW.finding_id;
        END IF;
        -- ON DATA NOT USED TO FORM THE HYPOTHESIS, AFTER PRE-REGISTRATION
        IF NEW.data_start < f.evidence_window_end OR NEW.data_start < exp.at
        THEN
            RAISE EXCEPTION 'agent_finding_stages: the evaluation data must '
                            'start after the evidence window and the '
                            'experiment registration';
        END IF;
        -- THE PRE-REGISTERED STOPPING RULE BOUNDS THE EVALUATED DATA
        IF exp.stopping_rule ? 'max_duration_s'
           AND extract(epoch FROM NEW.data_end - NEW.data_start)
               > (exp.stopping_rule->>'max_duration_s')::numeric THEN
            RAISE EXCEPTION 'agent_finding_stages: the evaluated window '
                            'exceeds the pre-registered stopping rule';
        END IF;
        IF exp.stopping_rule ? 'max_samples' AND (
               jsonb_typeof(NEW.body->'samples') IS DISTINCT FROM 'number'
               OR (NEW.body->>'samples')::numeric
                  > (exp.stopping_rule->>'max_samples')::numeric) THEN
            RAISE EXCEPTION 'agent_finding_stages: the evaluation must state '
                            'its samples within the pre-registered maximum';
        END IF;
        -- A PASS MEETS THE PRE-REGISTERED METRIC, as registered
        IF NEW.outcome = 'PASS' AND (
               jsonb_typeof(NEW.body->'metric_value') IS DISTINCT FROM
                   'number'
               OR (exp.metric->>'direction' = 'INCREASE'
                   AND (NEW.body->>'metric_value')::numeric
                       < (exp.metric->>'threshold')::numeric)
               OR (exp.metric->>'direction' = 'DECREASE'
                   AND (NEW.body->>'metric_value')::numeric
                       > (exp.metric->>'threshold')::numeric)) THEN
            RAISE EXCEPTION 'agent_finding_stages: a PASS must meet the '
                            'pre-registered metric';
        END IF;
        IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(NEW.evidence_refs) n
             WHERE EXISTS (
                SELECT 1 FROM jsonb_array_elements(
                        f.evidence_refs || hyp.evidence_refs) o
                 WHERE o->>'kind' = n->>'kind' AND o->>'id' = n->>'id'))
        THEN
            RAISE EXCEPTION 'agent_finding_stages: the evaluation cites '
                            'evidence the hypothesis was formed on';
        END IF;
    END IF;
    IF NEW.stage = 'RELEASE_ELIGIBILITY' THEN
        IF prev.outcome IS DISTINCT FROM 'PASS' THEN
            RAISE EXCEPTION 'agent_finding_stages: only a PASSED independent '
                            'evaluation is eligible for human release review';
        END IF;
        IF NEW.actor = f.proposer THEN
            RAISE EXCEPTION 'agent_finding_stages: the proposer cannot mark '
                            'its own finding eligible';
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_finding_stages_guard_trg
    ON agent_finding_stages;
CREATE TRIGGER agent_finding_stages_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON agent_finding_stages
    FOR EACH ROW EXECUTE FUNCTION agent_finding_stages_guard();

-- ── THE FINDING'S STAGE FOLLOWS ITS STAGE ROWS, AND NOTHING ELSE ──────
CREATE OR REPLACE FUNCTION agent_finding_stages_advance() RETURNS trigger
AS $$
BEGIN
    UPDATE agent_findings SET stage = NEW.stage, stage_seq = NEW.seq,
           updated_at = greatest(updated_at, NEW.at)
     WHERE finding_id = NEW.finding_id;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_finding_stages_advance_trg
    ON agent_finding_stages;
CREATE TRIGGER agent_finding_stages_advance_trg
    AFTER INSERT ON agent_finding_stages
    FOR EACH ROW EXECUTE FUNCTION agent_finding_stages_advance();

CREATE OR REPLACE FUNCTION agent_findings_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.stage IS NOT NULL OR NEW.stage_seq <> 0 THEN
            RAISE EXCEPTION 'agent_findings: a finding starts with no stage; '
                            'stages are recorded in agent_finding_stages';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'agent_findings: a finding is never deleted (%)',
            OLD.finding_id;
    END IF;
    IF NEW.finding_id IS DISTINCT FROM OLD.finding_id
       OR NEW.proposer IS DISTINCT FROM OLD.proposer
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.statement IS DISTINCT FROM OLD.statement
       OR NEW.evidence_refs IS DISTINCT FROM OLD.evidence_refs
       OR NEW.evidence_window_end IS DISTINCT FROM OLD.evidence_window_end
       OR NEW.source_lesson_id IS DISTINCT FROM OLD.source_lesson_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'agent_findings: the finding % is fixed at creation',
            OLD.finding_id;
    END IF;
    -- THE STAGE IS ALWAYS THE LATEST RECORDED STAGE ROW (set by the
    -- advance trigger); it can neither jump ahead nor move back.
    IF (NEW.stage, NEW.stage_seq) IS DISTINCT FROM (OLD.stage, OLD.stage_seq)
       AND NOT EXISTS (SELECT 1 FROM agent_finding_stages s
                        WHERE s.finding_id = NEW.finding_id
                          AND s.seq = NEW.stage_seq
                          AND s.stage = NEW.stage
                          AND s.seq = (SELECT max(seq)
                                         FROM agent_finding_stages m
                                        WHERE m.finding_id = NEW.finding_id))
    THEN
        RAISE EXCEPTION 'agent_findings: stage % of % has no stage record',
            NEW.stage, NEW.finding_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_findings_guard_trg ON agent_findings;
CREATE TRIGGER agent_findings_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON agent_findings
    FOR EACH ROW EXECUTE FUNCTION agent_findings_guard();
