-- BETTOR_EXPERIMENTAL_SHADOW: the prospective experimental ledger.
--
-- Owner directive 2026-09-19 22:4xZ §8. Append-only experimental
-- evidence. "No UPDATE/DELETE of original experimental
-- decisions/executions. Future markouts and settlement append
-- separately."
--
-- WHY THE DECISION AND ITS EXECUTION SHARE A ROW while markouts do
-- not. The T0 seal -- features, model output, action, intended
-- notional -- and the arrival walk that follows from it are one
-- prospective act: the decision is not complete until the size it
-- intended has met the book it actually met. A markout is a LATER FACT
-- about that act, and a later fact that could rewrite the row it
-- measures is the retrospective edit this whole ledger exists to
-- prevent. So markouts append to their own table, keyed back.
--
-- NOTHING HERE IS DECISION-GRADE. Every row carries not_decision_grade
-- TRUE, and a CHECK enforces it: promotion to the decision-grade lane
-- is governed by the existing frozen framework, which this table has
-- no relationship with.

BEGIN;

-- ── the eligible population (§7) ─────────────────────────────────────
--
-- X1 and its control must be scored on THE SAME opportunities. A
-- population identified only after the fact could be the set where X1
-- happened to fire, which would make the control meaningless. So the
-- population is written FIRST, both experiments point at it, and its
-- membership is fixed at the instant it was sealed.

