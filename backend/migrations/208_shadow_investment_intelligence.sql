-- ══════════════════════════════════════════════════════════════════════
-- 208 · SHADOW INSTITUTIONAL INVESTMENT-INTELLIGENCE LAYER
--       (chief allocator, calibration engine, alpha-vs-execution
--        attribution, evidence-quality sizing, portfolio risk with
--        Audrey's independent recompute, regime detection)
-- ══════════════════════════════════════════════════════════════════════
--
-- EVERYTHING HERE IS SHADOW. The runner (sportsassets/intel/runner.py)
-- computes, persists and displays. It has NO venue authority: it places
-- and cancels nothing, changes no live sizing, limit or threshold, and
-- never modifies a production probability. The database says so where a
-- column could otherwise be talked into saying the opposite:
--
--   * every table carries label = 'SHADOW' (CHECK);
--   * intel_sizing.applied and intel_regime_states.applied can only be
--     false (CHECK) -- a shadow size or recommendation is never applied;
--   * intel_calibration_overlays.production_applied can only be false
--     (CHECK), and an overlay's fitted parameters and freeze instant are
--     immutable once stored (trigger) -- the frozen out-of-sample protocol
--     cannot be satisfied by refitting after the test data is seen;
--   * intel_regime_states.recommendation is NORMAL / REDUCE / NO_TRADE
--     only, with authority 'SHADOW_NO_AUTHORITY'.
--
-- UNMEASURED IS NULL WITH A REASON, NEVER ZERO. Numeric columns are
-- nullable; every row carries an `unmeasured` jsonb map {field: reason}.
--
-- Paper and actual are SEPARATE rows (`book` IN ('PAPER','ACTUAL')) and are
-- never summed by this layer.
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE. No BEGIN/COMMIT of its own: the
-- runner applies the file inside one transaction with its
-- schema_migrations row.

-- ── ONE ROW PER COMPONENT PER RUN ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS intel_runs (
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz,
    status              text        NOT NULL,
    error               text,
    duration_ms         integer,
    summary             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, component),
    CONSTRAINT intel_runs_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_runs_status_ck CHECK (status IN (
        'OK', 'FAILED', 'TIMEOUT', 'SKIPPED')),
    CONSTRAINT intel_runs_component_ck CHECK (component IN (
        'CYCLE', 'CALIBRATION', 'ATTRIBUTION', 'RISK', 'AUDREY_RISK',
        'REGIME', 'SIZING', 'ALLOCATOR'))
);
CREATE INDEX IF NOT EXISTS intel_runs_at_idx
    ON intel_runs (component, started_at DESC);

-- ── THE PAYLOAD EACH READ ENDPOINT SERVES (latest per component/book) ─
CREATE TABLE IF NOT EXISTS intel_snapshots (
    snapshot_id         bigserial PRIMARY KEY,
    run_id              text        NOT NULL,
    component           text        NOT NULL,
    book                text        NOT NULL DEFAULT 'NONE',
    computed_at         timestamptz NOT NULL,
    payload             jsonb       NOT NULL,
    version             text        NOT NULL,
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT intel_snapshots_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_snapshots_book_ck CHECK (book IN (
        'PAPER', 'ACTUAL', 'NONE')),
    CONSTRAINT intel_snapshots_component_ck CHECK (component IN (
        'CALIBRATION', 'ATTRIBUTION', 'RISK', 'REGIME', 'SIZING',
        'ALLOCATOR')),
    CONSTRAINT intel_snapshots_one_per_run UNIQUE (run_id, component, book)
);
CREATE INDEX IF NOT EXISTS intel_snapshots_latest_idx
    ON intel_snapshots (component, book, computed_at DESC);

-- ── D · CALIBRATION, ONE ROW PER SEGMENT PER RUN ──────────────────────
CREATE TABLE IF NOT EXISTS intel_calibration_segments (
    run_id              text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    source              text        NOT NULL,
    segment_kind        text        NOT NULL,
    segment_value       text        NOT NULL,
    n                   integer     NOT NULL,
    brier               double precision,
    brier_ci_low        double precision,
    brier_ci_high       double precision,
    log_loss            double precision,
    log_loss_ci_low     double precision,
    log_loss_ci_high    double precision,
    mean_probability    double precision,
    observed_frequency  double precision,
    frequency_ci_low    double precision,
    frequency_ci_high   double precision,
    reliability         jsonb,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    label               text        NOT NULL DEFAULT 'SHADOW',
    PRIMARY KEY (run_id, source, segment_kind, segment_value),
    CONSTRAINT intel_cal_seg_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_cal_seg_source_ck CHECK (source IN (
        'DECISION_P_PINNACLE', 'VALUATION_PROBABILITY')),
    CONSTRAINT intel_cal_seg_n_ck CHECK (n >= 0),
    -- a segment with no resolved prediction has no score: NULL, not 0
    CONSTRAINT intel_cal_seg_empty_is_null_ck CHECK (
        n > 0 OR (brier IS NULL AND log_loss IS NULL))
);
CREATE INDEX IF NOT EXISTS intel_cal_seg_at_idx
    ON intel_calibration_segments (computed_at DESC);

