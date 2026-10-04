-- ══════════════════════════════════════════════════════════════════════
-- 216 · THE PROFITABILITY OPERATING SYSTEM, PART 1 (RESEARCH / SHADOW)
--       profitability data warehouse (lineage by position), capital-hour
--       economics, capacity model, five north-star metrics, predictable
--       monthly revenue engine (forecasts persisted for forward scoring)
-- ══════════════════════════════════════════════════════════════════════
--
-- NORTH STAR: repeatable forward net economic profit per unit of risk and
-- capital-time -- not trade count, win rate or headline EV.
--
-- EVERYTHING HERE IS RESEARCH WITH NO AUTHORITY. The runner
-- (sportsassets/profitability/runner.py) reads the existing records,
-- computes, and writes ONLY these pos_* tables. It has no venue, order,
-- sizing, limit, threshold or capital authority. The database says so:
--
--   * every table carries label = 'RESEARCH' and
--     authority = 'SHADOW_NO_AUTHORITY' (CHECK);
--   * every table is APPEND-ONLY: UPDATE and DELETE are refused by a
--     trigger (a later fact is a new row / a new revision);
--   * book is PAPER | ACTUAL | COUNTERFACTUAL (CHECK) and the layer never
--     sums across books; a COUNTERFACTUAL row names its kind and the real
--     position it is the counterfactual of (CHECK);
--   * an unmeasured value is NULL with a named reason in `unmeasured`
--     (CHECK on the headline columns), never 0;
--   * a forecast is UNPROVEN until its own forward calibration validates
--     it, and is persisted so it can be scored against what happened.
--
-- LINEAGE, NOT COPIES. pos_lineage holds, per position and revision, the
-- source record ids of every stage (candidate -> probability -> decision ->
-- execution intent -> order -> fill -> position -> management review ->
-- settlement -> P&L) and of the context (agent actions, challenges,
-- features, model / policy / experiment versions, counterfactuals, risk,
-- capacity, regime). The rows themselves stay where they are.
-- pos_paper_chain_v is a view over the existing paper records.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE. No BEGIN/COMMIT of its own: the
-- migration runner applies the file inside one transaction with its
-- schema_migrations row.

-- ── ONE ROW PER COMPONENT PER RUN ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS pos_runs (
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz NOT NULL,
    status              text        NOT NULL,
    error               text,
    duration_ms         integer,
    summary             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, component),
    CONSTRAINT pos_runs_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT pos_runs_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT pos_runs_status_ck CHECK (status IN (
        'OK', 'FAILED', 'TIMEOUT', 'SKIPPED')),
    CONSTRAINT pos_runs_component_ck CHECK (component IN (
        'CYCLE', 'ECONOMICS', 'WAREHOUSE', 'CAPACITY', 'NORTH_STAR',
        'CAPITAL', 'FORECAST'))
);
CREATE INDEX IF NOT EXISTS pos_runs_at_idx
    ON pos_runs (component, started_at DESC);

