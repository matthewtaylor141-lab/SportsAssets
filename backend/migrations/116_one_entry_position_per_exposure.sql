-- ONE AUTONOMOUS-ENTRY POSITION PER EXPOSURE, ENFORCED BY THE DATABASE.
--
-- `rn1x_positions` is UNIQUE on (experiment_id, policy, source_trade_id),
-- which protects the SEEDED lane: every seeded position comes from one
-- observed on-chain trade and that trade's id is the key.
--
-- AN AUTONOMOUS ENTRY HAS NO SOURCE TRADE. The column is NULL, and in
-- Postgres NULLs do not collide under a unique constraint, so two
-- entries into the same market on two successive cycles would both be
-- accepted. The lane derives its ids from the exposure and writes with
-- ON CONFLICT DO NOTHING, which makes a re-run idempotent -- but that is
-- a guard in one code path, and the harm being prevented is DUPLICATE
-- INVENTORY ON THE SAME EXPOSURE regardless of which path writes it.
--
-- WHY THESE FOUR COLUMNS AND NOT (experiment_id, policy). Keying on the
-- lane alone would forbid ever entering a second, different market --
-- which is the lane's normal operation. The exposure is the market and
-- the side, so it is the market and the side that may not be held twice.
--
-- IT IS CREATED ONLY IF THE DATA ALREADY SATISFIES IT, for the reason
-- migration 114 gives: a boot-time migration that raises on existing
-- duplicates takes the API down instead of reporting a data problem.
-- Skipped-with-a-warning is a finding somebody can act on.

DO $$
DECLARE
    dupes integer;
BEGIN
    SELECT count(*) INTO dupes FROM (
        SELECT experiment_id, policy, condition_id, outcome_index
          FROM rn1x_positions
         WHERE policy LIKE 'EXT\_%ENTRY\_%'
         GROUP BY 1, 2, 3, 4
        HAVING count(*) > 1) t;

    IF dupes = 0 THEN
        CREATE UNIQUE INDEX IF NOT EXISTS rn1x_one_entry_position
            ON rn1x_positions (experiment_id, policy, condition_id,
                               outcome_index)
         WHERE policy LIKE 'EXT\_%ENTRY\_%';
    ELSE
        RAISE WARNING
            'rn1x_one_entry_position NOT created: % duplicate group(s) '
            'already exist. Resolve the duplicates, then re-run this '
            'migration.', dupes;
    END IF;
END $$;


-- ── THE EXTERNAL SOURCE'S CALIBRATION, AS A MEASUREMENT OR NOT AT ALL ─
--
-- The risk engine's MODEL_TRUST_DRIFT gate asks whether the thing
-- producing the probability is still trustworthy. For this lane that
-- thing is PINNACLE_DEVIG_V1, an EXTERNAL bookmaker valuation whose own
-- module says of its default method: "validate in shadow, which is what
-- this source is for". Its calibration is therefore unmeasured, the gate
-- is NOT_EVALUABLE, and it BLOCKS the creation of inventory.
--
-- THIS TABLE IS WHY THAT IS A STATE AND NOT A DEAD END. The gate reads a
-- ROW, not a constant. With no row the answer is "not measured" and the
-- lane refuses; with a row the answer is whatever was measured. So the
-- blocker is cleared by producing evidence, which this lane's own
-- accumulating valuations plus settled outcomes can do -- and it cannot
-- be cleared by editing a boolean in a worker.
--
-- EVERY ROW CARRIES WHAT WOULD LET A READER REJECT IT: which window it
-- was measured over, how many resolved observations went into it, the
-- score and the metric's name, the tolerance it is being compared
-- against, and who measured it when. `within_tolerance` is the
-- comparison, stored so the gate does not recompute a policy threshold
-- at read time.
--
-- IT IS NOT A PLACE TO ASSERT CONFIDENCE. A row with sample_size 0 is
-- refused by a CHECK, because a calibration measured on nothing is not a
-- calibration.

CREATE TABLE IF NOT EXISTS external_source_calibration (
    source_version   text        NOT NULL,
    measured_at      timestamptz NOT NULL,
    window_start     timestamptz NOT NULL,
    window_end       timestamptz NOT NULL,
    sample_size      integer     NOT NULL CHECK (sample_size > 0),
    metric           text        NOT NULL,
    score            double precision NOT NULL,
    tolerance        double precision NOT NULL,
    within_tolerance boolean     NOT NULL,
    measured_by      text        NOT NULL,
    provenance       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (source_version, measured_at)
);

CREATE INDEX IF NOT EXISTS external_source_calibration_latest
    ON external_source_calibration (source_version, measured_at DESC);


-- ── A THIRD DECLARED PROVENANCE, NAMED RATHER THAN ALLOWED ───────────
--
-- `rn1x_provenance_declared` enumerates how a position came to exist:
-- RN1_SIGNAL_DERIVED (seeded from an observed on-chain trade) and
-- ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY (the acceptance harness's synthetic
-- position). An autonomous entry is neither, and the constraint correctly
-- refused it.
--
-- THE FIX IS A THIRD NAME, NOT A WIDER CHECK. Replacing the enumeration
-- with "anything non-null" would have let the next lane invent its own
-- label and make the column unreadable, which is exactly what the
-- constraint exists to prevent. The new value says what it is: this
-- system's own entry decision, priced on an external valuation, filled
-- against observed depth, with nothing submitted to a venue.
--
-- The existing values are untouched, so the acceptance position keeps its
-- synthetic, modelled, unfunded provenance exactly as recorded.

ALTER TABLE rn1x_positions
    DROP CONSTRAINT IF EXISTS rn1x_provenance_declared;

ALTER TABLE rn1x_positions
    ADD CONSTRAINT rn1x_provenance_declared
    CHECK (provenance IS NULL OR provenance = ANY (ARRAY[
        'RN1_SIGNAL_DERIVED',
        'ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY',
        'AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW'
    ])) NOT VALID;
