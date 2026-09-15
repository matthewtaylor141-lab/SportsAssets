-- Manual trade desk (owner directive 2026-08-07): an admin directs
-- trades ("$50 on Yankees ML") executed by the live account as its own
-- 'manual' sleeve. Manual rows live in live_orders — settlement and
-- P&L ride the existing pipeline — but are invisible to every
-- autonomous rule in BOTH directions: the one-fill-per-asset claim now
-- scopes to non-manual rows, so an admin ticket neither blocks nor is
-- blocked by the copy paths, and repeated manual buys of one market
-- are the admin's own business.
DROP INDEX IF EXISTS live_orders_one_fill_per_asset;
CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_fill_per_asset
    ON live_orders (asset)
    WHERE status IN ('submitting', 'filled', 'settled')
      AND COALESCE(whale_username, '') <> 'manual';
