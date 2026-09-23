-- THE SHADOW DESK LEDGER. The missing link, made persistent.
--
-- WHAT THE AUDIT FOUND (run 35868260156, 2026-09-23T13:36:43Z). The
-- chain up to the decision is LIVE and continuous:
--
--   bettor_state_observations   4,688 rows, newest 37 s old
--   shadow_decisions           25,995 rows, newest 1 s old
--                              BETTOR_EV_SHADOW_V5 and RN1_SHADOW_V1
--
-- and everything after the decision is EMPTY:
--
--   shadow_executions               0
--   shadow_positions                0
--   shadow_position_events          0
--   any shadow ORDER table          does not exist
--
-- `shadow_store.record_execution()` and `record_position()` are written,
-- correct, and have ZERO CALLERS in the entire tree. That is the whole
-- missing connection, and it is the same failure `bettor_fee_schedule`
-- already names about `record_settlement()`: a capability that is built
-- and never wired.
--
-- WHY NEW TABLES RATHER THAN FILLING THE OLD ONES. `shadow_executions`
-- is decision-centric -- one row per decision, carrying latency
-- scenarios -- and has no order to have a life. A desk needs an ORDER
-- with a state machine, fills that reference the evidence that produced
-- them, and a consumption ledger so one printed trade cannot fill three
-- hypothetical orders. Those are different shapes, and bending the old
-- one would have quietly changed what the existing COMMAND panels mean.
--
-- NOTHING HERE TOUCHES REAL MONEY. There is no venue client in the
-- import graph of anything that writes these tables, every price is a
-- simulation against recorded evidence, and the state column has no
-- value that means "sent to a venue".

