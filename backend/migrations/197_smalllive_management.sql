-- 197 · SMALL-LIVE MANAGEMENT AND RECONCILIATION (2026-10-03).
--
-- The execution mirror copies qualified paper orders to an ACTUAL venue
-- account. From the first ACTUAL fill, Xavier owns the actual position as a
-- separate object from the paper position, and Audrey reconciles the whole
-- chain independently. Paper and actual records are never merged.
--
--   smalllive_handoffs         one row per (venue, paper group) with actual
--                              filled inventory: the actual position Xavier
--                              owns (venue fills only; an accepted order is
--                              not a fill).
--   smalllive_reviews          Xavier's reviews of the ACTUAL position with a
--                              fresh venue quote: held, committed exits,
--                              resting protection (NOT filled protection),
--                              mark, the paper decision it follows, action.
--   smalllive_reconciliations  Audrey's latest chain reconciliation per
--                              group: paper decision -> paper order -> paper
--                              fill -> live intent -> venue order -> venue
--                              fill -> handoff -> account, discrepancies named.
CREATE TABLE IF NOT EXISTS smalllive_handoffs (
    handoff_id          text PRIMARY KEY,
    venue               text        NOT NULL CHECK (venue IN ('POLYMARKET', 'KALSHI')),
    group_id            text        NOT NULL,
    us_market_slug      text        NOT NULL,
    entry_mirror_id     text        NOT NULL,
    paper_handoff_id    text,
    owner               text        NOT NULL DEFAULT 'XAVIER',
    opened_intent       text,
    live_held           numeric(18,6) NOT NULL,
    live_bought         numeric(18,6) NOT NULL,
    avg_entry_px        numeric(18,6),
    fees_usd            numeric(18,6) NOT NULL DEFAULT 0,
    first_live_fill_at  timestamptz NOT NULL,
    state               text        NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN', 'CLOSED')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (venue, group_id)
);
CREATE TABLE IF NOT EXISTS smalllive_reviews (
    review_id           text PRIMARY KEY,
    handoff_id          text        NOT NULL REFERENCES smalllive_handoffs,
    reviewed_at         timestamptz NOT NULL DEFAULT now(),
    live_held           numeric(18,6) NOT NULL,
    committed_exit_qty  numeric(18,6) NOT NULL,
    resting_protection_qty numeric(18,6) NOT NULL,
    filled_protection_qty  numeric(18,6) NOT NULL,
    quote               jsonb       NOT NULL,
    mark_value_usd      numeric(18,6),
    cost_basis_usd      numeric(18,6),
    unrealized_usd      numeric(18,6),
    paper_review_id     text,
    paper_recommendation text,
    action              text        NOT NULL,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS smalllive_reviews_handoff
    ON smalllive_reviews (handoff_id, reviewed_at DESC);
CREATE TABLE IF NOT EXISTS smalllive_reconciliations (
    group_id            text PRIMARY KEY,
    venue               text        NOT NULL,
    reconciled_at       timestamptz NOT NULL DEFAULT now(),
    status              text        NOT NULL CHECK (status IN ('MATCHED', 'DISCREPANCY', 'PENDING')),
    discrepancies       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    chain               jsonb       NOT NULL,
    changed_at          timestamptz NOT NULL DEFAULT now()
);
