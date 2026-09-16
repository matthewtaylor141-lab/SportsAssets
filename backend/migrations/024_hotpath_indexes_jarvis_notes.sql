-- 024: Audit 2026-08-21 hot-path indexes + the JARVIS notes bridge.
--
-- live_orders had no index on whale_username (live-status groups by it
-- on every poll) and none on settled_at (today-live orders by it every
-- 12s from every open page; the copies record filters on it). Partial
-- on settled rows keeps them tiny.
CREATE INDEX IF NOT EXISTS live_orders_whale_idx
    ON live_orders (whale_username);
CREATE INDEX IF NOT EXISTS live_orders_settled_at_idx
    ON live_orders (settled_at DESC)
    WHERE status IN ('settled', 'cashed_out');

-- JARVIS notes: the voice cockpit's one-way bridge to the autonomous
-- engine session. The app POSTs a note (admin-token gated); the engine
-- session reads unread notes at its check-ins via the probe workflow
-- and marks them delivered. Deliberately append-only + tiny.
CREATE TABLE IF NOT EXISTS jarvis_notes (
    id         BIGSERIAL PRIMARY KEY,
    note       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS jarvis_notes_unread
    ON jarvis_notes (id) WHERE read_at IS NULL;
