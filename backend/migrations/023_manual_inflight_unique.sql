-- 023: Audit 2026-08-21 — the manual desk's double-click guard was a
-- check-then-act SELECT with seconds of venue HTTP between it and the
-- INSERT, so two concurrent submits could both pass and both buy.
-- Manual rows are deliberately exempt from live_orders_one_fill_per_asset
-- (the desk may legitimately re-buy an asset after a ticket settles), so
-- the concurrency guard gets its own narrow index: at most ONE in-flight
-- ('submitting') manual row per asset. The second concurrent INSERT
-- raises unique_violation and the API answers "already in flight";
-- sequential re-buys are untouched because terminal rows leave the
-- predicate. Stale 'submitting' rows (process died mid-order) are reaped
-- to 'error' by the executor before each manual insert, so a crash can
-- never wedge an asset behind this index.
CREATE UNIQUE INDEX IF NOT EXISTS live_orders_manual_one_inflight
    ON live_orders (asset)
    WHERE whale_username = 'manual' AND status = 'submitting';