CREATE TABLE IF NOT EXISTS bettor_desk_state (
    desk_id            TEXT PRIMARY KEY,
    boot_id            TEXT NOT NULL,
    policy_version     TEXT NOT NULL,
    model_version      TEXT NOT NULL,
    -- THE CURSOR IS WHY A RESTART DOES NOT DUPLICATE. It advances only
    -- after the batch's decisions, orders and positions are committed
    -- in the same transaction.
    -- THE CURSOR IS THE EVIDENCE'S OWN SERIAL ID, not a timestamp. A
    -- timestamp repeats and arrives out of order across ingestion
    -- lanes; a cursor that can go backwards replays work and a replay
    -- that is not idempotent duplicates orders.
    cursor_event_id    BIGINT,
    cash_usd           NUMERIC NOT NULL,
    starting_cash_usd  NUMERIC NOT NULL,
    halted             BOOLEAN NOT NULL DEFAULT FALSE,
    halt_reason        TEXT,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- EVERY DECISION, INCLUDING EVERY REFUSAL. A desk that only records
-- what it did cannot be audited for what it declined, and NO_TRADE is a
-- legitimate outcome that must be as visible as a fill.
CREATE TABLE IF NOT EXISTS bettor_desk_decisions (
    desk_decision_id   TEXT PRIMARY KEY,
    desk_id            TEXT NOT NULL,
    boot_id            TEXT NOT NULL,
    decided_at         TIMESTAMPTZ NOT NULL,
    feature_cutoff_at  TIMESTAMPTZ NOT NULL,
    source_decision_id TEXT,
    condition_id       TEXT,
    market_id          TEXT,
    outcome_index      INTEGER,
    action             TEXT NOT NULL,
    reason             TEXT NOT NULL,
    policy_version     TEXT NOT NULL,
    model_version      TEXT NOT NULL,
    proposed_side      TEXT,
    proposed_price     NUMERIC,
    proposed_qty       NUMERIC,
    proposed_expiry_s  INTEGER,
    inventory          JSONB NOT NULL DEFAULT '{}'::jsonb,
    ev                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk               JSONB NOT NULL DEFAULT '{}'::jsonb,
    inputs             JSONB NOT NULL DEFAULT '{}'::jsonb,
    inputs_sha         TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bettor_desk_decisions_at_idx
    ON bettor_desk_decisions (decided_at DESC);
CREATE INDEX IF NOT EXISTS bettor_desk_decisions_action_idx
    ON bettor_desk_decisions (action, decided_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS bettor_desk_decisions_source_idx
    ON bettor_desk_decisions (desk_id, source_decision_id)
    WHERE source_decision_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS bettor_desk_orders (
    order_id           TEXT PRIMARY KEY,
    desk_id            TEXT NOT NULL,
    boot_id            TEXT NOT NULL,
    desk_decision_id   TEXT NOT NULL REFERENCES bettor_desk_decisions
                           (desk_decision_id) ON DELETE RESTRICT,
    condition_id       TEXT NOT NULL,
    market_id          TEXT,
    outcome_index      INTEGER NOT NULL,
    side               TEXT NOT NULL,
    intent             TEXT NOT NULL,
    limit_price        NUMERIC NOT NULL,
    qty                NUMERIC NOT NULL,
    filled_qty         NUMERIC NOT NULL DEFAULT 0,
    notional_committed NUMERIC NOT NULL,
    avg_fill_price     NUMERIC,
    fees_usd           NUMERIC NOT NULL DEFAULT 0,
    -- PROPOSED RESTING PARTIALLY_FILLED FILLED CANCEL_PENDING CANCELLED
    -- EXPIRED REJECTED
    state              TEXT NOT NULL,
    state_reason       TEXT,
    placed_at          TIMESTAMPTZ,
    expires_at         TIMESTAMPTZ,
    terminal_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bettor_desk_orders_open_idx
    ON bettor_desk_orders (condition_id, outcome_index)
    WHERE state IN ('RESTING', 'PARTIALLY_FILLED', 'CANCEL_PENDING');
CREATE INDEX IF NOT EXISTS bettor_desk_orders_created_idx
    ON bettor_desk_orders (created_at DESC);

-- APPEND-ONLY TRANSITIONS. The order row carries the current state; this
-- carries how it got there, so a cancellation race is visible as two
-- events rather than inferred from one final value.
CREATE TABLE IF NOT EXISTS bettor_desk_order_events (
    event_id      BIGSERIAL PRIMARY KEY,
    order_id      TEXT NOT NULL REFERENCES bettor_desk_orders (order_id)
                      ON DELETE CASCADE,
    at            TIMESTAMPTZ NOT NULL,
    from_state    TEXT,
    to_state      TEXT NOT NULL,
    reason        TEXT,
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bettor_desk_order_events_order_idx
    ON bettor_desk_order_events (order_id, event_id);

-- EVERY FILL NAMES THE EVIDENCE THAT PRODUCED IT. A fill with no
-- evidence row is not a fill, it is an assumption, and the column is NOT
-- NULL so one cannot be written by accident.
CREATE TABLE IF NOT EXISTS bettor_desk_fills (
    fill_id        TEXT PRIMARY KEY,
    order_id       TEXT NOT NULL REFERENCES bettor_desk_orders (order_id)
                       ON DELETE CASCADE,
    at             TIMESTAMPTZ NOT NULL,
    qty            NUMERIC NOT NULL,
    price          NUMERIC NOT NULL,
    fee_usd        NUMERIC NOT NULL,
    liquidity      TEXT NOT NULL,           -- MAKER | TAKER
    evidence_kind  TEXT NOT NULL,           -- COHORT_PRINT | OBSERVED_BOOK
    evidence_id    TEXT NOT NULL,
    evidence_ts    TIMESTAMPTZ NOT NULL,
    exec_model     TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bettor_desk_fills_order_idx
    ON bettor_desk_fills (order_id);
CREATE INDEX IF NOT EXISTS bettor_desk_fills_at_idx
    ON bettor_desk_fills (at DESC);

-- THE CONSUMPTION LEDGER, which is what stops the same printed volume
-- from filling three hypothetical orders. One row per piece of
-- evidence; `consumed_qty` may never exceed `available_qty`, and the
-- CHECK is the enforcement rather than a convention in the caller.
CREATE TABLE IF NOT EXISTS bettor_desk_consumption (
    evidence_id    TEXT PRIMARY KEY,
    evidence_kind  TEXT NOT NULL,
    evidence_ts    TIMESTAMPTZ NOT NULL,
    condition_id   TEXT NOT NULL,
    outcome_index  INTEGER NOT NULL,
    price          NUMERIC NOT NULL,
    available_qty  NUMERIC NOT NULL,
    consumed_qty   NUMERIC NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bettor_desk_consumption_not_overdrawn
        CHECK (consumed_qty >= 0 AND consumed_qty <= available_qty)
);

-- PER-LEG INVENTORY. Never netted across the two legs of a condition:
-- `bettor_inventory` already carries why, and a pair is a claim the
-- identity layer makes, not an arithmetic fact.
CREATE TABLE IF NOT EXISTS bettor_desk_positions (
    desk_id           TEXT NOT NULL,
    condition_id      TEXT NOT NULL,
    outcome_index     INTEGER NOT NULL,
    qty               NUMERIC NOT NULL DEFAULT 0,
    cost_basis_usd    NUMERIC NOT NULL DEFAULT 0,
    realized_pnl_usd  NUMERIC NOT NULL DEFAULT 0,
    fees_usd          NUMERIC NOT NULL DEFAULT 0,
    opened_at         TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled           BOOLEAN NOT NULL DEFAULT FALSE,
    settled_payout    NUMERIC,
    settled_at        TIMESTAMPTZ,
    PRIMARY KEY (desk_id, condition_id, outcome_index)
);
CREATE INDEX IF NOT EXISTS bettor_desk_positions_open_idx
    ON bettor_desk_positions (desk_id) WHERE qty > 0 AND NOT settled;

-- THE RECONCILIATION RECORD. Written every cycle so a displayed figure
-- can be traced to the snapshot that produced it, and so the invariant
-- check is stored beside the numbers it checked rather than recomputed
-- by whoever is reading.
CREATE TABLE IF NOT EXISTS bettor_desk_ledger (
    snapshot_id       BIGSERIAL PRIMARY KEY,
    desk_id           TEXT NOT NULL,
    boot_id           TEXT NOT NULL,
    at                TIMESTAMPTZ NOT NULL,
    cash_usd          NUMERIC NOT NULL,
    committed_usd     NUMERIC NOT NULL,
    inventory_cost    NUMERIC NOT NULL,
    inventory_mark    NUMERIC,
    mark_basis        TEXT NOT NULL,
    realized_pnl_usd  NUMERIC NOT NULL,
    unrealized_pnl_usd NUMERIC,
    fees_usd          NUMERIC NOT NULL,
    open_orders       INTEGER NOT NULL,
    open_positions    INTEGER NOT NULL,
    invariant_ok      BOOLEAN NOT NULL,
    invariant_detail  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bettor_desk_ledger_at_idx
    ON bettor_desk_ledger (desk_id, at DESC);
