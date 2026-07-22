-- 002: live position snapshots imported from the Data API per tracked whale.
-- The trade ledger (trades/positions tables) covers activity we observed;
-- this table mirrors each whale's CURRENT full book — including positions
-- opened before tracking began — refreshed continuously.

CREATE TABLE IF NOT EXISTS api_positions (
    whale_id       BIGINT NOT NULL REFERENCES whales (id),
    asset          TEXT NOT NULL,             -- outcome tokenId
    condition_id   TEXT,
    outcome        TEXT,
    outcome_index  INTEGER,
    size           NUMERIC(24, 6) NOT NULL,   -- shares currently held
    avg_price      NUMERIC(10, 6),
    cur_price      NUMERIC(10, 6),
    initial_value  NUMERIC(24, 6),
    current_value  NUMERIC(24, 6),
    cash_pnl       NUMERIC(24, 6),            -- unrealized P&L per the API
    percent_pnl    NUMERIC(14, 6),
    redeemable     BOOLEAN NOT NULL DEFAULT FALSE,
    title          TEXT,
    slug           TEXT,
    event_slug     TEXT,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (whale_id, asset)
);

CREATE INDEX IF NOT EXISTS api_positions_event_idx ON api_positions (event_slug);

-- Deep history backfill bookkeeping: a whale's full past trade ledger is
-- imported once (silently — no notifications), so settled performance
-- metrics are complete from the moment tracking starts.
ALTER TABLE whales ADD COLUMN IF NOT EXISTS history_backfilled BOOLEAN NOT NULL DEFAULT FALSE;
