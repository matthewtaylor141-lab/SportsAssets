-- PROSPECTIVE PREDICTIONS, recorded before their outcomes exist.
--
-- WHY A SEPARATE TABLE FROM bettor_learn_model. That register holds
-- POLICY COMPARISONS: a challenger's verdict against the frozen champion,
-- fitted from nothing. This one holds MODEL PREDICTIONS with a target, a
-- version, and a label that arrives later. Putting them in one table
-- would let a comparison verdict and a forecast be counted as the same
-- kind of evidence, which is the conflation the whole exercise is
-- against.
--
-- WHAT THE COLUMNS ENFORCE:
--
--   * `outcome_known` starts FALSE and `outcome` starts NULL, with a
--     CHECK tying them together. A prediction whose label is already
--     present at insert is not prospective, and the table refuses to
--     store one that way -- the label may only arrive by UPDATE, after
--     the horizon has elapsed.
--   * `predicted_at` and `horizon_s` are both NOT NULL, so the instant a
--     prediction becomes judgeable is arithmetic rather than opinion.
--   * `p_hat` is CHECKed into [0, 1]. A probability outside it is a bug
--     and must not be stored and later averaged.
--   * `features_present` / `features_missing` are stored per row. Feature
--     availability is not a property of the model, it is a property of
--     the row the model scored, and recording it afterwards would be
--     recording a guess.
--   * `feature_sha` fixes the exact vector scored, so a later re-fit or a
--     feature-list edit cannot quietly change what a recorded prediction
--     was made from.
--
-- UNIQUE (experiment_id, target, model_key, model_version,
-- source_trade_id) makes re-recording idempotent: a loop that restarts
-- mid-batch cannot inflate its own sample size by predicting the same
-- entry twice.

CREATE TABLE IF NOT EXISTS rn1x_model_predictions (
    id                bigserial   PRIMARY KEY,
    experiment_id     text        NOT NULL,
    target            text        NOT NULL,
    model_key         text        NOT NULL,
    model_version     text        NOT NULL,
    dataset_sha       text        NOT NULL,
    feature_sha       text        NOT NULL,
    condition_id      text        NOT NULL,
    source_trade_id   bigint      NOT NULL,
    account           text        NOT NULL,
    predicted_at      timestamptz NOT NULL,
    horizon_s         double precision NOT NULL,
    p_hat             double precision NOT NULL,
    features_present  text[]      NOT NULL DEFAULT '{}',
    features_missing  text[]      NOT NULL DEFAULT '{}',
    outcome_known     boolean     NOT NULL DEFAULT FALSE,
    outcome           integer,
    outcome_at        timestamptz,
    written_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT rn1x_model_predictions_unique
        UNIQUE (experiment_id, target, model_key, model_version,
                source_trade_id),

    CONSTRAINT rn1x_model_predictions_p_hat_is_a_probability
        CHECK (p_hat >= 0.0 AND p_hat <= 1.0),

    CONSTRAINT rn1x_model_predictions_horizon_positive
        CHECK (horizon_s > 0.0),

    -- The label and the flag cannot disagree, in either direction.
    CONSTRAINT rn1x_model_predictions_outcome_matches_flag
        CHECK ((outcome_known = FALSE AND outcome IS NULL
                AND outcome_at IS NULL)
               OR (outcome_known = TRUE AND outcome IN (0, 1)
                   AND outcome_at IS NOT NULL)),

    -- An outcome may not predate the prediction it judges.
    CONSTRAINT rn1x_model_predictions_outcome_is_later
        CHECK (outcome_at IS NULL OR outcome_at >= predicted_at)
);

CREATE INDEX IF NOT EXISTS rn1x_model_predictions_pending_idx
    ON rn1x_model_predictions (experiment_id, outcome_known, predicted_at);

-- A CHECK CANNOT TELL AN INSERT FROM AN UPDATE, and the whole point of
-- this table is that the label arrives AFTERWARDS. The constraints above
-- happily accept a row inserted with outcome_known = TRUE and a label
-- already attached -- which is a description of the past wearing a
-- prediction's clothes, and is exactly what the ledger exists to make
-- impossible. I wrote the test first, watched it not raise, and added
-- this rather than soften the test.
CREATE OR REPLACE FUNCTION rn1x_model_predictions_must_be_prospective()
RETURNS trigger AS $$
BEGIN
    IF NEW.outcome_known OR NEW.outcome IS NOT NULL
       OR NEW.outcome_at IS NOT NULL THEN
        RAISE EXCEPTION
            'a prediction must be recorded BEFORE its outcome: insert with '
            'outcome_known = false and join the label by UPDATE'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS rn1x_model_predictions_prospective_only
    ON rn1x_model_predictions;
CREATE TRIGGER rn1x_model_predictions_prospective_only
    BEFORE INSERT ON rn1x_model_predictions
    FOR EACH ROW EXECUTE FUNCTION
        rn1x_model_predictions_must_be_prospective();
