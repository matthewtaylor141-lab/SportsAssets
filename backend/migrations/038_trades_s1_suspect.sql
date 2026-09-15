-- Round 42 (design round): the uncorroborated verdict splits
-- DETECTION from ARREST. A first qualification records a durable,
-- visible, NON-disarming suspect on the row itself (the round-3
-- anchoring law: state lives on the durable row, never in process
-- memory); only a suspicion that survives the hold with fresh
-- covering evidence and a live index becomes the sticky trip.
ALTER TABLE trades ADD COLUMN IF NOT EXISTS s1_suspect_at timestamptz;
CREATE INDEX IF NOT EXISTS trades_s1_suspect_idx ON trades (whale_id)
  WHERE source = 's1' AND s1_suspect_at IS NOT NULL
        AND s1_checked_at IS NULL;
