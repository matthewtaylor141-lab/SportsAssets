-- 003: distinguish imported historical trades from live detections.
-- Backfilled trades must never count toward detection-latency metrics
-- (they were filled long before we imported them).

ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_source_check;
ALTER TABLE trades ADD CONSTRAINT trades_source_check
    CHECK (source IN ('chain', 'poll', 'backfill'));

-- Reclassify any already-imported history (detected far after fill time).
UPDATE trades SET source = 'backfill'
WHERE source = 'poll' AND detected_at - ts > interval '1 hour';
