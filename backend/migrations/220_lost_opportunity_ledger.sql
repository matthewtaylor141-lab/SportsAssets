-- ══════════════════════════════════════════════════════════════════════
-- 220 · THE LOST OPPORTUNITY LEDGER AND THE OPPORTUNITY SCORE
--       (Profitability OS, RESEARCH / SHADOW_NO_AUTHORITY)
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS HOLDS. For every Derek REFUSE (paper_decisions verdict REFUSE,
-- and refusals recorded in the coverage ledger ext_candidate_outcomes)
-- whose market later SETTLED, one classification per classifier version:
--
--   GOOD_REFUSAL   the decision-time evidence did not support an executable
--                  positive net EV under the policy's own thresholds after
--                  fees, or the refusal was a correct control (identity,
--                  settlement, stale evidence, entries switch ...) and the
--                  decision record shows the control's condition held
--   FALSE_REFUSAL  the decision-time evidence showed executable net EV > 0
--                  under the policy's own thresholds after fees, and the
--                  refusal came from a named defect (a control that fired
--                  without its condition in the record, a contradictory
--                  record, a raised gate) -- `defect` names it
--   UNKNOWABLE     the information needed was not available at decision
--                  time, or the record cannot establish either answer
--
-- A HINDSIGHT WINNER IS NOT A MISSED OPPORTUNITY. The classification reads
-- ONLY the decision-time record; the settlement is used for exactly two
-- things: to decide that the market is settled (an unsettled market gets no
-- row) and to compute the HYPOTHETICAL P&L at the decision-time executable
-- price, which is stored beside the class and never feeds it.
--
-- THE OPPORTUNITY SCORE (lol_opportunity_scores), per candidate:
--     expected net executable EV (conditional on fill, pos_capacity)
--   x fill probability (pos-econ CAPACITY rates, PAPER-simulated)
--   x capacity factor  (min(1, idle PAPER capital / executable capacity))
--   / capital-hours    (executable capacity x expected hold hours)
-- NULL with a named reason when any input is missing.
--
-- NO AUTHORITY. Written only by sportsassets/lost_opportunity/store.py,
-- read by GET /api/command/profitability/{lost-opportunities,
-- opportunity-scores}. Nothing here reaches a venue, an order, a size, a
-- limit, a threshold or capital. The database says so:
--   * label = 'RESEARCH', authority = 'SHADOW_NO_AUTHORITY' (CHECK);
--   * APPEND-ONLY: UPDATE, DELETE and TRUNCATE are refused by a trigger;
--   * one row per (decision_ref, classifier_version) (UNIQUE) -- the runner
--     is idempotent;
--   * FALSE_REFUSAL needs a named defect and a positive decision-time
--     executable net EV; GOOD/UNKNOWABLE never carry a defect (CHECK);
--   * the hypothetical P&L is labelled HYPOTHETICAL and is NULL with a
--     reason when it cannot be priced (CHECK).
--
-- Table names use the lol_ prefix (not pos_) so migration 216's own
-- rollback / idempotency proof, which counts pos_% relations, is untouched.
-- HORIZON FORECASTS (lol_horizon_forecasts): 24H / 7D / 30D per book --
-- expected opportunities, qualified opportunities, turnover, deployable
-- capital, capital-hours, net P&L P10/P50/P90, P(positive), expected
-- drawdown, capacity utilization -- UNPROVEN until scored forward.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE. No BEGIN/COMMIT of its own.

-- ── ONE ROW PER COMPONENT PER RUN ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS lol_runs (
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    pos_run_id          text,
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
    CONSTRAINT lol_runs_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT lol_runs_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT lol_runs_status_ck CHECK (status IN (
        'OK', 'FAILED', 'TIMEOUT', 'SKIPPED')),
    CONSTRAINT lol_runs_component_ck CHECK (component IN (
        'LEDGER', 'SCORES', 'HORIZONS'))
);
CREATE INDEX IF NOT EXISTS lol_runs_at_idx
    ON lol_runs (component, started_at DESC);

