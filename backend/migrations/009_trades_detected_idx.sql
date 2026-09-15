-- 009: index for the admin latency panel. latency_stats filters on
-- detected_at with source IN ('chain','poll'); without an index this is a
-- sequential scan over the full (history-imported) trades table on every
-- Admin load — the cause of slow/timed-out unlocks. Partial index keeps it
-- tiny: backfill rows (the vast majority) are excluded by definition.
CREATE INDEX IF NOT EXISTS trades_detected_live_idx
    ON trades (detected_at DESC)
    WHERE source IN ('chain', 'poll');
