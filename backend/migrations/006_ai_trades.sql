-- 006: AI TRADER — internal paper-trading account that copies the reference
-- whale at a size ratio, filled from the LIVE residual order book at our
-- real reaction time. Each row stores the counterfactual (his price, same
-- clip) so slippage impact on profitability is measured in dollars.

CREATE TABLE IF NOT EXISTS ai_trades (
    id                  BIGSERIAL PRIMARY KEY,
    trade_id            BIGINT UNIQUE REFERENCES trades (id),
    whale_username      TEXT,
    asset               TEXT NOT NULL,
    condition_id        TEXT,
    side                TEXT NOT NULL,
    his_price           NUMERIC(10, 6) NOT NULL,
    his_notional        NUMERIC(24, 6),
    reaction_s          NUMERIC(10, 3),          -- his fill -> our simulated placement
    clip_target         NUMERIC(24, 6) NOT NULL, -- ratio * his notional
    filled_notional     NUMERIC(24, 6) NOT NULL DEFAULT 0,
    fill_vwap           NUMERIC(10, 6),          -- depth-walked achievable price
    shares              NUMERIC(24, 6) NOT NULL DEFAULT 0,
    slippage_cents      NUMERIC(10, 4),
    venue               TEXT NOT NULL DEFAULT 'polymarket',
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'settled', 'missed')),
    placed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    payout              NUMERIC(6, 2),
    pnl                 NUMERIC(24, 6),
    counterfactual_pnl  NUMERIC(24, 6),          -- at HIS price, same clip target
    settled_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ai_trades_placed_idx ON ai_trades (placed_at DESC);
CREATE INDEX IF NOT EXISTS ai_trades_open_idx ON ai_trades (status) WHERE status = 'open';
