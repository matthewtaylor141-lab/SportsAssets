-- ══════════════════════════════════════════════════════════════════════
-- 225 · BETTOR LIVE PARITY: ONE CANONICAL INTENT, TWO EXECUTION ADAPTERS
--       (SMALL LIVE IS SHADOW ONLY IN THIS MIGRATION)
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER REQUIREMENT (R30). Real-capital BETTOR-originated execution must use
-- the exact decision and management logic PAPER uses. It must NOT mirror
-- filled PAPER orders. PAPER and SMALL LIVE consume the SAME immutable
-- canonical decision intent and the SAME canonical Xavier management intent
-- through different execution adapters. Capital may be scaled; logic may
-- not differ.
--
--   canonical_decision_intents     ONE immutable row per qualified ENTER
--                                  decision: strategy + version, evidence
--                                  snapshot ids, opportunity score, Derek's
--                                  verdict, Karen's review state, Allie's
--                                  allocation, Eddie's executable-EV verdict,
--                                  side, venue, contract, limit, sizing basis,
--                                  created_at, and the sha256 of all of it.
--   canonical_management_intents   ONE immutable row per Xavier review of a
--                                  position: review/position/valuation ids,
--                                  evidence version, recommendation, target
--                                  quantity, target price/limit logic, the
--                                  ranked alternatives, freshness, reason.
--   canonical_intent_executions    what EACH adapter did with an intent: the
--                                  PAPER adapter (SIMULATED) and the SMALL
--                                  LIVE adapter (SHADOW). Each row names the
--                                  intent sha it consumed.
--   small_live_order_events        the SMALL LIVE venue lifecycle (REJECTED,
--                                  RESTING, PARTIAL, FILLED, CANCEL_PENDING,
--                                  CANCELLED) from the venue's own order
--                                  record. Nothing writes it while the mode
--                                  is SHADOW; a PAPER fill can never be one
--                                  (CHECK: source is the venue).
--   live_parity_ledger             per intent: PAPER vs SMALL LIVE, field by
--                                  field -> MATCHED | EXPECTED_SCALE_DIFFERENCE
--                                  | VENUE_EXECUTION_DIFFERENCE |
--                                  LOGIC_DIVERGENCE.
--   small_live_control             singleton. mode is CHECKed to 'SHADOW':
--                                  turning SMALL LIVE on needs a NEW
--                                  migration and the owner's explicit
--                                  approval. A LOGIC_DIVERGENCE halts it
--                                  (halted = true) in the same transaction;
--                                  clearing a halt needs a named human actor.
--   small_live_control_events      append-only audit of every control change.
--
--   live_parity_cutover            R30A: ONE append-only row PER DEPLOYMENT
--                                  of a release (was a singleton; a rollback
--                                  to an earlier sha appends a row of its
--                                  own): release / api / workers sha,
--                                  migrations, the decision-logic hash, the
--                                  hook install, the named human who recorded
--                                  it and the database's own clock. The
--                                  forward window starts at the latest release
--                                  whose decision-logic hash differs from its
--                                  predecessor's (live_parity_effective_
--                                  cutover).
--   live_approvals                 R30A: append-only owner LIVE approvals of a
--                                  policy version (keyed by its sha) or of a
--                                  live gate's configuration (keyed by its
--                                  config sha). Nothing in the application
--                                  writes it; a changed policy / gate config
--                                  no longer matches its approval.
--
-- R30A (this file was not yet deployed, so it is amended in place): the
-- decision intent gains opportunity_id, policy, probability, book,
-- risk_rails, binding_constraints, evidence_refs, latency_stages, expires_at
-- and expiry -- all inside its sha; the management intent gains the full
-- eight-alternative set, the chosen action, why, and the management policy.
--
-- APPEND-ONLY: every table except the control singleton refuses UPDATE,
-- DELETE and TRUNCATE. The control singleton refuses DELETE/TRUNCATE, refuses
-- any mode but SHADOW, and refuses clearing a halt without a human actor.
--
-- NO CAPITAL. Nothing here sends an order. execmirror_control, limits,
-- credentials, thresholds and strategy allowlists are untouched.
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. Idempotent.

