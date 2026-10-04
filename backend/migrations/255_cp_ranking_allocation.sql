-- ══════════════════════════════════════════════════════════════════════
-- 255 · CONTROL PLANE (STREAM C): THE GLOBAL OPPORTUNITY RANKING AND THE
--       CAPITAL ALLOCATION -- SHADOW RECORDS ONLY
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER (control plane sections 3 and 5): rank every candidate of a batch
-- before scarce capital and execution are allocated, persist RANK, RAW_EV,
-- NET_EV and each component with FINAL_PRIORITY; size every candidate with
-- measurable expected utility and explain every size (REQUESTED_SIZE,
-- APPROVED_SIZE, SIZE_LIMITING_FACTOR, PORTFOLIO_EXPOSURE_AFTER).
--
--   cp_opportunity_rankings   one row per candidate per rank run: the rank,
--                             raw / net EV, the six components, the final
--                             priority, the gates, the admission and the
--                             starvation credit, with ranker version,
--                             config sha, code sha and model versions.
--   cp_capital_allocations    one row per ranked candidate: requested and
--                             approved size, the limiting factor and its
--                             kind, every cap, the exposure after, the Kelly
--                             fraction used (< 1: never raw full Kelly), the
--                             uncertainty haircuts and Allie's evaluation,
--                             with allocator version, config / rails / code
--                             sha and model versions.
--   cp_ranking_allocation_configs  the frozen RESEARCH configurations by
--                             sha (so every row's config_sha resolves).
--
-- AUTHORITY: every row is SHADOW (CHECKed). Nothing here changes an order,
-- a size, an admission order or a gate on the production path; nothing
-- reads these tables on a decision path. Approved rails, thresholds,
-- execmirror_control, small_live_control and strategy allowlists are
-- untouched.
--
-- APPEND-ONLY: UPDATE, DELETE and TRUNCATE are refused on all three.
-- Registered in migration 217's no-authority registry (as 225 section 8
-- does) so Eddie / Scout sessions are refused here too.
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. Idempotent.

CREATE OR REPLACE FUNCTION cp_ranking_allocation_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'CP_RANKING_ALLOCATION_APPEND_ONLY: % on % is refused (append-only record)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END $$;

