-- ══════════════════════════════════════════════════════════════════════
-- 367 · THE KALSHI SHADOW PLANNER'S OWN RECORDS (rc6.3 kalshi-shadow).
-- ══════════════════════════════════════════════════════════════════════
--
-- SHADOW ONLY. NOTHING HERE IS AN ORDER, A FILL, A POSITION OR AN AUTHORITY.
-- Two NEW tables; no existing table, column, constraint or row is touched,
-- so the previous release runs unchanged on this schema (nothing it reads or
-- writes changes) and Kalshi live money stays exactly where it was:
-- kalshi_smalllive_control (196) untouched and disabled, SMALL LIVE mode
-- untouched (225's slc_shadow_only_ck), no Kalshi credential stored.
--
-- WHY NOT kalshi_live_intents (196). That table is the LIVE lane's: the
-- Command Center's Position Rooms list its PLANNED rows as ACTUAL Kalshi
-- orders (position_rooms._actual_kalshi, MIRROR_OPEN_RAW), the originated
-- status counts its non-EXCLUDED rows as sent_or_planned, the order-state
-- truth maps PLANNED to PROPOSED, and a row's paper_order_id / mirror_id /
-- client_order_id are UNIQUE claims a future live row would need. A SHADOW
-- plan written there would show as Kalshi activity that never happened --
-- in this release and in every older release a rollback would run. So the
-- SHADOW plans live here, where no live reader looks, and the database
-- itself refuses anything but a SHADOW plan or a named exclusion.
--
-- 1 · kalshi_shadow_intents: ONE ROW PER LINKED PAPER DECISION (an ENTER
--     decision with its ENTRY BUY paper order), PLANNED or EXCLUDED with the
--     named reason, written once and never changed (append-only, below).
--     PLANNED carries what kalshi_orders.plan built at 1:<scale> of the paper
--     quantity against the decision's settlement-certified Kalshi
--     counterpart (canonical_claim_aliases), the executable walk of the
--     current Kalshi book at the plan's limit, the published fee
--     (kalshi_fees) and the all-in price, and the net EV per contract
--     against the decision's own fair probability. The would-be V2 body is
--     kept as EVIDENCE ONLY in plan_payload; there is no venue order id, no
--     submit time and no state but PLANNED / EXCLUDED, by CHECK.
--
-- 2 · kalshi_shadow_account_reads: one row per reconciliation window:
--     RECORDED (a read-only account reconciliation was written to 196's
--     kalshi_account_reconciliations -- by GET requests only) or UNAVAILABLE
--     with the named reason (no Kalshi credential in the process, ...).
--     Nothing is ever written to kalshi_account_reconciliations without a
--     real read, so a missing credential is never shown there as an
--     "UNREADABLE account".

CREATE TABLE IF NOT EXISTS kalshi_shadow_intents (
    shadow_id           text PRIMARY KEY,
    paper_decision_id   text        NOT NULL UNIQUE,
    paper_order_id      text        NOT NULL,
    account_id          text,
    strategy            text,
    us_market_slug      text        NOT NULL,
    holding             text        NOT NULL CHECK (holding IN ('LONG', 'SHORT')),
    intent              text        NOT NULL,
    paper_decided_at    timestamptz NOT NULL,
    planned_at          timestamptz NOT NULL,
    decision_age_s      numeric(14,3),
    state               text        NOT NULL CHECK (state IN ('PLANNED', 'EXCLUDED')),
    exclusion           text,
    plan_exclusion      text,
    ticker              text,
    claim_fingerprint   text,
    counterpart         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    paper_qty           numeric(18,6),
    paper_wire_price    numeric(18,6),
    paper_notional_usd  numeric(18,6),
    scale               numeric(12,2),
    raw_scaled_qty      numeric(18,6),
    live_qty            integer     NOT NULL DEFAULT 0 CHECK (live_qty >= 0),
    rounding_delta      numeric(18,6),
    venue_minimum       integer,
    limit_price         numeric(6,4) CHECK (limit_price IS NULL OR
                                            (limit_price >= 0.01 AND limit_price <= 0.99)),
    exec_vwap           numeric(18,6),
    fee_usd             numeric(18,6),
    all_in_usd          numeric(18,6),
    all_in_per_contract numeric(18,6),
    fair_p              numeric(18,6),
    fair_basis          text,
    ev_per_contract     numeric(18,6),
    ev_usd              numeric(18,6),
    book                jsonb,
    fee_terms           jsonb,
    buying_power        jsonb,
    plan_payload        jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    planner_version     text        NOT NULL,
    mode                text        NOT NULL DEFAULT 'SHADOW' CHECK (mode = 'SHADOW'),
    production_effect   text        NOT NULL DEFAULT 'NONE'
                                    CHECK (production_effect = 'NONE'),
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kshadow_intents_excluded_ck CHECK (
        (state = 'EXCLUDED') = (exclusion IS NOT NULL)),
    -- a PLANNED row is a complete plan: a ticker, at least the venue
    -- minimum, a limit on the tick range, a fee, an all-in price and a
    -- strictly positive net EV; an EXCLUDED row plans nothing
    CONSTRAINT kshadow_intents_planned_ck CHECK (
        state <> 'PLANNED' OR (
            ticker IS NOT NULL AND live_qty >= 1 AND limit_price IS NOT NULL
            AND fee_usd IS NOT NULL AND all_in_usd IS NOT NULL
            AND ev_per_contract IS NOT NULL AND ev_per_contract > 0
            AND plan_payload IS NOT NULL)),
    CONSTRAINT kshadow_intents_excluded_plans_nothing_ck CHECK (
        state <> 'EXCLUDED' OR (live_qty = 0 AND plan_payload IS NULL))
);
CREATE INDEX IF NOT EXISTS kshadow_intents_planned_at_idx
    ON kalshi_shadow_intents (planned_at DESC);
CREATE INDEX IF NOT EXISTS kshadow_intents_state_idx
    ON kalshi_shadow_intents (state, exclusion);

CREATE TABLE IF NOT EXISTS kalshi_shadow_account_reads (
    read_id             text PRIMARY KEY,
    at                  timestamptz NOT NULL,
    outcome             text        NOT NULL CHECK (outcome IN ('RECORDED', 'UNAVAILABLE')),
    reason              text,
    reconciliation_id   bigint,
    verdict             text,
    credential          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    planner_version     text        NOT NULL,
    mode                text        NOT NULL DEFAULT 'SHADOW' CHECK (mode = 'SHADOW'),
    production_effect   text        NOT NULL DEFAULT 'NONE'
                                    CHECK (production_effect = 'NONE'),
    CONSTRAINT kshadow_reads_outcome_ck CHECK (
        (outcome = 'RECORDED' AND reconciliation_id IS NOT NULL AND reason IS NULL)
        OR (outcome = 'UNAVAILABLE' AND reconciliation_id IS NULL
            AND reason IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS kshadow_reads_at_idx
    ON kalshi_shadow_account_reads (at DESC);

-- ── append-only, in the database ────────────────────────────────────────
CREATE OR REPLACE FUNCTION kalshi_shadow_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'kalshi shadow records are append-only';
END $$;

DROP TRIGGER IF EXISTS kshadow_intents_append_only ON kalshi_shadow_intents;
CREATE TRIGGER kshadow_intents_append_only
BEFORE UPDATE OR DELETE ON kalshi_shadow_intents
FOR EACH ROW EXECUTE FUNCTION kalshi_shadow_append_only();

DROP TRIGGER IF EXISTS kshadow_reads_append_only ON kalshi_shadow_account_reads;
CREATE TRIGGER kshadow_reads_append_only
BEFORE UPDATE OR DELETE ON kalshi_shadow_account_reads
FOR EACH ROW EXECUTE FUNCTION kalshi_shadow_append_only();
