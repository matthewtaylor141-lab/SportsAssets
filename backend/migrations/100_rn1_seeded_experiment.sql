-- THE RN1-SEEDED MANAGEMENT EXPERIMENT. Its own tables, its own book.
--
-- WHY IT IS NOT IN THE DESK'S TABLES. `acct_fc2d773a2afa4851` is paused
-- and ACCOUNTING_UNCERTAIN: an identifier collision means records in its
-- affected window may have been overwritten, its P&L is suppressed, and
-- the authorization is explicit that it must not be resumed or reused.
-- Writing a new experiment into the same tables would mix clean records
-- with uncertain ones and the uncertainty would spread rather than stay
-- contained. So this experiment gets its own dataset, keyed on its own
-- experiment id, and nothing here references the desk's account.
--
-- IT ALSO HOLDS NO CAPITAL AND PLACES NO ORDERS. Every "order" below is
-- a modelled one; `is_modelled` is CHECKed true so a row claiming
-- otherwise cannot be inserted, which is the same structural refusal the
-- observation tables use rather than a convention someone must remember.

BEGIN;

-- ── the experiment, declared once ────────────────────────────────────
--
-- The policy register and the seed rule are stored ON the experiment
-- row. A policy edited after seeing its result is a different
-- experiment and needs a different id; keeping the register in the
-- database rather than only in code is what makes that checkable later.
CREATE TABLE IF NOT EXISTS rn1x_experiments (
    experiment_id    TEXT PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    code_version     TEXT NOT NULL,
    seed_rule        JSONB NOT NULL,
    policy_register  JSONB NOT NULL,
    execution_basis  TEXT NOT NULL,
    notes            TEXT NOT NULL DEFAULT '',
    -- NO REAL MONEY, NO VENUE ORDERS. Structural, not a convention.
    is_modelled      BOOLEAN NOT NULL DEFAULT TRUE
                     CHECK (is_modelled)
);

-- ── one seeded position per (experiment, source event, policy) ───────
--
-- THE THREE CLOCKS ARE THREE COLUMNS. Collapsing them is how look-ahead
-- arrives without anyone choosing to allow it:
--   source_ts    the chain's own instant for RN1's fill
--   detected_ts  when our pipeline first saw it
--   decision_ts  when our policy acted
-- A CHECK enforces detected >= source and decision >= detected, so a
-- replay cannot grant itself the detection latency for free.
CREATE TABLE IF NOT EXISTS rn1x_positions (
    position_id      TEXT PRIMARY KEY,
    experiment_id    TEXT NOT NULL REFERENCES rn1x_experiments,
    policy           TEXT NOT NULL,
    -- THE SOURCE EVENT, named so the whole row is traceable back to one
    -- real on-chain fill.
    source_trade_id  BIGINT,
    source_account   TEXT NOT NULL,
    condition_id     TEXT NOT NULL,
    outcome_index    INTEGER NOT NULL,
    -- THE CLASSIFICATION, and UNKNOWN is a legal value.
    entry_kind       TEXT NOT NULL,
    entry_kind_why   TEXT NOT NULL,
    unknown_reason   TEXT,
    position_is_a_lower_bound BOOLEAN NOT NULL DEFAULT FALSE,
    -- THE ASSIGNED INVENTORY. Identical across policies for one source
    -- event, which is what makes the comparison about management.
    seed_qty         NUMERIC NOT NULL,
    seed_price       NUMERIC NOT NULL,
    seed_basis_usd   NUMERIC NOT NULL,
    source_ts        TIMESTAMPTZ NOT NULL,
    detected_ts      TIMESTAMPTZ NOT NULL,
    decision_ts      TIMESTAMPTZ NOT NULL,
    CONSTRAINT rn1x_clocks_ordered
        CHECK (detected_ts >= source_ts AND decision_ts >= detected_ts),
    UNIQUE (experiment_id, policy, source_trade_id)
);

CREATE INDEX IF NOT EXISTS rn1x_positions_exp_idx
    ON rn1x_positions (experiment_id, condition_id);