-- ── 1 · THE WAREHOUSE: SOURCE-RECORD LINEAGE PER POSITION, BY REVISION ─
CREATE TABLE IF NOT EXISTS pos_lineage (
    lineage_id          text PRIMARY KEY,
    book                text        NOT NULL,
    position_key        text        NOT NULL,
    revision            integer     NOT NULL,
    run_id              text        NOT NULL,
    built_at            timestamptz NOT NULL,
    venue               text,
    account_id          text,
    group_id            text,
    us_market_slug      text,
    holding_side        text,
    strategy            text,
    opened_at           timestamptz,
    closed_at           timestamptz,
    state               text        NOT NULL,
    -- the chain
    valuation_ids       bigint[]    NOT NULL DEFAULT '{}',
    decision_ids        text[]      NOT NULL DEFAULT '{}',
    intent_ids          text[]      NOT NULL DEFAULT '{}',
    order_ids           text[]      NOT NULL DEFAULT '{}',
    fill_ids            text[]      NOT NULL DEFAULT '{}',
    book_obs_ids        bigint[]    NOT NULL DEFAULT '{}',
    settlement_ids      text[]      NOT NULL DEFAULT '{}',
    ledger_seqs         bigint[]    NOT NULL DEFAULT '{}',
    review_ids          text[]      NOT NULL DEFAULT '{}',
    -- the context
    handoff_ids         text[]      NOT NULL DEFAULT '{}',
    thesis_ids          text[]      NOT NULL DEFAULT '{}',
    assessment_ids      text[]      NOT NULL DEFAULT '{}',
    value_add_ids       text[]      NOT NULL DEFAULT '{}',
    postmortem_keys     text[]      NOT NULL DEFAULT '{}',
    challenge_ids       text[]      NOT NULL DEFAULT '{}',
    audit_finding_ids   text[]      NOT NULL DEFAULT '{}',
    agent_decision_refs text[]      NOT NULL DEFAULT '{}',
    attribution_refs    text[]      NOT NULL DEFAULT '{}',
    sizing_refs         text[]      NOT NULL DEFAULT '{}',
    allocation_refs     text[]      NOT NULL DEFAULT '{}',
    regime_run_ids      text[]      NOT NULL DEFAULT '{}',
    capacity_ids        text[]      NOT NULL DEFAULT '{}',
    model_versions      text[]      NOT NULL DEFAULT '{}',
    policy_versions     text[]      NOT NULL DEFAULT '{}',
    experiment_ids      text[]      NOT NULL DEFAULT '{}',
    simulator_versions  text[]      NOT NULL DEFAULT '{}',
    stages              jsonb       NOT NULL,
    gaps                jsonb       NOT NULL DEFAULT '[]'::jsonb,
    content_sha256      text        NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT pos_lineage_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT pos_lineage_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT pos_lineage_book_ck CHECK (book IN ('PAPER', 'ACTUAL')),
    CONSTRAINT pos_lineage_state_ck CHECK (state IN ('OPEN', 'CLOSED')),
    CONSTRAINT pos_lineage_revision_ck CHECK (revision >= 1),
    CONSTRAINT pos_lineage_one_revision UNIQUE (book, position_key, revision)
);
CREATE INDEX IF NOT EXISTS pos_lineage_key_idx
    ON pos_lineage (book, position_key, revision DESC);
CREATE INDEX IF NOT EXISTS pos_lineage_group_idx ON pos_lineage (group_id);

-- ── 2 · CAPITAL-HOUR ECONOMICS PER POSITION, BY REVISION ──────────────
-- Time-invariant between events: an OPEN position stores the capital-hours
-- accrued up to its last event plus the cost basis still accruing, so the
-- row changes only when the position does (capital-hours to date =
-- capital_hours + open_cost_basis_usd x hours since last_event_at).
CREATE TABLE IF NOT EXISTS pos_position_economics (
    econ_id             text PRIMARY KEY,
    book                text        NOT NULL,
    position_key        text        NOT NULL,
    revision            integer     NOT NULL,
    counterfactual_kind text,
    basis_book          text,
    basis_position_key  text,
    lineage_id          text,
    run_id              text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    venue               text,
    group_id            text,
    us_market_slug      text,
    holding_side        text,
    strategy            text,
    state               text        NOT NULL,
    opened_at           timestamptz,
    first_fill_at       timestamptz,
    last_event_at       timestamptz,
    released_at         timestamptz,
    event_start_at      timestamptz,
    expected_release_at timestamptz,
    release_basis       text,
    bought_qty          double precision,
    capital_committed_usd double precision,
    peak_capital_usd    double precision,
    open_cost_basis_usd double precision,
    time_committed_h    double precision,
    capital_hours       double precision,
    net_profit_usd      double precision,
    expected_net_profit_usd double precision,
    expected_capital_hours double precision,
    realized_profit_per_capital_hour double precision,
    expected_profit_per_capital_hour double precision,
    predicted_edge_per_dollar double precision,
    realized_edge_per_dollar double precision,
    probability         double precision,
    probability_basis   text,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    content_sha256      text        NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT pos_econ_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT pos_econ_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT pos_econ_book_ck CHECK (book IN (
        'PAPER', 'ACTUAL', 'COUNTERFACTUAL')),
    -- a counterfactual names its kind and the real position behind it;
    -- a real position is never labelled with a counterfactual kind
    CONSTRAINT pos_econ_counterfactual_ck CHECK (
        (book = 'COUNTERFACTUAL') = (counterfactual_kind IS NOT NULL)
        AND (book <> 'COUNTERFACTUAL' OR (
             basis_book IN ('PAPER', 'ACTUAL')
             AND basis_position_key IS NOT NULL))),
    CONSTRAINT pos_econ_state_ck CHECK (state IN ('OPEN', 'CLOSED')),
    CONSTRAINT pos_econ_nonneg_ck CHECK (
        (capital_hours IS NULL OR capital_hours >= 0)
        AND (capital_committed_usd IS NULL OR capital_committed_usd >= 0)),
    -- realized profit exists only for a closed position
    CONSTRAINT pos_econ_open_not_realized_ck CHECK (
        state = 'CLOSED' OR net_profit_usd IS NULL),
    -- unmeasured is NULL WITH A NAMED REASON, never a silent gap or a 0
    CONSTRAINT pos_econ_named_nulls_ck CHECK (
        (net_profit_usd IS NOT NULL OR unmeasured ? 'net_profit_usd')
        AND (expected_net_profit_usd IS NOT NULL
             OR unmeasured ? 'expected_net_profit_usd')
        AND (capital_hours IS NOT NULL OR unmeasured ? 'capital_hours')
        AND (expected_capital_hours IS NOT NULL
             OR unmeasured ? 'expected_capital_hours')
        AND (realized_profit_per_capital_hour IS NOT NULL
             OR unmeasured ? 'realized_profit_per_capital_hour')
        AND (expected_profit_per_capital_hour IS NOT NULL
             OR unmeasured ? 'expected_profit_per_capital_hour')),
    CONSTRAINT pos_econ_one_revision UNIQUE (book, position_key, revision)
);
CREATE INDEX IF NOT EXISTS pos_econ_key_idx
    ON pos_position_economics (book, position_key, revision DESC);
