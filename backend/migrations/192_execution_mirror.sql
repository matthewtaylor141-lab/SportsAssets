-- 1:1,000 EXECUTION MIRROR (fresh Polymarket US account, its own credential
-- PMUS_EXECMIRROR_KEY_ID / PMUS_EXECMIRROR_SECRET_KEY).
--
-- The paper experiment stays the decision source. Every NEW paper order
-- after the cutover becomes one mirror row: the live order derived from it
-- (same contract, intent, wire price, order type, time in force and
-- expiry; quantity = paper qty / scale rounded to the nearest whole
-- contract, the rounding recorded), or the precise reason it was not sent.
-- Live fills come only from the venue's own order records. Paper and live
-- ledgers stay separate: nothing here writes a paper table.

CREATE TABLE IF NOT EXISTS execmirror_control (
    id                  integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled             boolean     NOT NULL DEFAULT false,
    stopped             boolean     NOT NULL DEFAULT false,
    flatten_on_stop     boolean     NOT NULL DEFAULT false,
    stop_done_at        timestamptz,
    scale               numeric(12,2) NOT NULL DEFAULT 1000 CHECK (scale > 0),
    rounding            text        NOT NULL DEFAULT 'NEAREST_WHOLE_CONTRACT',
    cutover_at          timestamptz,
    account_fingerprint text,
    baseline            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    max_order_usd       numeric(12,2) NOT NULL DEFAULT 25 CHECK (max_order_usd > 0),
    actor               text,
    revision            integer     NOT NULL DEFAULT 0,
    updated_at          timestamptz NOT NULL DEFAULT now()
);
INSERT INTO execmirror_control (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS execmirror_orders (
    mirror_id           text PRIMARY KEY,
    paper_order_id      text UNIQUE,
    parent_mirror_id    text,
    group_id            text,
    role                text        NOT NULL,
    strategy            text,
    us_market_slug      text        NOT NULL,
    intent              text        NOT NULL,
    order_type          text        NOT NULL,
    tif                 text        NOT NULL,
    post_only           boolean     NOT NULL DEFAULT false,
    wire_price          numeric(18,6),
    good_till           timestamptz,
    paper_qty           numeric(18,6),
    scaled_qty          numeric(18,6),
    live_qty            integer     NOT NULL DEFAULT 0 CHECK (live_qty >= 0),
    rounding_delta      numeric(18,6),
    state               text        NOT NULL,
    exclusion           text,
    venue_order_id      text UNIQUE,
    venue_state         text,
    paper_decided_at    timestamptz,
    submit_started_at   timestamptz,
    accepted_at         timestamptz,
    latency_ms          integer,
    cum_qty             numeric(18,6) NOT NULL DEFAULT 0,
    avg_px              numeric(18,6),
    fees_usd            numeric(18,6) NOT NULL DEFAULT 0,
    attempts            integer     NOT NULL DEFAULT 0,
    last_polled_at      timestamptz,
    error               jsonb,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT execmirror_orders_state_ck CHECK (state IN (
        'PLANNED', 'SUBMITTING', 'UNKNOWN', 'OPEN', 'PARTIALLY_FILLED',
        'FILLED', 'CANCEL_REQUESTED', 'CANCELLED', 'EXPIRED', 'REJECTED',
        'EXCLUDED')),
    CONSTRAINT execmirror_orders_role_ck CHECK (role IN (
        'ENTRY', 'HEDGE', 'EXIT', 'REDUCE', 'STANDING_PROTECTION',
        'ORPHAN_CLOSE', 'FLATTEN')),
    CONSTRAINT execmirror_orders_excluded_ck CHECK (
        (state = 'EXCLUDED') = (exclusion IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS execmirror_orders_open_idx ON execmirror_orders (state)
    WHERE state IN ('PLANNED', 'SUBMITTING', 'UNKNOWN', 'OPEN',
                    'PARTIALLY_FILLED', 'CANCEL_REQUESTED');
CREATE INDEX IF NOT EXISTS execmirror_orders_group_idx ON execmirror_orders (group_id);

-- One row per increase of a venue order's cumulative quantity, read from the
-- venue's order record: the only source of a live fill.
CREATE TABLE IF NOT EXISTS execmirror_fills (
    fill_key            text PRIMARY KEY,
    mirror_id           text        NOT NULL REFERENCES execmirror_orders,
    venue_order_id      text        NOT NULL,
    group_id            text,
    us_market_slug      text        NOT NULL,
    intent              text        NOT NULL,
    qty                 numeric(18,6) NOT NULL CHECK (qty > 0),
    price               numeric(18,6) NOT NULL,
    fee_usd             numeric(18,6) NOT NULL DEFAULT 0,
    observed_at         timestamptz NOT NULL DEFAULT now(),
    source              text        NOT NULL DEFAULT 'VENUE_ORDER_RECORD'
);
CREATE INDEX IF NOT EXISTS execmirror_fills_group_idx ON execmirror_fills (group_id);

CREATE TABLE IF NOT EXISTS execmirror_events (
    event_id            bigserial PRIMARY KEY,
    at                  timestamptz NOT NULL DEFAULT now(),
    mirror_id           text,
    paper_order_id      text,
    kind                text        NOT NULL,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS execmirror_events_at_idx ON execmirror_events (at DESC);

CREATE TABLE IF NOT EXISTS execmirror_snapshots (
    snapshot_id         bigserial PRIMARY KEY,
    at                  timestamptz NOT NULL DEFAULT now(),
    account_fingerprint text,
    balances            jsonb       NOT NULL DEFAULT '[]'::jsonb,
    positions           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    open_orders         integer,
    reconciliation      jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS execmirror_snapshots_at_idx ON execmirror_snapshots (at DESC);