CREATE TABLE IF NOT EXISTS bettor_eligible_populations (
    eligible_population_id  TEXT PRIMARY KEY,
    sealed_at               TIMESTAMPTZ NOT NULL,
    feature_source_version  TEXT NOT NULL,
    opportunity_count       INTEGER NOT NULL,
    eligibility_rule        TEXT NOT NULL,
    eligibility_rule_sha    TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS bettor_eligible_populations_immutable
    ON bettor_eligible_populations;
CREATE TRIGGER bettor_eligible_populations_immutable
    BEFORE UPDATE OR DELETE ON bettor_eligible_populations
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── the frozen experiment registry, as written down ──────────────────

CREATE TABLE IF NOT EXISTS bettor_experiments (
    experiment_id           TEXT NOT NULL,
    experiment_sha          TEXT NOT NULL,
    policy_version          TEXT NOT NULL,
    model_version           TEXT NOT NULL,
    role                    TEXT NOT NULL,
    control_for             TEXT,
    readiness               TEXT NOT NULL,
    declaration             JSONB NOT NULL,
    frozen_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (experiment_id, experiment_sha),
    CONSTRAINT bettor_experiment_role
        CHECK (role IN ('CANDIDATE', 'CONTROL')),
    -- A CONTROL beside nothing answers no question.
    CONSTRAINT bettor_experiment_control_names_its_candidate
        CHECK (role <> 'CONTROL' OR control_for IS NOT NULL)
);

DROP TRIGGER IF EXISTS bettor_experiments_immutable ON bettor_experiments;
CREATE TRIGGER bettor_experiments_immutable
    BEFORE UPDATE OR DELETE ON bettor_experiments
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── the decision and its arrival execution ───────────────────────────

CREATE TABLE IF NOT EXISTS bettor_experimental_decisions (
    experimental_decision_id TEXT PRIMARY KEY,

    experiment_id            TEXT NOT NULL,
    experiment_version       TEXT NOT NULL,
    experiment_sha           TEXT NOT NULL,
    control_id               TEXT,
    eligible_population_id   TEXT
        REFERENCES bettor_eligible_populations (eligible_population_id),

    bettor_opportunity_id    TEXT
        REFERENCES bettor_opportunities (bettor_opportunity_id),
    market_id                TEXT NOT NULL,
    outcome_leg              TEXT,

    -- §4 of the identity directive: the binding travels with the row,
    -- so a trade can always be re-read against the mapping it was
    -- actually executed under rather than today's belief about it.
    institutional_instrument_id TEXT,
    identity_binding_status  TEXT NOT NULL,
    identity_binding_sha     TEXT,

    -- §3: which collector semantics produced the features. The rows
    -- written while yes and no carried the same BBO are historical
    -- evidence of that behaviour and are NOT repaired; an experiment
    -- simply requires the corrected version.
    feature_source_version   TEXT NOT NULL,
    feature_asof             TIMESTAMPTZ NOT NULL,
    features                 JSONB NOT NULL,
    features_sha             TEXT NOT NULL,

    model_output             JSONB,
    signal_strength          DOUBLE PRECISION,
    action                   TEXT NOT NULL,
    decision_timestamp       TIMESTAMPTZ NOT NULL,

    intended_notional_usd    NUMERIC(20, 6) NOT NULL,

    -- the arrival walk
    arrival_timestamp        TIMESTAMPTZ,
    execution_status         TEXT NOT NULL,
    l2_reference             JSONB,
    l2_source_timestamp      TEXT,
    l2_received_timestamp    TIMESTAMPTZ,
    l2_book_sha              TEXT,
    price_scale              INTEGER,
    quantity_scale           INTEGER,
    execution_contract       TEXT,
    execution_contract_sha   TEXT,

    executed_notional_usd    NUMERIC(20, 6),
    unfilled_notional_usd    NUMERIC(20, 6),
    filled_qty               DOUBLE PRECISION,
    vwap                     DOUBLE PRECISION,
    slippage                 DOUBLE PRECISION,
    spread_cost              DOUBLE PRECISION,

    position_id              TEXT,

    not_decision_grade       BOOLEAN NOT NULL DEFAULT TRUE,
    real_order_submitted     BOOLEAN NOT NULL DEFAULT FALSE,
    capital_at_risk          NUMERIC(20, 6) NOT NULL DEFAULT 0,

    written_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- THE THREE THE LANE CANNOT EXIST WITHOUT.
    CONSTRAINT bettor_exp_not_decision_grade
        CHECK (not_decision_grade IS TRUE),
    CONSTRAINT bettor_exp_no_real_order
        CHECK (real_order_submitted IS FALSE),
    CONSTRAINT bettor_exp_no_capital
        CHECK (capital_at_risk = 0),

    CONSTRAINT bettor_exp_intended_notional_positive
        CHECK (intended_notional_usd > 0),

    -- "Do not convert it to NO_TRADE after seeing the signal." A
    -- decision blocked on identity keeps its ACTION and says so in
    -- execution_status; the two are separate columns for that reason.
    CONSTRAINT bettor_exp_execution_status
        CHECK (execution_status IN (
            'EXECUTED', 'PARTIAL', 'UNFILLED', 'NOT_IDENTIFIED',
            'BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE',
            'BLOCKED_INPUT_INVALID', 'NO_EXECUTION_INTENDED')),

    -- NEVER MORE THAN WAS INTENDED, and the two halves must add up.
    CONSTRAINT bettor_exp_notional_balances
        CHECK (executed_notional_usd IS NULL
               OR unfilled_notional_usd IS NULL
               OR abs((executed_notional_usd + unfilled_notional_usd)
                      - intended_notional_usd) < 0.01),
    CONSTRAINT bettor_exp_executed_not_negative
        CHECK (executed_notional_usd IS NULL
               OR executed_notional_usd >= 0),
    -- A fill with no book behind it is the invented liquidity the
    -- whole lane refuses.
    CONSTRAINT bettor_exp_fill_has_a_book
        CHECK (executed_notional_usd IS NULL
               OR executed_notional_usd = 0
               OR l2_book_sha IS NOT NULL)
);

DROP TRIGGER IF EXISTS bettor_experimental_decisions_immutable
    ON bettor_experimental_decisions;
CREATE TRIGGER bettor_experimental_decisions_immutable
    BEFORE UPDATE OR DELETE ON bettor_experimental_decisions
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

CREATE INDEX IF NOT EXISTS bettor_exp_decisions_at_idx
    ON bettor_experimental_decisions (decision_timestamp DESC);
CREATE INDEX IF NOT EXISTS bettor_exp_decisions_experiment_idx
    ON bettor_experimental_decisions (experiment_id,
                                      decision_timestamp DESC);
CREATE INDEX IF NOT EXISTS bettor_exp_decisions_population_idx
    ON bettor_experimental_decisions (eligible_population_id);

-- ── the shadow position opened by an execution ───────────────────────

CREATE TABLE IF NOT EXISTS bettor_experimental_positions (
    position_id              TEXT PRIMARY KEY,
    experimental_decision_id TEXT NOT NULL
        REFERENCES bettor_experimental_decisions
                   (experimental_decision_id),
    experiment_id            TEXT NOT NULL,
    market_id                TEXT NOT NULL,
    institutional_instrument_id TEXT,
    side                     TEXT NOT NULL,
    opened_at                TIMESTAMPTZ NOT NULL,
    entry_qty                DOUBLE PRECISION NOT NULL,
    entry_vwap               DOUBLE PRECISION NOT NULL,
    entry_notional_usd       NUMERIC(20, 6) NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'OPEN',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bettor_exp_position_qty_positive CHECK (entry_qty > 0),
    CONSTRAINT bettor_exp_position_status
        CHECK (status IN ('OPEN', 'CLOSED', 'SETTLED'))
);

DROP TRIGGER IF EXISTS bettor_experimental_positions_immutable
    ON bettor_experimental_positions;
CREATE TRIGGER bettor_experimental_positions_immutable
    BEFORE UPDATE OR DELETE ON bettor_experimental_positions
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── LATER FACTS, APPENDED (§12) ──────────────────────────────────────
--
-- Markouts, exits and settlement never touch the decision row. Each is
-- its own append, keyed back, so the T0 decision is readable forever
-- exactly as it was sealed.

CREATE TABLE IF NOT EXISTS bettor_experimental_markouts (
    markout_id               TEXT PRIMARY KEY,
    experimental_decision_id TEXT NOT NULL
        REFERENCES bettor_experimental_decisions
                   (experimental_decision_id),
    position_id              TEXT,
    horizon                  TEXT NOT NULL,
    observed_at              TIMESTAMPTZ NOT NULL,
    -- BOTH marks, never collapsed: a mid markout is where the quote
    -- went, an executable markout is what could have been got out.
    mid_markout_usd          NUMERIC(20, 6),
    executable_markout_usd   NUMERIC(20, 6),
    mark_price               DOUBLE PRECISION,
    l2_book_sha              TEXT,
    l2_source_timestamp      TEXT,
    status                   TEXT NOT NULL,
    why                      TEXT,
    written_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bettor_exp_markout_horizon
        CHECK (horizon IN ('30S', '60S', '300S', 'SETTLEMENT')),
    CONSTRAINT bettor_exp_markout_status
        CHECK (status IN ('OBSERVED', 'NOT_IDENTIFIED', 'NOT_YET_MATURE'))
);

DROP TRIGGER IF EXISTS bettor_experimental_markouts_immutable
    ON bettor_experimental_markouts;
CREATE TRIGGER bettor_experimental_markouts_immutable
    BEFORE UPDATE OR DELETE ON bettor_experimental_markouts
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

CREATE UNIQUE INDEX IF NOT EXISTS bettor_exp_markout_once
    ON bettor_experimental_markouts (experimental_decision_id, horizon);

COMMIT;
