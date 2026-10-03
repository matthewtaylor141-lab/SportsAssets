-- KALSHI SMALL-LIVE (1:1,000 execution mirror on Kalshi), credential-free and
-- OFF. No Kalshi credential exists yet; these tables hold the durable
-- control, the read-only account reconciliations that must precede any
-- submission, and the live-intent <-> paper linkage. Nothing here writes or
-- references a paper table; paper ids are stored as plain text.
--
-- THE CONTROL CANNOT BE ENABLED WITHOUT A RECONCILIATION OF THE SAME KEY.
-- enabled = true requires key_fingerprint and reconciliation_id, and the
-- composite foreign key (reconciliation_id, key_fingerprint) makes the named
-- reconciliation one recorded for THAT fingerprint. Completeness, verdict,
-- baseline acceptance and freshness are re-checked in code on every
-- submission (kalshi_venue.submission_gate).

CREATE TABLE IF NOT EXISTS kalshi_account_reconciliations (
    reconciliation_id   bigserial PRIMARY KEY,
    at                  timestamptz NOT NULL DEFAULT now(),
    key_fingerprint     text        NOT NULL,
    kalshi_env          text        NOT NULL CHECK (kalshi_env IN ('prod', 'demo')),
    verdict             text        NOT NULL
                        CHECK (verdict IN ('EMPTY', 'NOT_EMPTY', 'UNREADABLE')),
    complete            boolean     NOT NULL,
    read_only           boolean     NOT NULL DEFAULT true CHECK (read_only),
    balance_usd         numeric(18,6),
    positions           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    resting_orders      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    fills_recent        integer,
    settlements_recent  integer,
    errors              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    baseline_accepted   boolean     NOT NULL DEFAULT false,
    actor               text,
    CONSTRAINT kalshi_recon_complete_ck CHECK (
        complete = (verdict <> 'UNREADABLE')),
    CONSTRAINT kalshi_recon_baseline_ck CHECK (
        NOT baseline_accepted OR verdict = 'NOT_EMPTY'),
    CONSTRAINT kalshi_recon_key_uq UNIQUE (reconciliation_id, key_fingerprint)
);
CREATE INDEX IF NOT EXISTS kalshi_recon_fp_at_idx
    ON kalshi_account_reconciliations (key_fingerprint, at DESC);

CREATE TABLE IF NOT EXISTS kalshi_smalllive_control (
    id                  integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled             boolean     NOT NULL DEFAULT false,
    stopped             boolean     NOT NULL DEFAULT false,
    kalshi_env          text        CHECK (kalshi_env IN ('prod', 'demo')),
    key_fingerprint     text,
    reconciliation_id   bigint,
    scale               numeric(12,2) NOT NULL DEFAULT 1000 CHECK (scale > 0),
    max_order_usd       numeric(12,2) NOT NULL DEFAULT 25 CHECK (max_order_usd > 0),
    cutover_at          timestamptz,
    actor               text,
    revision            integer     NOT NULL DEFAULT 0,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kalshi_control_recon_fk FOREIGN KEY (reconciliation_id, key_fingerprint)
        REFERENCES kalshi_account_reconciliations (reconciliation_id, key_fingerprint),
    CONSTRAINT kalshi_control_enable_ck CHECK (
        NOT enabled OR (key_fingerprint IS NOT NULL AND reconciliation_id IS NOT NULL
                        AND kalshi_env IS NOT NULL))
);
INSERT INTO kalshi_smalllive_control (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- One row per paper order considered for Kalshi: the live intent derived from
-- it, or the precise exclusion. Same sizing columns as execmirror_orders.
CREATE TABLE IF NOT EXISTS kalshi_live_intents (
    link_id             text PRIMARY KEY,
    mirror_id           text        NOT NULL UNIQUE,
    paper_decision_id   text,
    paper_order_id      text        NOT NULL UNIQUE,
    paper_fill_ids      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    group_id            text,
    role                text,
    us_market_slug      text,
    ticker              text,
    holding             text        CHECK (holding IN ('LONG', 'SHORT')),
    action              text        CHECK (action IN ('buy', 'sell')),
    intent              text        NOT NULL,
    paper_qty           numeric(18,6),
    scale               numeric(12,2),
    raw_scaled_qty      numeric(18,6),
    rounded_qty         integer     NOT NULL DEFAULT 0 CHECK (rounded_qty >= 0),
    rounding_delta      numeric(18,6),
    venue_minimum       integer     NOT NULL DEFAULT 1,
    paper_wire_price    numeric(18,6),
    kalshi_price        numeric(6,4) CHECK (kalshi_price IS NULL OR
                                            (kalshi_price >= 0.01 AND kalshi_price <= 0.99)),
    price_rounding_delta numeric(18,6),
    live_notional       numeric(18,6),
    fee_estimate        numeric(18,6),
    client_order_id     text UNIQUE,
    venue_order_id      text UNIQUE,
    state               text        NOT NULL,
    exclusion           text,
    mapping             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    paper_decided_at    timestamptz,
    submit_started_at   timestamptz,
    accepted_at         timestamptz,
    cum_qty             numeric(18,6) NOT NULL DEFAULT 0,
    attempts            integer     NOT NULL DEFAULT 0,
    error               jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kalshi_intents_state_ck CHECK (state IN (
        'PLANNED', 'SUBMITTING', 'UNKNOWN', 'OPEN', 'PARTIALLY_FILLED',
        'FILLED', 'CANCEL_REQUESTED', 'CANCELLED', 'EXPIRED', 'REJECTED',
        'EXCLUDED')),
    CONSTRAINT kalshi_intents_excluded_ck CHECK (
        (state = 'EXCLUDED') = (exclusion IS NOT NULL))
);

-- Venue fills (GET /portfolio/fills), de-duplicated by trade_id: the only
-- source of a live position. An accepted order writes nothing here.
CREATE TABLE IF NOT EXISTS kalshi_live_fills (
    trade_id            text PRIMARY KEY,
    venue_order_id      text        NOT NULL,
    link_id             text REFERENCES kalshi_live_intents,
    ticker              text        NOT NULL,
    side                text        NOT NULL,
    action              text        NOT NULL,
    count               numeric(18,6) NOT NULL CHECK (count > 0),
    price               numeric(18,6) NOT NULL CHECK (price > 0 AND price < 1),
    fee_usd             numeric(18,6),
    is_taker            boolean,
    created_time        timestamptz,
    observed_at         timestamptz NOT NULL DEFAULT now(),
    source              text        NOT NULL DEFAULT 'VENUE_FILLS_ENDPOINT'
                        CHECK (source = 'VENUE_FILLS_ENDPOINT')
);
CREATE INDEX IF NOT EXISTS kalshi_live_fills_order_idx ON kalshi_live_fills (venue_order_id);

CREATE TABLE IF NOT EXISTS kalshi_live_events (
    event_id            bigserial PRIMARY KEY,
    at                  timestamptz NOT NULL DEFAULT now(),
    link_id             text,
    kind                text        NOT NULL,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS kalshi_live_events_at_idx ON kalshi_live_events (at DESC);
