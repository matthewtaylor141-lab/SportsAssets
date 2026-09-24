-- 104 · TIMESTAMP INTEGRITY FOR THE RN1-SEEDED EXPERIMENT
--
-- THE DEFECT, STATED PLAINLY. `bettor_rn1x_run` set
--     detected_ts = max(source_ts, detected_at)
-- and then
--     decision_ts = detected_ts
-- and the second line carried a comment saying it existed so that the
-- `rn1x_clocks_ordered` CHECK would pass. A constraint satisfied by
-- construction is not a constraint; it is a statement that stopped being
-- checked. Two distinct losses followed.
--
--   1. THE RECEIPT TIMESTAMP WAS OVERWRITTEN. The chain lane's
--      `detected_at` legitimately PRECEDES the fill's own `ts` -- median
--      -0.6 s over 77,712 RN1 fills, and every chain account shows a
--      negative median. So max() silently replaced an OBSERVED receipt
--      instant with a DERIVED availability instant, in a column named
--      `detected_ts`. The column did not hold what its name claimed, and
--      the raw value was never persisted anywhere.
--
--   2. THE DECISION TIMESTAMP WAS BACKDATED. The prospective lane's
--      worker decides when its cycle actually runs. The traced
--      prospective position carries source_ts = detected_ts =
--      decision_ts = 2026-09-23T22:14:47Z, which is not three
--      observations; it is one instant copied twice. The cycle that
--      wrote it ran later. An order stamped at the earlier instant is
--      then modelled as able to consume prints that occurred BEFORE IT
--      EXISTED, which is look-ahead, and the fill loop's own filter
--      (`at > decision_ts`) made exactly that admission.
--
-- THE REPAIR. Four clocks, and which are observed is part of the record:
--
--   source_ts     the venue's own instant for the fill        OBSERVED
--   detected_ts   when our pipeline recorded seeing it        OBSERVED
--   available_at  max(source_ts, detected_ts)                 DERIVED
--   decision_ts   when the policy actually decided            OBSERVED
--
-- `available_at` is the conservative availability timestamp the previous
-- code was really computing, now stored SEPARATELY instead of
-- overwriting an observed column. The CHECK constrains the DERIVED value
-- against the observed ones and requires a decision to follow
-- availability. It no longer requires detected_ts >= source_ts, because
-- measurement contradicts that ordering and a constraint contradicted by
-- the data is how the prospective lane wedged in the first place.

ALTER TABLE rn1x_positions
    ADD COLUMN IF NOT EXISTS available_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS decision_basis  TEXT,
    ADD COLUMN IF NOT EXISTS decision_lag_s  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS clock_integrity TEXT;

-- ── the audit, and the labelling it obliges ─────────────────────────
--
-- BACKFILL FIRST, so the constraints below are validated against real
-- rows rather than added NOT VALID and forgotten. For every pre-existing
-- row, detected_ts already holds max(source_ts, detected_at), so
-- GREATEST() reproduces the availability instant exactly and
-- decision_ts = available_at holds by construction -- which is the
-- defect, recorded rather than hidden.
UPDATE rn1x_positions
   SET available_at = GREATEST(source_ts, detected_ts)
 WHERE available_at IS NULL;

-- EVERY ROW WRITTEN BEFORE THIS MIGRATION IS LABELLED. An unresolved
-- outcome does not prove a decision was made prospectively: it proves
-- only that the market had not settled when the row was written. These
-- rows do not record when their policy actually ran, so they cannot
-- support a prospective claim, and the label says so in the row itself
-- rather than in a document beside it.
UPDATE rn1x_positions
   SET decision_basis = 'BACKDATED_TO_AVAILABILITY_UNAUDITED',
       clock_integrity =
           'DECISION_TS_NOT_OBSERVED. decision_ts was written equal to '
           'max(source_ts, detected_at) to satisfy a CHECK, not read '
           'from the clock at the moment the policy ran. The raw receipt '
           'instant was overwritten by that same max() and was never '
           'persisted, so it cannot be recovered for this row. Any fill '
           'on this position may have been modelled against a print that '
           'preceded the order''s actual creation. An unresolved outcome '
           'here does NOT establish a prospective decision.'
 WHERE decision_basis IS NULL;

ALTER TABLE rn1x_positions DROP CONSTRAINT IF EXISTS rn1x_clocks_ordered;

