-- ══════════════════════════════════════════════════════════════════════
-- 171 · PAPER TRADING: THE FICTIONAL ACCOUNT, ITS ONE LEDGER, PAPER ORDERS,
--       SIMULATED FILLS, SETTLEMENTS, THE SESSION AND ITS CONTROL ROW
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER AUTHORIZATION, EXACT SCOPE: a $500,000 FICTIONAL bankroll. No real
-- money, no real venue orders, no funded activation. Paper execution only,
-- on live market data. Every figure these tables hold is labelled
-- LIVE_MARKET_DATA / SIMULATED_EXECUTION.
--
-- SEPARATE FROM THE FUNDED PATH, BY CONSTRUCTION. Every table here is named
-- paper_*; no column references a funded table (bettor_funded_*); no funded
-- module reads or writes these tables; every paper id carries a `paper`
-- prefix that the funded executor refuses (bettor_paper_guard, tests in
-- tests/test_paper_records_cannot_reach_the_funded_path.py).
--
-- ONE AUTHORITATIVE LEDGER. `paper_ledger` is append-only. Every cash
-- figure on every page is DERIVED from it (bettor_paper_ledger.balances):
--   cash      = sum(cash_delta_usd)
--   reserved  = sum(reserved_delta_usd)       (part of cash, not extra)
--   available = cash - reserved               (CHECKed >= 0)
-- Entry kinds and their rules (CHECKed below):
--   INITIAL_FUNDING       +500,000 cash, ONCE per account (unique index)
--   ORDER_SUBMITTED       reserve limit x qty + max fees; cash unchanged
--   FILL                  debit the FILLED cost + fees; release the filled
--                         share of the reservation
--   RESERVATION_RELEASED  cancel / expire of an unfilled remainder; no cash
--   SALE                  credit proceeds - fees
--   SETTLEMENT            credit the payout EXACTLY ONCE per position +
--                         settlement event (unique settlement_key); a loser
--                         credits 0
--   CORRECTION            a separate entry that keeps history (corrects_seq)
-- A price movement changes marks only, never cash: there is no MARK kind.
--
-- SERIALISED BY THE ACCOUNT ROW. The BEFORE INSERT trigger takes the account
-- row lock (SELECT ... FOR UPDATE), THEN assigns `seq` and computes the
-- running balances. So two concurrent reservations cannot both spend the same
-- available cash (the second sees the first's reservation or waits for it),
-- and `seq` order equals commit order -- which is what lets the live stream
-- publish committed entries in sequence without ever skipping one.
--
-- NO RESET, NO REPLENISHMENT. The account row is immutable; the ledger is
-- append-only (UPDATE and DELETE raise); INITIAL_FUNDING is unique per
-- account and CHECKed to equal the account's starting cash, which is CHECKed
-- to be 500000. There is no DEPOSIT kind.

BEGIN;

CREATE TABLE IF NOT EXISTS paper_accounts (
    account_id          text PRIMARY KEY,
    account_key         text        NOT NULL UNIQUE,
    starting_cash_usd   numeric(18,6) NOT NULL,
    currency            text        NOT NULL DEFAULT 'SIMULATED_USD',
    data_label          text        NOT NULL
                        DEFAULT 'LIVE_MARKET_DATA / SIMULATED_EXECUTION',
    reporting_tz        text        NOT NULL DEFAULT 'America/New_York',
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_accounts_paper_id_ck CHECK (account_id LIKE 'paper%'),
    CONSTRAINT paper_accounts_fictional_500k_ck CHECK (
        starting_cash_usd = 500000),
    CONSTRAINT paper_accounts_currency_ck CHECK (currency = 'SIMULATED_USD')
);

-- ── THE CONTROL ROW: the paper session runs only when this row is enabled
-- AND the process environment sets PAPER_SESSION=on. Inserted ENABLED, so
-- setting the flag starts the session once this migration applies; turning
-- the row off stops it without a deploy.
CREATE TABLE IF NOT EXISTS paper_control (
    control_key         text PRIMARY KEY,
    enabled             boolean     NOT NULL,
    why                 text,
    updated_by          text        NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_sessions (
    session_id          text PRIMARY KEY,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    started_at          timestamptz NOT NULL,
    config              jsonb       NOT NULL,
    config_sha          text        NOT NULL,
    simulator_version   text        NOT NULL,
    reporting_tz        text        NOT NULL DEFAULT 'America/New_York',
    status              text        NOT NULL DEFAULT 'ACTIVE',
    stopped_at          timestamptz,
    stopped_why         text,
    CONSTRAINT paper_sessions_paper_id_ck CHECK (session_id LIKE 'paper%'),
    CONSTRAINT paper_sessions_status_ck CHECK (status IN ('ACTIVE', 'STOPPED'))
);
-- One ACTIVE session per account: a restart RESUMES it, never opens another.
CREATE UNIQUE INDEX IF NOT EXISTS paper_sessions_one_active_idx
    ON paper_sessions (account_id) WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS paper_session_health (
    session_id          text PRIMARY KEY REFERENCES paper_sessions,
    heartbeat_at        timestamptz,
    passes              bigint      NOT NULL DEFAULT 0,
    errors              bigint      NOT NULL DEFAULT 0,
    -- EVERY ATTEMPTED VENUE MUTATION FROM THE PAPER PATH. Expected 0: the
    -- paper market-data client refuses place / modify / cancel before any
    -- network I/O and counts the attempt here.
    mutation_attempts   bigint      NOT NULL DEFAULT 0,
    last_mutation_attempt jsonb,
    last_pass           jsonb,
    last_error          text,
    recent_heartbeats   jsonb       NOT NULL DEFAULT '[]'::jsonb
);

-- ── THE MARKET DATA THE SIMULATOR USES: every book the paper path read,
-- through the read-only market-data client, with our receipt instant.
CREATE TABLE IF NOT EXISTS paper_book_observations (
    obs_id              bigserial PRIMARY KEY,
    us_market_slug      text        NOT NULL,
    observed_at         timestamptz NOT NULL,
    venue_ts            text,
    source              text        NOT NULL,
    bids                jsonb,
    offers              jsonb,
    tick                jsonb,
    market_state        text,
    error               text,
    read_basis          text        NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS paper_book_observations_slug_idx
    ON paper_book_observations (us_market_slug, observed_at);

-- ── PAPER ORDERS. Their state is mutable (the simulator advances it); every
-- change is also an append-only event below.
CREATE TABLE IF NOT EXISTS paper_orders (
    order_id            text PRIMARY KEY,
    idempotency_key     text        NOT NULL UNIQUE,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    session_id          text        NOT NULL REFERENCES paper_sessions,
    group_id            text        NOT NULL,
    role                text        NOT NULL,
    direction           text        NOT NULL,
    holding_side        text        NOT NULL,
    intent              text        NOT NULL,
    us_market_slug      text        NOT NULL,
    fixture             text,
    label               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    order_type          text        NOT NULL,
    time_in_force       text        NOT NULL,
    allow_partial       boolean     NOT NULL,
    qty                 numeric(18,6) NOT NULL,
    -- per contract, in OUR cost space: BUY = the most we pay, SELL = the
    -- least we accept. `wire_price` is the venue price the order carries.
    limit_price         numeric(18,6) NOT NULL,
    wire_price          numeric(18,6) NOT NULL,
    reserved_usd        numeric(18,6) NOT NULL DEFAULT 0,
    reserved_remaining_usd numeric(18,6) NOT NULL DEFAULT 0,
    filled_qty          numeric(18,6) NOT NULL DEFAULT 0,
    state               text        NOT NULL,
    decision_id         text,
    decided_at          timestamptz NOT NULL,
    eligible_at         timestamptz NOT NULL,
    expires_at          timestamptz NOT NULL,
    queue_ahead_qty     numeric(18,6),
    queue_basis         jsonb,
    event_source        text        NOT NULL DEFAULT 'SIMULATOR',
    simulator_version   text        NOT NULL,
    terminal_at         timestamptz,
    terminal_reason     text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_orders_paper_id_ck CHECK (order_id LIKE 'paper%'),
    CONSTRAINT paper_orders_role_ck CHECK (role IN (
        'ENTRY', 'HEDGE', 'EXIT', 'REDUCE', 'STANDING_PROTECTION')),
    CONSTRAINT paper_orders_direction_ck CHECK (direction IN ('BUY', 'SELL')),
    CONSTRAINT paper_orders_side_ck CHECK (holding_side IN ('LONG', 'SHORT')),
    CONSTRAINT paper_orders_type_ck CHECK (order_type IN (
        'MARKETABLE', 'RESTING')),
    CONSTRAINT paper_orders_tif_ck CHECK (time_in_force IN (
        'IOC', 'FOK', 'GTD')),
    CONSTRAINT paper_orders_state_ck CHECK (state IN (
        'PENDING_SIMULATION', 'RESTING', 'PARTIALLY_FILLED', 'FILLED',
        'EXPIRED', 'CANCEL_PENDING', 'CANCELED', 'REJECTED')),
    CONSTRAINT paper_orders_qty_ck CHECK (qty > 0 AND filled_qty >= 0
                                          AND filled_qty <= qty),
    CONSTRAINT paper_orders_price_ck CHECK (limit_price > 0 AND limit_price < 1
                                            AND wire_price > 0
                                            AND wire_price < 1),
    CONSTRAINT paper_orders_reservation_ck CHECK (
        reserved_usd >= 0 AND reserved_remaining_usd >= 0
        AND reserved_remaining_usd <= reserved_usd
        AND (direction = 'BUY' OR reserved_usd = 0)),
    CONSTRAINT paper_orders_simulator_ck CHECK (event_source = 'SIMULATOR')
);
CREATE INDEX IF NOT EXISTS paper_orders_open_idx ON paper_orders (state)
    WHERE state IN ('PENDING_SIMULATION', 'RESTING', 'PARTIALLY_FILLED',
                    'CANCEL_PENDING');
CREATE INDEX IF NOT EXISTS paper_orders_group_idx ON paper_orders (group_id);
-- XAVIER'S INVARIANT ON THE PAPER BOOK: at most ONE live or potentially live
-- standing protective order per group (the funded rule, migration 157).
CREATE UNIQUE INDEX IF NOT EXISTS paper_orders_one_live_standing_idx
    ON paper_orders (group_id)
    WHERE role = 'STANDING_PROTECTION'
      AND state IN ('PENDING_SIMULATION', 'RESTING', 'PARTIALLY_FILLED',
                    'CANCEL_PENDING');

CREATE TABLE IF NOT EXISTS paper_order_events (
    event_id            bigserial PRIMARY KEY,
    order_id            text        NOT NULL REFERENCES paper_orders,
    kind                text        NOT NULL,
    event_source        text        NOT NULL,
    simulator_version   text        NOT NULL,
    at                  timestamptz NOT NULL,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_order_events_kind_ck CHECK (kind IN (
        'SUBMITTED', 'ACKNOWLEDGED', 'FILL', 'EXPIRED', 'CANCEL_REQUESTED',
        'CANCELED', 'REJECTED', 'NO_FILL_EVIDENCE')),
    -- EVERY SIMULATED ACKNOWLEDGEMENT AND FILL IS LABELLED SIMULATOR.
    CONSTRAINT paper_order_events_source_ck CHECK (
        event_source IN ('SIMULATOR', 'PAPER_AGENT'))
);
CREATE INDEX IF NOT EXISTS paper_order_events_order_idx
    ON paper_order_events (order_id, event_id);

CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id             text PRIMARY KEY,
    idempotency_key     text        NOT NULL UNIQUE,
    order_id            text        NOT NULL REFERENCES paper_orders,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    session_id          text        NOT NULL REFERENCES paper_sessions,
    group_id            text        NOT NULL,
    role                text        NOT NULL,
    direction           text        NOT NULL,
    holding_side        text        NOT NULL,
    us_market_slug      text        NOT NULL,
    fixture             text,
    label               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    qty                 numeric(18,6) NOT NULL,
    price               numeric(18,6) NOT NULL,
    wire_price          numeric(18,6) NOT NULL,
    fee_usd             numeric(18,6) NOT NULL,
    gross_usd           numeric(18,6) NOT NULL,
    book_obs_id         bigint REFERENCES paper_book_observations,
    book_observed_at    timestamptz,
    filled_at           timestamptz NOT NULL,
    basis               text        NOT NULL,
    evidence            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    event_source        text        NOT NULL DEFAULT 'SIMULATOR',
    simulator_version   text        NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_fills_paper_id_ck CHECK (fill_id LIKE 'paper%'),
    CONSTRAINT paper_fills_simulator_ck CHECK (event_source = 'SIMULATOR'),
    CONSTRAINT paper_fills_qty_ck CHECK (qty > 0 AND price > 0 AND price < 1
                                         AND fee_usd >= 0),
    CONSTRAINT paper_fills_basis_ck CHECK (basis IN (
        'DEPTH_WALK_WITHIN_LIMIT', 'CROSSING_LIQUIDITY_AFTER_QUEUE'))
);
CREATE INDEX IF NOT EXISTS paper_fills_group_idx ON paper_fills (group_id);
CREATE INDEX IF NOT EXISTS paper_fills_at_idx ON paper_fills (filled_at);

-- ── THE CONSUMED-LIQUIDITY LEDGER: an observed level at one book instant is
-- consumed at most once across every paper order.
CREATE TABLE IF NOT EXISTS paper_liquidity_consumed (
    us_market_slug      text          NOT NULL,
    side_consumed       text          NOT NULL,
    wire_price          numeric(18,6) NOT NULL,
    book_obs_id         bigint        NOT NULL REFERENCES
                                      paper_book_observations,
    displayed_qty       numeric(18,6) NOT NULL,
    consumed_qty        numeric(18,6) NOT NULL,
    PRIMARY KEY (us_market_slug, side_consumed, wire_price, book_obs_id),
    CONSTRAINT paper_liquidity_consumed_ck CHECK (
        side_consumed IN ('bids', 'offers')
        AND consumed_qty >= 0 AND consumed_qty <= displayed_qty)
);

-- ── SETTLEMENTS, FROM AUTHORITATIVE EVIDENCE, WITH CORRECTION HISTORY ─────
CREATE TABLE IF NOT EXISTS paper_settlements (
    settlement_id       text PRIMARY KEY,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    position_key        text        NOT NULL,
    settlement_event_key text       NOT NULL,
    version             integer     NOT NULL,
    supersedes          text,
    group_id            text        NOT NULL,
    us_market_slug      text        NOT NULL,
    holding_side        text        NOT NULL,
    qty                 numeric(18,6) NOT NULL,
    outcome             text        NOT NULL,
    payout_per_contract numeric(18,6) NOT NULL,
    payout_usd          numeric(18,6) NOT NULL,
    evidence            jsonb       NOT NULL,
    evidence_source     text        NOT NULL,
    settled_at          timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT paper_settlements_paper_id_ck CHECK (
        settlement_id LIKE 'paper%'),
    CONSTRAINT paper_settlements_one_version_ck UNIQUE (
        position_key, settlement_event_key, version),
    CONSTRAINT paper_settlements_outcome_ck CHECK (outcome IN (
        'WON', 'LOST', 'VOID_REFUND')),
    CONSTRAINT paper_settlements_payout_ck CHECK (
        payout_per_contract >= 0 AND payout_per_contract <= 1
        AND payout_usd >= 0)
);

-- ── THE ONE LEDGER ─────────────────────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS paper_ledger_seq;
CREATE TABLE IF NOT EXISTS paper_ledger (
    seq                 bigint PRIMARY KEY,
    account_id          text        NOT NULL REFERENCES paper_accounts,
    session_id          text,
    idempotency_key     text        NOT NULL UNIQUE,
    kind                text        NOT NULL,
    cash_delta_usd      numeric(18,6) NOT NULL,
    reserved_delta_usd  numeric(18,6) NOT NULL,
    cash_after_usd      numeric(18,6) NOT NULL,
    reserved_after_usd  numeric(18,6) NOT NULL,
    order_id            text,
    fill_id             text,
    group_id            text,
    position_key        text,
    settlement_key      text,
    corrects_seq        bigint,
    event_source        text        NOT NULL,
    simulator_version   text,
    data_label          text        NOT NULL
                        DEFAULT 'LIVE_MARKET_DATA / SIMULATED_EXECUTION',
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    committed_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT paper_ledger_kind_ck CHECK (kind IN (
        'INITIAL_FUNDING', 'ORDER_SUBMITTED', 'FILL', 'RESERVATION_RELEASED',
        'SALE', 'SETTLEMENT', 'CORRECTION')),
    CONSTRAINT paper_ledger_rules_ck CHECK (
        (kind = 'INITIAL_FUNDING' AND cash_delta_usd = 500000
         AND reserved_delta_usd = 0)
        OR (kind = 'ORDER_SUBMITTED' AND cash_delta_usd = 0
            AND reserved_delta_usd > 0 AND order_id IS NOT NULL)
        OR (kind = 'FILL' AND cash_delta_usd <= 0 AND reserved_delta_usd <= 0
            AND fill_id IS NOT NULL AND order_id IS NOT NULL)
        OR (kind = 'RESERVATION_RELEASED' AND cash_delta_usd = 0
            AND reserved_delta_usd < 0 AND order_id IS NOT NULL)
        OR (kind = 'SALE' AND reserved_delta_usd = 0
            AND fill_id IS NOT NULL AND order_id IS NOT NULL)
        OR (kind = 'SETTLEMENT' AND cash_delta_usd >= 0
            AND reserved_delta_usd = 0 AND settlement_key IS NOT NULL
            AND position_key IS NOT NULL)
        OR (kind = 'CORRECTION' AND reserved_delta_usd = 0
            AND corrects_seq IS NOT NULL)),
    CONSTRAINT paper_ledger_balances_ck CHECK (
        reserved_after_usd >= 0
        AND cash_after_usd - reserved_after_usd >= 0),
    CONSTRAINT paper_ledger_source_ck CHECK (event_source IN (
        'PAPER_LEDGER', 'SIMULATOR', 'AUTHORITATIVE_SETTLEMENT_EVIDENCE')),
    CONSTRAINT paper_ledger_simulated_fills_ck CHECK (
        kind NOT IN ('FILL', 'SALE')
        OR (event_source = 'SIMULATOR' AND simulator_version IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_ledger_one_initial_funding_idx
    ON paper_ledger (account_id) WHERE kind = 'INITIAL_FUNDING';
CREATE UNIQUE INDEX IF NOT EXISTS paper_ledger_one_settlement_idx
    ON paper_ledger (settlement_key) WHERE kind = 'SETTLEMENT';
CREATE INDEX IF NOT EXISTS paper_ledger_account_idx
    ON paper_ledger (account_id, seq);

-- THE ORDERING TRIGGER: lock the account row, THEN number the entry and
-- compute its running balances from the previous committed entry.
CREATE OR REPLACE FUNCTION paper_ledger_before_insert() RETURNS trigger AS $$
DECLARE
    prev_cash numeric(18,6);
    prev_res  numeric(18,6);
    start_cash numeric(18,6);
BEGIN
    SELECT starting_cash_usd INTO start_cash FROM paper_accounts
     WHERE account_id = NEW.account_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'paper ledger entry for unknown account %',
            NEW.account_id;
    END IF;
    IF NEW.kind = 'INITIAL_FUNDING' AND NEW.cash_delta_usd <> start_cash THEN
        RAISE EXCEPTION 'INITIAL_FUNDING must equal the account''s starting '
                        'cash';
    END IF;
    SELECT cash_after_usd, reserved_after_usd INTO prev_cash, prev_res
      FROM paper_ledger WHERE account_id = NEW.account_id
     ORDER BY seq DESC LIMIT 1;
    NEW.seq := nextval('paper_ledger_seq');
    NEW.cash_after_usd := coalesce(prev_cash, 0) + NEW.cash_delta_usd;
    NEW.reserved_after_usd := coalesce(prev_res, 0) + NEW.reserved_delta_usd;
    NEW.committed_at := clock_timestamp();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS paper_ledger_before_insert_trg ON paper_ledger;
CREATE TRIGGER paper_ledger_before_insert_trg
    BEFORE INSERT ON paper_ledger
    FOR EACH ROW EXECUTE FUNCTION paper_ledger_before_insert();

-- Delivered on COMMIT only: the stream never sees an uncommitted entry.
CREATE OR REPLACE FUNCTION paper_ledger_after_insert() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('paper_ledger', NEW.seq::text);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS paper_ledger_after_insert_trg ON paper_ledger;
CREATE TRIGGER paper_ledger_after_insert_trg
    AFTER INSERT ON paper_ledger
    FOR EACH ROW EXECUTE FUNCTION paper_ledger_after_insert();

CREATE OR REPLACE FUNCTION paper_record_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: a paper record is never rewritten',
        TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS paper_ledger_append_only_trg ON paper_ledger;
CREATE TRIGGER paper_ledger_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_ledger
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();
DROP TRIGGER IF EXISTS paper_accounts_immutable_trg ON paper_accounts;
CREATE TRIGGER paper_accounts_immutable_trg
    BEFORE UPDATE OR DELETE ON paper_accounts
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();
DROP TRIGGER IF EXISTS paper_fills_append_only_trg ON paper_fills;
CREATE TRIGGER paper_fills_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_fills
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();
DROP TRIGGER IF EXISTS paper_order_events_append_only_trg
    ON paper_order_events;
CREATE TRIGGER paper_order_events_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_order_events
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();
DROP TRIGGER IF EXISTS paper_settlements_append_only_trg ON paper_settlements;
CREATE TRIGGER paper_settlements_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_settlements
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();
DROP TRIGGER IF EXISTS paper_book_observations_append_only_trg
    ON paper_book_observations;
CREATE TRIGGER paper_book_observations_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_book_observations
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();

-- A SESSION'S CONFIG AND SIMULATOR VERSION ARE FROZEN AT ITS START.
CREATE OR REPLACE FUNCTION paper_session_frozen() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'a paper session is never deleted';
    END IF;
    IF NEW.config IS DISTINCT FROM OLD.config
       OR NEW.config_sha IS DISTINCT FROM OLD.config_sha
       OR NEW.simulator_version IS DISTINCT FROM OLD.simulator_version
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.account_id IS DISTINCT FROM OLD.account_id THEN
        RAISE EXCEPTION 'a paper session''s config, simulator version, '
                        'start and account are frozen';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS paper_sessions_frozen_trg ON paper_sessions;
CREATE TRIGGER paper_sessions_frozen_trg
    BEFORE UPDATE OR DELETE ON paper_sessions
    FOR EACH ROW EXECUTE FUNCTION paper_session_frozen();

-- ── THE ONE PAPER ACCOUNT, FUNDED ONCE ────────────────────────────────
-- The same idempotency key `bettor_paper_ledger.ensure_account` uses, so a
-- re-run of this file, a deploy or a restart never funds it twice.
INSERT INTO paper_accounts (account_id, account_key, starting_cash_usd)
VALUES ('paper_acct_main', 'BETTORTOKEN_PAPER_MAIN', 500000)
ON CONFLICT DO NOTHING;
INSERT INTO paper_ledger (seq, account_id, idempotency_key, kind,
                          cash_delta_usd, reserved_delta_usd, cash_after_usd,
                          reserved_after_usd, event_source, detail)
SELECT 0, 'paper_acct_main', 'INITIAL_FUNDING:BETTORTOKEN_PAPER_MAIN',
       'INITIAL_FUNDING', 500000, 0, 0, 0, 'PAPER_LEDGER',
       '{"why": "owner-authorized fictional bankroll, funded once"}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM paper_ledger
                   WHERE account_id = 'paper_acct_main'
                     AND kind = 'INITIAL_FUNDING');

INSERT INTO paper_control (control_key, enabled, why, updated_by)
VALUES ('PAPER_SESSION', TRUE,
        'enabled at migration 171; the process also needs PAPER_SESSION=on',
        'migration 171')
ON CONFLICT DO NOTHING;

COMMENT ON TABLE paper_ledger IS
    'The ONE authoritative ledger of the fictional $500,000 paper account. '
    'Append-only; every balance is derived from it. LIVE MARKET DATA / '
    'SIMULATED EXECUTION: no entry is real money or a real venue order.';

COMMIT;
