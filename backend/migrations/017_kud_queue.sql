-- Kalshi leg of the $1 underdog sleeve (owner directive 2026-08-08:
-- "Can you get the $1 underdog sleeve also placing at Kalshi").
--
-- The backend stays the sleeve's brain — it knows every game, its venue
-- start time, and the T-minus-5 window — but only the engine process
-- holds Kalshi credentials. So the worker enqueues one task per game
-- here and the engine's relay places it, mirroring manual_kalshi_queue.
-- UNIQUE(game_slug) is the one-entry-per-game guarantee: the enqueue is
-- ON CONFLICT DO NOTHING, so re-sweeps can never double a game.
CREATE TABLE IF NOT EXISTS kud_queue (
    id           bigserial PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz,
    game_slug    text NOT NULL UNIQUE,
    league       text NOT NULL,
    dog_outcome  text NOT NULL,
    other_outcome text NOT NULL,
    per_fill_usd numeric NOT NULL,
    take_profit  numeric NOT NULL,
    -- queued -> filled -> cashed_out, or a terminal skip reason from the
    -- engine (no_market / band_fail / held / unfilled / error).
    status       text NOT NULL DEFAULT 'queued',
    ticker       text,
    entry_price  numeric,
    qty          integer,
    exit_price   numeric,
    pnl          numeric,
    error        text
);

CREATE INDEX IF NOT EXISTS kud_queue_status_idx ON kud_queue (status);
