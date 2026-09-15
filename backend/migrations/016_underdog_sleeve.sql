-- Underdog cash-out sleeve (owner directive 2026-08-08): a live tandem
-- test — $1 on every MLB/tennis underdog moneyline, auto-sold when the
-- book bids a 20% profit. Rows live in live_orders as their own
-- 'underdog' sleeve, invisible to every autonomous rule in BOTH
-- directions (same isolation contract as the manual desk): the
-- one-fill-per-asset claim scopes to non-underdog rows so this sleeve
-- neither blocks nor is blocked by the copy paths.
--
-- 'cashed_out' is a NEW terminal status: settled by OUR sale, not by
-- resolution. The settlement sweep targets status='filled' only, so a
-- cashed-out row's sale P&L can never be overwritten by a later
-- resolution of the same market.

ALTER TABLE live_orders DROP CONSTRAINT IF EXISTS live_orders_status_check;
ALTER TABLE live_orders ADD CONSTRAINT live_orders_status_check
    CHECK (status IN ('submitting', 'filled', 'unfilled', 'rejected',
                      'error', 'settled', 'cashed_out'));

DROP INDEX IF EXISTS live_orders_one_fill_per_asset;
CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_fill_per_asset
    ON live_orders (asset)
    WHERE status IN ('submitting', 'filled', 'settled')
      AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog');
