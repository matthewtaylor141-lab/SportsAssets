-- SHADOW TRADING. Prospective decisions, recorded before the outcome is
-- known, with no real order behind any of them.
--
-- Owner directive 2026-09-19: SHADOW_MODE=TRUE, REAL_ORDER_SUBMISSION
-- DISABLED, CAPITAL_AT_RISK=0, mirror_live=false. This is NOT a backtest.
--
-- THE IMMUTABILITY IS ENFORCED BY THE DATABASE, NOT BY CONVENTION.
--
-- The whole value of a prospective ledger is that nothing learned later
-- can change what the system claimed to know at T0. A comment saying
-- "append-only" does not survive one convenient UPDATE during an
-- incident, so `shadow_decisions` carries a trigger that refuses UPDATE
-- and DELETE outright. Scores and outcomes, which necessarily arrive
-- later, live in their own tables keyed by the decision -- they annotate
-- it and can never rewrite it.

BEGIN;

-- ── 1. THE PROSPECTIVE DECISION LEDGER ──────────────────────────────

CREATE TABLE IF NOT EXISTS shadow_decisions (
    shadow_decision_id  TEXT PRIMARY KEY,

    -- subject
    event_id            TEXT,
    market_id           TEXT,
    symbol              TEXT        NOT NULL,
    outcome_leg         TEXT        NOT NULL,
    sport               TEXT,
    league              TEXT,

    -- provenance: which brain, which rules, which data
    model_version       TEXT        NOT NULL,
    policy_version      TEXT        NOT NULL,
    -- EVIDENCE SOURCE TRAVELS WITH EVERY DECISION. Sources are never
    -- merged silently; a decision knows which book it was looking at.
    evidence_source     TEXT        NOT NULL,

    -- the four clocks, kept apart
    venue_source_ts     TIMESTAMPTZ,
    bettor_received_ts  TIMESTAMPTZ,
    feature_asof_ts     TIMESTAMPTZ,
    decision_ts         TIMESTAMPTZ NOT NULL,

    -- the book as seen at decision time
    market_bid          NUMERIC,
    market_ask          NUMERIC,
    mid                 NUMERIC,
    spread              NUMERIC,
    available_depth     JSONB,
    l2_reference        JSONB,

    -- belief and expectation. NULL means NOT ESTABLISHED, never zero.
    p_market            NUMERIC,
    p_bettor            NUMERIC,
    information_ev      NUMERIC,
    execution_ev        NUMERIC,
    total_action_ev     NUMERIC,
    uncertainty         NUMERIC,
    p_ev_gt_zero        NUMERIC,
    break_even_fill_p   NUMERIC,
    p_fill              NUMERIC,

    -- what it proposed
    proposed_action     TEXT        NOT NULL,
    proposed_side       TEXT,
    proposed_price      NUMERIC,
    proposed_quantity   NUMERIC,

    -- why, and why not the alternatives
    reason_codes        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    gate_results        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    blockers            JSONB       NOT NULL DEFAULT '[]'::jsonb,
    alternatives        JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- the standing safety label, carried on the row itself so it cannot
    -- be lost by a query that forgets to add it
    shadow_mode         BOOLEAN     NOT NULL DEFAULT TRUE,
    capital_at_risk     NUMERIC     NOT NULL DEFAULT 0,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT shadow_decisions_action CHECK (proposed_action IN (
        'NO_TRADE','HOLD','BUY','SELL','CASH_OUT','PAIR',
        'COMPLETE_COMPLEMENT','CANCEL','REPRICE','REDUCE',
        'HOLD_TO_SETTLEMENT')),
    CONSTRAINT shadow_decisions_shadow_only CHECK (
        shadow_mode IS TRUE AND capital_at_risk = 0)
);

CREATE INDEX IF NOT EXISTS shadow_decisions_ts_idx
    ON shadow_decisions (decision_ts DESC);
CREATE INDEX IF NOT EXISTS shadow_decisions_symbol_idx
    ON shadow_decisions (symbol, decision_ts DESC);
CREATE INDEX IF NOT EXISTS shadow_decisions_action_idx
    ON shadow_decisions (proposed_action, decision_ts DESC);

-- THE REFUSAL. A prospective record that can be edited is not evidence.
CREATE OR REPLACE FUNCTION shadow_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'shadow ledger is append-only: % on % is refused. What BETTOR '
        'claimed at T0 cannot be revised by anything learned after it.',
        TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS shadow_decisions_immutable ON shadow_decisions;
CREATE TRIGGER shadow_decisions_immutable
    BEFORE UPDATE OR DELETE ON shadow_decisions
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── 2. SHADOW EXECUTION, RECONSTRUCTED ──────────────────────────────
--
-- Separate from the decision because it happens at a DIFFERENT TIME --
-- T_SHADOW_ARRIVAL, not T0 -- and against a different book.

