-- 001_init.sql — full schema for the whale-tracking platform.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Roster ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS whales (
    id                      BIGSERIAL PRIMARY KEY,
    address                 TEXT NOT NULL UNIQUE,          -- Polymarket proxy wallet (lowercase hex)
    username                TEXT,
    pinned                  BOOLEAN NOT NULL DEFAULT FALSE, -- admin override: always tracked
    banned                  BOOLEAN NOT NULL DEFAULT FALSE, -- admin override: never tracked
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    source_rank             INTEGER,
    sports_profit_alltime   NUMERIC(20, 2),
    added_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS whales_active_idx ON whales (active) WHERE active;

-- ── Market metadata (hot cache persisted; sport classification lives here) ──

CREATE TABLE IF NOT EXISTS markets (
    condition_id     TEXT PRIMARY KEY,
    title            TEXT,
    slug             TEXT,
    event_slug       TEXT,
    event_title      TEXT,
    sport            TEXT NOT NULL DEFAULT 'unclassified',
    tags             JSONB NOT NULL DEFAULT '[]'::jsonb,
    closed           BOOLEAN NOT NULL DEFAULT FALSE,
    resolved         BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_prices  JSONB,                 -- payout per outcome index at resolution, e.g. [1, 0]
    resolved_at      TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS markets_event_idx ON markets (event_slug);
CREATE INDEX IF NOT EXISTS markets_open_idx  ON markets (closed) WHERE NOT closed;

CREATE TABLE IF NOT EXISTS market_tokens (
    token_id       TEXT PRIMARY KEY,        -- CTF ERC-1155 tokenId (decimal string)
    condition_id   TEXT NOT NULL REFERENCES markets (condition_id) ON DELETE CASCADE,
    outcome        TEXT,
    outcome_index  INTEGER
);

CREATE INDEX IF NOT EXISTS market_tokens_condition_idx ON market_tokens (condition_id);

-- ── Trade ledger ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS trades (
    id             BIGSERIAL PRIMARY KEY,
    whale_id       BIGINT NOT NULL REFERENCES whales (id),
    tx_hash        TEXT NOT NULL,
    asset          TEXT NOT NULL,           -- outcome tokenId
    condition_id   TEXT,
    side           TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    outcome        TEXT,
    outcome_index  INTEGER,
    size           NUMERIC(24, 6) NOT NULL, -- shares
    price          NUMERIC(10, 6) NOT NULL, -- USDC per share, 0..1
    notional       NUMERIC(24, 6) NOT NULL, -- size * price
    market_title   TEXT,
    market_slug    TEXT,
    event_slug     TEXT,
    sport          TEXT NOT NULL DEFAULT 'unclassified',
    ts             TIMESTAMPTZ NOT NULL,    -- fill time (block timestamp / API timestamp)
    source         TEXT NOT NULL CHECK (source IN ('chain', 'poll')),
    detected_at    TIMESTAMPTZ NOT NULL,    -- when our pipeline first saw it
    enriched_at    TIMESTAMPTZ,
    dedupe_key     TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS trades_whale_ts_idx  ON trades (whale_id, ts DESC);
CREATE INDEX IF NOT EXISTS trades_ts_idx        ON trades (ts DESC);
CREATE INDEX IF NOT EXISTS trades_condition_idx ON trades (condition_id);
CREATE INDEX IF NOT EXISTS trades_sport_idx     ON trades (sport);

-- ── Position lifecycle (rebuilt deterministically from the ledger) ──

CREATE TABLE IF NOT EXISTS positions (
    id             BIGSERIAL PRIMARY KEY,
    whale_id       BIGINT NOT NULL REFERENCES whales (id),
    condition_id   TEXT NOT NULL,
    token_id       TEXT NOT NULL,
    outcome        TEXT,
    outcome_index  INTEGER,
    net_shares     NUMERIC(24, 6) NOT NULL DEFAULT 0,
    avg_cost       NUMERIC(10, 6) NOT NULL DEFAULT 0,
    realized_pnl   NUMERIC(24, 6) NOT NULL DEFAULT 0,
    notional_in    NUMERIC(24, 6) NOT NULL DEFAULT 0,  -- total cost deployed (buys)
    resolved       BOOLEAN NOT NULL DEFAULT FALSE,
    first_trade_ts TIMESTAMPTZ,
    last_trade_ts  TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (whale_id, token_id)
);

CREATE INDEX IF NOT EXISTS positions_whale_idx ON positions (whale_id);
CREATE INDEX IF NOT EXISTS positions_open_idx  ON positions (whale_id) WHERE net_shares > 0 AND NOT resolved;

-- ── Per-sport aggregates (rollup tables, recomputed on resolution) ──

CREATE TABLE IF NOT EXISTS whale_sport_stats (
    whale_id        BIGINT NOT NULL REFERENCES whales (id),
    sport           TEXT NOT NULL,
    time_window     TEXT NOT NULL CHECK (time_window IN ('7d', '30d', 'all')),
    markets_traded  INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    scratches       INTEGER NOT NULL DEFAULT 0,
    win_pct         NUMERIC(6, 4),
    realized_pnl    NUMERIC(24, 6) NOT NULL DEFAULT 0,
    notional        NUMERIC(24, 6) NOT NULL DEFAULT 0,
    roi             NUMERIC(10, 6),
    avg_position    NUMERIC(24, 6),
    open_exposure   NUMERIC(24, 6) NOT NULL DEFAULT 0,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (whale_id, sport, time_window)
);

-- ── Notifications ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          BIGSERIAL PRIMARY KEY,
    user_key    TEXT NOT NULL,              -- anonymous client-generated id
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS push_subscriptions_user_idx ON push_subscriptions (user_key);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_key      TEXT PRIMARY KEY,
    min_notional  NUMERIC(24, 6) NOT NULL DEFAULT 0,   -- default: any trade
    muted_whales  JSONB NOT NULL DEFAULT '[]'::jsonb,  -- whale ids
    sports        JSONB NOT NULL DEFAULT '[]'::jsonb,  -- empty = all sports
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Outbox: one row per (trade, channel). Dispatcher expands to recipients,
-- applies prefs + burst collapsing, then flips sent. Restart-safe.
CREATE TABLE IF NOT EXISTS notification_outbox (
    id           BIGSERIAL PRIMARY KEY,
    trade_id     BIGINT NOT NULL REFERENCES trades (id),
    kind         TEXT NOT NULL CHECK (kind IN ('webpush', 'telegram')),
    payload      JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent         BOOLEAN NOT NULL DEFAULT FALSE,
    sent_at      TIMESTAMPTZ,
    collapsed    BOOLEAN NOT NULL DEFAULT FALSE,       -- delivered inside a burst summary
    UNIQUE (trade_id, kind)
);

CREATE INDEX IF NOT EXISTS outbox_pending_idx ON notification_outbox (kind, created_at) WHERE NOT sent;

-- ── Ops: heartbeats, reconciliation, ingestion cursors ──────────────

CREATE TABLE IF NOT EXISTS service_heartbeats (
    service    TEXT PRIMARY KEY,
    status     TEXT NOT NULL DEFAULT 'ok',
    detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
    beat_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_state (
    key    TEXT PRIMARY KEY,                -- e.g. 'chain.last_block'
    value  JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    missed       INTEGER NOT NULL DEFAULT 0,
    details      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS roster_events (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,              -- added / removed / pinned / banned / refreshed
    whale_id    BIGINT,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
