-- 004: internal engine recommendations (shadow fills), recorded in OUR
-- database. Strictly separated from whale data: engine_fills is what OUR
-- model would trade; trades/api_positions are what the whales actually did.

CREATE TABLE IF NOT EXISTS engine_fills (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    venue           TEXT NOT NULL,             -- polymarket | kalshi
    market_id       TEXT NOT NULL,             -- conditionId (PM) / event ticker (Kalshi)
    outcome_id      TEXT NOT NULL,             -- token id (PM) / market ticker (Kalshi)
    league          TEXT,
    band            TEXT,
    limit_price     NUMERIC(10, 6) NOT NULL,   -- entry price the engine would take
    size_usd        NUMERIC(24, 6) NOT NULL,
    fair_value      NUMERIC(10, 6),
    edge            NUMERIC(10, 6),
    would_fill      BOOLEAN NOT NULL DEFAULT TRUE,  -- displayed book depth covered the size
    whale_alignment JSONB,
    book            JSONB,                     -- top-of-book snapshot at decision time
    settled         BOOLEAN NOT NULL DEFAULT FALSE,
    payout          NUMERIC(6, 2),
    pnl             NUMERIC(24, 6),
    settled_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    dedupe_key      TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS engine_fills_ts_idx ON engine_fills (ts DESC);
CREATE INDEX IF NOT EXISTS engine_fills_open_idx ON engine_fills (venue) WHERE NOT settled;
