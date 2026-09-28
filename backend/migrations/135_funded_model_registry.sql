-- ══════════════════════════════════════════════════════════════════════
-- 135 · THE MODEL THAT MADE THE PREDICTION, AND THE AUTHORITY TO USE IT
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT WAS MISSING, AND THE CRITICISM WAS EXACT:
--
--     "A ledger or bound check alone is not self-improvement."
--
-- 132 and 134 record what was DECIDED and what RESULTED. Neither records what
-- ESTIMATE the decision was made from, or which model produced it -- so a later
-- reader can see that a decision was wrong and cannot see what to fix. And with
-- no registry there is no such thing as an approved model: whatever the running
-- code happened to compute was used, and a change to it was a deploy rather than
-- a promotion anyone evaluated.
--
-- ── 1 · THE PREDICTION, ON THE DECISION, BEFORE THE OUTCOME ──────────
--
-- Four columns on `bettor_funded_decisions`, which is ALREADY immutable by
-- trigger -- that is why they belong there rather than in a side table a later
-- write could adjust. A prediction that can be edited after its outcome is known
-- is not a prediction.
--
--   `model_key`     WHICH question this model answers
--   `model_version` WHICH fitted instance answered it
--   `features`      THE EXACT VECTOR SCORED, as it stood at decision time
--   `feature_sha`   its identity, so a re-fit cannot quietly change what a
--                   recorded prediction was made from
--   `predicted`     what the model said, with its own basis
--
-- ── 2 · THE REGISTRY, AND WHY A STATE MACHINE ───────────────────────
--
-- CANDIDATE -> APPROVED -> RETIRED, and only one APPROVED version per key at a
-- time, enforced by a partial unique index rather than by a convention. The
-- decision path reads the APPROVED row; a CANDIDATE is fitted and evaluated and
-- has no authority over a decision at all.
--
-- `fit_through` IS THE HONESTY CONSTRAINT AND IT IS NOT NULLABLE. It is the last
-- instant whose data the fit could see. An evaluation is only prospective on
-- decisions made strictly AFTER it, and the promotion code checks that against
-- this column rather than against the evaluator's word for it -- because a model
-- evaluated on rows it was fitted to is measuring its own memory, and that is
-- how a model with no skill gets promoted.
--
-- ROLLBACK IS A FIRST-CLASS TRANSITION, not a deploy. `superseded_by` and
-- `retired_reason` make the history readable: which version replaced which, and
-- whether it was replaced by a promotion or pulled back after one.
--
-- ADDITIVE: four nullable columns and one new table. KEEP OUT OF PRODUCTION
-- until its consumer is released with it.

BEGIN;

-- ── 1 · WHAT THE DECISION WAS PREDICTED FROM ────────────────────────
ALTER TABLE bettor_funded_decisions
    ADD COLUMN IF NOT EXISTS model_key text,
    ADD COLUMN IF NOT EXISTS model_version text,
    ADD COLUMN IF NOT EXISTS features jsonb,
    ADD COLUMN IF NOT EXISTS feature_sha text,
    ADD COLUMN IF NOT EXISTS predicted jsonb;

COMMENT ON COLUMN bettor_funded_decisions.feature_sha IS
    'Identity of the exact vector scored. A later re-fit changes the model, '
    'not what this decision was made from, and this column is what makes that '
    'checkable rather than asserted.';

CREATE INDEX IF NOT EXISTS bettor_funded_decisions_model_idx
    ON bettor_funded_decisions (model_key, model_version, decided_at);

-- ── 2 · THE REGISTRY ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bettor_funded_models (
    model_id        text PRIMARY KEY,
    model_key       text        NOT NULL,
    model_version   text        NOT NULL,
    state           text        NOT NULL DEFAULT 'CANDIDATE',
    kernel          text        NOT NULL,
    estimator       text        NOT NULL,
    features        text[]      NOT NULL,
    params          jsonb       NOT NULL,
    -- THE LAST INSTANT THE FIT COULD SEE. Not nullable: without it there is no
    -- way to tell a prospective evaluation from a re-read of the training set.
    fit_through     timestamptz NOT NULL,
    train_rows      int         NOT NULL,
    train_base_rate numeric,
    evaluation      jsonb,
    -- ── THE PROMOTION RECORD ─────────────────────────────────────
    approved_at     timestamptz,
    approved_by     text,
    retired_at      timestamptz,
    retired_reason  text,
    superseded_by   text,
    created_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT bettor_funded_model_state_ck CHECK (
        state IN ('CANDIDATE', 'APPROVED', 'RETIRED')),
    CONSTRAINT bettor_funded_model_version_uniq
        UNIQUE (model_key, model_version),
    --: AN APPROVAL NAMES WHO MADE IT. A promotion with no approver is a deploy
    --: wearing the word "approved".
    CONSTRAINT bettor_funded_model_approved_ck CHECK (
        state <> 'APPROVED'
        OR (approved_at IS NOT NULL AND approved_by IS NOT NULL
            AND evaluation IS NOT NULL)),
    --: AND A RETIREMENT SAYS WHY. "Retired" with no reason loses the one fact a
    --: later reader needs: whether it was superseded or pulled back.
    CONSTRAINT bettor_funded_model_retired_ck CHECK (
        state <> 'RETIRED'
        OR (retired_at IS NOT NULL AND retired_reason IS NOT NULL))
);

--: ONE APPROVED VERSION PER KEY, ENFORCED. The decision path asks for "the
--: approved model" and there has to be exactly one answer; two would make which
--: model decided depend on row order.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_approved_model
    ON bettor_funded_models (model_key)
    WHERE state = 'APPROVED';

CREATE INDEX IF NOT EXISTS bettor_funded_models_key_idx
    ON bettor_funded_models (model_key, created_at DESC);

-- ── A FITTED MODEL'S ARITHMETIC IS NEVER EDITED ─────────────────────
--
-- The STATE moves -- that is the whole point of a registry -- but the params,
-- the features, the estimator, the kernel and `fit_through` are what the model
-- IS. Editing any of them would make every prediction already recorded against
-- this version a prediction from a model that no longer exists, and every
-- evaluation of it a measurement of something else.
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
    --: AND A STATE CANNOT GO BACKWARDS INTO CANDIDATE. A retired or approved
    --: model returning to candidacy would let an evaluation be re-run and
    --: re-approved without a new version, which is the audit trail again.
    IF NEW.state = 'CANDIDATE' AND OLD.state <> 'CANDIDATE' THEN
        RAISE EXCEPTION 'model % version % has already been %; it does not '
                        'return to CANDIDATE. Register a new version',
            OLD.model_key, OLD.model_version, OLD.state;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_model_arithmetic_is_fixed_trg
    ON bettor_funded_models;
CREATE TRIGGER bettor_funded_model_arithmetic_is_fixed_trg
    BEFORE UPDATE ON bettor_funded_models
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_model_arithmetic_is_fixed();

COMMENT ON TABLE bettor_funded_models IS
    'The models the funded lane may decide from. A CANDIDATE has no authority '
    'over any decision; the decision path reads the one APPROVED row per key. '
    'Promotion requires an evaluation on decisions made after fit_through, '
    'which is what separates measured skill from measured memory.';

COMMIT;
