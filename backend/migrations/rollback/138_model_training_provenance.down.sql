-- down for 138: restore 135's trigger body, then drop the CHECK and the three
-- columns. Dropping the columns discards every model's training provenance;
-- a model registered under 138 is then indistinguishable from a DECLARED one,
-- which is why `promote` refuses any model whose provenance it cannot read.
CREATE OR REPLACE FUNCTION bettor_funded_model_arithmetic_is_fixed()
RETURNS trigger AS $$
BEGIN
    IF NEW.model_key IS DISTINCT FROM OLD.model_key
       OR NEW.model_version IS DISTINCT FROM OLD.model_version
       OR NEW.kernel IS DISTINCT FROM OLD.kernel
       OR NEW.estimator IS DISTINCT FROM OLD.estimator
       OR NEW.features IS DISTINCT FROM OLD.features
       OR NEW.params::text IS DISTINCT FROM OLD.params::text
       OR NEW.fit_through IS DISTINCT FROM OLD.fit_through
       OR NEW.train_rows IS DISTINCT FROM OLD.train_rows THEN
        RAISE EXCEPTION 'model % version % is a fitted object; its arithmetic '
                        'and its fit window are not edited. Register a NEW '
                        'version instead',
            OLD.model_key, OLD.model_version;
    END IF;
    IF NEW.state = 'CANDIDATE' AND OLD.state <> 'CANDIDATE' THEN
        RAISE EXCEPTION 'model % version % has already been %; it does not '
                        'return to CANDIDATE. Register a new version',
            OLD.model_key, OLD.model_version, OLD.state;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

ALTER TABLE IF EXISTS bettor_funded_models
    DROP CONSTRAINT IF EXISTS bettor_funded_model_approved_is_record_bound_ck;
ALTER TABLE IF EXISTS bettor_funded_models
    DROP COLUMN IF EXISTS training_provenance,
    DROP COLUMN IF EXISTS trained_through,
    DROP COLUMN IF EXISTS outcomes_available_through;
