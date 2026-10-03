-- 199 · ONE DECISION, TWO EXECUTIONS (owner correction 2026-10-03).
--
-- The authoritative object is the QUALIFIED investment decision. When the
-- versioned PinnAPI investment policy decides ENTER, ONE immutable execution
-- intent is written with the decision; from it two SIBLING executions fan out
-- concurrently:
--   PAPER   the full modelled quantity -> paper order -> simulated fill;
--   ACTUAL  target / 1,000, venue-valid rounding, final executable-book and
--           account checks -> one Polymarket retail order -> venue fills.
-- The actual order never waits for the paper order, its persistence or its
-- simulated fill; the paper order never waits for the venue.
--
--   execution_intents         one row per qualified decision (decision_id
--                             UNIQUE): the evidence, the paper target, the
--                             live sizing, the actual lane's state/refusal,
--                             and the high-resolution timeline.
--   execmirror_orders.execution_intent_id
--                             UNIQUE: AT MOST ONE actual submission per
--                             intent (the idempotency key of the hot path).
CREATE TABLE IF NOT EXISTS execution_intents (
    intent_id           text PRIMARY KEY,
    decision_id         text        NOT NULL UNIQUE,
    valuation_id        bigint,
    strategy            text        NOT NULL,
    policy_version      text,
    us_market_slug      text        NOT NULL,
    order_intent        text        NOT NULL,
    holding_side        text,
    group_id            text        NOT NULL,
    order_type          text        NOT NULL,
    time_in_force       text        NOT NULL,
    paper_target_qty    numeric(18,6) NOT NULL CHECK (paper_target_qty > 0),
    limit_price         numeric(18,6),
    wire_price          numeric(18,6) NOT NULL,
    book_obs_id         bigint,
    book_observed_at    timestamptz,
    decided_at          timestamptz NOT NULL,
    live_eligible       boolean     NOT NULL,
    live_eligibility    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    live_scale          numeric(18,6),
    live_raw_qty        numeric(18,6),
    live_qty            integer     CHECK (live_qty IS NULL OR live_qty >= 0),
    rounding_delta      numeric(18,6),
    actual_state        text        NOT NULL,
    actual_refusal      text,
    actual_mirror_id    text,
    evidence            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    timeline            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT execution_intents_actual_state_ck CHECK (actual_state IN (
        'PAPER_ONLY', 'DISPATCHED', 'LANE_NOT_RUNNING', 'REFUSED',
        'SUBMITTING', 'SUBMITTED', 'UNKNOWN', 'REJECTED')),
    CONSTRAINT execution_intents_refusal_ck CHECK (
        (actual_state IN ('REFUSED', 'PAPER_ONLY', 'REJECTED'))
        = (actual_refusal IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS execution_intents_created_idx
    ON execution_intents (created_at DESC);

ALTER TABLE execmirror_orders
    ADD COLUMN IF NOT EXISTS execution_intent_id text;
CREATE UNIQUE INDEX IF NOT EXISTS execmirror_orders_execution_intent_uq
    ON execmirror_orders (execution_intent_id)
    WHERE execution_intent_id IS NOT NULL;
