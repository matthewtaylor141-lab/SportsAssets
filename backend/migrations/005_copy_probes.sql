-- 005: copy-trade feasibility probes.
-- For every live-detected whale BUY, snapshot the residual order book at the
-- exact moment our executor would act (detection + fetch), and precompute
-- achievable prices for standard clip sizes. This measures whether the
-- whale's edge survives copying, per trade, continuously.

CREATE TABLE IF NOT EXISTS copy_probes (
    id               BIGSERIAL PRIMARY KEY,
    trade_id         BIGINT REFERENCES trades (id),
    whale_id         BIGINT NOT NULL,
    username         TEXT,
    asset            TEXT NOT NULL,
    side             TEXT NOT NULL,
    his_price        NUMERIC(10, 6) NOT NULL,
    his_size         NUMERIC(24, 6),
    his_notional     NUMERIC(24, 6),
    fill_ts          TIMESTAMPTZ,
    probe_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    reaction_s       NUMERIC(10, 3),   -- full workflow latency: his fill -> our probe
    book_ok          BOOLEAN NOT NULL DEFAULT FALSE,
    best_ask         NUMERIC(10, 6),
    best_ask_usd     NUMERIC(24, 6),   -- notional available at best ask
    slippage_cents   NUMERIC(10, 4),   -- (best_ask - his_price) * 100
    vwap_1k          NUMERIC(10, 6),   -- depth-weighted price to fill $1k
    vwap_5k          NUMERIC(10, 6),
    fillable_1k      BOOLEAN,
    fillable_5k      BOOLEAN,
    residual_roi_1k  NUMERIC(10, 6),   -- (1+edge)*his_price/vwap - 1
    residual_roi_5k  NUMERIC(10, 6),
    depth            JSONB,            -- top ask levels at probe time
    error            TEXT
);

CREATE INDEX IF NOT EXISTS copy_probes_whale_idx ON copy_probes (whale_id, probe_at DESC);
