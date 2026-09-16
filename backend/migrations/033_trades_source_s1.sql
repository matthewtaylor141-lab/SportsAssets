-- S1 emitter rows carry source='s1' so the shadow instrument (whose
-- evidence queries filter source IN ('chain','poll')) can never see
-- the emitter's own rows as venue coverage — a wrong emission must not
-- be able to silence the orphan alarms that would catch it (C2,
-- backend/docs/s1_flip_contract.md; fleet round 1).
--
-- Without this the trades_source_check constraint (003) would REJECT
-- every armed S1 insert outright.

ALTER TABLE trades DROP CONSTRAINT IF EXISTS trades_source_check;
ALTER TABLE trades ADD CONSTRAINT trades_source_check
    CHECK (source IN ('chain', 'poll', 'backfill', 's1'));

-- The detection partial index (009) must cover s1 rows too: they are
-- real detections and the feed/latency queries will include them. The
-- shadow's ('chain','poll') predicate still implies this wider
-- predicate, so its plans keep using the index.
DROP INDEX IF EXISTS trades_detected_live_idx;
CREATE INDEX trades_detected_live_idx
    ON trades (detected_at DESC)
    WHERE source IN ('chain', 'poll', 's1');
