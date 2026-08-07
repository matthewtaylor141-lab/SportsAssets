-- Manual desk, Kalshi leg (owner directive 2026-08-07: venue toggle —
-- Sam browses and executes on either venue). Only the ENGINE holds
-- Kalshi credentials, so desk orders for Kalshi are queued here by the
-- API and picked up by the engine's relay loop (~10s poll), which
-- places the order and reports the outcome back onto the same row.
CREATE TABLE IF NOT EXISTS manual_kalshi_queue (
    id          bigserial PRIMARY KEY,
    ticker      text NOT NULL,
    title       text,
    side        text NOT NULL DEFAULT 'yes',
    limit_price numeric NOT NULL,
    count       integer NOT NULL,
    usd         numeric NOT NULL,
    status      text NOT NULL DEFAULT 'pending',
    order_id    text,
    fill_count  integer,
    fill_price  numeric,
    error       text,
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS manual_kalshi_queue_pending
    ON manual_kalshi_queue (status) WHERE status = 'pending';