-- ── THE LEDGER: ONE CLASSIFICATION PER DECISION PER CLASSIFIER VERSION ─
CREATE TABLE IF NOT EXISTS lol_ledger (
    ledger_id           text PRIMARY KEY,
    decision_ref        text        NOT NULL,
    source              text        NOT NULL,
    classifier_version  text        NOT NULL,
    run_id              text        NOT NULL,
    classified_at       timestamptz NOT NULL,
    decided_at          timestamptz NOT NULL,
    strategy            text,
    league              text,
    league_basis        text,
    us_market_slug      text,
    holding_side        text,
    classification      text        NOT NULL,
    reason              text        NOT NULL,
    defect              text,
    refusal             text,
    refusals            text[]      NOT NULL DEFAULT '{}',
    refusal_category    text,
    -- the management attribution of the refusal that decided the class
    attribution         text        NOT NULL,
    attribution_code    text,
    -- the decision-time evidence (never the outcome)
    decision_evidence_ids text[]    NOT NULL DEFAULT '{}',
    decision_time_net_ev_usd double precision,
    decision_time_net_ev_basis text,
    decision_time_executable_price double precision,
    decision_time_qty   double precision,
    decision_time_fees_usd double precision,
    decision_time_cost_usd double precision,
    policy_min_gross_edge double precision,
    policy_min_net_ev_usd double precision,
    -- the subsequent settlement evidence
    settlement_evidence_id text     NOT NULL,
    settlement_basis    text        NOT NULL,
    settled_at          timestamptz NOT NULL,
    settlement_outcome  text        NOT NULL,
    payout_per_contract double precision,
    -- the HYPOTHETICAL P&L at the decision-time executable price
    hypothetical_pnl_usd double precision,
    hypothetical_pnl_label text     NOT NULL DEFAULT 'HYPOTHETICAL',
    hypothetical_pnl_why text,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    content_sha256      text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT lol_ledger_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT lol_ledger_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT lol_ledger_source_ck CHECK (source IN (
        'PAPER_DECISION', 'COVERAGE_LEDGER')),
    CONSTRAINT lol_ledger_class_ck CHECK (classification IN (
        'GOOD_REFUSAL', 'FALSE_REFUSAL', 'UNKNOWABLE')),
    -- a FALSE_REFUSAL names its defect and rests on a positive
    -- decision-time executable net EV; the others never name a defect
    CONSTRAINT lol_ledger_false_needs_defect_ck CHECK (
        (classification = 'FALSE_REFUSAL') = (defect IS NOT NULL)
        AND (classification <> 'FALSE_REFUSAL'
             OR (decision_time_net_ev_usd IS NOT NULL
                 AND decision_time_net_ev_usd > 0))),
    CONSTRAINT lol_ledger_settled_outcome_ck CHECK (settlement_outcome IN (
        'WON', 'LOST', 'VOID_REFUND')),
    CONSTRAINT lol_ledger_hypothetical_ck CHECK (
        hypothetical_pnl_label = 'HYPOTHETICAL'
        AND (hypothetical_pnl_usd IS NOT NULL
             OR hypothetical_pnl_why IS NOT NULL)),
    CONSTRAINT lol_ledger_attribution_ck CHECK (attribution IN (
        'SETTLEMENT', 'IDENTITY_MAPPING', 'FRESHNESS', 'LIQUIDITY',
        'EXECUTION_UNCERTAINTY', 'THRESHOLD', 'RISK', 'CAPACITY',
        'KAREN_CHALLENGE', 'UNSUPPORTED_LEAGUE', 'EXPLICIT_POLICY',
        'MISSING_PROBABILITY', 'MISSING_EXECUTABLE_BOOK', 'DEFECT',
        'UNATTRIBUTED')),
    CONSTRAINT lol_ledger_settled_after_decision_ck CHECK (
        settled_at >= decided_at),
    CONSTRAINT lol_ledger_once UNIQUE (decision_ref, classifier_version)
);
CREATE INDEX IF NOT EXISTS lol_ledger_class_idx
    ON lol_ledger (classifier_version, classification, decided_at DESC);
CREATE INDEX IF NOT EXISTS lol_ledger_at_idx
    ON lol_ledger (decided_at DESC);