-- ── shared append-only guard ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION live_parity_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'LIVE_PARITY_APPEND_ONLY: % on % is refused (append-only record)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END $$;

-- ── the named-human rule (one definition for every CHECK below) ────────
-- A halt clear, a cutover's recorded_by and an owner LIVE approval's
-- approved_by must be a NAMED HUMAN. The pattern is canonical_intent.
-- NON_HUMAN_ACTOR_PATTERN character for character (a test pins them equal):
-- an agent / system identity at the start; a machine word anywhere as a
-- whole word (bot, ci, cron, codex, assistant, openai, gpt, automation,
-- service, root, admin, scheduler, deploy, github, actions, worker ...); a
-- GitHub App `[bot]` suffix. R30A review: the R30 rule was the first part
-- alone, so 'github-actions[bot]', 'ci', 'cron', 'root' or 'admin' passed
-- as a named human.
CREATE OR REPLACE FUNCTION live_parity_named_human(actor text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT actor IS NOT NULL AND length(btrim(actor)) > 0
       AND btrim(actor) !~* '^(system|derek|xavier|audrey|karen|allie|chief_allocator|eddie|scout|bettor|claude|agent|migration|test_harness_system)|(^|[^a-z0-9])(bots?|ci|cron|codex|assistant|openai|gpt|chatgpt|anthropic|llm|copilot|automation|automated|service|svc|root|admin|administrator|scheduler|deploy|deployer|render|github|actions|worker|daemon|robot|script|pipeline|webhook|unknown|anonymous|none|null)([^a-z0-9]|$)|\[bot\]'
$$;

-- ── 1 · canonical decision intents ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonical_decision_intents (
    intent_id          text PRIMARY KEY,
    intent_version     text NOT NULL,
    decision_id        text NOT NULL UNIQUE,
    -- fixture | us_market_slug | holding_side | line | scope
    -- (canonical_intent.opportunity_key: the unique-opportunity funnel key)
    opportunity_id     text NOT NULL,
    strategy           text NOT NULL,
    strategy_version   text NOT NULL,
    -- the parameter version + its row sha + policy_sha (what a LIVE approval
    -- names); a PAPER shipped-default fallback is labelled here
    policy             jsonb NOT NULL,
    sleeve             text NOT NULL,
    evidence           jsonb NOT NULL,
    -- source, source version, observed / received stamps, age at decision
    probability        jsonb NOT NULL,
    -- book observation id, receipt stamp, age at decision, the entry rule
    book               jsonb NOT NULL,
    -- the rails in force: paper per-order cap, live rail, scale
    risk_rails         jsonb NOT NULL,
    -- Allie's binding_constraint / final_binding and the sizing stop
    binding_constraints jsonb NOT NULL,
    evidence_refs      jsonb NOT NULL,
    -- the latency chain's decision-side stages (R30A section 6)
    latency_stages     jsonb NOT NULL,
    opportunity_score  jsonb NOT NULL,
    derek              jsonb NOT NULL,
    karen              jsonb NOT NULL,
    allie              jsonb NOT NULL,
    eddie              jsonb NOT NULL,
    venue              text NOT NULL,
    us_market_slug     text NOT NULL,
    contract           jsonb NOT NULL,
    holding_side       text NOT NULL,
    order_intent       text NOT NULL,
    order_type         text NOT NULL,
    time_in_force      text NOT NULL,
    limit_price        numeric(12,6),
    wire_price         numeric(12,6) NOT NULL,
    target_qty         numeric(20,6) NOT NULL,
    sizing_basis       jsonb NOT NULL,
    created_at         timestamptz NOT NULL,
    -- THE VALIDITY WINDOW (canonical_intent.decision_expiry): the earlier of
    -- the probability stamp + its 30 s rule and the book receipt + the entry
    -- rule's book age. No adapter executes the intent after it; NULL (an
    -- underivable window, with its reason in `expiry`) refuses everywhere.
    expires_at         timestamptz,
    expiry             jsonb NOT NULL,
    content_sha        text NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cdi_opportunity_ck CHECK (
        opportunity_id ~ '^[^|]*\|[^|]+\|(LONG|SHORT)\|[^|]*\|[^|]*$'),
    CONSTRAINT cdi_policy_ck CHECK (
        policy ? 'policy_sha' AND policy ? 'parameters_source'),
    CONSTRAINT cdi_expiry_ck CHECK (
        expiry ? 'status'
        AND (expires_at IS NOT NULL) = (expiry->>'status' = 'DERIVED')),
    CONSTRAINT cdi_refs_ck CHECK (jsonb_typeof(evidence_refs) = 'array'),
    CONSTRAINT cdi_id_ck CHECK (intent_id ~ '^cdi_[0-9a-f]{24}$'),
    CONSTRAINT cdi_sha_ck CHECK (content_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cdi_side_ck CHECK (holding_side IN ('LONG', 'SHORT')),
    CONSTRAINT cdi_venue_ck CHECK (venue IN ('POLYMARKET')),
    CONSTRAINT cdi_qty_ck CHECK (target_qty > 0),
    CONSTRAINT cdi_wire_ck CHECK (wire_price > 0 AND wire_price < 1),
    CONSTRAINT cdi_sleeve_ck CHECK (sleeve IN ('INVESTMENT', 'TRAINING',
                                               'BENCHMARK', 'UNCLASSIFIED')),
    -- every agent component is either measured or carries its reason
    CONSTRAINT cdi_components_ck CHECK (
        opportunity_score ? 'status' AND derek ? 'verdict' AND karen ? 'state'
        AND allie ? 'status' AND eddie ? 'status')
);
CREATE INDEX IF NOT EXISTS cdi_created_idx ON canonical_decision_intents (created_at DESC);
CREATE INDEX IF NOT EXISTS cdi_strategy_idx ON canonical_decision_intents (strategy, created_at DESC);
CREATE INDEX IF NOT EXISTS cdi_opportunity_idx ON canonical_decision_intents (opportunity_id, created_at DESC);

-- ── 2 · canonical management intents ────────────────────────────────────
-- THE EIGHT ALTERNATIVES every review evaluates (canonical_intent.
-- ALTERNATIVES): exactly these keys, each EVALUATED or UNAVAILABLE with a
-- reason (R30A section 8: nothing dropped silently).
CREATE OR REPLACE FUNCTION cmi_alternative_set_ok(s jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_typeof(s) = 'object'
       AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(s) k)
           = ARRAY['CANCEL_PROTECTION_BEFORE_EXIT', 'HOLD', 'INDIRECT_HEDGE',
                   'MAINTAIN_STANDING_PROTECTION', 'NO_ORDER', 'REALLOCATE',
                   'SELL_EXIT', 'SELL_REDUCE']
       AND NOT EXISTS (
           SELECT 1 FROM jsonb_each(s) e
            WHERE NOT (e.value->>'status' = 'EVALUATED'
                       OR (e.value->>'status' = 'UNAVAILABLE'
                           AND coalesce(e.value->>'why', '') <> ''
                           -- R30A review: whether the evaluation RAN (an
                           -- evaluated fact) or was NOT_RUN (the comparison
                           -- is incomplete on it) -- parity reads this
                           AND e.value->>'evaluation' IN ('RAN', 'NOT_RUN'))))
$$;

CREATE TABLE IF NOT EXISTS canonical_management_intents (
    intent_id            text PRIMARY KEY,
    intent_version       text NOT NULL,
    review_id            text NOT NULL UNIQUE,
    group_id             text NOT NULL,
    position_key         text NOT NULL,
    strategy             text,
    sleeve               text NOT NULL,
    valuation_id         text,
    evidence_version     text,
    evidence_state       text NOT NULL,
    recommendation       text,
    mechanical_selection text,
    action               text NOT NULL,
    us_market_slug       text NOT NULL,
    holding_side         text NOT NULL,
    order_intent         text,
    target_qty           numeric(20,6),
    target_limit         jsonb NOT NULL,
    alternatives         jsonb NOT NULL,
    alternative_set      jsonb NOT NULL,
    chosen               text NOT NULL,
    chosen_why           jsonb NOT NULL,
    policy               jsonb NOT NULL,
    freshness            jsonb NOT NULL,
    reason               jsonb NOT NULL,
    created_at           timestamptz NOT NULL,
    content_sha          text NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cmi_alternative_set_ck CHECK (cmi_alternative_set_ok(alternative_set)),
    CONSTRAINT cmi_chosen_ck CHECK (chosen = action
                                    AND alternative_set ? chosen),
    CONSTRAINT cmi_id_ck CHECK (intent_id ~ '^cmi_[0-9a-f]{24}$'),
    CONSTRAINT cmi_sha_ck CHECK (content_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cmi_side_ck CHECK (holding_side IN ('LONG', 'SHORT')),
    CONSTRAINT cmi_sleeve_ck CHECK (sleeve IN ('INVESTMENT', 'TRAINING',
                                               'BENCHMARK', 'UNCLASSIFIED')),
    CONSTRAINT cmi_action_ck CHECK (action IN (
        'SELL_EXIT', 'SELL_REDUCE', 'CANCEL_PROTECTION_BEFORE_EXIT',
        'MAINTAIN_STANDING_PROTECTION', 'NO_ORDER')),
    -- a discretionary sale is only ever decided on FRESH evidence
    CONSTRAINT cmi_fresh_sale_ck CHECK (
        action NOT IN ('SELL_EXIT', 'SELL_REDUCE', 'CANCEL_PROTECTION_BEFORE_EXIT')
        OR evidence_state = 'FRESH_CURRENT_PROBABILITY'),
    CONSTRAINT cmi_sale_qty_ck CHECK (
        action NOT IN ('SELL_EXIT', 'SELL_REDUCE') OR (target_qty IS NOT NULL
                                                      AND target_qty > 0))
);
CREATE INDEX IF NOT EXISTS cmi_group_idx ON canonical_management_intents (group_id, created_at DESC);
CREATE INDEX IF NOT EXISTS cmi_created_idx ON canonical_management_intents (created_at DESC);

-- ── 3 · adapter executions ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonical_intent_executions (
    execution_id   text PRIMARY KEY,
    intent_kind    text NOT NULL,
    intent_id      text NOT NULL,
    intent_sha     text NOT NULL,
    adapter        text NOT NULL,
    mode           text NOT NULL,
    adapter_version text NOT NULL,
    state          text NOT NULL,
    exclusion      text,
    requested      jsonb NOT NULL,
    capital_scale  numeric(14,4) NOT NULL,
    venue_params   jsonb,
    refs           jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- the instant of the write itself (clock_timestamp, not the enclosing
    -- transaction's start): a long paper pass must not date its adapter
    -- records before a cutover recorded while it ran
    created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT cie_kind_ck CHECK (intent_kind IN ('DECISION', 'MANAGEMENT')),
    CONSTRAINT cie_sha_ck CHECK (intent_sha ~ '^[0-9a-f]{64}$'),
    -- THE ONLY TWO ADAPTER MODES THIS MIGRATION ADMITS. A LIVE mode needs a
    -- new migration and the owner's explicit approval.
    CONSTRAINT cie_mode_ck CHECK (
        (adapter = 'PAPER' AND mode = 'SIMULATED' AND capital_scale = 1)
        OR (adapter = 'SMALL_LIVE' AND mode = 'SHADOW' AND capital_scale >= 1)),
    CONSTRAINT cie_state_ck CHECK (state IN (
        'PAPER_SUBMITTED', 'PAPER_REFUSED', 'PAPER_NO_ORDER',
        'SHADOW_PROPOSED', 'SHADOW_EXCLUDED', 'SHADOW_NO_ORDER',
        'SHADOW_HALTED')),
    CONSTRAINT cie_state_adapter_ck CHECK (
        (adapter = 'PAPER' AND state LIKE 'PAPER_%')
        OR (adapter = 'SMALL_LIVE' AND state LIKE 'SHADOW_%')),
    -- a SHADOW proposal never carries a venue order id: nothing was sent
    CONSTRAINT cie_shadow_unsent_ck CHECK (
        mode <> 'SHADOW' OR NOT (refs ? 'venue_order_id')),
    CONSTRAINT cie_one_per_adapter UNIQUE (intent_id, adapter)
);
CREATE INDEX IF NOT EXISTS cie_created_idx ON canonical_intent_executions (created_at DESC);

-- ── 4 · SMALL LIVE venue lifecycle (written only by a LIVE adapter) ────
CREATE TABLE IF NOT EXISTS small_live_order_events (
    event_id        bigserial PRIMARY KEY,
    execution_id    text NOT NULL REFERENCES canonical_intent_executions (execution_id),
    venue_order_id  text NOT NULL,
    state           text NOT NULL,
    cum_qty         numeric(20,6) NOT NULL DEFAULT 0,
    avg_price       numeric(12,6),
    source          text NOT NULL,
    venue_record    jsonb NOT NULL,
    observed_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sloe_state_ck CHECK (state IN ('REJECTED', 'RESTING', 'PARTIAL',
                                              'FILLED', 'CANCEL_PENDING',
                                              'CANCELLED')),
    -- fills come ONLY from the venue's own order record, never from PAPER
    CONSTRAINT sloe_source_ck CHECK (source = 'VENUE_ORDER_RECORD')
);

-- ── 5 · parity ledger ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS live_parity_ledger (
    parity_id           text PRIMARY KEY,
    parity_version      text NOT NULL,
    intent_kind         text NOT NULL,
    intent_id           text NOT NULL UNIQUE,
    intent_sha          text NOT NULL,
    strategy            text,
    sleeve              text NOT NULL,
    paper_execution_id  text NOT NULL REFERENCES canonical_intent_executions (execution_id),
    live_execution_id   text NOT NULL REFERENCES canonical_intent_executions (execution_id),
    capital_scale       numeric(14,4) NOT NULL,
    parity_state        text NOT NULL,
    divergence_fields   text[] NOT NULL DEFAULT '{}',
    comparison          jsonb NOT NULL,
    -- the instant of the write (see canonical_intent_executions.created_at):
    -- the forward window compares it with the cutover's clock_timestamp
    created_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT lpl_kind_ck CHECK (intent_kind IN ('DECISION', 'MANAGEMENT')),
    -- INCOMPLETE_COMPARISON (R30A section 8): identical on every compared
    -- field, but the management alternative set was evaluated on NEITHER
    -- side for some alternative (the paper book runs no indirect-hedge
    -- search), so exact parity is NOT claimed. Never a match, never a halt.
    CONSTRAINT lpl_state_ck CHECK (parity_state IN (
        'MATCHED', 'EXPECTED_SCALE_DIFFERENCE', 'VENUE_EXECUTION_DIFFERENCE',
        'INCOMPLETE_COMPARISON', 'LOGIC_DIVERGENCE')),
    CONSTRAINT lpl_divergence_named_ck CHECK (
        parity_state <> 'LOGIC_DIVERGENCE' OR cardinality(divergence_fields) > 0),
    CONSTRAINT lpl_incomplete_ck CHECK (
        parity_state <> 'INCOMPLETE_COMPARISON'
        OR (intent_kind = 'MANAGEMENT'
            AND coalesce(comparison->>'why_not_exact', '') <> '')),
    CONSTRAINT lpl_sleeve_ck CHECK (sleeve IN ('INVESTMENT', 'TRAINING',
                                               'BENCHMARK', 'UNCLASSIFIED'))
);
CREATE INDEX IF NOT EXISTS lpl_created_idx ON live_parity_ledger (created_at DESC);
CREATE INDEX IF NOT EXISTS lpl_state_idx ON live_parity_ledger (parity_state, created_at DESC);

-- ── 6 · SMALL LIVE control (SHADOW only) and its audit ─────────────────
CREATE TABLE IF NOT EXISTS small_live_control (
    id              smallint PRIMARY KEY DEFAULT 1,
    mode            text NOT NULL DEFAULT 'SHADOW',
    halted          boolean NOT NULL DEFAULT false,
    halted_at       timestamptz,
    halt_reason     text,
    halt_parity_id  text,
    cleared_by      text,
    cleared_at      timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT slc_singleton_ck CHECK (id = 1),
    CONSTRAINT slc_shadow_only_ck CHECK (mode = 'SHADOW'),
    CONSTRAINT slc_halt_reason_ck CHECK (NOT halted OR (halt_reason IS NOT NULL
                                                        AND halted_at IS NOT NULL))
);
INSERT INTO small_live_control (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS small_live_control_events (
    event_id    bigserial PRIMARY KEY,
    action      text NOT NULL,
    actor       text NOT NULL,
    reason      text,
    parity_id   text,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT slce_action_ck CHECK (action IN ('HALT', 'CLEAR_HALT')),
    -- a halt is cleared only by a named human, never by the system or an agent
    CONSTRAINT slce_clear_actor_ck CHECK (
        action <> 'CLEAR_HALT' OR live_parity_named_human(actor))
);

CREATE OR REPLACE FUNCTION small_live_control_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'SMALL_LIVE_CONTROL_IS_PERMANENT: % refused', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.halted AND NOT NEW.halted
            AND NOT coalesce(live_parity_named_human(NEW.cleared_by), false) THEN
        RAISE EXCEPTION 'SMALL_LIVE_HALT_CLEAR_NEEDS_A_NAMED_HUMAN'
            USING ERRCODE = 'restrict_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS small_live_control_guard_trg ON small_live_control;
CREATE TRIGGER small_live_control_guard_trg BEFORE UPDATE OR DELETE
    ON small_live_control FOR EACH ROW EXECUTE FUNCTION small_live_control_guard();
DROP TRIGGER IF EXISTS small_live_control_truncate_trg ON small_live_control;
CREATE TRIGGER small_live_control_truncate_trg BEFORE TRUNCATE
    ON small_live_control FOR EACH STATEMENT EXECUTE FUNCTION small_live_control_guard();

-- ── 6b · hook installs and THE PRODUCTION CUTOVER, ONE ROW PER RELEASE ──
-- Each executing process records, at boot, the canonical hooks it installed
-- and the commit it runs. A CUTOVER is recorded per release, by
-- live_parity.record_cutover INSIDE THE SERVING PROCESS (POST /api/admin/
-- live-parity/cutover), only after it has verified in production: the
-- release SHA = the API's own commit = the workers' boot commit; migrations
-- 225 and 226 applied; the canonical decision + management hooks installed
-- by a process on that commit AND in the recording process itself; SMALL
-- LIVE SHADOW and not halted; no capital activated; the readback checks
-- passing; and the DECISION-LOGIC HASH (sha256 over a pinned list of
-- decision-path source files, computed from the running build).
--
-- WHY PER RELEASE (R30A section 31). The R30 singleton was written once and
-- then never again: a later release that CHANGED the decision logic would
-- still have counted the old logic's parity rows and forward profitability
-- as its own evidence, and a release that changed nothing could not be
-- recorded at all. Now every release appends its row; the forward window
-- (parity sample and INVESTMENT profitability) starts at the latest cutover
-- whose decision_logic_hash DIFFERS from its predecessor's
-- (live_parity_effective_cutover): a release that does not change decision
-- logic does not restart the sample, one that does restarts it.
--
-- recorded_at is the DATABASE's clock_timestamp() at insert (a trigger
-- overwrites any value a caller sends): a cutover instant is never chosen by
-- whoever records it. recorded_by is a named human (system / agent actors
-- are refused by CHECK).
CREATE TABLE IF NOT EXISTS live_parity_hook_installs (
    install_id    bigserial PRIMARY KEY,
    process       text NOT NULL,
    commit_sha    text,
    hooks         text[] NOT NULL,
    installed_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lphi_hooks_ck CHECK (cardinality(hooks) > 0)
);
CREATE INDEX IF NOT EXISTS lphi_commit_idx ON live_parity_hook_installs (commit_sha, installed_at DESC);

-- ONE ROW PER DEPLOYMENT OF A RELEASE, not per release sha. R30A review: with
-- release_sha UNIQUE a rollback (or a redeploy of an earlier release) could
-- not be recorded -- record_cutover returned `already` -- so after A -> B ->
-- A the effective cutover stayed on B and the forward window kept counting
-- A's logic as B's evidence. A sha may now appear again; what is refused
-- (trigger below) is recording the SAME release twice IN A ROW, which would
-- add nothing. A -> B -> A appends a third row, and when its logic hash
-- differs from B's the forward window restarts there.
CREATE TABLE IF NOT EXISTS live_parity_cutover (
    cutover_id           bigserial PRIMARY KEY,
    release_sha          text NOT NULL,
    api_sha              text NOT NULL,
    workers_sha          text NOT NULL,
    migrations           text[] NOT NULL,
    decision_logic_hash  text NOT NULL,
    decision_logic_files jsonb NOT NULL,
    hook_install_id      bigint NOT NULL REFERENCES live_parity_hook_installs (install_id),
    small_live_mode      text NOT NULL,
    small_live_halted    boolean NOT NULL,
    capital_activated    boolean NOT NULL,
    evidence             jsonb NOT NULL,
    recorded_by          text NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT lpc_sha_ck CHECK (release_sha ~ '^[0-9a-f]{40}$'),
    CONSTRAINT lpc_same_sha_ck CHECK (api_sha = release_sha AND workers_sha = release_sha),
    CONSTRAINT lpc_migrations_ck CHECK (migrations @> ARRAY['225', '226']),
    CONSTRAINT lpc_logic_hash_ck CHECK (decision_logic_hash ~ '^[0-9a-f]{64}$'
                                        AND jsonb_typeof(decision_logic_files) = 'object'),
    CONSTRAINT lpc_shadow_ck CHECK (small_live_mode = 'SHADOW' AND NOT small_live_halted),
    CONSTRAINT lpc_no_capital_ck CHECK (NOT capital_activated),
    CONSTRAINT lpc_named_human_ck CHECK (live_parity_named_human(recorded_by))
);
CREATE INDEX IF NOT EXISTS lpc_recorded_idx ON live_parity_cutover (recorded_at, cutover_id);
CREATE INDEX IF NOT EXISTS lpc_release_idx ON live_parity_cutover (release_sha, recorded_at DESC);

-- the database's own clock, and NO CONSECUTIVE DUPLICATE: the latest row
-- (by recorded_at, cutover_id) may not already be this release. Serialized
-- by a transaction-scoped advisory lock, so two concurrent recorders of the
-- same deployment cannot both append.
CREATE OR REPLACE FUNCTION live_parity_cutover_stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    last_sha text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('live_parity_cutover'));
    SELECT release_sha INTO last_sha FROM live_parity_cutover
     ORDER BY recorded_at DESC, cutover_id DESC LIMIT 1;
    IF last_sha IS NOT DISTINCT FROM NEW.release_sha THEN
        RAISE EXCEPTION 'LIVE_PARITY_CUTOVER_ALREADY_LATEST: release % is already the latest recorded cutover',
            NEW.release_sha USING ERRCODE = 'unique_violation';
    END IF;
    NEW.recorded_at := clock_timestamp();
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS live_parity_cutover_stamp_trg ON live_parity_cutover;
CREATE TRIGGER live_parity_cutover_stamp_trg BEFORE INSERT ON live_parity_cutover
    FOR EACH ROW EXECUTE FUNCTION live_parity_cutover_stamp();

-- THE EFFECTIVE CUTOVER: the latest release whose decision-logic hash
-- differs from its predecessor's (the first release differs from nothing).
-- cutover_at is its recorded_at. Every reader of the forward window
-- (live_parity.readiness_report, profitability validation) reads THIS view.
CREATE OR REPLACE VIEW live_parity_effective_cutover AS
SELECT x.*, x.recorded_at AS cutover_at
  FROM (SELECT c.*,
               lag(c.decision_logic_hash) OVER (ORDER BY c.recorded_at, c.cutover_id)
                   AS previous_logic_hash,
               count(*) OVER () AS releases_recorded
          FROM live_parity_cutover c) x
 WHERE x.previous_logic_hash IS DISTINCT FROM x.decision_logic_hash
 ORDER BY x.recorded_at DESC, x.cutover_id DESC
 LIMIT 1;

-- ── 6c · OWNER LIVE APPROVALS (policy versions, live gate configs) ─────
-- R30A sections 23 and 24. LIVE refuses new exposure unless the policy
-- version it would act under is approved FOR LIVE (a paper-only
-- authorization is not one), and the live book-currentness and
-- settlement-compatibility gates admit only under an approval of their
-- CURRENT configuration. Each approval names what it approves by sha:
--   POLICY_VERSION  subject_id = strategy, subject_version = strategy
--                   version | parameter version id, config_sha256 =
--                   canonical_intent.policy_block(...).policy_sha
--   LIVE_GATE       subject_id = the gate id, subject_version = its version,
--                   config_sha256 = live_approvals.config_sha256(gate): the sha
--                   of the gate's ENFORCED configuration, so a changed gate
--                   config no longer matches and the approval stops admitting
-- The latest row per (kind, subject, version) decides: APPROVE admits only
-- while its config_sha256 equals the code's; REVOKE withdraws. Append-only;
-- named human approvers only (CHECK); recorded_at is the database clock.
-- NOTHING IN THE APPLICATION WRITES THIS TABLE; no approval is created here.
CREATE TABLE IF NOT EXISTS live_approvals (
    approval_id      bigserial PRIMARY KEY,
    subject_kind     text NOT NULL,
    subject_id       text NOT NULL,
    subject_version  text NOT NULL,
    config_sha256    text NOT NULL,
    decision         text NOT NULL,
    approved_by      text NOT NULL,
    statement        text NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT la_kind_ck CHECK (subject_kind IN ('POLICY_VERSION', 'LIVE_GATE')),
    CONSTRAINT la_sha_ck CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT la_decision_ck CHECK (decision IN ('APPROVE', 'REVOKE')),
    CONSTRAINT la_statement_ck CHECK (length(btrim(statement)) > 0),
    CONSTRAINT la_named_human_ck CHECK (live_parity_named_human(approved_by))
);
CREATE INDEX IF NOT EXISTS la_subject_idx ON live_approvals
    (subject_kind, subject_id, subject_version, recorded_at DESC, approval_id DESC);

CREATE OR REPLACE FUNCTION live_approvals_stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.recorded_at := clock_timestamp();
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS live_approvals_stamp_trg ON live_approvals;
CREATE TRIGGER live_approvals_stamp_trg BEFORE INSERT ON live_approvals
    FOR EACH ROW EXECUTE FUNCTION live_approvals_stamp();

-- the decision in force per approved subject (latest row wins)
CREATE OR REPLACE VIEW live_approvals_current AS
SELECT DISTINCT ON (subject_kind, subject_id, subject_version) *
  FROM live_approvals
 ORDER BY subject_kind, subject_id, subject_version, recorded_at DESC,
          approval_id DESC;

-- ── 7 · append-only triggers ────────────────────────────────────────────
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['canonical_decision_intents',
                             'canonical_management_intents',
                             'canonical_intent_executions',
                             'small_live_order_events',
                             'live_parity_ledger',
                             'small_live_control_events',
                             'live_parity_hook_installs',
                             'live_parity_cutover',
                             'live_approvals'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION live_parity_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION live_parity_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;

-- ── 8 · Eddie and Scout hold no authority on the order path ────────────
-- Migration 217's registry of guarded tables, re-declared with the R30
-- order-path tables added (so installing AND rolling back the guard cover
-- them), then the guard attached exactly as 217 attaches it.
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
      -- R30A: the per-release cutover and the owner LIVE approvals
      ('live_parity_cutover',                ARRAY['recorded_by'], 'CONTROL'),
      ('live_approvals',                     ARRAY['approved_by'], 'APPROVAL')
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
              WHERE tbl IN ('canonical_decision_intents',
                            'canonical_management_intents',
                            'canonical_intent_executions',
                            'small_live_order_events', 'live_parity_ledger',
                            'small_live_control', 'small_live_control_events',
                            'live_parity_cutover', 'live_approvals')
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

-- ── 9 · the latency chain's read path (R30A section 6) ─────────────────
-- GET /api/command/live-parity/latency joins each canonical decision intent
-- to its paper ENTRY order and that order's first fill. R30A review: with no
-- index on paper_orders.decision_id or paper_fills.order_id every intent
-- cost a sequential scan of both tables (EXPLAIN: SubPlan Seq Scan on
-- paper_orders / paper_fills), so the read-only endpoint would time out at
-- production size. Plain indexes; nothing about the paper ledger changes.
CREATE INDEX IF NOT EXISTS paper_orders_decision_role_idx
    ON paper_orders (decision_id, role);
CREATE INDEX IF NOT EXISTS paper_fills_order_idx ON paper_fills (order_id);
