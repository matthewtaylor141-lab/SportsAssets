-- 027: desk cash-out, Kalshi leg (owner directive 2026-08-22). The desk
-- can now SELL a held Kalshi position: sells queue in the same relay
-- table the buys use, distinguished by action, and the engine's relay
-- places them (it alone holds Kalshi credentials, and it clamps the
-- requested count to what the account actually holds). Existing rows
-- are all buys — the default backfills them truthfully.

ALTER TABLE manual_kalshi_queue
    ADD COLUMN IF NOT EXISTS action text NOT NULL DEFAULT 'buy';