CREATE TABLE IF NOT EXISTS shadow_executions (
    shadow_execution_id TEXT PRIMARY KEY,
    shadow_decision_id  TEXT NOT NULL REFERENCES shadow_decisions
                             (shadow_decision_id),

    -- EXECUTION CLASS. This is what keeps simulated and observed
    -- economics from ever being added together.
    execution_class     TEXT NOT NULL,

    -- the four latencies, never blended when their parts are known
    data_latency_ms          NUMERIC,
    decision_compute_ms      NUMERIC,
    execution_latency_ms     NUMERIC,
    total_to_arrival_ms      NUMERIC,
    -- SCENARIO or OBSERVED. A predeclared grid value is not a measurement.
    latency_basis       TEXT NOT NULL,
    latency_scenario_ms NUMERIC,

    arrival_ts          TIMESTAMPTZ,
    arrival_book        JSONB,

    shadow_filled_qty   NUMERIC,
    vwap                NUMERIC,
    slippage            NUMERIC,
    spread_cost         NUMERIC,
    unfilled_qty        NUMERIC,

    status              TEXT NOT NULL,
    why                 TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT shadow_exec_class CHECK (execution_class IN (
        'MARKETABLE_RECONSTRUCTED',
        'PASSIVE_COUNTERFACTUAL_MARKOUT',
        'PASSIVE_QUEUE_MODEL_ESTIMATE',
        'ACTUAL_FILL')),
    CONSTRAINT shadow_exec_status CHECK (status IN (
        'FILLED','PARTIAL','UNFILLED','NOT_IDENTIFIED')),
    CONSTRAINT shadow_exec_latency_basis CHECK (latency_basis IN (
        'OBSERVED','SCENARIO','NOT_IDENTIFIED')),
    -- ACTUAL_FILL cannot carry a quantity while no real order exists.
    CONSTRAINT shadow_exec_no_actual_fill CHECK (
        execution_class <> 'ACTUAL_FILL'
        OR COALESCE(shadow_filled_qty, 0) = 0)
);

CREATE INDEX IF NOT EXISTS shadow_exec_decision_idx
    ON shadow_executions (shadow_decision_id);

DROP TRIGGER IF EXISTS shadow_executions_immutable ON shadow_executions;
CREATE TRIGGER shadow_executions_immutable
    BEFORE UPDATE OR DELETE ON shadow_executions
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── 3. THE SHADOW POSITION LEDGER ───────────────────────────────────
--
-- One row per LEG. YES and NO are never netted into one number: that
-- defect cost real money on the live mirror and is not repeated here.

CREATE TABLE IF NOT EXISTS shadow_positions (
    shadow_position_id  TEXT PRIMARY KEY,
    originating_decision_id TEXT NOT NULL REFERENCES shadow_decisions
                                 (shadow_decision_id),
    event_id            TEXT,
    market_id           TEXT,
    symbol              TEXT        NOT NULL,
    leg                 TEXT        NOT NULL,

    entry_time          TIMESTAMPTZ NOT NULL,
    entry_price         NUMERIC,
    entry_qty           NUMERIC,
    entry_ev            NUMERIC,

    opened_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS shadow_positions_symbol_idx
    ON shadow_positions (symbol, leg);

-- The OPENING facts of a position are as prospective as the decision
-- that created them, so they are immutable too. Everything that changes
-- afterwards is a new row in shadow_position_events.
DROP TRIGGER IF EXISTS shadow_positions_immutable ON shadow_positions;
CREATE TRIGGER shadow_positions_immutable
    BEFORE UPDATE OR DELETE ON shadow_positions
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- State CHANGES are new rows. Nothing about a position is overwritten.
CREATE TABLE IF NOT EXISTS shadow_position_events (
    shadow_position_event_id BIGSERIAL PRIMARY KEY,
    shadow_position_id  TEXT NOT NULL REFERENCES shadow_positions
                             (shadow_position_id),
    shadow_decision_id  TEXT REFERENCES shadow_decisions
                             (shadow_decision_id),
    at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind                TEXT NOT NULL,

    current_qty         NUMERIC,
    executable_mark     NUMERIC,
    unrealized_pnl      NUMERIC,
    realized_pnl        NUMERIC,
    settlement_pnl      NUMERIC,

    pair_status         TEXT,
    exit_intention      TEXT,
    next_decision       TEXT,

    capital_hours       NUMERIC,
    max_adverse_move    NUMERIC,
    max_favorable_move  NUMERIC,

    -- economics never travel without the class that produced them
    execution_class     TEXT,

    CONSTRAINT shadow_pos_event_kind CHECK (kind IN (
        'OPEN','MARK','REDUCE','PAIR','CASH_OUT','SETTLE','CLOSE'))
);

CREATE INDEX IF NOT EXISTS shadow_position_events_pos_idx
    ON shadow_position_events (shadow_position_id, at DESC);

DROP TRIGGER IF EXISTS shadow_position_events_immutable
    ON shadow_position_events;
CREATE TRIGGER shadow_position_events_immutable
    BEFORE UPDATE OR DELETE ON shadow_position_events
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── 4. SCORES, WHICH ARRIVE LATER AND ANNOTATE ──────────────────────
--
-- A separate table precisely BECAUSE labels mature after the decision.
-- Writing a markout back onto the decision row would be the system
-- editing its own past claim.

CREATE TABLE IF NOT EXISTS shadow_scores (
    shadow_decision_id  TEXT NOT NULL REFERENCES shadow_decisions
                             (shadow_decision_id),
    horizon             TEXT NOT NULL,

    observable          BOOLEAN NOT NULL,
    why_unobservable    TEXT,

    direction_correct   BOOLEAN,
    mid_markout         NUMERIC,
    executable_markout  NUMERIC,
    spread_relative_move NUMERIC,
    adverse_selection   NUMERIC,
    slippage            NUMERIC,
    realized_vs_expected_ev NUMERIC,
    settlement_result   TEXT,

    scored_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (shadow_decision_id, horizon),
    CONSTRAINT shadow_scores_horizon CHECK (horizon IN (
        '5S','30S','60S','300S','SETTLEMENT'))
);

COMMIT;
