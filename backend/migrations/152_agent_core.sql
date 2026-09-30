-- THE THREE-AGENT OPERATING SYSTEM'S SHARED FOUNDATION (Derek / Xavier / Audrey).
--
-- WHAT THESE TABLES ARE, AND WHAT THEY ARE NOT.
--
--   They are an OPERATING RECORD for three named agents: who each agent is
--   (mandate, versioned policy/model/code identity, explicit tool
--   permissions), what each is doing now (a heartbeat with a truthful state),
--   what each ran, which policy versions exist, the tasks handed between them
--   and the owner, an INDEX of their decisions that LINKS to the authoritative
--   records, and the durable Derek -> Xavier position handoff.
--
--   They are NOT a second book. Economics live in bettor_funded_intents /
--   bettor_funded_fills / bettor_funded_economics and nowhere else.
--   `agent_decisions` carries references (evidence_refs) to the records that
--   hold the numbers; `agent_position_handoffs` carries quantities only as a
--   MIRROR of the fills ledger, recomputed from it on every write.
--
--   They are NOT an order path. Nothing here can submit, cancel or recover an
--   order; the one execution authority is unchanged.
--
-- THE HANDOFF'S ONE RULE: OWNERSHIP TRANSFERS ONLY ON CONFIRMED FILLED
-- QUANTITY. A row exists only once the fills ledger holds at least one ENTRY
-- fill for the entry intent (CHECK confirmed_qty > 0), never on intent creation
-- or acknowledgement. `agent_handoff_fills.fill_id` is the fills ledger's own
-- primary key, so a replay or a concurrent second consumer writes nothing new.
--
-- IDEMPOTENT: every object is IF NOT EXISTS / OR REPLACE, so a re-run is a
-- no-op.

BEGIN;

