-- ══════════════════════════════════════════════════════════════════════
-- 234 · DURABLE WORK QUEUES FOR ALL SEVEN AGENTS, LESSON RETRIEVAL AND
--       SUPERSESSION, ROOT-CAUSE IMPROVEMENT CLUSTERS
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECTS (owner R30 program sections 17-20; production read-only
-- evidence, research-sql run 37226555657, 2026-10-04 19:00Z):
--
--   * only Xavier had durable work (226: his fresh-evidence requests).
--     Derek's 22 stale-probability refusals in 24 h, Eddie's 95 estimates
--     without an outcome, Scout's 2 features under test, Karen's deferred
--     detector candidates, the 9 + 12 OPEN agent_tasks and every challenge
--     waiting for an answer or an evaluation were each a COUNT read at
--     display time: no owner, no SLA, no last / next attempt, nothing an
--     overdue item could be seen by;
--   * long-term lessons (agent_memory_events, 224; paper_agent_lessons,
--     185) are learned and never measured: nothing records which lesson was
--     in context for which decision, so a lesson that coincides with worse
--     outcomes can never be downweighted;
--   * Karen's HOLD_ON_STALE_PROBABILITY detector has 684 UPHELD challenges
--     -- 684 distinct reviews of 7 groups, one strategy -- and the
--     improvement pipeline seeded 232 separate items from them. Meanwhile
--     the defect itself ran at 9,978 stale HOLDs on 2026-10-04 alone: the
--     challenges are a throttled sample (3 per detector per pass), the
--     items are a reporting sink, and nothing measures whether a fix took.
--
-- WHAT THIS ADDS -- ON THE EXISTING 226 QUEUE, NOT BESIDE IT
--
--   §1 agent_work_requests (226) becomes every agent's queue. New columns
--      carry the owner's accountability terms on EVERY open item:
--        owner            agent_id (226)
--        blocker          what blocks it at enqueue (an ATTEMPTED event
--                         carries the current one)
--        SLA              due_at -- counted from when the work AROSE (a
--                         challenge's challenged_at, a decision's decided_at),
--                         so backlog older than the queue is overdue the
--                         moment it is enqueued; past it, an open item is
--                         OVERDUE (it stays open and visible; expires_at is
--                         the later hard horizon after which it is FAILED)
--        dependency       depends_on -> another request
--        last attempt     the newest ATTEMPTED / DISPATCHED event
--        next attempt     next_attempt_at (the newest ATTEMPTED event's,
--                         else the request's own)
--        evidence needed  evidence_needed (a non-empty array)
--        collaborator     the agent the work is shared with or handed from
--      A 226-shaped insert (Xavier's evidence requests, work_queue.py
--      unchanged) is completed by a BEFORE INSERT trigger: SLA = its expiry,
--      evidence = its kind, next attempt = its enqueue, blocker = its
--      reason. The kinds widen to one or two per agent, each owned by
--      exactly the agents the CHECK names (a pairing CHECK, not a
--      convention). (position_kind, group_id) is the 226 dedupe key; for
--      work that is not about a position it is the SUBJECT KIND and the
--      SUBJECT KEY -- so the one-open-item-per-(agent, subject, kind) slot of
--      agent_work_open dedupes every producer exactly as it deduped Xavier.
--   §1b agent_work_request_events gains ATTEMPTED (repeatable, bounded):
--      the consumer's attempt and its outcome (PROGRESSED / BLOCKED /
--      WAITING_FOR_FRESH_EVIDENCE / NO_CHANGE), the blocker when BLOCKED,
--      and the next attempt. Nothing follows a terminal event (226's guard).
--   §2 agent_lesson_retrievals     which lesson was in a decision's context
--                                  (point in time: never a lesson learned
--                                  after the decision), append-only.
--      agent_lesson_supersessions  the append-only DOWNWEIGHT / SUPERSEDE
--                                  record from forward INVESTMENT-sleeve
--                                  outcome evidence. A weight only ever
--                                  falls; nothing follows SUPERSEDE; the
--                                  record names the evaluator version, never
--                                  an agent or a person, and carries no
--                                  authority (CHECK).
--   §3 improvement_clusters        one ROOT-CAUSE engineering item per
--                                  repeated finding class (Karen detector x
--                                  target agent; Audrey finding kind).
--      improvement_cluster_events  its append-only status: OPENED (runner),
--                                  OWNER_ASSIGNED, FIX_LINKED (a 40-hex
--                                  commit SHA, by a named HUMAN /
--                                  ENGINEERING actor who also recorded it --
--                                  never a machine, never in the runner's
--                                  session), EFFECT_MEASURED (runner, the
--                                  measured before / after defect rate),
--                                  CLOSED (human only), REOPENED.
--      paper_xavier_reviews_at_idx a read-only time index for the rule
--                                  measurements over Xavier's reviews (the
--                                  cluster runner's before / after, the
--                                  agent scorecards' census, section 18).
--   §4 the 217 authority-guard registry gains the two governance tables
--      (agent_lesson_supersessions, improvement_cluster_events): a session
--      declared as Eddie or Scout can write neither, and neither can name
--      them. The QUEUE tables are deliberately NOT guarded: every agent,
--      Eddie and Scout included, must be able to enqueue and attempt its own
--      work; the queue carries no order, venue, capital or approval effect
--      (production_effect = 'NONE', 226's CHECK).
--
-- NO CAPITAL AUTHORITY. Records only. Nothing here places, cancels or sizes
-- an order; no freshness, EV, risk, settlement or allowlist threshold is
-- defined or changed. Memory never grants authority: a supersession can only
-- lower a lesson's weight, and no lesson weight is read by any order path.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / DROP ... IF EXISTS then ADD /
-- DROP TRIGGER IF EXISTS. New CHECKs on 226's tables are NOT VALID (they
-- bind every new row; a 226 row recorded before this migration is history
-- and is not rewritten -- the tables are append-only).

-- ══ §1 · THE QUEUE TERMS ON agent_work_requests ══════════════════════
ALTER TABLE agent_work_requests ADD COLUMN IF NOT EXISTS due_at timestamptz;
ALTER TABLE agent_work_requests ADD COLUMN IF NOT EXISTS blocker text;
ALTER TABLE agent_work_requests ADD COLUMN IF NOT EXISTS depends_on text
    REFERENCES agent_work_requests (request_id);
ALTER TABLE agent_work_requests ADD COLUMN IF NOT EXISTS collaborator text;
ALTER TABLE agent_work_requests ADD COLUMN IF NOT EXISTS evidence_needed jsonb;
ALTER TABLE agent_work_requests ADD COLUMN IF NOT EXISTS next_attempt_at
    timestamptz;

-- every agent's kinds (226's four stay Xavier's evidence requests)
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_kind_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT agent_work_requests_kind_ck
    CHECK (kind IN (
        'PROBABILITY', 'VENUE_BOOK', 'GAME_STATE', 'MANAGEMENT_REASSESSMENT',
        'CANDIDATE_FRESH_EVIDENCE', 'CHALLENGE_RESPONSE',
        'CHALLENGE_EVALUATION', 'CHALLENGE_INVESTIGATION',
        'ALLOCATION_REVIEW', 'EXECUTION_ESTIMATE', 'OUTCOME_CALIBRATION',
        'RESEARCH_QUESTION', 'AUDIT_RECONCILIATION', 'ROOT_CAUSE_TRIAGE'))
    NOT VALID;
-- the subject kinds (226: a position, PAPER / ACTUAL)
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_position_kind_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT
    agent_work_requests_position_kind_ck CHECK (position_kind IN (
        'PAPER', 'ACTUAL', 'MARKET', 'DECISION', 'CHALLENGE', 'RECORD',
        'ESTIMATE', 'FEATURE', 'RECONCILIATION', 'CLUSTER')) NOT VALID;
-- what raised it
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_reason_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT agent_work_requests_reason_ck
    CHECK (reason IN (
        'WAITING_FOR_FRESH_EVIDENCE', 'MANAGEMENT_UNAVAILABLE_STALE_INPUT',
        'REFUSED_ON_STALE_PROBABILITY', 'KAREN_CHALLENGE_OPEN',
        'CHALLENGE_RESPONDED', 'DETECTOR_CANDIDATE_DEFERRED',
        'ENTER_AFTER_LAST_ALLOCATION_RUN', 'ENTER_WITHOUT_ESTIMATE',
        'ESTIMATE_FILLED_WITHOUT_OUTCOME', 'FEATURE_UNDER_TEST',
        'RECONCILIATION_DISCREPANCY', 'ROOT_CAUSE_CLUSTER_OPEN')) NOT VALID;
-- 226's evidence requests keep their one-hour horizon; other work may stay
-- open (and overdue) up to thirty days before it is FAILED as expired
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_expiry_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT agent_work_requests_expiry_ck
    CHECK (expires_at > enqueued_at AND (
        (kind IN ('PROBABILITY', 'VENUE_BOOK', 'GAME_STATE',
                  'MANAGEMENT_REASSESSMENT')
         AND expires_at <= enqueued_at + interval '1 hour')
        OR (kind NOT IN ('PROBABILITY', 'VENUE_BOOK', 'GAME_STATE',
                         'MANAGEMENT_REASSESSMENT')
            AND expires_at <= enqueued_at + interval '30 days'))) NOT VALID;
-- WHO OWNS WHICH WORK, on which subject: one pairing per kind
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_owner_kind_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT
    agent_work_requests_owner_kind_ck CHECK (
        (kind IN ('PROBABILITY', 'VENUE_BOOK', 'GAME_STATE',
                  'MANAGEMENT_REASSESSMENT')
         AND position_kind IN ('PAPER', 'ACTUAL'))
        OR (kind = 'CANDIDATE_FRESH_EVIDENCE' AND agent_id = 'DEREK'
            AND position_kind = 'MARKET')
        OR (kind = 'CHALLENGE_RESPONSE' AND agent_id IN (
                'DEREK', 'XAVIER', 'AUDREY', 'CHIEF_ALLOCATOR')
            AND position_kind = 'CHALLENGE')
        OR (kind = 'CHALLENGE_EVALUATION' AND agent_id IN ('AUDREY', 'XAVIER')
            AND position_kind = 'CHALLENGE')
        OR (kind = 'CHALLENGE_INVESTIGATION' AND agent_id = 'KAREN'
            AND position_kind = 'RECORD')
        OR (kind = 'ALLOCATION_REVIEW' AND agent_id = 'CHIEF_ALLOCATOR'
            AND position_kind = 'DECISION')
        OR (kind = 'EXECUTION_ESTIMATE' AND agent_id = 'EDDIE'
            AND position_kind = 'DECISION')
        OR (kind = 'OUTCOME_CALIBRATION' AND agent_id = 'EDDIE'
            AND position_kind = 'ESTIMATE')
        OR (kind = 'RESEARCH_QUESTION' AND agent_id = 'SCOUT'
            AND position_kind = 'FEATURE')
        OR (kind = 'AUDIT_RECONCILIATION' AND agent_id = 'AUDREY'
            AND position_kind = 'RECONCILIATION')
        OR (kind = 'ROOT_CAUSE_TRIAGE' AND agent_id = 'AUDREY'
            AND position_kind = 'CLUSTER')) NOT VALID;
-- EVERY OPEN ITEM CARRIES ITS TERMS (the fill trigger supplies 226 rows')
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_terms_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT agent_work_requests_terms_ck
    CHECK (
        due_at IS NOT NULL AND next_attempt_at IS NOT NULL
        AND due_at <= expires_at
        AND due_at >= enqueued_at - interval '30 days'
        AND next_attempt_at >= enqueued_at
        AND evidence_needed IS NOT NULL
        AND jsonb_typeof(evidence_needed) = 'array'
        AND jsonb_array_length(evidence_needed) BETWEEN 1 AND 20) NOT VALID;
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_collaborator_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT
    agent_work_requests_collaborator_ck CHECK (
        collaborator IS NULL OR (collaborator IN (
            'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
            'SCOUT') AND collaborator <> agent_id)) NOT VALID;
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_depends_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT agent_work_requests_depends_ck
    CHECK (depends_on IS NULL OR depends_on <> request_id) NOT VALID;
ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS
    agent_work_requests_blocker_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT agent_work_requests_blocker_ck
    CHECK (blocker IS NULL OR length(btrim(blocker)) BETWEEN 1 AND 200)
    NOT VALID;
CREATE INDEX IF NOT EXISTS agent_work_requests_agent_kind_idx
    ON agent_work_requests (agent_id, kind, enqueued_at DESC);

-- a 226-shaped insert carries its terms too (work_queue.py is unchanged)
CREATE OR REPLACE FUNCTION agent_work_requests_terms_fill()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.due_at := coalesce(NEW.due_at, NEW.expires_at);
    NEW.next_attempt_at := coalesce(NEW.next_attempt_at, NEW.enqueued_at);
    NEW.evidence_needed := coalesce(NEW.evidence_needed,
                                    jsonb_build_array(NEW.kind));
    IF NEW.blocker IS NULL AND NEW.kind IN (
            'PROBABILITY', 'VENUE_BOOK', 'GAME_STATE',
            'MANAGEMENT_REASSESSMENT') THEN
        NEW.blocker := NEW.reason;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_work_requests_terms_fill_trg
    ON agent_work_requests;
CREATE TRIGGER agent_work_requests_terms_fill_trg
    BEFORE INSERT ON agent_work_requests
    FOR EACH ROW EXECUTE FUNCTION agent_work_requests_terms_fill();

-- ══ §1b · ATTEMPTS ON agent_work_request_events ═══════════════════════
ALTER TABLE agent_work_request_events ADD COLUMN IF NOT EXISTS outcome text;
ALTER TABLE agent_work_request_events ADD COLUMN IF NOT EXISTS blocker text;
ALTER TABLE agent_work_request_events ADD COLUMN IF NOT EXISTS
    next_attempt_at timestamptz;
ALTER TABLE agent_work_request_events DROP CONSTRAINT IF EXISTS
    agent_work_events_state_ck;
ALTER TABLE agent_work_request_events ADD CONSTRAINT
    agent_work_events_state_ck CHECK (state IN (
        'ENQUEUED', 'DISPATCHED', 'ATTEMPTED', 'COMPLETED', 'FAILED'))
    NOT VALID;
-- an ATTEMPT names its outcome and the next attempt (later than now); a
-- BLOCKED attempt names the blocker; no other event carries any of them
ALTER TABLE agent_work_request_events DROP CONSTRAINT IF EXISTS
    agent_work_events_attempt_ck;
ALTER TABLE agent_work_request_events ADD CONSTRAINT
    agent_work_events_attempt_ck CHECK (
        (state = 'ATTEMPTED'
         AND outcome IN ('PROGRESSED', 'BLOCKED',
                         'WAITING_FOR_FRESH_EVIDENCE', 'NO_CHANGE')
         AND next_attempt_at IS NOT NULL AND next_attempt_at > at
         AND (outcome <> 'BLOCKED'
              OR length(btrim(coalesce(blocker, ''))) BETWEEN 1 AND 200)
         AND (blocker IS NULL OR length(btrim(blocker)) BETWEEN 1 AND 200))
        OR (state <> 'ATTEMPTED' AND outcome IS NULL AND blocker IS NULL
            AND next_attempt_at IS NULL)) NOT VALID;

-- BOUNDED: at most this many attempts per request (a producer attempts an
-- item at most once per pass; this is the database's backstop)
CREATE OR REPLACE FUNCTION agent_work_attempt_bound()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state = 'ATTEMPTED' AND (
            SELECT count(*) FROM agent_work_request_events
             WHERE request_id = NEW.request_id AND state = 'ATTEMPTED')
            >= 2000 THEN
        RAISE EXCEPTION 'agent_work_request_events: % has reached its '
                        'attempt bound', NEW.request_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_work_events_attempt_bound_trg
    ON agent_work_request_events;
CREATE TRIGGER agent_work_events_attempt_bound_trg
    BEFORE INSERT ON agent_work_request_events
    FOR EACH ROW EXECUTE FUNCTION agent_work_attempt_bound();
CREATE INDEX IF NOT EXISTS agent_work_events_request_idx
    ON agent_work_request_events (request_id, at DESC, event_id DESC);

-- ══ §2 · LESSON RETRIEVAL AND SUPERSESSION ════════════════════════════
CREATE OR REPLACE FUNCTION agent_lesson_record_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused (a lesson''s record is '
                    'never rewritten)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

-- does the lesson exist, and is it the named agent's?
CREATE OR REPLACE FUNCTION agent_lesson_exists(tbl text, lid text,
                                               agent text)
RETURNS boolean LANGUAGE plpgsql STABLE AS $$
DECLARE hit boolean;
BEGIN
    IF tbl NOT IN ('agent_memory_events', 'paper_agent_lessons')
       OR to_regclass(tbl) IS NULL THEN
        RETURN false;
    END IF;
    IF tbl = 'agent_memory_events' THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM agent_memory_events '
                ' WHERE memory_id = $1 AND agent_id = $2)'
            INTO hit USING lid, agent;
    ELSE
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM paper_agent_lessons '
                ' WHERE lesson_id = $1 AND agent_id = $2)'
            INTO hit USING lid, agent;
    END IF;
    RETURN coalesce(hit, false);
END;
$$;

CREATE TABLE IF NOT EXISTS agent_lesson_retrievals (
    retrieval_id       text        PRIMARY KEY,
    agent_id           text        NOT NULL,
    lesson_table       text        NOT NULL,
    lesson_id          text        NOT NULL,
    lesson_learned_at  timestamptz NOT NULL,
    decision_table     text        NOT NULL,
    decision_id        text        NOT NULL,
    decided_at         timestamptz NOT NULL,
    retrieved_at       timestamptz NOT NULL,
    rank               integer     NOT NULL,
    weight             numeric     NOT NULL,
    strategy           text,
    relevance          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    retriever_version  text        NOT NULL,
    influence          text        NOT NULL DEFAULT 'NONE_RECORD_ONLY',
    production_effect  text        NOT NULL DEFAULT 'NONE',
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_lesson_retrievals_once UNIQUE (
        lesson_table, lesson_id, decision_table, decision_id),
    CONSTRAINT agent_lesson_retrievals_id_ck CHECK (
        retrieval_id ~ '^alr:[0-9a-f]{24}$'),
    CONSTRAINT agent_lesson_retrievals_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT')),
    CONSTRAINT agent_lesson_retrievals_lesson_ck CHECK (
        lesson_table IN ('agent_memory_events', 'paper_agent_lessons')
        AND length(btrim(lesson_id)) BETWEEN 1 AND 300),
    CONSTRAINT agent_lesson_retrievals_decision_ck CHECK (
        decision_table IN ('paper_decisions', 'paper_xavier_reviews')
        AND length(btrim(decision_id)) BETWEEN 1 AND 300),
    -- POINT IN TIME: a decision never "used" a lesson learned after it
    CONSTRAINT agent_lesson_retrievals_pit_ck CHECK (
        lesson_learned_at <= decided_at),
    CONSTRAINT agent_lesson_retrievals_rank_ck CHECK (rank BETWEEN 1 AND 10),
    CONSTRAINT agent_lesson_retrievals_weight_ck CHECK (
        weight > 0 AND weight <= 1),
    CONSTRAINT agent_lesson_retrievals_relevance_ck CHECK (
        jsonb_typeof(relevance) = 'object'),
    CONSTRAINT agent_lesson_retrievals_version_ck CHECK (
        retriever_version ~ '^LESSON_RETRIEVAL_V[0-9]+$'),
    -- MEMORY NEVER GRANTS AUTHORITY: the retrieval is a record, nothing
    -- reads it to decide
    CONSTRAINT agent_lesson_retrievals_influence_ck CHECK (
        influence = 'NONE_RECORD_ONLY'),
    CONSTRAINT agent_lesson_retrievals_effect_ck CHECK (
        production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS agent_lesson_retrievals_lesson_idx
    ON agent_lesson_retrievals (lesson_table, lesson_id, decided_at);
CREATE INDEX IF NOT EXISTS agent_lesson_retrievals_decision_idx
    ON agent_lesson_retrievals (decision_table, decision_id);

CREATE TABLE IF NOT EXISTS agent_lesson_supersessions (
    supersession_id    text        PRIMARY KEY,
    agent_id           text        NOT NULL,
    lesson_table       text        NOT NULL,
    lesson_id          text        NOT NULL,
    action             text        NOT NULL,
    previous_weight    numeric     NOT NULL,
    weight             numeric     NOT NULL,
    basis              text        NOT NULL,
    evidence           jsonb       NOT NULL,
    evidence_refs      jsonb       NOT NULL,
    decided_by         text        NOT NULL,
    decided_at         timestamptz NOT NULL,
    grants_authority   boolean     NOT NULL DEFAULT false,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_lesson_supersessions_id_ck CHECK (
        supersession_id ~ '^als:[0-9a-f]{24}$'),
    CONSTRAINT agent_lesson_supersessions_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT')),
    CONSTRAINT agent_lesson_supersessions_lesson_ck CHECK (
        lesson_table IN ('agent_memory_events', 'paper_agent_lessons')
        AND length(btrim(lesson_id)) BETWEEN 1 AND 300),
    -- A WEIGHT ONLY EVER FALLS: DOWNWEIGHT strictly below the previous
    -- weight (and above zero), SUPERSEDE to zero
    CONSTRAINT agent_lesson_supersessions_action_ck CHECK (
        previous_weight > 0 AND previous_weight <= 1
        AND ((action = 'DOWNWEIGHT' AND weight > 0
              AND weight < previous_weight)
             OR (action = 'SUPERSEDE' AND weight = 0))),
    CONSTRAINT agent_lesson_supersessions_basis_ck CHECK (
        basis = 'FORWARD_INVESTMENT_OUTCOMES'),
    CONSTRAINT agent_lesson_supersessions_evidence_ck CHECK (
        jsonb_typeof(evidence) = 'object'
        AND evidence ? 'n_used' AND evidence ? 'n_comparable'
        AND evidence ? 'mean_difference_usd'
        AND agent_memory_refs_grounded(evidence_refs)),
    -- the evaluator VERSION decided it -- never an agent, never a person
    CONSTRAINT agent_lesson_supersessions_decider_ck CHECK (
        decided_by ~ '^MEMORY_USEFULNESS_V[0-9]+$'),
    CONSTRAINT agent_lesson_supersessions_no_authority_ck CHECK (
        NOT grants_authority),
    CONSTRAINT agent_lesson_supersessions_effect_ck CHECK (
        production_effect = 'NONE')
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_lesson_supersessions_one_supersede
    ON agent_lesson_supersessions (lesson_table, lesson_id)
    WHERE action = 'SUPERSEDE';
CREATE INDEX IF NOT EXISTS agent_lesson_supersessions_lesson_idx
    ON agent_lesson_supersessions (lesson_table, lesson_id, decided_at DESC);

-- a retrieval names an existing lesson of its agent, never a superseded one
CREATE OR REPLACE FUNCTION agent_lesson_retrievals_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT agent_lesson_exists(NEW.lesson_table, NEW.lesson_id,
                               NEW.agent_id) THEN
        RAISE EXCEPTION 'agent_lesson_retrievals: % % is not a lesson of %',
            NEW.lesson_table, NEW.lesson_id, NEW.agent_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM agent_lesson_supersessions s
                WHERE s.lesson_table = NEW.lesson_table
                  AND s.lesson_id = NEW.lesson_id
                  AND s.action = 'SUPERSEDE'
                  AND s.decided_at <= NEW.decided_at) THEN
        RAISE EXCEPTION 'agent_lesson_retrievals: % was superseded before '
            'the decision', NEW.lesson_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_lesson_retrievals_guard_trg
    ON agent_lesson_retrievals;
CREATE TRIGGER agent_lesson_retrievals_guard_trg
    BEFORE INSERT ON agent_lesson_retrievals
    FOR EACH ROW EXECUTE FUNCTION agent_lesson_retrievals_guard();
DROP TRIGGER IF EXISTS agent_lesson_retrievals_append_only_trg
    ON agent_lesson_retrievals;
CREATE TRIGGER agent_lesson_retrievals_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_lesson_retrievals
    FOR EACH ROW EXECUTE FUNCTION agent_lesson_record_is_append_only();

-- the previous weight IS the lesson's current weight (1 before any
-- supersession), and nothing follows SUPERSEDE
CREATE OR REPLACE FUNCTION agent_lesson_supersessions_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur numeric;
BEGIN
    IF NOT agent_lesson_exists(NEW.lesson_table, NEW.lesson_id,
                               NEW.agent_id) THEN
        RAISE EXCEPTION 'agent_lesson_supersessions: % % is not a lesson '
            'of %', NEW.lesson_table, NEW.lesson_id, NEW.agent_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT weight INTO cur FROM agent_lesson_supersessions
     WHERE lesson_table = NEW.lesson_table AND lesson_id = NEW.lesson_id
     ORDER BY decided_at DESC, recorded_at DESC LIMIT 1;
    IF coalesce(cur, 1) = 0 THEN
        RAISE EXCEPTION 'agent_lesson_supersessions: % is superseded; '
            'nothing follows', NEW.lesson_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.previous_weight <> coalesce(cur, 1) THEN
        RAISE EXCEPTION 'agent_lesson_supersessions: % previous weight % '
            'is not its current weight %', NEW.lesson_id,
            NEW.previous_weight, coalesce(cur, 1)
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS agent_lesson_supersessions_guard_trg
    ON agent_lesson_supersessions;
CREATE TRIGGER agent_lesson_supersessions_guard_trg
    BEFORE INSERT ON agent_lesson_supersessions
    FOR EACH ROW EXECUTE FUNCTION agent_lesson_supersessions_guard();
DROP TRIGGER IF EXISTS agent_lesson_supersessions_append_only_trg
    ON agent_lesson_supersessions;
CREATE TRIGGER agent_lesson_supersessions_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_lesson_supersessions
    FOR EACH ROW EXECUTE FUNCTION agent_lesson_record_is_append_only();

-- ══ §3 · ROOT-CAUSE IMPROVEMENT CLUSTERS ═════════════════════════════
-- WHO IS A MACHINE, and NO AUTHORITY KEYS: copies of 221's
-- improve_is_machine_actor / improve_no_authority under this migration's
-- own names, so 234's CHECKs depend on nothing 221's rollback drops.
CREATE OR REPLACE FUNCTION agent_ops_is_machine_actor(v text)
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

CREATE OR REPLACE FUNCTION agent_ops_no_authority(doc jsonb)
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

CREATE TABLE IF NOT EXISTS improvement_clusters (
    cluster_id         text        PRIMARY KEY,
    cluster_key        text        NOT NULL UNIQUE,
    source             text        NOT NULL,
    finding_class      text        NOT NULL,
    target_agent       text,
    owner_agent        text        NOT NULL,
    title              text        NOT NULL,
    rule_ref           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at      timestamptz NOT NULL,
    opened_at          timestamptz NOT NULL,
    opened_by          text        NOT NULL,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    authority          text        NOT NULL
                       DEFAULT 'NO_DEPLOY_NO_MERGE_NO_CAPITAL',
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT improvement_clusters_id_ck CHECK (
        cluster_id ~ '^rcc:[0-9a-f]{24}$'),
    CONSTRAINT improvement_clusters_key_ck CHECK (
        length(btrim(cluster_key)) BETWEEN 3 AND 300
        AND cluster_key = source || '|' || finding_class
                          || '|' || coalesce(target_agent, '*')),
    CONSTRAINT improvement_clusters_source_ck CHECK (
        source IN ('KAREN', 'AUDREY')),
    CONSTRAINT improvement_clusters_class_ck CHECK (
        length(btrim(finding_class)) BETWEEN 1 AND 100),
    CONSTRAINT improvement_clusters_agents_ck CHECK (
        owner_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                        'CHIEF_ALLOCATOR', 'EDDIE', 'SCOUT')
        AND (target_agent IS NULL OR target_agent IN (
            'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
            'SCOUT'))),
    CONSTRAINT improvement_clusters_text_ck CHECK (
        length(btrim(title)) BETWEEN 1 AND 300
        AND jsonb_typeof(rule_ref) = 'object'),
    CONSTRAINT improvement_clusters_opener_ck CHECK (
        opened_by ~ '^IMPROVEMENT_CLUSTERS_V[0-9]+$'),
    CONSTRAINT improvement_clusters_window_ck CHECK (
        first_seen_at <= opened_at),
    CONSTRAINT improvement_clusters_effect_ck CHECK (
        production_effect = 'NONE'),
    CONSTRAINT improvement_clusters_authority_ck CHECK (
        authority = 'NO_DEPLOY_NO_MERGE_NO_CAPITAL')
);

CREATE TABLE IF NOT EXISTS improvement_cluster_events (
    event_id           bigserial   PRIMARY KEY,
    cluster_id         text        NOT NULL
                       REFERENCES improvement_clusters (cluster_id),
    kind               text        NOT NULL,
    status_to          text        NOT NULL,
    owner_agent        text,
    fix_commit_sha     text,
    fix_ref            text,
    fix_effective_at   timestamptz,
    effect             jsonb,
    note               text,
    actor              text        NOT NULL,
    actor_class        text        NOT NULL,
    recorded_by        text        NOT NULL,
    at                 timestamptz NOT NULL,
    production_effect  text        NOT NULL DEFAULT 'NONE',
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT improvement_cluster_events_kind_ck CHECK (kind IN (
        'OPENED', 'OWNER_ASSIGNED', 'FIX_LINKED', 'EFFECT_MEASURED',
        'CLOSED', 'REOPENED')),
    CONSTRAINT improvement_cluster_events_status_ck CHECK (
        (kind = 'OPENED' AND status_to = 'OPEN')
        OR (kind = 'OWNER_ASSIGNED' AND status_to IN ('OPEN', 'FIX_LINKED'))
        OR (kind = 'FIX_LINKED' AND status_to = 'FIX_LINKED')
        OR (kind = 'EFFECT_MEASURED' AND status_to IN (
                'FIX_LINKED', 'FIX_EFFECTIVE', 'FIX_NOT_EFFECTIVE'))
        OR (kind = 'CLOSED' AND status_to = 'CLOSED')
        OR (kind = 'REOPENED' AND status_to = 'OPEN')),
    CONSTRAINT improvement_cluster_events_class_ck CHECK (
        actor_class IN ('RUNNER', 'HUMAN', 'ENGINEERING')),
    CONSTRAINT improvement_cluster_events_actor_ck CHECK (
        length(btrim(actor)) BETWEEN 2 AND 100
        AND length(btrim(recorded_by)) BETWEEN 2 AND 100),
    -- the runner opens and measures; a person links a fix, assigns, closes
    -- and reopens -- and a person is never a machine name and is who
    -- recorded the row (agent_ops_is_machine_actor = 221's rule)
    CONSTRAINT improvement_cluster_events_provenance_ck CHECK (
        (kind IN ('OPENED', 'EFFECT_MEASURED')
         AND actor_class = 'RUNNER'
         AND actor ~ '^IMPROVEMENT_CLUSTERS_V[0-9]+$')
        OR (kind NOT IN ('OPENED', 'EFFECT_MEASURED')
            AND actor_class IN ('HUMAN', 'ENGINEERING')
            AND NOT agent_ops_is_machine_actor(actor)
            AND upper(btrim(recorded_by)) = upper(btrim(actor)))),
    -- THE LINKED FIX IS A REFERENCE (a commit SHA and when it took
    -- effect), never an instruction; only FIX_LINKED carries one
    CONSTRAINT improvement_cluster_events_fix_ck CHECK (
        (kind = 'FIX_LINKED' AND fix_commit_sha ~ '^[0-9a-f]{40}$'
         AND fix_effective_at IS NOT NULL
         AND (fix_ref IS NULL OR length(btrim(fix_ref)) BETWEEN 1 AND 300))
        OR (kind <> 'FIX_LINKED' AND fix_commit_sha IS NULL
            AND fix_effective_at IS NULL AND fix_ref IS NULL)),
    CONSTRAINT improvement_cluster_events_effect_doc_ck CHECK (
        (kind = 'EFFECT_MEASURED' AND jsonb_typeof(effect) = 'object'
         AND agent_ops_no_authority(effect))
        OR (kind <> 'EFFECT_MEASURED' AND effect IS NULL)),
    CONSTRAINT improvement_cluster_events_owner_ck CHECK (
        (kind <> 'OWNER_ASSIGNED' OR owner_agent IS NOT NULL)
        AND (owner_agent IS NULL OR owner_agent IN (
            'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
            'SCOUT'))),
    CONSTRAINT improvement_cluster_events_note_ck CHECK (
        note IS NULL OR length(btrim(note)) BETWEEN 1 AND 2000),
    CONSTRAINT improvement_cluster_events_effect_none_ck CHECK (
        production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS improvement_cluster_events_cluster_idx
    ON improvement_cluster_events (cluster_id, at, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS improvement_cluster_events_one_opened
    ON improvement_cluster_events (cluster_id) WHERE kind = 'OPENED';

-- THE RULE TABLE'S TIME INDEX. The cluster runner's before / after rule
-- measurement (improvement_clusters.rule_rate) and the scorecards' Xavier
-- false-approval census (agent_scorecards, owner R30 section 18) read
-- paper_xavier_reviews by reviewed_at over a window; the table's only
-- secondary index is (group_id, reviewed_at DESC), so each such read was a
-- sequential scan of every review ever written (production: ~16,000 a day,
-- research-sql run 37226555657). A plain b-tree that only reads use;
-- nothing decides from it.
CREATE INDEX IF NOT EXISTS paper_xavier_reviews_at_idx
    ON paper_xavier_reviews (reviewed_at);

-- ORDER: OPENED first; after CLOSED only REOPENED; EFFECT_MEASURED only
-- after a FIX_LINKED (since the last REOPENED); a session that declared
-- itself the cluster runner writes no HUMAN / ENGINEERING row
CREATE OR REPLACE FUNCTION improvement_cluster_events_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE last_kind text;
DECLARE last_reopen timestamptz;
BEGIN
    IF NEW.actor_class IN ('HUMAN', 'ENGINEERING') AND coalesce(
            current_setting('bettor.cluster_runner', true), '') = 'on' THEN
        RAISE EXCEPTION 'improvement_cluster_events: the cluster runner '
            'records no % step', NEW.actor_class
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT kind INTO last_kind FROM improvement_cluster_events
     WHERE cluster_id = NEW.cluster_id ORDER BY at DESC, event_id DESC
     LIMIT 1;
    IF NEW.kind = 'OPENED' THEN
        IF last_kind IS NOT NULL THEN
            RAISE EXCEPTION 'improvement_cluster_events: % is already open',
                NEW.cluster_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF last_kind IS NULL THEN
        RAISE EXCEPTION 'improvement_cluster_events: % before OPENED',
            NEW.kind USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF last_kind = 'CLOSED' AND NEW.kind <> 'REOPENED' THEN
        RAISE EXCEPTION 'improvement_cluster_events: % is closed; only '
            'REOPENED follows', NEW.cluster_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.kind = 'REOPENED' AND last_kind <> 'CLOSED' THEN
        RAISE EXCEPTION 'improvement_cluster_events: only a closed cluster '
            'reopens' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.kind = 'EFFECT_MEASURED' THEN
        SELECT max(at) INTO last_reopen FROM improvement_cluster_events
         WHERE cluster_id = NEW.cluster_id AND kind = 'REOPENED';
        IF NOT EXISTS (SELECT 1 FROM improvement_cluster_events
                        WHERE cluster_id = NEW.cluster_id
                          AND kind = 'FIX_LINKED'
                          AND (last_reopen IS NULL OR at >= last_reopen)) THEN
            RAISE EXCEPTION 'improvement_cluster_events: an effect is '
                'measured only after a linked fix'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS improvement_cluster_events_guard_trg
    ON improvement_cluster_events;
CREATE TRIGGER improvement_cluster_events_guard_trg
    BEFORE INSERT ON improvement_cluster_events
    FOR EACH ROW EXECUTE FUNCTION improvement_cluster_events_guard();

CREATE OR REPLACE FUNCTION improvement_cluster_record_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused (a root-cause record is '
                    'never rewritten)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
DROP TRIGGER IF EXISTS improvement_clusters_append_only_trg
    ON improvement_clusters;
CREATE TRIGGER improvement_clusters_append_only_trg
    BEFORE UPDATE OR DELETE ON improvement_clusters
    FOR EACH ROW EXECUTE FUNCTION improvement_cluster_record_is_append_only();
DROP TRIGGER IF EXISTS improvement_cluster_events_append_only_trg
    ON improvement_cluster_events;
CREATE TRIGGER improvement_cluster_events_append_only_trg
    BEFORE UPDATE OR DELETE ON improvement_cluster_events
    FOR EACH ROW EXECUTE FUNCTION improvement_cluster_record_is_append_only();

-- ══ §4 · THE AUTHORITY-GUARD REGISTRY (217), EXTENDED ═════════════════
-- Migration 225's registry, re-declared with the two R30B governance tables
-- added (so installing AND rolling back the guard cover them), then the
-- guard attached to those two exactly as 217 attaches it. INTEGRATION
-- NOTE: this list must stay a superset of 225's (a test parses both files
-- and asserts it).
CREATE OR REPLACE FUNCTION pos_agents_authority_guarded_tables()
RETURNS TABLE (tbl text, cols text[], kind text) LANGUAGE sql IMMUTABLE AS $$
    VALUES
      ('agent_policy_versions',              ARRAY['created_by', 'approved_by'], 'APPROVAL'),
      ('agent_policy_artifacts',             ARRAY['created_by', 'owner_approval_actor'], 'APPROVAL'),
      ('live_rule_artifacts',                ARRAY['created_by', 'owner_approval_actor'], 'APPROVAL'),
      ('bettor_funded_models',               ARRAY['approved_by'], 'APPROVAL'),
      ('calibration_lifecycles',             ARRAY['approved_by'], 'APPROVAL'),
      ('improvement_candidates',             ARRAY['proposed_by', 'approved_by'], 'PROMOTION'),
      ('improvement_releases',               ARRAY['released_by'], 'PROMOTION'),
      ('paper_improvement_proposals',        ARRAY['proposed_by', 'activated_by'], 'ACTIVATION'),
      ('paper_policy_parameter_activations', ARRAY['actor'], 'ACTIVATION'),
      ('paper_policy_parameter_versions',    ARRAY['approved_by'], 'APPROVAL'),
      ('execmirror_control',                 ARRAY['actor'], 'CONTROL'),
      ('kalshi_smalllive_control',           ARRAY['actor'], 'CONTROL'),
      ('agent_slack_control_audit',          ARRAY['actor'], 'CONTROL'),
      ('paper_control',                      ARRAY[]::text[], 'CONTROL'),
      ('bettor_funded_owner_authorization_audit',
                                             ARRAY['operator', 'authenticated_by'], 'CONTROL'),
      ('management_directive_events',        ARRAY['actor_label'], 'CONTROL'),
      ('derek_entry_decisions',              ARRAY['decided_by'], 'DECISION'),
      -- the order path: orders, intents, fills, their events and plans
      ('paper_orders',                       ARRAY['strategy', 'event_source'], 'ORDER'),
      ('paper_order_events',                 ARRAY['event_source'], 'ORDER'),
      ('paper_fills',                        ARRAY['strategy', 'event_source'], 'ORDER'),
      ('execution_intents',                  ARRAY['strategy'], 'ORDER'),
      ('bettor_funded_intents',              ARRAY[]::text[], 'ORDER'),
      ('bettor_funded_fills',                ARRAY[]::text[], 'ORDER'),
      ('live_orders',                        ARRAY['lane'], 'ORDER'),
      ('mirror_orders',                      ARRAY[]::text[], 'ORDER'),
      ('execmirror_orders',                  ARRAY['strategy'], 'ORDER'),
      ('execmirror_fills',                   ARRAY['source'], 'ORDER'),
      ('kalshi_live_intents',                ARRAY[]::text[], 'ORDER'),
      ('kalshi_live_fills',                  ARRAY['source'], 'ORDER'),
      ('bettor_desk_orders',                 ARRAY[]::text[], 'ORDER'),
      ('bettor_desk_order_events',           ARRAY[]::text[], 'ORDER'),
      ('bettor_desk_fills',                  ARRAY[]::text[], 'ORDER'),
      ('bettor_standing_order_plans',        ARRAY[]::text[], 'ORDER'),
      ('bettor_standing_order_events',       ARRAY['source'], 'ORDER'),
      ('rn1x_orders',                        ARRAY[]::text[], 'ORDER'),
      ('rn1x_fills',                         ARRAY[]::text[], 'ORDER'),
      -- R30 (migration 225): the canonical intents, both adapters' records,
      -- the parity ledger and the SMALL LIVE control
      ('canonical_decision_intents',         ARRAY['strategy'], 'ORDER'),
      ('canonical_management_intents',       ARRAY['strategy'], 'ORDER'),
      ('canonical_intent_executions',        ARRAY[]::text[], 'ORDER'),
      ('small_live_order_events',            ARRAY['source'], 'ORDER'),
      ('live_parity_ledger',                 ARRAY['strategy'], 'ORDER'),
      ('small_live_control',                 ARRAY['cleared_by'], 'CONTROL'),
      ('small_live_control_events',          ARRAY['actor'], 'CONTROL'),
      -- R30B (migration 234): a lesson's downweight / supersession and a
      -- root-cause cluster's status, owner and linked fix are governance
      -- records -- Eddie and Scout neither write nor are named by them
      ('agent_lesson_supersessions',         ARRAY['decided_by'], 'DECISION'),
      ('improvement_cluster_events',         ARRAY['actor', 'recorded_by'], 'PROMOTION')
$$;

DO $$
DECLARE
    r record;
    c text;
    usable text[];
BEGIN
    IF to_regproc('pos_agents_refuse_authority') IS NULL THEN
        RETURN;
    END IF;
    FOR r IN SELECT * FROM pos_agents_authority_guarded_tables()
              WHERE tbl IN ('agent_lesson_supersessions',
                            'improvement_cluster_events')
    LOOP
        usable := ARRAY[]::text[];
        FOREACH c IN ARRAY r.cols LOOP
            IF EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = r.tbl AND column_name = c) THEN
                usable := usable || c;
            END IF;
        END LOOP;
        EXECUTE format('DROP TRIGGER IF EXISTS aa_pos_agents_no_authority_trg '
                       'ON %I', r.tbl);
        EXECUTE format(
            'CREATE TRIGGER aa_pos_agents_no_authority_trg BEFORE INSERT OR '
            'UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION '
            'pos_agents_refuse_authority(%s)', r.tbl,
            coalesce((SELECT string_agg(quote_literal(x), ', ')
                        FROM unnest(usable) x), ''));
    END LOOP;
END $$;
