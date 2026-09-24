-- WHAT MIGRATION 116 COULD NOT CARRY, BECAUSE IT WAS ALREADY APPLIED.
--
-- THE DEFECT, AND IT WAS MINE. `sportsassets.scripts.migrate` records an
-- applied migration BY FILENAME and skips that name forever after. I
-- appended these statements to 116 after 116 had already run at a deploy,
-- so in production they never executed at all -- while a local
-- `psql -f migrations/116_...sql` ran the whole file and showed green.
--
-- The symptom was a bare HTTP 500 from
-- /api/command/rn1x/external/entry-evidence: it reads columns and a table
-- that existed only on the machine where the file had been replayed by
-- hand.
--
-- AN APPLIED MIGRATION IS IMMUTABLE. Everything below is what was
-- appended, moved into a file that has not run. 116 keeps only the index
-- it actually created.

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


-- ── THE ENTRY DECISION'S OWN INPUTS, ON ITS OWN ROW ──────────────────
--
-- `external_valuations` records the probability, the price, the cost, the
-- edge and the refusals. That was enough while every candidate refused
-- for want of an execution estimate. It is not enough now: the lane
-- computes a marketable fill, a size from the frozen policy, an exposure
-- measurement and a risk verdict per rail, and NONE of that was readable
-- back from production.
--
-- A directive asking for "probabilities, prices and depth, costs, sizes,
-- risk verdicts and exact refusals" cannot be answered from columns that
-- do not exist, so these four carry the rest of the decision:
--
--   execution_estimate    p_fill and its BASIS, the budget walk, the
--                         VWAP, the frozen policy's three notional
--                         figures, the observation age
--   risk_verdict          every rail with its limit, its measurement and
--                         its verdict, every state gate, and the sha of
--                         the limit set that governed
--   exposure_observed     what each rail was measured AGAINST, including
--                         the proposed position
--   settlement_comparison the condition-to-payout verdict and the
--                         fixture evidence that scoped it
--
-- They are nullable: rows written before this migration did not have
-- these inputs and must not be made to look as though they did.

ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS execution_estimate    jsonb,
    ADD COLUMN IF NOT EXISTS risk_verdict          jsonb,
    ADD COLUMN IF NOT EXISTS exposure_observed     jsonb,
    ADD COLUMN IF NOT EXISTS settlement_comparison jsonb;
