-- ══════════════════════════════════════════════════════════════════════
-- 138 · A MODEL NAMES THE RECORDS IT WAS FIT ON, AND WHEN THEIR OUTCOMES
--       BECAME KNOWN
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT WAS MISSING. `fit_through` was the caller's word. The registry checked
-- evaluation rows against it, so a model fit on the very decisions it was then
-- scored on was reported PROSPECTIVE whenever the caller declared a window
-- that closed before them. Code-side checks now require the fit to state its
-- rows' instants; this migration makes the TRAINING SET ITSELF part of the
-- registered object:
--
--   training_provenance        {kind: RECORDS | DECLARED, decision_ids,
--                               records_sha, fixtures, weighting, ...}
--   trained_through            the latest DECISION instant in the training set
--   outcomes_available_through the latest instant any training OUTCOME became
--                              known (the later leg's settlement)
--
-- A RECORDS provenance is re-read from the ledger at registration and must
-- reproduce: every decision exists, carries the same label and feature sha,
-- and both instants lie inside `fit_through`. A DECLARED provenance (rows
-- supplied by a caller, e.g. a pure unit test) may be registered as a
-- candidate and can never be approved -- the CHECK below enforces that in
-- the database as well as in `promote`.
--
-- THE THREE COLUMNS ARE PART OF WHAT THE MODEL IS, so the arithmetic trigger
-- now fixes them with the rest -- and `created_at`, the instant the model was
-- frozen, which separates PROSPECTIVE evidence (decisions made after it) from
-- RETROSPECTIVE out-of-sample evidence (decisions after the training cutoff
-- but before the freeze).
--
-- ADDITIVE. Nullable columns on an existing table; the CHECK is NOT VALID so
-- rows already present are not re-examined, and it binds every later insert
-- and every state change.

ALTER TABLE IF EXISTS bettor_funded_models
    ADD COLUMN IF NOT EXISTS training_provenance jsonb,
    ADD COLUMN IF NOT EXISTS trained_through timestamptz,
    ADD COLUMN IF NOT EXISTS outcomes_available_through timestamptz;

DO $$
BEGIN
    IF to_regclass('bettor_funded_models') IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'bettor_funded_model_approved_is_record_bound_ck') THEN
        ALTER TABLE bettor_funded_models
            ADD CONSTRAINT bettor_funded_model_approved_is_record_bound_ck
            CHECK (state <> 'APPROVED'
                   OR (training_provenance ->> 'kind' = 'RECORDS'
                       AND trained_through IS NOT NULL
                       AND outcomes_available_through IS NOT NULL
                       AND trained_through <= fit_through
                       AND outcomes_available_through <= fit_through))
            NOT VALID;
    END IF;
END $$;

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
       OR NEW.train_rows IS DISTINCT FROM OLD.train_rows
       OR NEW.training_provenance::text IS DISTINCT FROM
          OLD.training_provenance::text
       OR NEW.trained_through IS DISTINCT FROM OLD.trained_through
       OR NEW.outcomes_available_through IS DISTINCT FROM
          OLD.outcomes_available_through
       -- THE FREEZE INSTANT. `created_at` is when the model was registered and
       -- could no longer change; PROSPECTIVE evidence is only what it predicted
       -- for decisions made after this, so it is not editable either.
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'model % version % is a fitted object; its arithmetic, '
                        'its fit window and its training records are not '
                        'edited. Register a NEW version instead',
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

COMMENT ON COLUMN bettor_funded_models.training_provenance IS
    'The training set: RECORDS (decision ids re-read and verified at '
    'registration) or DECLARED (caller-supplied rows; never approvable).';
COMMENT ON COLUMN bettor_funded_models.outcomes_available_through IS
    'The latest instant any training outcome became known. Must not exceed '
    'fit_through, or the fit saw a result after the window it declares.';
