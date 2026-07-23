-- 007: LIVE beta orders — real Polymarket CLOB orders placed by the copier.
-- Strictly separated from paper (ai_trades). Every order stores the raw API
-- response for auditability; settlement runs through the same resolution
-- pipeline as everything else.

CREATE TABLE IF NOT EXISTS live_orders (
    id               BIGSERIAL PRIMARY KEY,
    trade_id         BIGINT UNIQUE REFERENCES trades (id),  -- source whale trade
    whale_username   TEXT,
    asset            TEXT NOT NULL,
    condition_id     TEXT,
    side             TEXT NOT NULL,
    his_price        NUMERIC(10, 6) NOT NULL,
    reaction_s       NUMERIC(10, 3),
    limit_price      NUMERIC(10, 6) NOT NULL,   -- FOK limit (his price + max slippage cap)
    requested_usd    NUMERIC(24, 6) NOT NULL,
    requested_shares NUMERIC(24, 6) NOT NULL,
    order_id         TEXT,
    status           TEXT NOT NULL DEFAULT 'submitting'
                     CHECK (status IN ('submitting', 'filled', 'unfilled', 'rejected',
                                       'error', 'settled')),
    filled_shares    NUMERIC(24, 6) NOT NULL DEFAULT 0,
    fill_price       NUMERIC(10, 6),
    filled_usd       NUMERIC(24, 6) NOT NULL DEFAULT 0,
    raw              JSONB,                     -- full API response (audit trail)
    error            TEXT,
    placed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    payout           NUMERIC(6, 2),
    pnl              NUMERIC(24, 6),
    settled_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS live_orders_placed_idx ON live_orders (placed_at DESC);
CREATE INDEX IF NOT EXISTS live_orders_filled_idx ON live_orders (status)
    WHERE status = 'filled';