-- RE-RUNNABLE. `ADD CONSTRAINT` has no IF NOT EXISTS, so each one is
-- dropped first. Without this the migration raises DuplicateObjectError
-- on a second application -- which is not hypothetical: the deploy path
-- re-applies migrations, and a test caught this on the first re-run.
ALTER TABLE rn1x_positions
    DROP CONSTRAINT IF EXISTS rn1x_available_at_is_the_later;
ALTER TABLE rn1x_positions
    DROP CONSTRAINT IF EXISTS rn1x_decision_follows_availability;
ALTER TABLE rn1x_positions
    DROP CONSTRAINT IF EXISTS rn1x_decision_basis_declared;

-- The DERIVED value is checked against the OBSERVED ones. This is the
-- direction that cannot be gamed: taking the later of two observations
-- can only delay when we treat a fill as known, never advance it.
ALTER TABLE rn1x_positions
    ADD CONSTRAINT rn1x_available_at_is_the_later
        CHECK (available_at IS NULL
               OR (available_at >= source_ts
                   AND available_at >= detected_ts));

-- A decision cannot precede the instant its evidence became available.
-- Note what is NOT asserted: nothing here forces decision_ts to EQUAL
-- availability, which is what the old constraint effectively did.
ALTER TABLE rn1x_positions
    ADD CONSTRAINT rn1x_decision_follows_availability
        CHECK (available_at IS NULL OR decision_ts >= available_at);

ALTER TABLE rn1x_positions
    ADD CONSTRAINT rn1x_decision_basis_declared
        CHECK (decision_basis IS NULL OR decision_basis IN (
            'RUNTIME_WALL_CLOCK',
            'REPLAY_AT_AVAILABILITY',
            'BACKDATED_TO_AVAILABILITY_UNAUDITED'));

-- ── orders: when were they ACTUALLY created ─────────────────────────
--
-- `placed_at` is the modelled instant the lifecycle used. It is kept,
-- because the replay's arithmetic is built on it. What was missing is
-- the runtime instant, and without it there is no way to ask whether a
-- fill preceded the order.
ALTER TABLE rn1x_orders
    ADD COLUMN IF NOT EXISTS created_at_runtime TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_at_basis   TEXT;

UPDATE rn1x_orders
   SET created_at_basis = 'NOT_RECORDED_BEFORE_MIGRATION_104'
 WHERE created_at_basis IS NULL;

-- ── the look-ahead guard, as a trigger ──────────────────────────────
--
-- A CHECK cannot read another table, so this is a trigger for the same
-- reason migration 102's prospectivity guard is one. It fires only where
-- the runtime instant is KNOWN: rows predating this migration have NULL
-- there and are governed by their label instead, because inventing a
-- creation time for them would be the original defect again.
CREATE OR REPLACE FUNCTION rn1x_fill_cannot_precede_its_order()
RETURNS trigger AS $$
DECLARE
    ord_runtime TIMESTAMPTZ;
    ord_basis   TEXT;
BEGIN
    SELECT created_at_runtime, created_at_basis
      INTO ord_runtime, ord_basis
      FROM rn1x_orders
     WHERE order_id = NEW.order_id;

    IF ord_runtime IS NOT NULL AND NEW.at < ord_runtime THEN
        RAISE EXCEPTION
            'rn1x_fills: modelled fill at % precedes the actual creation '
            'of order % at % (basis %). An order created during delayed '
            'processing cannot fill against a print that preceded it.',
            NEW.at, NEW.order_id, ord_runtime, ord_basis;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS rn1x_fills_not_before_order ON rn1x_fills;
CREATE TRIGGER rn1x_fills_not_before_order
    BEFORE INSERT ON rn1x_fills
    FOR EACH ROW
    EXECUTE FUNCTION rn1x_fill_cannot_precede_its_order();

-- ── what management reads to see the damage ─────────────────────────
CREATE OR REPLACE VIEW rn1x_clock_audit AS
SELECT experiment_id,
       coalesce(decision_basis, 'UNSET')            AS decision_basis,
       count(*)                                     AS positions,
       min(decision_ts)                             AS first_decision_ts,
       max(decision_ts)                             AS last_decision_ts,
       count(*) FILTER (WHERE decision_ts = available_at) AS decision_eq_available,
       avg(decision_lag_s)                          AS avg_decision_lag_s,
       max(decision_lag_s)                          AS max_decision_lag_s
  FROM rn1x_positions
 GROUP BY 1, 2;