-- ── 1 · the frozen research configurations ──────────────────────────────
CREATE TABLE IF NOT EXISTS cp_ranking_allocation_configs (
    config_sha   text PRIMARY KEY,
    config_kind  text NOT NULL,
    version      text NOT NULL,
    label        text NOT NULL,
    config       jsonb NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cprac_sha_ck CHECK (config_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cprac_kind_ck CHECK (config_kind IN ('CP_RANKER_CONFIG',
                                                    'CP_ALLOCATOR_CONFIG',
                                                    'CP_APPROVED_RAILS')),
    -- a research parameter set is labelled RESEARCH; the rails snapshot is
    -- labelled by the approved sources it was read from
    CONSTRAINT cprac_label_ck CHECK (
        (config_kind = 'CP_APPROVED_RAILS' AND label = 'APPROVED_RAILS_AS_READ')
        OR (config_kind <> 'CP_APPROVED_RAILS' AND label = 'RESEARCH'))
);

-- ── 2 · the rankings ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cp_opportunity_rankings (
    ranking_id                    text PRIMARY KEY,
    rank_run_id                   text NOT NULL,
    as_of                         timestamptz NOT NULL,
    opportunity_id                text NOT NULL,
    snapshot_id                   text NOT NULL,
    meta_decision_id              text,
    strategy                      text,
    strategy_class                text NOT NULL,
    agent                         text,
    rank                          integer NOT NULL,
    tier                          text NOT NULL,
    raw_ev                        numeric,
    net_ev                        numeric,
    confidence_component          numeric,
    execution_component           numeric,
    liquidity_component           numeric,
    capital_efficiency_component  numeric,
    risk_component                numeric,
    correlation_component         numeric,
    final_priority                numeric,
    components                    jsonb NOT NULL,
    gates_passed                  boolean NOT NULL,
    gate_failures                 text[] NOT NULL,
    admitted                      boolean NOT NULL,
    admission_basis               text NOT NULL,
    starvation_credit             integer NOT NULL DEFAULT 0,
    admission                     jsonb NOT NULL,
    ranker_version                text NOT NULL,
    config_sha                    text NOT NULL,
    code_sha                      text NOT NULL,
    model_versions                jsonb NOT NULL,
    source                        text NOT NULL,
    authority                     text NOT NULL DEFAULT 'SHADOW',
    recorded_at                   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cpor_ids_ck CHECK (ranking_id ~ '^cpr_[0-9a-f]{24}$'
                                  AND rank_run_id ~ '^cprr_[0-9a-f]{24}$'
                                  AND opportunity_id ~ '^opp_'
                                  AND snapshot_id ~ '^snap_'),
    CONSTRAINT cpor_rank_ck CHECK (rank > 0),
    CONSTRAINT cpor_one_rank_uq UNIQUE (rank_run_id, rank),
    CONSTRAINT cpor_one_row_uq UNIQUE (rank_run_id, opportunity_id, snapshot_id),
    CONSTRAINT cpor_tier_ck CHECK (tier IN ('RANKABLE', 'UNRANKABLE')),
    -- a rankable row has its economic base and a priority; an unrankable
    -- row has no priority and cannot pass the gates
    CONSTRAINT cpor_priority_ck CHECK (
        (tier = 'RANKABLE' AND final_priority IS NOT NULL
         AND net_ev IS NOT NULL AND capital_efficiency_component IS NOT NULL)
        OR (tier = 'UNRANKABLE' AND final_priority IS NULL
            AND NOT gates_passed)),
    -- factors are shares in [0, 1] when stated
    CONSTRAINT cpor_factor_ck CHECK (
        (confidence_component IS NULL OR confidence_component BETWEEN 0 AND 1)
        AND (execution_component IS NULL OR execution_component BETWEEN 0 AND 1)
        AND (liquidity_component IS NULL OR liquidity_component BETWEEN 0 AND 1)
        AND (risk_component IS NULL OR risk_component BETWEEN 0 AND 1)
        AND (correlation_component IS NULL
             OR correlation_component BETWEEN 0 AND 1)),
    CONSTRAINT cpor_gates_ck CHECK (gates_passed = (cardinality(gate_failures) = 0)),
    CONSTRAINT cpor_admission_ck CHECK (admission_basis IN (
        'RANK_TOP_K', 'CLASS_QUOTA', 'NOT_ADMITTED_CAPACITY',
        'NOT_ADMITTED_GATES')),
    -- only a candidate that clears EVERY gate is admitted, by rank or by
    -- its class quota; a gate failure is never admitted
    CONSTRAINT cpor_admitted_ck CHECK (
        (admitted AND gates_passed
         AND admission_basis IN ('RANK_TOP_K', 'CLASS_QUOTA'))
        OR (NOT admitted AND (
            (gates_passed AND admission_basis = 'NOT_ADMITTED_CAPACITY')
            OR (NOT gates_passed AND admission_basis = 'NOT_ADMITTED_GATES')))),
    -- the starvation credit marks a class-quota admission, nothing else
    CONSTRAINT cpor_starvation_ck CHECK (
        starvation_credit IN (0, 1)
        AND (starvation_credit = 1) = (admission_basis = 'CLASS_QUOTA')),
    CONSTRAINT cpor_versions_ck CHECK (
        ranker_version <> '' AND config_sha ~ '^[0-9a-f]{64}$'
        AND code_sha <> '' AND jsonb_typeof(model_versions) = 'object'),
    CONSTRAINT cpor_source_ck CHECK (source IN ('PRODUCTION_SHADOW',
                                                'PAPER_REPLAY', 'TEST')),
    CONSTRAINT cpor_authority_ck CHECK (authority = 'SHADOW'),
    -- a rank run is never recorded before its own clock (no future rows)
    CONSTRAINT cpor_clock_ck CHECK (as_of <= recorded_at)
);
CREATE INDEX IF NOT EXISTS cpor_run_idx ON cp_opportunity_rankings (rank_run_id, rank);
CREATE INDEX IF NOT EXISTS cpor_asof_idx ON cp_opportunity_rankings (source, as_of DESC);
CREATE INDEX IF NOT EXISTS cpor_opp_idx ON cp_opportunity_rankings (opportunity_id, as_of DESC);

-- ── 3 · the allocations ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cp_capital_allocations (
    allocation_id             text PRIMARY KEY,
    ranking_id                text NOT NULL REFERENCES cp_opportunity_rankings (ranking_id),
    rank_run_id               text NOT NULL,
    as_of                     timestamptz NOT NULL,
    opportunity_id            text NOT NULL,
    snapshot_id               text NOT NULL,
    meta_decision_id          text,
    strategy                  text,
    rank                      integer NOT NULL,
    requested_size            numeric,
    approved_size             numeric NOT NULL,
    approved_qty              numeric NOT NULL,
    size_limiting_factor      text NOT NULL,
    size_limiting_kind        text NOT NULL,
    size_limiting_why         text,
    caps                      jsonb NOT NULL,
    portfolio_exposure_after  jsonb NOT NULL,
    kelly_fraction_used       numeric,
    uncertainty_haircuts      jsonb NOT NULL,
    capital_efficiency        jsonb NOT NULL,
    expected_log_utility_gain numeric,
    allie                     jsonb NOT NULL,
    allie_basis               text NOT NULL,
    live_lane                 jsonb NOT NULL,
    lane                      text NOT NULL DEFAULT 'PAPER_CANONICAL',
    allocator_version         text NOT NULL,
    config_sha                text NOT NULL,
    rails_sha                 text,
    code_sha                  text NOT NULL,
    model_versions            jsonb NOT NULL,
    source                    text NOT NULL,
    authority                 text NOT NULL DEFAULT 'SHADOW',
    recorded_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cpca_ids_ck CHECK (allocation_id ~ '^cpa_[0-9a-f]{24}$'
                                  AND rank_run_id ~ '^cprr_[0-9a-f]{24}$'
                                  AND opportunity_id ~ '^opp_'
                                  AND snapshot_id ~ '^snap_'),
    CONSTRAINT cpca_one_uq UNIQUE (ranking_id),
    CONSTRAINT cpca_sizes_ck CHECK (approved_size >= 0 AND approved_qty >= 0),
    -- never more than requested; no requested size -> nothing approved
    CONSTRAINT cpca_request_ck CHECK (
        (requested_size IS NULL AND approved_size = 0)
        OR (requested_size IS NOT NULL
            AND approved_size <= requested_size + 0.000001)),
    CONSTRAINT cpca_kind_ck CHECK (size_limiting_kind IN (
        'RAIL', 'EVIDENCE', 'RESEARCH', 'KELLY', 'REQUEST', 'GATE')),
    -- a gate funds nothing
    CONSTRAINT cpca_gate_ck CHECK (size_limiting_kind <> 'GATE'
                                   OR approved_size = 0),
    -- NEVER RAW FULL KELLY: a funded size carries the fraction it used,
    -- strictly below one
    CONSTRAINT cpca_kelly_ck CHECK (
        (kelly_fraction_used IS NULL OR (kelly_fraction_used >= 0
                                         AND kelly_fraction_used < 1))
        AND (approved_size = 0 OR kelly_fraction_used IS NOT NULL)),
    CONSTRAINT cpca_explained_ck CHECK (
        size_limiting_factor <> ''
        AND jsonb_typeof(portfolio_exposure_after) = 'object'
        AND jsonb_typeof(caps) = 'object'),
    CONSTRAINT cpca_lane_ck CHECK (lane = 'PAPER_CANONICAL'),
    CONSTRAINT cpca_versions_ck CHECK (
        allocator_version <> '' AND config_sha ~ '^[0-9a-f]{64}$'
        AND code_sha <> '' AND jsonb_typeof(model_versions) = 'object'),
    CONSTRAINT cpca_source_ck CHECK (source IN ('PRODUCTION_SHADOW',
                                                'PAPER_REPLAY', 'TEST')),
    CONSTRAINT cpca_authority_ck CHECK (authority = 'SHADOW'),
    -- Allie's evaluation is evidence; her authority is never recorded as
    -- anything but pending the owner's decision
    CONSTRAINT cpca_allie_ck CHECK (
        allie ? 'status'
        AND coalesce(allie->>'authority', 'SHADOW_PENDING_OWNER_APPROVAL')
            = 'SHADOW_PENDING_OWNER_APPROVAL'),
    CONSTRAINT cpca_clock_ck CHECK (as_of <= recorded_at)
);
CREATE INDEX IF NOT EXISTS cpca_run_idx ON cp_capital_allocations (rank_run_id, rank);
CREATE INDEX IF NOT EXISTS cpca_asof_idx ON cp_capital_allocations (source, as_of DESC);

-- ── 4 · append-only ────────────────────────────────────────────────────
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['cp_ranking_allocation_configs',
                             'cp_opportunity_rankings',
                             'cp_capital_allocations'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION cp_ranking_allocation_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION cp_ranking_allocation_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;

-- ── 5 · Eddie and Scout hold no authority here either ───────────────────
-- Migration 217's registry of guarded tables, re-declared (exactly as 225
-- section 8 does) with this migration's tables added, then the guard
-- attached to them. INTEGRATION NOTE: other control-plane migrations
-- (253-259) re-declare the same registry; the integrated migration keeps
-- the UNION of every list.
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
      -- control plane stream C (migration 255): the ranking and the capital
      -- allocation records (capital allocation is a forbidden action for
      -- both agents -- pos_authority.FORBIDDEN_ACTIONS)
      ('cp_opportunity_rankings',            ARRAY['strategy'], 'DECISION'),
      ('cp_capital_allocations',             ARRAY['strategy'], 'DECISION'),
      ('cp_ranking_allocation_configs',      ARRAY[]::text[], 'DECISION')
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
              WHERE tbl IN ('cp_opportunity_rankings', 'cp_capital_allocations',
                            'cp_ranking_allocation_configs')
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
