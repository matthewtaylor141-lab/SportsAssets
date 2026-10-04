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

-- ── 1 · canonical decision intents ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonical_decision_intents (
    intent_id          text PRIMARY KEY,
    intent_version     text NOT NULL,
    decision_id        text NOT NULL UNIQUE,
    strategy           text NOT NULL,
    strategy_version   text NOT NULL,
    sleeve             text NOT NULL,
    evidence           jsonb NOT NULL,
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
    content_sha        text NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
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

-- ── 2 · canonical management intents ────────────────────────────────────
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
    freshness            jsonb NOT NULL,
    reason               jsonb NOT NULL,
    created_at           timestamptz NOT NULL,
    content_sha          text NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT now(),
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
    created_at     timestamptz NOT NULL DEFAULT now(),
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
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT lpl_kind_ck CHECK (intent_kind IN ('DECISION', 'MANAGEMENT')),
    CONSTRAINT lpl_state_ck CHECK (parity_state IN (
        'MATCHED', 'EXPECTED_SCALE_DIFFERENCE', 'VENUE_EXECUTION_DIFFERENCE',
        'LOGIC_DIVERGENCE')),
    CONSTRAINT lpl_divergence_named_ck CHECK (
        parity_state <> 'LOGIC_DIVERGENCE' OR cardinality(divergence_fields) > 0),
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
        action <> 'CLEAR_HALT' OR (actor !~* '^(system|derek|xavier|audrey|karen|allie|chief_allocator|eddie|scout|bettor)'
                                   AND length(btrim(actor)) > 0))
);

CREATE OR REPLACE FUNCTION small_live_control_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'SMALL_LIVE_CONTROL_IS_PERMANENT: % refused', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.halted AND NOT NEW.halted AND (NEW.cleared_by IS NULL
            OR NEW.cleared_by ~* '^(system|derek|xavier|audrey|karen|allie|chief_allocator|eddie|scout|bettor)') THEN
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
                             'small_live_control_events'] LOOP
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
      ('small_live_control_events',          ARRAY['actor'], 'CONTROL')
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
                            'small_live_control', 'small_live_control_events')
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
