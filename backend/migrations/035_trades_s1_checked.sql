-- Corroboration verdicts anchor to the ROWS, not to process memory
-- (fleet round 3). The round-2 design judged venue corroboration at
-- the shadow's finalize pass — a once-per-(tx,wallet) event in
-- process memory — so the verdict was lost to early finalizes,
-- restarts, and tombstones, and a wrong armed emission could
-- permanently evade the alarm built to catch it.
--
-- s1_checked_at makes judgment exactly-once and restart-proof: the
-- EMITTER sweeps its own unjudged rows past the maturity deadline,
-- reads venue_seen_at, counts confirmed / trips sticky on
-- uncorroborated, and stamps the row judged. The partial index keeps
-- the sweep O(unjudged).

ALTER TABLE trades ADD COLUMN IF NOT EXISTS s1_checked_at timestamptz;

CREATE INDEX IF NOT EXISTS trades_s1_unchecked_idx
    ON trades (detected_at)
    WHERE source = 's1' AND s1_checked_at IS NULL;
