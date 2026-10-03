-- ══════════════════════════════════════════════════════════════════════
-- 207 · KAREN: THE RED TEAM / CHALLENGE AGENT -- PERSISTED, GROUNDED,
--       PEER-ANSWERED, AND WITH NO AUTHORITY OF ANY KIND
-- ══════════════════════════════════════════════════════════════════════
--
-- Karen is a fourth named agent beside Derek (entry), Xavier (management)
-- and Audrey (audit). Her one job is to CHALLENGE the other three's records
-- with evidence. She holds no order path, no venue submission, no capital,
-- no risk limit, no approval, no policy activation and no promotion -- and
-- this migration makes the database refuse each of those, independently of
-- the application code (agents/karen.py refuses the same things first).
--
-- WHAT THIS MIGRATION ADDS
--   1. KAREN as an agent identity (agent_identities' CHECK widened).
--   2. KAREN in the collaboration loop (migration 203) ONLY as a challenger:
--      she may record the PEER_CHALLENGE stage of another agent's finding and
--      NO other stage. She cannot propose a finding (agent_findings.proposer
--      is unchanged), evaluate one, or mark one release-eligible. 203's
--      trigger already refuses an agent challenging its own finding.
--   3. 'karen' as a Slack bridge identity (agent_slack_delivery's CHECK), so
--      her messages go out under her OWN bot token or not at all.
--   4. karen_challenges: one challenge -- target agent, target record,
--      evidence references (GROUNDED: at least one {kind, id}), severity,
--      state OPEN -> RESPONDED -> UPHELD | REJECTED (or WITHDRAWN by Karen),
--      the target agent's peer response, the outcome, the false-block
--      assessment and the downstream improvement link. A trigger fixes the
--      record at creation and permits only the lifecycle's transitions:
--        * only the TARGET agent records the response;
--        * Karen can never resolve (UPHELD / REJECTED) her own challenge;
--        * the target may concede (UPHELD) but never reject a challenge
--          against itself -- REJECTED needs a third party;
--        * resolution needs the peer response first;
--        * false-block and improvement links are recorded once, never by
--          Karen.
--      karen_challenge_events is its append-only history.
--   5. KAREN HAS NO AUTHORITY, IN THE DATABASE: a trigger on every approval,
--      activation, promotion, release, control and decision-of-record table
--      refuses Karen as the acting identity; agent_task_events refuses Karen
--      moving a task to APPROVAL_READY / APPROVED / RELEASED / ROLLED_BACK.
--
-- NO FOREIGN KEY into agent_findings or paper_improvement_proposals: the
-- references are checked by the trigger instead, so 203's and 185's own
-- rollbacks stay independent of this migration.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP-then-ADD. No BEGIN/COMMIT of
-- its own (the runner wraps each file in one transaction).

-- ── 1 · THE IDENTITY ─────────────────────────────────────────────────
ALTER TABLE agent_identities
    DROP CONSTRAINT IF EXISTS agent_identities_agent_id_check;
ALTER TABLE agent_identities
    ADD CONSTRAINT agent_identities_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN'));

-- ── 2 · THE COLLABORATION LOOP: KAREN CHALLENGES, AND ONLY CHALLENGES ─
ALTER TABLE agent_finding_stages
    DROP CONSTRAINT IF EXISTS agent_finding_stages_actor_ck;
ALTER TABLE agent_finding_stages
    ADD CONSTRAINT agent_finding_stages_actor_ck CHECK (
        actor IN ('DEREK', 'XAVIER', 'AUDREY')
        OR (actor = 'KAREN' AND stage = 'PEER_CHALLENGE'));

-- ── 3 · SLACK: HER OWN BOT IDENTITY ──────────────────────────────────
ALTER TABLE agent_slack_delivery
    DROP CONSTRAINT IF EXISTS agent_slack_delivery_agent_check;
ALTER TABLE agent_slack_delivery
    ADD CONSTRAINT agent_slack_delivery_agent_check
    CHECK (agent IN ('derek', 'xavier', 'audrey', 'karen'));

-- ── WHO IS "KAREN" AS AN ACTING IDENTITY ─────────────────────────────
-- The agent's own id, or a machine-style label for her (agent:karen,
-- slack:karen, karen-bot, karen red team). A person who happens to be called
-- Karen ("Karen Smith") is NOT matched.
CREATE OR REPLACE FUNCTION karen_is_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(
        upper(btrim(v)) = 'KAREN'
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE)[:/ ._-]+KAREN([^A-Z]|$)'
        OR upper(btrim(v)) ~ 'KAREN[ ._:-]*(AGENT|BOT|RED[ ._-]*TEAM)',
        false)
