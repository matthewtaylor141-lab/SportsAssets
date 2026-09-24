-- 107 — THE BASELINE, FIXED BEFORE THE OUTCOME EXISTED.
--
-- `workers/rn1x_model_loop.evaluate` compared the model against
--
--     base = sum(y) / len(y)
--
-- the base rate OF THE EVALUATION LABELS. That is not a baseline, it is an
-- oracle: it is computed from the very outcomes the model is being scored
-- against, so it cannot be beaten by luck and cannot be compared to
-- fairly. A baseline has to be fixed before the outcomes exist, exactly as
-- the predictions are.
--
-- The loop already holds the right number: the TRAINING base rate from the
-- fit that produced the model (`dataset_base_rate_uncensored`), known at
-- prediction time and independent of every label being scored. This column
-- stores it ON THE PREDICTION, so the comparison is per row and survives
-- the heartbeat being overwritten by the next cycle.
--
-- EXISTING ROWS GET NULL, NOT A GUESS. The 159 predictions already joined
-- were written without a stored prior, and back-filling them from today's
-- training rate would be inventing a number that was never used. The
-- evaluation reports them as "baseline unavailable for these rows" and
-- does not substitute the evaluation base rate -- which is the whole point
-- of this migration.

BEGIN;

ALTER TABLE rn1x_model_predictions
    ADD COLUMN IF NOT EXISTS baseline_p double precision;

ALTER TABLE rn1x_model_predictions
    ADD COLUMN IF NOT EXISTS baseline_basis text;

COMMENT ON COLUMN rn1x_model_predictions.baseline_p IS
    'The prior this prediction is to be scored against, fixed at '
    'prediction time -- the TRAINING base rate of the fit that produced '
    'the model. NULL means no prior was stored and no comparison is '
    'available for this row; it is never filled from the evaluation '
    'labels, which would make the baseline an oracle.';

COMMENT ON COLUMN rn1x_model_predictions.baseline_basis IS
    'Where baseline_p came from, e.g. '
    'TRAINING_BASE_RATE_UNCENSORED_AT_FIT. Named so a future baseline '
    'cannot be silently swapped for a different one.';

ALTER TABLE rn1x_model_predictions
    DROP CONSTRAINT IF EXISTS rn1x_prediction_baseline_in_range;
ALTER TABLE rn1x_model_predictions
    ADD CONSTRAINT rn1x_prediction_baseline_in_range
    CHECK (baseline_p IS NULL OR (baseline_p >= 0.0 AND baseline_p <= 1.0));

ALTER TABLE rn1x_model_predictions
    DROP CONSTRAINT IF EXISTS rn1x_prediction_baseline_basis_declared;
ALTER TABLE rn1x_model_predictions
    ADD CONSTRAINT rn1x_prediction_baseline_basis_declared
    CHECK ((baseline_p IS NULL) = (baseline_basis IS NULL));

COMMIT;