-- ── every decision, with the whole comparison that produced it ───────
--
-- THE COMPARISON IS STORED, NOT JUST THE CHOICE. A decision record that
-- keeps only the selected action cannot be audited: nobody can see what
-- else was on the table, what it was worth, or which alternatives had no
-- value at all. `alternatives` holds the full candidate set including
-- every refusal and its named blocker.
CREATE TABLE IF NOT EXISTS rn1x_decisions (
    decision_id      TEXT PRIMARY KEY,
    position_id      TEXT NOT NULL REFERENCES rn1x_positions,
    decision_ts      TIMESTAMPTZ NOT NULL,
    -- the evidence instant this decision stood on
    evidence_ts      TIMESTAMPTZ,
    evidence_id      TEXT,
    selected_action  TEXT,
    selection_reason TEXT NOT NULL,
    alternatives     JSONB NOT NULL,
    -- EV AT DECISION TIME, kept apart from anything realized later. A
    -- winning settlement does not make a hold the better decision
    -- beforehand, and these two columns exist so nobody can conflate
    -- them by reading one number.
    ev_at_decision_usd NUMERIC,
    ev_basis         TEXT NOT NULL,
    conditional_on_our_fill JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- WHICH INPUTS WERE MODEL, RULE OR ASSUMPTION. Stored per decision
    -- because it changes with the evidence available at that instant.
    input_labels     JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS rn1x_decisions_pos_idx
    ON rn1x_decisions (position_id, decision_ts);

-- ── modelled orders and their fills ─────────────────────────────────
CREATE TABLE IF NOT EXISTS rn1x_orders (
    order_id         TEXT PRIMARY KEY,
    position_id      TEXT NOT NULL REFERENCES rn1x_positions,
    decision_id      TEXT REFERENCES rn1x_decisions,
    condition_id     TEXT NOT NULL,
    outcome_index    INTEGER NOT NULL,
    side             TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    intent           TEXT NOT NULL,
    -- TAKER or RESTING, and they are not the same thing. A resting
    -- order's fill is NOT_IDENTIFIED and must never be modelled as
    -- certain.
    liquidity        TEXT NOT NULL CHECK (liquidity IN ('TAKER', 'RESTING')),
    limit_price      NUMERIC NOT NULL,
    qty              NUMERIC NOT NULL,
    filled_qty       NUMERIC NOT NULL DEFAULT 0,
    state            TEXT NOT NULL,
    placed_at        TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    fill_basis       TEXT NOT NULL,
    is_modelled      BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_modelled)
);

CREATE INDEX IF NOT EXISTS rn1x_orders_pos_idx
    ON rn1x_orders (position_id, state);

CREATE TABLE IF NOT EXISTS rn1x_fills (
    fill_id          TEXT PRIMARY KEY,
    order_id         TEXT NOT NULL REFERENCES rn1x_orders
                         ON DELETE CASCADE,
    at               TIMESTAMPTZ NOT NULL,
    qty              NUMERIC NOT NULL,
    price            NUMERIC NOT NULL,
    fee_usd          NUMERIC NOT NULL,
    -- WHICH OBSERVED PRINT LICENSED THIS MODELLED FILL. Without it a
    -- fill is an assertion; with it, it is traceable to a real
    -- execution by someone else.
    evidence_id      TEXT NOT NULL,
    evidence_qty     NUMERIC,
    queue_share      NUMERIC NOT NULL,
    fill_basis       TEXT NOT NULL,
    is_modelled      BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_modelled)
);

-- ── the outcome, recorded separately from the decision ──────────────
--
-- SEPARATE TABLE ON PURPOSE. Outcomes land after the fact and never
-- update a decision row, so there is no path by which a settlement can
-- rewrite the expected value that preceded it -- the same structural
-- protection bettor_state_observations uses.
CREATE TABLE IF NOT EXISTS rn1x_outcomes (
    position_id      TEXT PRIMARY KEY REFERENCES rn1x_positions,
    settled_at       TIMESTAMPTZ,
    payout_per_leg   JSONB,
    realized_cash_usd NUMERIC,
    fees_usd         NUMERIC,
    residual_qty     NUMERIC,
    residual_settled_usd NUMERIC,
    -- THE UNPAIRED REMAINDER, called out rather than netted away. A
    -- partial completion leaves directional exposure and the summary
    -- must show it.
    unpaired_qty     NUMERIC,
    turnover_usd     NUMERIC,
    committed_peak_usd NUMERIC,
    net_usd          NUMERIC,
    outcome_basis    TEXT NOT NULL,
    written_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