$$;

-- Karen's own copies of 203's grounding / no-authority predicates (same
-- definitions), so that 203's rollback never depends on this migration.
CREATE OR REPLACE FUNCTION karen_refs_grounded(refs jsonb)
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

CREATE OR REPLACE FUNCTION karen_no_authority(doc jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT doc IS NULL OR jsonb_typeof(doc) <> 'object' OR NOT (
        doc ?| ARRAY['risk_limits', 'limits', 'capital_usd',
                     'max_downside_usd', 'max_incremental_capital_usd',
                     'per_order_usd', 'max_order_usd', 'daily_loss_stop_usd',
                     'credentials', 'api_key', 'account_authority',
                     'submission_enabled', 'approved', 'approved_by',
                     'approval', 'activate', 'activation', 'active',
                     'policy_activation', 'promote', 'promotion', 'release',
                     'order', 'submit'])
$$;

-- ── 4 · THE CHALLENGES ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS karen_challenges (
    challenge_id              text        PRIMARY KEY,
    challenger                text        NOT NULL DEFAULT 'KAREN',
    target_agent              text        NOT NULL,
    target_kind               text        NOT NULL,
    target_id                 text        NOT NULL,
    -- the agent_findings row whose PEER_CHALLENGE stage this challenge is
    -- (target_kind = 'agent_findings'); checked by the trigger, not an FK
    finding_id                text,
    detector                  text        NOT NULL,
    claim                     text        NOT NULL,
    severity                  text        NOT NULL,
    evidence_refs             jsonb       NOT NULL,
    body                      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- the account the target record belongs to, when it has one
    account_id                text,
    -- when the challenged record was made (time-to-challenge starts here)
    record_at                 timestamptz NOT NULL,
    challenged_at             timestamptz NOT NULL,
    state                     text        NOT NULL DEFAULT 'OPEN',
    -- did raising this challenge block or delay anything (e.g. a loop
    -- PEER_CHALLENGE recorded REFUTED stops that finding's experiment)?
    blocked                   boolean     NOT NULL DEFAULT false,
    -- THE PEER RESPONSE: by the target agent, and nobody else
    response_stance           text,
    response                  text,
    response_evidence_refs    jsonb,
    responded_by              text,
    responded_at              timestamptz,
    -- THE OUTCOME
    outcome                   text,
    outcome_reason            text,
    resolved_by               text,
    resolved_at               timestamptz,
    -- THE FALSE-BLOCK RESULT: a blocked challenge that later proved wrong
    false_block               boolean,
    false_block_assessed_by   text,
    false_block_assessed_at   timestamptz,
    false_block_evidence_refs jsonb,
    -- DOWNSTREAM: the improvement an upheld challenge led to
    improvement_finding_id    text,
    improvement_proposal_id   text,
    improvement_linked_by     text,
    improvement_linked_at     timestamptz,
    downstream_impact         jsonb,
    production_effect         text        NOT NULL DEFAULT 'NONE',
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT karen_challenges_once_ck
        UNIQUE (detector, target_kind, target_id),
    CONSTRAINT karen_challenges_challenger_ck CHECK (challenger = 'KAREN'),
    -- NEVER HERSELF: Karen challenges the other three.
    CONSTRAINT karen_challenges_target_ck CHECK (
        target_agent IN ('DEREK', 'XAVIER', 'AUDREY')),
    CONSTRAINT karen_challenges_text_ck CHECK (
        length(btrim(claim)) BETWEEN 1 AND 4000
        AND length(btrim(target_kind)) BETWEEN 1 AND 100
        AND length(btrim(target_id)) BETWEEN 1 AND 300
        AND length(btrim(detector)) BETWEEN 1 AND 100),
    CONSTRAINT karen_challenges_severity_ck CHECK (
        severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CONSTRAINT karen_challenges_state_ck CHECK (
        state IN ('OPEN', 'RESPONDED', 'UPHELD', 'REJECTED', 'WITHDRAWN')),
    -- GROUNDED: a challenge without an evidence reference cannot exist.
    CONSTRAINT karen_challenges_grounded_ck CHECK (
        karen_refs_grounded(evidence_refs)),
    CONSTRAINT karen_challenges_window_ck CHECK (record_at <= challenged_at),
    CONSTRAINT karen_challenges_no_production_effect_ck CHECK (
        production_effect = 'NONE'),
    CONSTRAINT karen_challenges_no_authority_ck CHECK (
        karen_no_authority(body)
        AND karen_no_authority(downstream_impact)),
    CONSTRAINT karen_challenges_finding_ck CHECK (
        (target_kind = 'agent_findings') = (finding_id IS NOT NULL)
        AND (finding_id IS NULL OR finding_id = target_id)),
    -- THE PEER RESPONSE IS THE TARGET'S
    CONSTRAINT karen_challenges_response_ck CHECK (
        (responded_by IS NULL AND responded_at IS NULL
         AND response_stance IS NULL AND response IS NULL)
        OR (responded_by = target_agent AND responded_at IS NOT NULL
            AND response_stance IN ('CONCEDE', 'DISPUTE')
            AND length(btrim(response)) BETWEEN 1 AND 4000
            AND responded_at >= challenged_at)),
    CONSTRAINT karen_challenges_response_refs_ck CHECK (
        response_evidence_refs IS NULL
        OR karen_refs_grounded(response_evidence_refs)),
    -- THE OUTCOME: Karen may only WITHDRAW; she never resolves her own
    -- challenge. The target may concede but never reject. A resolution
    -- follows the peer response.
    CONSTRAINT karen_challenges_outcome_ck CHECK (
        (state IN ('OPEN', 'RESPONDED')
         AND outcome IS NULL AND resolved_by IS NULL AND resolved_at IS NULL
         AND outcome_reason IS NULL)
        OR (state = 'WITHDRAWN' AND outcome = 'WITHDRAWN'
            AND resolved_by = 'KAREN' AND resolved_at IS NOT NULL
            AND length(btrim(outcome_reason)) > 0)
        OR (state = 'UPHELD' AND outcome = 'UPHELD'
            AND resolved_by IS NOT NULL AND NOT karen_is_actor(resolved_by)
            AND length(btrim(resolved_by)) BETWEEN 2 AND 100
            AND responded_by IS NOT NULL AND resolved_at >= responded_at
            AND length(btrim(outcome_reason)) > 0)
        OR (state = 'REJECTED' AND outcome = 'REJECTED'
            AND resolved_by IS NOT NULL AND NOT karen_is_actor(resolved_by)
            AND length(btrim(resolved_by)) BETWEEN 2 AND 100
            AND upper(btrim(resolved_by)) <> target_agent
            AND responded_by IS NOT NULL AND resolved_at >= responded_at
            AND length(btrim(outcome_reason)) > 0)),
    -- FALSE-BLOCK: only a challenge that blocked something, once it is
    -- finished, assessed by someone other than Karen.
    CONSTRAINT karen_challenges_false_block_ck CHECK (
        (false_block IS NULL AND false_block_assessed_by IS NULL
         AND false_block_assessed_at IS NULL
         AND false_block_evidence_refs IS NULL)
        OR (blocked AND state IN ('UPHELD', 'REJECTED', 'WITHDRAWN')
            AND false_block IS NOT NULL
            AND false_block_assessed_by IS NOT NULL
            AND NOT karen_is_actor(false_block_assessed_by)
            AND false_block_assessed_at IS NOT NULL
            AND karen_refs_grounded(false_block_evidence_refs))),
    -- IMPROVEMENT: only an UPHELD challenge, linked by someone else.
    CONSTRAINT karen_challenges_improvement_ck CHECK (
        (improvement_linked_by IS NULL AND improvement_linked_at IS NULL
         AND improvement_finding_id IS NULL
         AND improvement_proposal_id IS NULL)
        OR (state = 'UPHELD' AND improvement_linked_by IS NOT NULL
            AND NOT karen_is_actor(improvement_linked_by)
            AND improvement_linked_at IS NOT NULL
            AND (improvement_finding_id IS NOT NULL
                 OR improvement_proposal_id IS NOT NULL)))
);
CREATE INDEX IF NOT EXISTS karen_challenges_state_idx
    ON karen_challenges (state, challenged_at DESC);
CREATE INDEX IF NOT EXISTS karen_challenges_target_idx
    ON karen_challenges (target_agent, target_kind, target_id);

CREATE TABLE IF NOT EXISTS karen_challenge_events (
    event_id      bigserial   PRIMARY KEY,
    challenge_id  text        NOT NULL REFERENCES karen_challenges,
    at            timestamptz NOT NULL,
    kind          text        NOT NULL,
    actor         text        NOT NULL,
    detail        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT karen_challenge_events_kind_ck CHECK (kind IN (
        'OPENED', 'RESPONDED', 'UPHELD', 'REJECTED', 'WITHDRAWN',
        'FALSE_BLOCK_ASSESSED', 'IMPROVEMENT_LINKED')),
    CONSTRAINT karen_challenge_events_no_authority_ck CHECK (
        karen_no_authority(detail))
);
CREATE INDEX IF NOT EXISTS karen_challenge_events_challenge_idx
    ON karen_challenge_events (challenge_id, event_id);

CREATE OR REPLACE FUNCTION karen_challenge_events_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'karen_challenge_events is append-only: % refused',
        TG_OP;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS karen_challenge_events_append_only_trg
    ON karen_challenge_events;
CREATE TRIGGER karen_challenge_events_append_only_trg
    BEFORE UPDATE OR DELETE ON karen_challenge_events
    FOR EACH ROW EXECUTE FUNCTION karen_challenge_events_append_only();

-- ── THE LIFECYCLE GUARD ──────────────────────────────────────────────
CREATE OR REPLACE FUNCTION karen_challenges_guard() RETURNS trigger AS $$
DECLARE
    f_proposer text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'karen_challenges: a challenge is never deleted (%)',
            OLD.challenge_id;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'OPEN' OR NEW.responded_by IS NOT NULL
           OR NEW.resolved_by IS NOT NULL OR NEW.false_block IS NOT NULL
           OR NEW.improvement_linked_by IS NOT NULL THEN
            RAISE EXCEPTION 'karen_challenges: a challenge starts OPEN, with '
                            'no response, outcome or assessment';
        END IF;
        IF NEW.finding_id IS NOT NULL THEN
            IF to_regclass('agent_findings') IS NULL THEN
                RAISE EXCEPTION 'karen_challenges: no agent_findings table';
            END IF;
            EXECUTE 'SELECT proposer FROM agent_findings WHERE finding_id = $1'
               INTO f_proposer USING NEW.finding_id;
            IF f_proposer IS NULL THEN
                RAISE EXCEPTION 'karen_challenges: no finding %',
                    NEW.finding_id;
            END IF;
            IF f_proposer <> NEW.target_agent THEN
                RAISE EXCEPTION 'karen_challenges: a finding challenge '
                                'targets its proposer %', f_proposer;
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    -- UPDATE: THE CHALLENGE ITSELF IS FIXED AT CREATION
    IF NEW.challenge_id IS DISTINCT FROM OLD.challenge_id
       OR NEW.challenger IS DISTINCT FROM OLD.challenger
       OR NEW.target_agent IS DISTINCT FROM OLD.target_agent
       OR NEW.target_kind IS DISTINCT FROM OLD.target_kind
       OR NEW.target_id IS DISTINCT FROM OLD.target_id
       OR NEW.finding_id IS DISTINCT FROM OLD.finding_id
       OR NEW.detector IS DISTINCT FROM OLD.detector
       OR NEW.claim IS DISTINCT FROM OLD.claim
       OR NEW.severity IS DISTINCT FROM OLD.severity
       OR NEW.evidence_refs IS DISTINCT FROM OLD.evidence_refs
       OR NEW.body IS DISTINCT FROM OLD.body
       OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.record_at IS DISTINCT FROM OLD.record_at
       OR NEW.challenged_at IS DISTINCT FROM OLD.challenged_at
       OR NEW.blocked IS DISTINCT FROM OLD.blocked
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'karen_challenges: challenge % is fixed at creation',
            OLD.challenge_id;
    END IF;
    -- WHAT IS RECORDED ONCE STAYS RECORDED
    IF OLD.responded_by IS NOT NULL AND (
           NEW.responded_by IS DISTINCT FROM OLD.responded_by
           OR NEW.responded_at IS DISTINCT FROM OLD.responded_at
           OR NEW.response IS DISTINCT FROM OLD.response
           OR NEW.response_stance IS DISTINCT FROM OLD.response_stance
           OR NEW.response_evidence_refs IS DISTINCT FROM
              OLD.response_evidence_refs) THEN
        RAISE EXCEPTION 'karen_challenges: the peer response to % is '
                        'recorded once', OLD.challenge_id;
    END IF;
    IF OLD.resolved_by IS NOT NULL AND (
           NEW.resolved_by IS DISTINCT FROM OLD.resolved_by
           OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at
           OR NEW.outcome IS DISTINCT FROM OLD.outcome
           OR NEW.outcome_reason IS DISTINCT FROM OLD.outcome_reason) THEN
        RAISE EXCEPTION 'karen_challenges: the outcome of % is recorded once',
            OLD.challenge_id;
    END IF;
    IF OLD.false_block IS NOT NULL AND (
           NEW.false_block IS DISTINCT FROM OLD.false_block
           OR NEW.false_block_assessed_by IS DISTINCT FROM
              OLD.false_block_assessed_by
           OR NEW.false_block_assessed_at IS DISTINCT FROM
              OLD.false_block_assessed_at
           OR NEW.false_block_evidence_refs IS DISTINCT FROM
              OLD.false_block_evidence_refs) THEN
        RAISE EXCEPTION 'karen_challenges: the false-block result of % is '
                        'recorded once', OLD.challenge_id;
    END IF;
    IF OLD.improvement_linked_by IS NOT NULL AND (
           NEW.improvement_linked_by IS DISTINCT FROM
              OLD.improvement_linked_by
           OR NEW.improvement_linked_at IS DISTINCT FROM
              OLD.improvement_linked_at
           OR NEW.improvement_finding_id IS DISTINCT FROM
              OLD.improvement_finding_id
           OR NEW.improvement_proposal_id IS DISTINCT FROM
              OLD.improvement_proposal_id
           OR NEW.downstream_impact IS DISTINCT FROM OLD.downstream_impact)
    THEN
        RAISE EXCEPTION 'karen_challenges: the improvement link of % is '
                        'recorded once', OLD.challenge_id;
    END IF;
    -- THE TRANSITIONS
    IF NEW.state IS DISTINCT FROM OLD.state THEN
        IF NOT ((OLD.state = 'OPEN' AND NEW.state = 'RESPONDED')
                OR (OLD.state IN ('OPEN', 'RESPONDED')
                    AND NEW.state = 'WITHDRAWN')
                OR (OLD.state = 'RESPONDED'
                    AND NEW.state IN ('UPHELD', 'REJECTED'))) THEN
            RAISE EXCEPTION 'karen_challenges: % -> % is not a challenge '
                            'transition (%)', OLD.state, NEW.state,
                            OLD.challenge_id;
        END IF;
    ELSIF NEW.responded_by IS DISTINCT FROM OLD.responded_by THEN
        RAISE EXCEPTION 'karen_challenges: a response moves OPEN to '
                        'RESPONDED';
    END IF;
    IF NEW.improvement_finding_id IS NOT NULL
       AND OLD.improvement_finding_id IS NULL THEN
        IF to_regclass('agent_findings') IS NULL THEN
            RAISE EXCEPTION 'karen_challenges: no agent_findings table';
        END IF;
        EXECUTE 'SELECT proposer FROM agent_findings WHERE finding_id = $1'
           INTO f_proposer USING NEW.improvement_finding_id;
        IF f_proposer IS NULL THEN
            RAISE EXCEPTION 'karen_challenges: no improvement finding %',
                NEW.improvement_finding_id;
        END IF;
    END IF;
    IF NEW.improvement_proposal_id IS NOT NULL
       AND OLD.improvement_proposal_id IS NULL THEN
        IF to_regclass('paper_improvement_proposals') IS NULL THEN
            RAISE EXCEPTION 'karen_challenges: no proposals table';
        END IF;
        EXECUTE 'SELECT agent_id FROM paper_improvement_proposals '
                'WHERE proposal_id = $1'
           INTO f_proposer USING NEW.improvement_proposal_id;
        IF f_proposer IS NULL THEN
            RAISE EXCEPTION 'karen_challenges: no improvement proposal %',
                NEW.improvement_proposal_id;
        END IF;
    END IF;
    NEW.updated_at := greatest(OLD.updated_at, now());
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS karen_challenges_guard_trg ON karen_challenges;
CREATE TRIGGER karen_challenges_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON karen_challenges
    FOR EACH ROW EXECUTE FUNCTION karen_challenges_guard();

-- ── 5 · KAREN HAS NO AUTHORITY, IN THE DATABASE ──────────────────────
-- One trigger function, given the acting-identity columns of each table as
-- arguments: any of them naming Karen refuses the write.
CREATE OR REPLACE FUNCTION karen_refuses_authority() RETURNS trigger AS $$
DECLARE
    col  text;
    doc  jsonb := to_jsonb(NEW);
BEGIN
    FOREACH col IN ARRAY TG_ARGV LOOP
        IF karen_is_actor(doc->>col) THEN
            RAISE EXCEPTION 'KAREN_HAS_NO_AUTHORITY: % cannot be %.% -- Karen '
                            'challenges; she never approves, activates, '
                            'promotes, releases, decides an order or changes '
                            'a control', doc->>col, TG_TABLE_NAME, col;
        END IF;
    END LOOP;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

-- The guarded tables and their acting-identity columns. A table absent in a
-- given database is skipped (and guarded by a later re-run of this file).
CREATE OR REPLACE FUNCTION karen_authority_guarded_tables()
RETURNS TABLE (tbl text, cols text[]) LANGUAGE sql IMMUTABLE AS $$
    VALUES
      -- policy versions: candidates (write.policy_candidates) and approvals
      ('agent_policy_versions',              ARRAY['created_by', 'approved_by']),
      ('agent_policy_artifacts',             ARRAY['created_by', 'owner_approval_actor']),
      ('live_rule_artifacts',                ARRAY['created_by', 'owner_approval_actor']),
      ('bettor_funded_models',               ARRAY['approved_by']),
      ('calibration_lifecycles',             ARRAY['approved_by']),
      -- improvement candidates, releases (promotion) and paper activations
      ('improvement_candidates',             ARRAY['proposed_by', 'approved_by']),
      ('improvement_releases',               ARRAY['released_by']),
      ('paper_improvement_proposals',        ARRAY['proposed_by', 'activated_by']),
      ('paper_policy_parameter_activations', ARRAY['actor']),
      ('paper_policy_parameter_versions',    ARRAY['approved_by']),
      -- submission / mirror / bridge controls (submission switches)
      ('execmirror_control',                 ARRAY['actor']),
      ('kalshi_smalllive_control',           ARRAY['actor']),
      ('agent_slack_control_audit',          ARRAY['actor']),
      -- owner authorization (account authority) and directive confirmation
      ('bettor_funded_owner_authorization_audit',
                                             ARRAY['operator', 'authenticated_by']),
      ('management_directive_events',        ARRAY['actor_label']),
      -- entry decisions of record (the order path's verdicts)
      ('derek_entry_decisions',              ARRAY['decided_by'])
$$;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT * FROM karen_authority_guarded_tables() LOOP
        IF to_regclass(r.tbl) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS karen_no_authority_trg '
                           'ON %I', r.tbl);
            EXECUTE format(
                'CREATE TRIGGER karen_no_authority_trg BEFORE INSERT OR '
                'UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION '
                'karen_refuses_authority(%s)', r.tbl,
                (SELECT string_agg(quote_literal(c), ', ')
                   FROM unnest(r.cols) c));
        END IF;
    END LOOP;
END $$;

-- Karen may write tasks (write.agent_tasks) but never move one into an
-- approval, release or rollback state.
CREATE OR REPLACE FUNCTION karen_task_events_no_authority() RETURNS trigger
AS $$
BEGIN
    IF karen_is_actor(NEW.actor)
       AND (NEW.detail->>'status_to' IN ('APPROVAL_READY', 'APPROVED',
                                         'RELEASED', 'ROLLED_BACK')
            OR upper(NEW.kind) IN ('APPROVED', 'APPROVAL', 'RELEASED',
                                   'RELEASE', 'ACTIVATED', 'PROMOTED',
                                   'ROLLED_BACK')) THEN
        RAISE EXCEPTION 'KAREN_HAS_NO_AUTHORITY: Karen cannot move task % to '
                        '% (%)', NEW.task_id,
                        coalesce(NEW.detail->>'status_to', NEW.kind),
                        NEW.kind;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS karen_task_events_no_authority_trg
    ON agent_task_events;
CREATE TRIGGER karen_task_events_no_authority_trg
    BEFORE INSERT ON agent_task_events
    FOR EACH ROW EXECUTE FUNCTION karen_task_events_no_authority();