-- ── 1 · WHO EACH AGENT IS ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_identities (
    agent_id          text PRIMARY KEY
        CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY')),
    display_name      text NOT NULL,
    mandate           text NOT NULL,
    policy_version    text,
    model_version     text,
    code_version      text,
    -- EXPLICIT allow and deny lists. Audrey's deny list names every order
    -- tool, deploy and risk-limit write; no agent is granted risk limits,
    -- credentials, account authority or approval controls.
    tool_permissions  jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- ── 2 · WHAT EACH AGENT IS DOING NOW ─────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_status (
    agent_id              text PRIMARY KEY REFERENCES agent_identities(agent_id),
    state                 text NOT NULL
        CHECK (state IN ('IDLE', 'EVALUATING', 'WAITING_FOR_EVIDENCE',
                         'WAITING_FOR_PROVIDER', 'BLOCKED',
                         'DECISION_RECORDED', 'RECOVERING', 'FAILED')),
    activity              text,
    waiting_on            jsonb,
    dependencies          jsonb,
    last_heartbeat_at     timestamptz,
    last_run_started_at   timestamptz,
    last_run_finished_at  timestamptz,
    last_run_elapsed_s    numeric,
    runs                  bigint NOT NULL DEFAULT 0 CHECK (runs >= 0),
    errors                bigint NOT NULL DEFAULT 0 CHECK (errors >= 0),
    last_error            text,
    cadence               jsonb
);

-- ── 3 · WHAT EACH AGENT RAN (append-only; a run is finished once) ────
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id       text PRIMARY KEY,
    agent_id     text NOT NULL REFERENCES agent_identities(agent_id),
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz,
    outcome      text,
    summary      jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX IF NOT EXISTS agent_runs_agent_idx
    ON agent_runs (agent_id, started_at DESC);

CREATE OR REPLACE FUNCTION agent_runs_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'agent_runs is append-only: a run is never deleted';
    END IF;
    -- AN UPDATE MAY ONLY FINISH AN UNFINISHED RUN, once, and may not rewrite
    -- who ran it or when it started.
    IF OLD.finished_at IS NOT NULL THEN
        RAISE EXCEPTION 'agent_runs is append-only: run % is already finished',
            OLD.run_id;
    END IF;
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.agent_id IS DISTINCT FROM OLD.agent_id
       OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'agent_runs is append-only: identity of run % is fixed',
            OLD.run_id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_runs_append_only_trg ON agent_runs;
CREATE TRIGGER agent_runs_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION agent_runs_append_only();

-- ── 4 · POLICY VERSIONS: ONE ACTIVE PER (agent, key) ─────────────────
CREATE TABLE IF NOT EXISTS agent_policy_versions (
    agent_id     text NOT NULL REFERENCES agent_identities(agent_id),
    policy_key   text NOT NULL,
    version      text NOT NULL,
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    state        text NOT NULL
        CHECK (state IN ('ACTIVE', 'CANDIDATE', 'REJECTED', 'RETIRED')),
    created_by   text NOT NULL,
    approved_by  text,
    approved_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, policy_key, version),
    -- AN ACTIVE POLICY NAMES WHO APPROVED IT. A candidate cannot become
    -- binding by a state flip alone.
    CHECK (state <> 'ACTIVE' OR (approved_by IS NOT NULL
                                 AND approved_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_policy_one_active
    ON agent_policy_versions (agent_id, policy_key)
    WHERE state = 'ACTIVE';

-- ── 5 · TASKS AND THEIR APPEND-ONLY HISTORY ──────────────────────────
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id       text PRIMARY KEY,
    assignee      text NOT NULL REFERENCES agent_identities(agent_id),
    created_by    text NOT NULL,
    kind          text NOT NULL,
    title         text NOT NULL,
    spec          jsonb NOT NULL DEFAULT '{}'::jsonb,
    status        text NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'IN_PROGRESS', 'WAITING', 'CANDIDATE_READY',
                          'EVALUATING', 'REJECTED', 'APPROVAL_READY',
                          'APPROVED', 'RELEASED', 'ROLLED_BACK',
                          'CLOSED_NO_CHANGE', 'CANCELLED')),
    directive_id  text,
    evidence      jsonb NOT NULL DEFAULT '[]'::jsonb,
    outcome       jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_tasks_assignee_idx
    ON agent_tasks (assignee, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_task_events (
    event_id  bigserial PRIMARY KEY,
    task_id   text NOT NULL REFERENCES agent_tasks(task_id),
    at        timestamptz NOT NULL DEFAULT now(),
    kind      text NOT NULL,
    actor     text NOT NULL,
    detail    jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS agent_task_events_task_idx
    ON agent_task_events (task_id, event_id);

CREATE OR REPLACE FUNCTION agent_task_events_append_only() RETURNS trigger
AS $$
BEGIN
    RAISE EXCEPTION 'agent_task_events is append-only: % refused', TG_OP;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_task_events_append_only_trg ON agent_task_events;
CREATE TRIGGER agent_task_events_append_only_trg
    BEFORE UPDATE OR DELETE ON agent_task_events
    FOR EACH ROW EXECUTE FUNCTION agent_task_events_append_only();

-- ── 6 · THE DECISION INDEX (links, never copies) ─────────────────────
CREATE TABLE IF NOT EXISTS agent_decisions (
    decision_ref   text PRIMARY KEY,
    agent_id       text NOT NULL REFERENCES agent_identities(agent_id),
    kind           text NOT NULL,
    subject        text,
    decided_at     timestamptz NOT NULL,
    verdict        text,
    summary        jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- [{"kind": "<table or record kind>", "id": "<pk>", "href": "..."}]
    evidence_refs  jsonb NOT NULL DEFAULT '[]'::jsonb,
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (jsonb_typeof(evidence_refs) = 'array')
);
CREATE INDEX IF NOT EXISTS agent_decisions_agent_idx
    ON agent_decisions (agent_id, decided_at DESC);

-- ── 7 · THE DEREK -> XAVIER HANDOFF, ON CONFIRMED FILLS ONLY ─────────
CREATE TABLE IF NOT EXISTS agent_position_handoffs (
    entry_intent_id     text PRIMARY KEY,
    portfolio_group_id  text,
    from_agent          text NOT NULL DEFAULT 'DEREK'
        CHECK (from_agent = 'DEREK'),
    owner_agent         text NOT NULL DEFAULT 'XAVIER'
        CHECK (owner_agent = 'XAVIER'),
    ordered_qty         numeric NOT NULL CHECK (ordered_qty > 0),
    -- OWNERSHIP IS CONFIRMED QUANTITY. Zero confirmed is no handoff at all.
    confirmed_qty       numeric NOT NULL CHECK (confirmed_qty > 0),
    -- The entry order's remaining open obligation; 0 once it is terminal.
    outstanding_qty     numeric NOT NULL CHECK (outstanding_qty >= 0),
    first_fill_id       text NOT NULL,
    first_fill_at       timestamptz,
    last_fill_id        text NOT NULL,
    last_fill_at        timestamptz,
    handoff_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_position_handoffs_group_idx
    ON agent_position_handoffs (portfolio_group_id)
    WHERE portfolio_group_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_handoff_fills (
    -- THE FILLS LEDGER'S OWN IDENTITY (bettor_funded_fills.fill_id).
    fill_id          text PRIMARY KEY,
    entry_intent_id  text NOT NULL
        REFERENCES agent_position_handoffs(entry_intent_id),
    qty              numeric NOT NULL CHECK (qty > 0),
    filled_at        timestamptz,
    recorded_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_handoff_fills_intent_idx
    ON agent_handoff_fills (entry_intent_id);

COMMIT;