-- ── D · THE FROZEN OUT-OF-SAMPLE OVERLAY REGISTER ─────────────────────
CREATE TABLE IF NOT EXISTS intel_calibration_overlays (
    overlay_id          text PRIMARY KEY,
    source              text        NOT NULL,
    method              text        NOT NULL,
    params              jsonb       NOT NULL,
    params_sha256       text        NOT NULL,
    fit_n               integer     NOT NULL,
    fit_window_end      timestamptz NOT NULL,
    frozen_at           timestamptz NOT NULL,
    protocol            jsonb       NOT NULL,
    status              text        NOT NULL DEFAULT 'NOT_VALIDATED',
    oos_n               integer     NOT NULL DEFAULT 0,
    oos_result          jsonb,
    evaluated_at        timestamptz,
    production_applied  boolean     NOT NULL DEFAULT false,
    label               text        NOT NULL DEFAULT 'SHADOW',
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT intel_overlay_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_overlay_never_applied_ck CHECK (
        production_applied = false),
    CONSTRAINT intel_overlay_status_ck CHECK (status IN (
        'NOT_VALIDATED', 'VALIDATED_SHADOW_ONLY', 'FAILED_OOS')),
    CONSTRAINT intel_overlay_source_ck CHECK (source IN (
        'DECISION_P_PINNACLE', 'VALUATION_PROBABILITY')),
    CONSTRAINT intel_overlay_freeze_ck CHECK (fit_window_end <= frozen_at)
);
CREATE INDEX IF NOT EXISTS intel_overlay_source_idx
    ON intel_calibration_overlays (source, frozen_at DESC);

CREATE OR REPLACE FUNCTION intel_overlay_frozen() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'intel_calibration_overlays: an overlay is never '
                        'deleted (frozen out-of-sample protocol)';
    END IF;
    IF NEW.params IS DISTINCT FROM OLD.params
       OR NEW.params_sha256 IS DISTINCT FROM OLD.params_sha256
       OR NEW.method IS DISTINCT FROM OLD.method
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.fit_n IS DISTINCT FROM OLD.fit_n
       OR NEW.fit_window_end IS DISTINCT FROM OLD.fit_window_end
       OR NEW.frozen_at IS DISTINCT FROM OLD.frozen_at
       OR NEW.protocol IS DISTINCT FROM OLD.protocol THEN
        RAISE EXCEPTION 'intel_calibration_overlays: fitted parameters, '
                        'freeze instant and protocol are immutable';
    END IF;
    IF OLD.status <> 'NOT_VALIDATED' AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'intel_calibration_overlays: a decided out-of-'
                        'sample verdict is final';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS intel_overlay_frozen_trg ON intel_calibration_overlays;
CREATE TRIGGER intel_overlay_frozen_trg
    BEFORE UPDATE OR DELETE ON intel_calibration_overlays
    FOR EACH ROW EXECUTE FUNCTION intel_overlay_frozen();

-- ── E · ALPHA VS EXECUTION ATTRIBUTION (latest per subject) ──────────
CREATE TABLE IF NOT EXISTS intel_attribution (
    book                text        NOT NULL,
    subject_id          text        NOT NULL,
    decision_id         text,
    group_id            text,
    run_id              text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    strategy            text,
    us_market_slug      text,
    holding_side        text,
    p_decision          double precision,
    decision_price      double precision,
    fill_vwap           double precision,
    entry_qty           double precision,
    model_edge_pc       double precision,
    executable_edge_pc  double precision,
    slippage_pc         double precision,
    fees_usd            double precision,
    model_edge_usd      double precision,
    executable_edge_usd double precision,
    slippage_usd        double precision,
    execution_edge_usd  double precision,
    management_usd      double precision,
    settlement_usd      double precision,
    outcome_variance_usd double precision,
    realized_pnl_usd    double precision,
    cash_pnl_usd        double precision,
    reconciles          boolean,
    settlement_class    text,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    label               text        NOT NULL DEFAULT 'SHADOW',
    PRIMARY KEY (book, subject_id),
    CONSTRAINT intel_attr_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_attr_book_ck CHECK (book IN ('PAPER', 'ACTUAL'))
);
CREATE INDEX IF NOT EXISTS intel_attr_at_idx
    ON intel_attribution (computed_at DESC);

