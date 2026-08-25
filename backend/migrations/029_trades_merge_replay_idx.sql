-- The merge replay walks each (whale, condition) in time order. The
-- existing indexes (trades_whale_ts_idx on (whale_id, ts DESC) and
-- trades_condition_idx) do not serve that walk: one orders by time
-- across ALL conditions, the other has no whale in it. On an 860k-fill
-- whale the planner falls back to a sort of the whole ledger, which is
-- why /api/admin/whale-merge-pnl timed out on its first probe.
CREATE INDEX IF NOT EXISTS trades_whale_condition_ts_idx
    ON trades (whale_id, condition_id, ts, id);
