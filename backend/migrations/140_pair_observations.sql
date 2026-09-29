-- ══════════════════════════════════════════════════════════════════════
-- 140 · NON-FUNDED PAIR OBSERVATIONS: THE BOOTSTRAP FOR THE PAIRING MODEL'S
--       LABELS
-- ══════════════════════════════════════════════════════════════════════
--
-- THE CIRCLE THIS BREAKS. The pairing model's labels came only from funded
-- groups whose BOTH legs were held and settled. Holding the second leg needs
-- an approved model's region probabilities; an approved model needs labels.
-- No production data could ever produce the first label.
--
-- WHAT A LABEL ACTUALLY IS. "Did both legs' own sides win" is a fact about the
-- FIXTURE and the two contracts, read from the venue's own settlement of each.
-- It does not depend on whether we held either contract. So the lane can
-- OBSERVE a pairing structure it discovers -- frozen when it was observed,
-- with the displayed prices it saw and how current they were -- and label it
-- later from the two settlements, without holding anything.
--
-- WHAT THIS DOES NOT DO. It approves nothing and changes no evidence bar. A
-- model trained on observations is a CANDIDATE like any other: record-bound
-- (138), scored prospectively and event-balanced on observations made after it
-- froze, and promoted only by a named approver. Observations are a separate
-- record SOURCE, named in each model's provenance.
--
-- ADDITIVE.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_pair_observations (
    observation_id      text PRIMARY KEY,
    observed_at         timestamptz NOT NULL,
    fixture             text        NOT NULL,
    primary_slug        text        NOT NULL,
    primary_side        text        NOT NULL,
    hedge_slug          text        NOT NULL,
    hedge_side          text        NOT NULL,
    taxonomy            text,
    structure           jsonb       NOT NULL,
    primary_cost_cents  numeric     NOT NULL,
    hedge_cost_cents    numeric     NOT NULL,
    overtime_included   boolean,
    features            jsonb       NOT NULL,
    feature_sha         text        NOT NULL,
    feature_schema_sha  text        NOT NULL,
    -- HOW CURRENT EACH DISPLAYED PRICE WAS. An observation is not an order and
    -- is not refused for an unestablished book; it RECORDS the verdict so a
    -- model trained on it states what its cost features were.
    price_basis         jsonb       NOT NULL,
    source              text        NOT NULL
                        DEFAULT 'SCHEDULED_NON_FUNDED_OBSERVATION',
    -- ── THE LABEL, written later from the venue's two settlements ──────
    label_status        text        NOT NULL DEFAULT 'AWAITING_SETTLEMENT',
    label_why           text,
    primary_settlement_price numeric,
    hedge_settlement_price   numeric,
    primary_won         boolean,
    hedge_won           boolean,
    middle_occurred     boolean,
    -- The instant WE READ the settlements: when the label became known here.
    outcome_available_at timestamptz,
    label_version       integer     NOT NULL DEFAULT 0,
    -- When the settlements were last read for this row, so the labeller
    -- rotates through what it re-reads instead of starving the newest.
    last_read_at        timestamptz,
    CONSTRAINT bettor_pair_obs_sides_ck CHECK (
        primary_side IN ('ORDER_INTENT_BUY_LONG', 'ORDER_INTENT_BUY_SHORT')
        AND hedge_side IN ('ORDER_INTENT_BUY_LONG', 'ORDER_INTENT_BUY_SHORT')),
    CONSTRAINT bettor_pair_obs_distinct_ck CHECK (primary_slug <> hedge_slug),
    CONSTRAINT bettor_pair_obs_label_status_ck CHECK (
        label_status IN ('AWAITING_SETTLEMENT', 'LABELLED', 'NOT_A_LABEL')),
    CONSTRAINT bettor_pair_obs_labelled_ck CHECK (
        (label_status = 'LABELLED') = (middle_occurred IS NOT NULL
                                       AND outcome_available_at IS NOT NULL
                                       AND primary_won IS NOT NULL
                                       AND hedge_won IS NOT NULL)),
    CONSTRAINT bettor_pair_obs_label_is_both_won_ck CHECK (
        middle_occurred IS NULL
        OR middle_occurred = (primary_won AND hedge_won))
);

-- ── A LABELLED ROW'S "WON" IS ITS SIDE AT ITS PRICE, NOTHING ELSE ──────
-- The one label definition, shared with the funded LABEL_SQL: a side won
-- only at the payout that pays it in full (LONG at 1, SHORT at 0). A push
-- is a side that did not win. Enforced here, not only in Python.
ALTER TABLE bettor_pair_observations
    DROP CONSTRAINT IF EXISTS bettor_pair_obs_won_is_the_price_ck;
ALTER TABLE bettor_pair_observations
    ADD CONSTRAINT bettor_pair_obs_won_is_the_price_ck CHECK (
        label_status <> 'LABELLED' OR (
            primary_won = CASE WHEN primary_side = 'ORDER_INTENT_BUY_LONG'
                               THEN primary_settlement_price = 1
                               ELSE primary_settlement_price = 0 END
            AND hedge_won = CASE WHEN hedge_side = 'ORDER_INTENT_BUY_LONG'
                                 THEN hedge_settlement_price = 1
                                 ELSE hedge_settlement_price = 0 END
        ) IS TRUE);