-- ── I · EVIDENCE-QUALITY SHADOW SIZE, BESIDE THE ACTUAL/PAPER SIZE ────
CREATE TABLE IF NOT EXISTS intel_sizing (
    decision_id         text PRIMARY KEY,
    run_id              text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    strategy            text,
    us_market_slug      text,
    paper_qty           double precision,
    paper_usd           double precision,
    actual_qty          double precision,
    actual_usd          double precision,
    shadow_usd          double precision,
    shadow_qty          double precision,
    binding_constraint  text,
    factors             jsonb       NOT NULL,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    applied             boolean     NOT NULL DEFAULT false,
    label               text        NOT NULL DEFAULT 'SHADOW',
    CONSTRAINT intel_sizing_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_sizing_never_applied_ck CHECK (applied = false),
    CONSTRAINT intel_sizing_nonneg_ck CHECK (
        shadow_usd IS NULL OR shadow_usd >= 0)
);
CREATE INDEX IF NOT EXISTS intel_sizing_at_idx
    ON intel_sizing (computed_at DESC);

-- ── A · THE CHIEF ALLOCATOR'S RANKED SHADOW ALLOCATION, PER RUN ──────
CREATE TABLE IF NOT EXISTS intel_allocations (
    run_id              text        NOT NULL,
    candidate_id        text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    rank                integer     NOT NULL,
    candidate_kind      text        NOT NULL,
    decision_id         text,
    group_id            text,
    us_market_slug      text,
    score               double precision,
    net_ev_per_dollar   double precision,
    shadow_weight       double precision NOT NULL,
    shadow_usd          double precision NOT NULL,
    binding_constraint  text,
    reasons             jsonb       NOT NULL,
    inputs              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    unmeasured          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    label               text        NOT NULL DEFAULT 'SHADOW',
    PRIMARY KEY (run_id, candidate_id),
    CONSTRAINT intel_alloc_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_alloc_kind_ck CHECK (candidate_kind IN (
        'NEW_DECISION', 'OPEN_POSITION')),
    CONSTRAINT intel_alloc_weight_ck CHECK (
        shadow_weight >= 0 AND shadow_weight <= 1 AND shadow_usd >= 0)
);
CREATE INDEX IF NOT EXISTS intel_alloc_at_idx
    ON intel_allocations (computed_at DESC);

-- ── J · AUDREY'S INDEPENDENT RISK RECOMPUTE, ONE ROW PER METRIC ──────
CREATE TABLE IF NOT EXISTS intel_audrey_risk_checks (
    run_id              text        NOT NULL,
    book                text        NOT NULL,
    metric              text        NOT NULL,
    computed_at         timestamptz NOT NULL,
    primary_value       double precision,
    audrey_value        double precision,
    abs_diff            double precision,
    tolerance           double precision NOT NULL,
    agrees              boolean,
    finding_id          text,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    label               text        NOT NULL DEFAULT 'SHADOW',
    PRIMARY KEY (run_id, book, metric),
    CONSTRAINT intel_audrey_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_audrey_book_ck CHECK (book IN ('PAPER', 'ACTUAL'))
);
CREATE INDEX IF NOT EXISTS intel_audrey_at_idx
    ON intel_audrey_risk_checks (computed_at DESC);

-- ── K · REGIME, A SHADOW RECOMMENDATION WITH NO AUTHORITY ────────────
CREATE TABLE IF NOT EXISTS intel_regime_states (
    run_id              text PRIMARY KEY,
    computed_at         timestamptz NOT NULL,
    recommendation      text        NOT NULL,
    reasons             jsonb       NOT NULL,
    signals             jsonb       NOT NULL,
    authority           text        NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    applied             boolean     NOT NULL DEFAULT false,
    label               text        NOT NULL DEFAULT 'SHADOW',
    CONSTRAINT intel_regime_label_ck CHECK (label = 'SHADOW'),
    CONSTRAINT intel_regime_rec_ck CHECK (recommendation IN (
        'NORMAL', 'REDUCE', 'NO_TRADE')),
    CONSTRAINT intel_regime_authority_ck CHECK (
        authority = 'SHADOW_NO_AUTHORITY'),
    CONSTRAINT intel_regime_never_applied_ck CHECK (applied = false)
);
CREATE INDEX IF NOT EXISTS intel_regime_at_idx
    ON intel_regime_states (computed_at DESC);