-- ── THE OPPORTUNITY SCORE PER CANDIDATE (written when its inputs change) ─
CREATE TABLE IF NOT EXISTS lol_opportunity_scores (
    score_id            text PRIMARY KEY,
    candidate_id        text        NOT NULL,
    run_id              text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    decided_at          timestamptz,
    strategy            text,
    verdict             text,
    league              text,
    us_market_slug      text,
    holding_side        text,
    capacity_id         text,
    expected_net_executable_ev_usd double precision,
    fill_probability    double precision,
    fill_probability_basis text,
    capacity_factor     double precision,
    capacity_factor_basis text,
    executable_capacity_usd double precision,
    expected_hold_h     double precision,
    expected_hold_basis text,
    capital_hours       double precision,
    opportunity_score   double precision,
    -- the decomposition: NET_EV, EDGE_CONFIDENCE, CALIBRATION_CONFIDENCE,
    -- EXECUTION_CONFIDENCE, LIQUIDITY_CAPACITY, SETTLEMENT_CONFIDENCE,
    -- REGIME_CONFIDENCE, CORRELATION_RISK_COST -- each {value, status,
    -- why, basis}; an unmeasured component is null with its reason
    components          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    score_unit          text        NOT NULL DEFAULT
        'USD_EXPECTED_NET_PER_USD_CAPITAL_HOUR',
    status              text        NOT NULL,
    why                 text,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    content_sha256      text        NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT lol_score_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT lol_score_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT lol_score_status_ck CHECK (status IN (
        'MEASURED', 'UNAVAILABLE')),
    -- a score exists only when measured; otherwise a reason and no number
    CONSTRAINT lol_score_null_ck CHECK (
        (status = 'MEASURED') = (opportunity_score IS NOT NULL)
        AND (status = 'MEASURED' OR why IS NOT NULL)),
    CONSTRAINT lol_score_once UNIQUE (candidate_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS lol_score_candidate_idx
    ON lol_opportunity_scores (candidate_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS lol_score_at_idx
    ON lol_opportunity_scores (computed_at DESC);

-- ── HORIZON FORECASTS: 24H / 7D / 30D PER BOOK, PERSISTED TO BE SCORED ─
-- UNPROVEN until the horizon's own scored forecasts validate it (>= 12
-- scored, P10-P90 coverage in band, Brier of P(positive) <= 0.25);
-- UNAVAILABLE (no numbers) below 14 days / 10 closed positions of history.
-- Rounded on write: dollars to $1, probabilities to 0.01, counts to 0.1 --
-- no false precision. PAPER and ACTUAL are separate rows, never summed.
CREATE TABLE IF NOT EXISTS lol_horizon_forecasts (
    forecast_id         text PRIMARY KEY,
    book                text        NOT NULL,
    horizon             text        NOT NULL,
    horizon_days        double precision NOT NULL,
    run_id              text        NOT NULL,
    issued_at           timestamptz NOT NULL,
    issued_day          date        NOT NULL,
    horizon_start       timestamptz NOT NULL,
    horizon_end         timestamptz NOT NULL,
    method              text        NOT NULL,
    seed                bigint,
    sample_days         integer     NOT NULL DEFAULT 0,
    sample_positions    integer     NOT NULL DEFAULT 0,
    expected_opportunities double precision,
    expected_qualified_opportunities double precision,
    expected_turnover_usd double precision,
    deployable_capital_usd double precision,
    expected_capital_hours double precision,
    expected_pnl_usd    double precision,
    p10_pnl_usd         double precision,
    p50_pnl_usd         double precision,
    p90_pnl_usd         double precision,
    prob_positive       double precision,
    expected_max_drawdown_usd double precision,
    capacity_utilization double precision,
    expected_capacity_usd double precision,
    quantiles           jsonb,
    status              text        NOT NULL,
    why                 text,
    validation          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    basis               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    inputs_sha256       text        NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT lol_hfc_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT lol_hfc_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT lol_hfc_book_ck CHECK (book IN ('PAPER', 'ACTUAL')),
    CONSTRAINT lol_hfc_horizon_ck CHECK (horizon IN ('24H', '7D', '30D')),
    CONSTRAINT lol_hfc_status_ck CHECK (status IN (
        'UNPROVEN', 'FORWARD_VALIDATED', 'UNAVAILABLE')),
    CONSTRAINT lol_hfc_unavailable_ck CHECK (
        status <> 'UNAVAILABLE' OR (why IS NOT NULL
                                    AND expected_pnl_usd IS NULL
                                    AND prob_positive IS NULL)),
    CONSTRAINT lol_hfc_one_per_day UNIQUE (book, horizon, issued_day)
);
CREATE INDEX IF NOT EXISTS lol_hfc_at_idx
    ON lol_horizon_forecasts (book, horizon, issued_at DESC);

CREATE TABLE IF NOT EXISTS lol_horizon_forecast_scores (
    forecast_id         text PRIMARY KEY
                        REFERENCES lol_horizon_forecasts (forecast_id),
    book                text        NOT NULL,
    horizon             text        NOT NULL,
    scored_at           timestamptz NOT NULL,
    realized_pnl_usd    double precision NOT NULL,
    realized_positions  integer     NOT NULL,
    pit                 double precision,
    inside_p10_p90      boolean     NOT NULL,
    realized_positive   boolean     NOT NULL,
    brier_positive      double precision NOT NULL,
    abs_error_vs_p50_usd double precision NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'RESEARCH',
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    CONSTRAINT lol_hfcs_label_ck CHECK (label = 'RESEARCH'),
    CONSTRAINT lol_hfcs_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT lol_hfcs_book_ck CHECK (book IN ('PAPER', 'ACTUAL'))
);

-- ── APPEND-ONLY: every lol_* table refuses UPDATE, DELETE, TRUNCATE ───
CREATE OR REPLACE FUNCTION lol_record_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused (a later fact is a new row)',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['lol_runs', 'lol_ledger',
                             'lol_opportunity_scores',
                             'lol_horizon_forecasts',
                             'lol_horizon_forecast_scores'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_append_only_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION '
                       'lol_record_is_append_only()',
                       t || '_append_only_trg', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_no_truncate_trg', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION '
                       'lol_record_is_append_only()',
                       t || '_no_truncate_trg', t);
    END LOOP;
END $$;

CREATE OR REPLACE VIEW lol_opportunity_scores_latest AS
SELECT DISTINCT ON (candidate_id) *
  FROM lol_opportunity_scores ORDER BY candidate_id, computed_at DESC;