CREATE INDEX IF NOT EXISTS bettor_pair_obs_awaiting_idx
    ON bettor_pair_observations (last_read_at NULLS FIRST, observed_at)
    WHERE label_status = 'AWAITING_SETTLEMENT';
CREATE INDEX IF NOT EXISTS bettor_pair_obs_labelled_idx
    ON bettor_pair_observations (observed_at)
    WHERE label_status = 'LABELLED';

-- ── EVERY LABEL EVER WRITTEN, append-only ─────────────────────────────
CREATE TABLE IF NOT EXISTS bettor_pair_observation_labels (
    observation_id      text        NOT NULL
                        REFERENCES bettor_pair_observations (observation_id),
    label_version       integer     NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    label_status        text        NOT NULL,
    label_why           text,
    primary_settlement_price numeric,
    hedge_settlement_price   numeric,
    middle_occurred     boolean,
    reads               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (observation_id, label_version)
);

-- ── WHAT WAS OBSERVED IS FIXED; A LABEL CHANGES ONLY WITH ITS VERSION ──
CREATE OR REPLACE FUNCTION bettor_pair_observation_is_fixed()
RETURNS trigger AS $$
BEGIN
    IF NEW.observation_id IS DISTINCT FROM OLD.observation_id
       OR NEW.observed_at IS DISTINCT FROM OLD.observed_at
       OR NEW.fixture IS DISTINCT FROM OLD.fixture
       OR NEW.primary_slug IS DISTINCT FROM OLD.primary_slug
       OR NEW.primary_side IS DISTINCT FROM OLD.primary_side
       OR NEW.hedge_slug IS DISTINCT FROM OLD.hedge_slug
       OR NEW.hedge_side IS DISTINCT FROM OLD.hedge_side
       OR NEW.structure::text IS DISTINCT FROM OLD.structure::text
       OR NEW.primary_cost_cents IS DISTINCT FROM OLD.primary_cost_cents
       OR NEW.hedge_cost_cents IS DISTINCT FROM OLD.hedge_cost_cents
       OR NEW.overtime_included IS DISTINCT FROM OLD.overtime_included
       OR NEW.features::text IS DISTINCT FROM OLD.features::text
       OR NEW.feature_sha IS DISTINCT FROM OLD.feature_sha
       OR NEW.feature_schema_sha IS DISTINCT FROM OLD.feature_schema_sha
       OR NEW.taxonomy IS DISTINCT FROM OLD.taxonomy
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.price_basis::text IS DISTINCT FROM OLD.price_basis::text THEN
        RAISE EXCEPTION 'observation % records what was seen at %; it is not '
                        'edited', OLD.observation_id, OLD.observed_at;
    END IF;
    -- EVERY LABEL COLUMN IS VERSIONED, and the version moves ONLY with the
    -- label: exactly +1 when any of them changes, and not at all otherwise.
    IF (NEW.label_status, NEW.label_why, NEW.middle_occurred,
        NEW.primary_won, NEW.hedge_won, NEW.primary_settlement_price,
        NEW.hedge_settlement_price, NEW.outcome_available_at)
       IS DISTINCT FROM
       (OLD.label_status, OLD.label_why, OLD.middle_occurred,
        OLD.primary_won, OLD.hedge_won, OLD.primary_settlement_price,
        OLD.hedge_settlement_price, OLD.outcome_available_at) THEN
        IF NEW.label_version <> OLD.label_version + 1 THEN
            RAISE EXCEPTION 'observation %: a label changes only as a new '
                            'version (% -> % required)', OLD.observation_id,
                OLD.label_version, OLD.label_version + 1;
        END IF;
    ELSIF NEW.label_version <> OLD.label_version THEN
        RAISE EXCEPTION 'observation %: the label version moves only with '
                        'the label', OLD.observation_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── EVERY LABEL VERSION HAS ITS HISTORY ROW, checked at commit ────────
CREATE OR REPLACE FUNCTION bettor_pair_observation_version_has_history()
RETURNS trigger AS $$
BEGIN
    IF NEW.label_version > 0 AND NOT EXISTS (
        SELECT 1 FROM bettor_pair_observation_labels l
         WHERE l.observation_id = NEW.observation_id
           AND l.label_version = NEW.label_version) THEN
        RAISE EXCEPTION 'observation %: label version % has no history row',
            NEW.observation_id, NEW.label_version;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_pair_observation_version_has_history_trg
    ON bettor_pair_observations;
CREATE CONSTRAINT TRIGGER bettor_pair_observation_version_has_history_trg
    AFTER UPDATE ON bettor_pair_observations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION
        bettor_pair_observation_version_has_history();

DROP TRIGGER IF EXISTS bettor_pair_observation_is_fixed_trg
    ON bettor_pair_observations;
CREATE TRIGGER bettor_pair_observation_is_fixed_trg
    BEFORE UPDATE ON bettor_pair_observations
    FOR EACH ROW EXECUTE FUNCTION bettor_pair_observation_is_fixed();

CREATE OR REPLACE FUNCTION bettor_pair_observation_label_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'label version % of % is history; it is not edited',
        OLD.label_version, OLD.observation_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_pair_observation_label_is_append_only_trg
    ON bettor_pair_observation_labels;
CREATE TRIGGER bettor_pair_observation_label_is_append_only_trg
    BEFORE UPDATE OR DELETE ON bettor_pair_observation_labels
    FOR EACH ROW EXECUTE FUNCTION
        bettor_pair_observation_label_is_append_only();

COMMIT;
