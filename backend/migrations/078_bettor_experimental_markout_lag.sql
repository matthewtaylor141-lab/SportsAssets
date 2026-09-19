-- THE MARKOUT'S OWN HONESTY COLUMNS.
--
-- Owner directive 2026-09-19 22:4xZ §12: 30S / 60S / 300S markouts,
-- appended, never overwriting the decision they measure.
--
-- WHY A MARKOUT NEEDS TO RECORD ITS OWN LAG. A markout is a statement
-- about an INSTANT: "where was this position 60 seconds after the
-- decision". The institutional books reaching us today arrive through
-- the GitHub bridge every ten minutes, so the nearest observation to
-- T+60s is usually MINUTES away -- and a book eight minutes late
-- priced as a 60-second markout is not a slightly noisy measurement,
-- it is a different measurement wearing the label of the one we
-- wanted. So the target instant, the tolerance it had to land inside,
-- and the lag actually achieved are all on the row:
--
--   * inside tolerance -> OBSERVED, and the number means what it says;
--   * outside          -> NOT_IDENTIFIED, naming the nearest lag, which
--                         is itself a real finding about this regime.
--
-- The alternative -- widening the window until the markouts "work" --
-- would produce a full table of numbers that measure the bridge's
-- cadence rather than the model's edge.
--
-- WHY THE EXITABLE QUANTITY IS SEPARATE FROM THE MARKOUT. A mid
-- markout is where the quote went; an executable markout is what could
-- actually have been got out, and those are different questions on a
-- thin book. If the bid side can absorb only part of the position, the
-- executable figure covers THAT PART and exitable_qty says so. Valuing
-- the remainder at the last price would invent liquidity, which is the
-- one thing this whole lane refuses.

BEGIN;

ALTER TABLE bettor_experimental_markouts
    ADD COLUMN IF NOT EXISTS target_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS tolerance_ms     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS observed_lag_ms  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS latency_regime   TEXT,
    ADD COLUMN IF NOT EXISTS l2_evidence_id   TEXT
        REFERENCES bettor_l2_evidence (l2_evidence_id),
    ADD COLUMN IF NOT EXISTS position_qty     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS entry_vwap       DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS exitable_qty     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS exit_vwap        DOUBLE PRECISION;

-- A markout from one regime and a markout from another are not
-- comparable, exactly as their decisions are not.
ALTER TABLE bettor_experimental_markouts
    DROP CONSTRAINT IF EXISTS bettor_exp_markout_regime;
ALTER TABLE bettor_experimental_markouts
    ADD CONSTRAINT bettor_exp_markout_regime
        CHECK (latency_regime IS NULL
               OR latency_regime IN ('GITHUB_BRIDGE',
                                     'PERSISTENT_INSTITUTIONAL_WORKER'));

-- An OBSERVED markout must name the book it was taken from. Without
-- that, the number cannot be re-derived and is an assertion rather
-- than a measurement.
ALTER TABLE bettor_experimental_markouts
    DROP CONSTRAINT IF EXISTS bettor_exp_markout_observed_has_a_book;
ALTER TABLE bettor_experimental_markouts
    ADD CONSTRAINT bettor_exp_markout_observed_has_a_book
        CHECK (status <> 'OBSERVED' OR l2_book_sha IS NOT NULL);

COMMIT;