CREATE INDEX IF NOT EXISTS pos_econ_released_idx
    ON pos_position_economics (book, released_at DESC);

-- ── 3 · CAPACITY PER CANDIDATE (from the recorded book at decision) ──
CREATE TABLE IF NOT EXISTS pos_capacity (
    capacity_id         text PRIMARY KEY,
    candidate_id        text        NOT NULL,
    run_id              text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    decided_at          timestamptz,
    us_market_slug      text,
    holding_side        text,
    strategy            text,
    book_obs_id         bigint,
    book_observed_at    timestamptz,
    book_age_s          double precision,
    probability         double precision,
    status              text        NOT NULL,
    why                 text,
    best_price          double precision,
    fee_basis           text,
    visible_depth_usd   double precision,
    visible_depth_contracts double precision,
    max_executable_contracts double precision,
    executable_capacity_usd double precision,
    capacity_ceiling_usd double precision,
    capacity_ceiling_depth_bound boolean,
    theoretical_opportunity_dollars double precision,
    executable_opportunity_dollars double precision,
    expected_price_impact double precision,
    edge_decay_per_1000_usd double precision,
    exit_liquidity_usd  double precision,
    exit_unabsorbed_contracts double precision,
    edge_at_size        jsonb,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    content_sha256      text        NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT pos_cap_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT pos_cap_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT pos_cap_status_ck CHECK (status IN ('MEASURED', 'UNAVAILABLE')),
    -- no depth evidence -> UNAVAILABLE with a reason and NO capacity number
    CONSTRAINT pos_cap_unavailable_is_null_ck CHECK (
        status = 'MEASURED' OR (
            why IS NOT NULL
            AND theoretical_opportunity_dollars IS NULL
            AND executable_opportunity_dollars IS NULL
            AND executable_capacity_usd IS NULL
            AND capacity_ceiling_usd IS NULL
            AND visible_depth_usd IS NULL)),
    CONSTRAINT pos_cap_once UNIQUE (candidate_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS pos_cap_candidate_idx
    ON pos_capacity (candidate_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS pos_cap_at_idx
    ON pos_capacity (decided_at DESC);

-- ── 4 · THE FIVE NORTH-STAR METRICS, ONE ROW PER CHANGE ──────────────
CREATE TABLE IF NOT EXISTS pos_metric_observations (
    observation_id      text PRIMARY KEY,
    run_id              text        NOT NULL,
    book                text        NOT NULL,
    metric              text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    period_start        timestamptz,
    period_end          timestamptz,
    value               double precision,
    sample_n            integer     NOT NULL DEFAULT 0,
    ci_low              double precision,
    ci_high             double precision,
    status              text        NOT NULL,
    why                 text,
    data_as_of          timestamptz,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    content_sha256      text        NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT pos_metric_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT pos_metric_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT pos_metric_book_ck CHECK (book IN ('PAPER', 'ACTUAL')),
    CONSTRAINT pos_metric_name_ck CHECK (metric IN (
        'REALIZED_NET_EDGE', 'PROFIT_PER_CAPITAL_HOUR',
        'PROB_POSITIVE_ROLLING_30D_PNL', 'MAX_DRAWDOWN',
        'EDGE_CALIBRATION')),
    CONSTRAINT pos_metric_status_ck CHECK (status IN (
        'MEASURED', 'INSUFFICIENT_SAMPLE', 'UNAVAILABLE', 'UNPROVEN')),
    -- a measured status carries a value; an absent value carries a reason
    CONSTRAINT pos_metric_value_ck CHECK (
        (status IN ('MEASURED', 'INSUFFICIENT_SAMPLE')) = (value IS NOT NULL)
        AND (value IS NOT NULL OR why IS NOT NULL)),
    CONSTRAINT pos_metric_n_ck CHECK (sample_n >= 0)
);
CREATE INDEX IF NOT EXISTS pos_metric_at_idx
    ON pos_metric_observations (book, metric, computed_at DESC);

-- ── AGGREGATE PAYLOADS PER RUN (written only when they change) ───────
CREATE TABLE IF NOT EXISTS pos_snapshots (
    snapshot_id         text PRIMARY KEY,
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    book                text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    payload             jsonb       NOT NULL,
    data_sha256         text        NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT pos_snap_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT pos_snap_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT pos_snap_component_ck CHECK (component IN (
        'CAPITAL', 'CAPACITY', 'WAREHOUSE')),
    CONSTRAINT pos_snap_book_ck CHECK (book IN (
        'PAPER', 'ACTUAL', 'COUNTERFACTUAL', 'NONE'))
);
CREATE INDEX IF NOT EXISTS pos_snap_latest_idx
    ON pos_snapshots (component, book, computed_at DESC);

-- ── 5 · THE MONTHLY REVENUE ENGINE: FORECASTS, PERSISTED TO BE SCORED ─
CREATE TABLE IF NOT EXISTS pos_forecasts (
    forecast_id         text PRIMARY KEY,
    book                text        NOT NULL,
    run_id              text        NOT NULL,
    issued_at           timestamptz NOT NULL,
    issued_day          date        NOT NULL,
    horizon_start       timestamptz NOT NULL,
    horizon_end         timestamptz NOT NULL,
    horizon_days        integer     NOT NULL,
    method              text        NOT NULL,
    seed                bigint,
    resamples           integer,
    block_days          integer,
    sample_days         integer     NOT NULL DEFAULT 0,
    sample_positions    integer     NOT NULL DEFAULT 0,
    expected_pnl_usd    double precision,
    p10_pnl_usd         double precision,
    p50_pnl_usd         double precision,
    p90_pnl_usd         double precision,
    prob_positive       double precision,
    expected_max_drawdown_usd double precision,
    p95_max_drawdown_usd double precision,
    worst_modelled_drawdown_usd double precision,
    capital_required_usd double precision,
    capital_hours_required double precision,
    turnover_required_usd double precision,
    capacity_ceiling_usd double precision,
    quantiles           jsonb,
    status              text        NOT NULL,
    why                 text,
    validation          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    inputs_sha256       text        NOT NULL,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT pos_fc_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT pos_fc_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT pos_fc_book_ck CHECK (book IN ('PAPER', 'ACTUAL')),
    CONSTRAINT pos_fc_status_ck CHECK (status IN (
        'UNPROVEN', 'FORWARD_VALIDATED', 'UNAVAILABLE')),
    CONSTRAINT pos_fc_unavailable_ck CHECK (
        status <> 'UNAVAILABLE' OR (why IS NOT NULL
                                    AND expected_pnl_usd IS NULL
                                    AND prob_positive IS NULL)),
    CONSTRAINT pos_fc_horizon_ck CHECK (horizon_end > horizon_start),
    CONSTRAINT pos_fc_one_per_day UNIQUE (book, issued_day)
);
CREATE INDEX IF NOT EXISTS pos_fc_at_idx
    ON pos_forecasts (book, issued_at DESC);

CREATE TABLE IF NOT EXISTS pos_forecast_scores (
    forecast_id         text PRIMARY KEY REFERENCES pos_forecasts (forecast_id),
    book                text        NOT NULL,
    scored_at           timestamptz NOT NULL,
    realized_pnl_usd    double precision NOT NULL,
    realized_positions  integer     NOT NULL,
    pit                 double precision,
    inside_p10_p90      boolean     NOT NULL,
    realized_positive   boolean     NOT NULL,
    brier_positive      double precision NOT NULL,
    abs_error_vs_p50_usd double precision NOT NULL,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT pos_fcs_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT pos_fcs_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT pos_fcs_book_ck CHECK (book IN ('PAPER', 'ACTUAL'))
);

-- ── APPEND-ONLY: every pos_* table refuses UPDATE and DELETE ─────────
CREATE OR REPLACE FUNCTION pos_record_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused (a later fact is a new row)',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['pos_runs', 'pos_lineage',
                             'pos_position_economics', 'pos_capacity',
                             'pos_metric_observations', 'pos_snapshots',
                             'pos_forecasts', 'pos_forecast_scores'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION '
                       'pos_record_is_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION '
                       'pos_record_is_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;

-- ── LATEST-REVISION VIEWS AND THE WAREHOUSE JOIN ─────────────────────
CREATE OR REPLACE VIEW pos_lineage_latest AS
SELECT DISTINCT ON (book, position_key) *
  FROM pos_lineage ORDER BY book, position_key, revision DESC;

CREATE OR REPLACE VIEW pos_economics_latest AS
SELECT DISTINCT ON (book, position_key) *
  FROM pos_position_economics ORDER BY book, position_key, revision DESC;

CREATE OR REPLACE VIEW pos_capacity_latest AS
SELECT DISTINCT ON (candidate_id) *
  FROM pos_capacity ORDER BY candidate_id, computed_at DESC;

-- one row per real position: its lineage and its economics, side by side
-- (counterfactual economics join on basis_position_key, never summed in)
CREATE OR REPLACE VIEW pos_warehouse AS
SELECT l.book, l.position_key, l.lineage_id, l.revision AS lineage_revision,
       l.group_id, l.us_market_slug, l.holding_side, l.strategy, l.venue,
       l.state, l.opened_at, l.closed_at, l.valuation_ids, l.decision_ids,
       l.intent_ids, l.order_ids, l.fill_ids, l.settlement_ids,
       l.review_ids, l.challenge_ids, l.model_versions, l.policy_versions,
       l.experiment_ids, l.gaps,
       e.econ_id, e.revision AS econ_revision, e.capital_committed_usd,
       e.capital_hours, e.net_profit_usd, e.expected_net_profit_usd,
       e.expected_capital_hours, e.realized_profit_per_capital_hour,
       e.expected_profit_per_capital_hour, e.unmeasured AS econ_unmeasured,
       l.label, l.authority
  FROM pos_lineage_latest l
  LEFT JOIN pos_economics_latest e
    ON e.book = l.book AND e.position_key = l.position_key;

-- a VIEW OVER THE EXISTING PAPER RECORDS: each paper position's fills,
-- orders, books, decisions and settlement versions, computed live from the
-- paper ledger's own append-only rows (nothing copied)
CREATE OR REPLACE VIEW pos_paper_chain_v AS
SELECT 'PAPER'::text AS book,
       'paperpos:' || f.account_id || ':' || f.group_id || ':'
           || f.us_market_slug || ':' || f.holding_side AS position_key,
       f.account_id, f.group_id, f.us_market_slug, f.holding_side,
       array_agg(DISTINCT f.fill_id) AS fill_ids,
       array_agg(DISTINCT f.order_id) AS order_ids,
       array_remove(array_agg(DISTINCT f.book_obs_id), NULL) AS book_obs_ids,
       min(f.filled_at) AS first_fill_at, max(f.filled_at) AS last_fill_at,
       (SELECT array_agg(DISTINCT o.decision_id) FROM paper_orders o
         WHERE o.group_id = f.group_id AND o.account_id = f.account_id
           AND o.decision_id IS NOT NULL) AS decision_ids,
       (SELECT array_agg(DISTINCT d.valuation_id) FROM paper_orders o
          JOIN paper_decisions d ON d.decision_id = o.decision_id
         WHERE o.group_id = f.group_id AND o.account_id = f.account_id
           AND d.valuation_id IS NOT NULL) AS valuation_ids,
       (SELECT array_agg(s.settlement_id ORDER BY s.version)
          FROM paper_settlements s
         WHERE s.position_key = 'paperpos:' || f.account_id || ':'
               || f.group_id || ':' || f.us_market_slug || ':'
               || f.holding_side) AS settlement_ids
  FROM paper_fills f
 GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side;
