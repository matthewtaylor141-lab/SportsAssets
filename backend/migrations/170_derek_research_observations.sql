-- ══════════════════════════════════════════════════════════════════════
-- 170 · DEREK'S NON-FUNDED RESEARCH OBSERVATIONS: WHAT THE INTERNAL ENTRY
--       MODEL LEARNS FROM WHILE BOOK CURRENCY IS NOT ESTABLISHED
-- ══════════════════════════════════════════════════════════════════════
--
-- NUMBERED 170, DELIBERATELY APART. 156 is the highest migration on the
-- foundation this was built on, and the standing-order branch uses 157+.
-- Nothing here depends on anything after 153 (Derek) and 144 (record purpose).
--
-- THE DEADLOCK THIS BREAKS. Derek's approved internal model
-- (bettor_funded_model.KEY_ENTRY_PAYOUT: P(the event this contract pays on
-- occurs) from (acquisition_price, payout_is_complement)) was trained only on
-- `derek_entry_decisions`, and Derek decides only ENTRY_DECISION valuations,
-- which exist only when the venue read established book currency. In
-- production no read does (P5), so every valuation is CALIBRATION_ONLY and no
-- training record could ever accrue -- yet a settlement-probability model does
-- not need proof that the price was executable to learn how the price relates
-- to the settlement.
--
-- WHAT A ROW IS. One observation per entry-experiment valuation (either
-- purpose), FROZEN from that valuation's decision-time fields: the fixture,
-- the payout side, the price the model's feature uses WITH ITS BASIS AND
-- COHORT, that price's timing (our receipt instant, the venue's source stamp
-- only when it supplied one, the timing uncertainty) and source identity, the
-- contemporaneous de-vigged Pinnacle reading, the feature vector and its sha, and the approved model's prediction when one was
-- approved at the decision instant. The label is NOT copied here: it is joined
-- from the valuation's own settlement outcome when the model is fit.
--
--   price_basis
--     DISPLAYED_BOOK_CURRENCY_UNESTABLISHED  a CALIBRATION_ONLY valuation: the
--        price the venue DISPLAYED on a book whose currency was not
--        established (the valuation's calibration evidence,
--        compared_at_the_displayed_price.price, usable_for_orders false).
--     EXECUTABLE_BOOK_CURRENCY_ESTABLISHED   an ENTRY_DECISION valuation: its
--        executable price on an established book.
--
--   cohort   DISPLAYED_PRICE_AGE_UNKNOWN / EXECUTABLE_PRICE_CURRENT, one per
--            basis. A model declares which cohort(s) it was trained and
--            evaluated on; the two are never pooled silently.
--
-- STRUCTURALLY UNABLE TO AUTHORIZE AN ORDER.
--   * No quantity, size, limit, verdict, plan, admission, fill, depth or edge
--     column exists, so nothing here can be sized against or sent.
--   * `price_usable_for_orders` is CHECKed false and `execution_quality` is
--     CHECKed 'UNKNOWN': the row states that it claims nothing about fill,
--     depth or executable edge, for either basis.
--   * The price basis is CHECKed to follow the valuation's purpose, so a
--     displayed price can never be recorded as executable.
--   * Append-only: the observation is what was known at the decision.
--   * No execution module reads it (census in
--     tests/test_derek_research_observations_break_the_deadlock.py). The
--     model registry reads it to fit, evaluate and re-verify a model's
--     training records -- which is a probability, never an order.
--
-- ADDITIVE. Two new tables; nothing existing is altered. No foreign key to the
-- valuation, deliberately: an observation is a frozen record and must not
-- block (or be cascaded by) a later clean-up of the valuation table; the
-- labeller joins by id and an unjoinable observation is simply not a label.

BEGIN;

CREATE TABLE IF NOT EXISTS derek_research_observations (
    observation_id          text PRIMARY KEY,
    valuation_id            bigint      NOT NULL,
    experiment_id           text        NOT NULL,
    record_purpose          text        NOT NULL,
    -- IDENTITY (never a trading instruction)
    fixture                 text        NOT NULL,
    event_key               text,
    condition_id            text,
    us_market_slug          text,
    buy_intent              text,
    payout_event            text,
    payout_is_complement    boolean     NOT NULL,
    decided_at              timestamptz NOT NULL,
    -- THE COHORT: never pooled silently. DISPLAYED_PRICE_AGE_UNKNOWN for a
    -- CALIBRATION_ONLY valuation, EXECUTABLE_PRICE_CURRENT for an
    -- ENTRY_DECISION valuation (CHECKed below).
    cohort                  text        NOT NULL,
    -- THE PRICE THE FEATURE USES, AND WHAT KIND OF PRICE IT WAS
    price                   double precision NOT NULL,
    price_basis             text        NOT NULL,
    price_source            text        NOT NULL,
    -- ITS TIMING. OUR receipt instant (our clock); the venue's SOURCE
    -- timestamp only when the venue supplied one -- NULL otherwise, with the
    -- reason in price_source_ts_basis, never defaulted or inferred; and the
    -- timing uncertainty with its named basis.
    price_received_at       timestamptz,
    price_source_ts         timestamptz,
    price_source_ts_basis   text        NOT NULL,
    price_timing_uncertainty text       NOT NULL,
    price_timing_basis      text        NOT NULL,
    -- ITS SOURCE IDENTITY: venue, endpoint, market slug, intent, side.
    price_source_identity   jsonb       NOT NULL,
    price_usable_for_orders boolean     NOT NULL DEFAULT FALSE,
    execution_quality       text        NOT NULL DEFAULT 'UNKNOWN',
    -- THE CONTEMPORANEOUS PINNACLE READING THE VALUATION WAS MADE WITH
    pinnacle_p              double precision NOT NULL,
    pinnacle_observed_at    timestamptz,
    pinnacle_received_at    timestamptz,
    pinnacle_overround      double precision,
    devig_method            text,
    pinnacle_source_version text,
    -- THE MODEL'S DECISION-TIME VECTOR
    features                jsonb       NOT NULL,
    feature_sha             text        NOT NULL,
    -- THE APPROVED MODEL'S FROZEN PREDICTION, when one was approved at the
    -- decision instant; otherwise why not.
    model_id                text,
    model_version           text,
    model_p                 double precision,
    model_absent_reason     text,
    observer_version        text        NOT NULL,
    -- HOW IT WAS COLLECTED: by the scheduled cycle that wrote the valuation
    -- (LIVE_CYCLE), or later, from the stored valuation row alone
    -- (BACKFILL_FROM_STORED_VALUATION: every value is what that row
    -- recorded, nothing re-queried or inferred; recorded_at is the backfill
    -- instant; no model prediction is frozen retroactively).
    collection_mode         text        NOT NULL,
    recorded_at             timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT derek_research_observations_one_per_valuation
        UNIQUE (valuation_id),
    CONSTRAINT derek_research_observations_purpose_ck CHECK (
        record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')),
    --: A DISPLAYED PRICE IS NEVER RECORDED AS EXECUTABLE, NOR THE REVERSE,
    --: AND ITS COHORT AND TIMING FOLLOW: a displayed price's age is unknown.
    CONSTRAINT derek_research_observations_basis_follows_purpose_ck CHECK (
        (record_purpose = 'CALIBRATION_ONLY'
         AND price_basis = 'DISPLAYED_BOOK_CURRENCY_UNESTABLISHED'
         AND cohort = 'DISPLAYED_PRICE_AGE_UNKNOWN'
         AND price_timing_uncertainty = 'AGE_BOUND_UNKNOWN')
        OR (record_purpose = 'ENTRY_DECISION'
            AND price_basis = 'EXECUTABLE_BOOK_CURRENCY_ESTABLISHED'
            AND cohort = 'EXECUTABLE_PRICE_CURRENT'
            AND price_timing_uncertainty IN (
                'AGE_BOUNDED_BY_ESTABLISHED_BOOK_CURRENCY',
                'AGE_BOUND_NOT_RECORDED_ON_THE_VALUATION'))),
    CONSTRAINT derek_research_observations_collection_mode_ck CHECK (
        collection_mode IN ('LIVE_CYCLE', 'BACKFILL_FROM_STORED_VALUATION')),
    --: A BACKFILL NEVER CARRIES A PREDICTION IT DID NOT MAKE AT THE DECISION.
    CONSTRAINT derek_research_observations_backfill_no_model_ck CHECK (
        collection_mode = 'LIVE_CYCLE' OR model_p IS NULL),
    CONSTRAINT derek_research_observations_identity_ck CHECK (
        jsonb_typeof(price_source_identity) = 'object'),
    --: NOTHING ON THE ROW CAN AUTHORIZE OR SIZE AN ORDER.
    CONSTRAINT derek_research_observations_never_for_orders_ck CHECK (
        price_usable_for_orders = FALSE),
    --: NOTHING ON THE ROW CLAIMS FILL, DEPTH OR EXECUTABLE EDGE.
    CONSTRAINT derek_research_observations_execution_unknown_ck CHECK (
        execution_quality = 'UNKNOWN'),
    CONSTRAINT derek_research_observations_price_ck CHECK (
        price > 0 AND price < 1),
    CONSTRAINT derek_research_observations_pinnacle_ck CHECK (
        pinnacle_p >= 0 AND pinnacle_p <= 1),
    CONSTRAINT derek_research_observations_model_ck CHECK (
        (model_p IS NULL AND model_id IS NULL AND model_absent_reason IS NOT NULL)
        OR (model_p IS NOT NULL AND model_id IS NOT NULL
            AND model_p >= 0 AND model_p <= 1)),
    CONSTRAINT derek_research_observations_features_ck CHECK (
        jsonb_typeof(features) = 'object'
        AND features ? 'acquisition_price'
        AND features ? 'payout_is_complement')
);

CREATE INDEX IF NOT EXISTS derek_research_observations_at_idx
    ON derek_research_observations (decided_at);
CREATE INDEX IF NOT EXISTS derek_research_observations_fixture_idx
    ON derek_research_observations (fixture, decided_at);
CREATE INDEX IF NOT EXISTS derek_research_observations_cohort_idx
    ON derek_research_observations (cohort, decided_at);

-- ── DEREK'S DAILY MODEL RUN: fit and evaluate, NEVER promote ────────────
-- One row per UTC day. When the labelled research fixtures of a cohort reach
-- MIN_TRAIN_EVENTS the run fits, registers and evaluates a CANDIDATE for that
-- cohort (bettor_funded_models, never approved here); otherwise it records
-- INSUFFICIENT_LABELLED_FIXTURES with the exact counts. `promoted` is CHECKed
-- false: promotion is a named person's act through bettor_funded_model.promote.
CREATE TABLE IF NOT EXISTS derek_research_model_runs (
    run_id       text PRIMARY KEY,
    run_day      date        NOT NULL,
    ran_at       timestamptz NOT NULL,
    outcome      text        NOT NULL,
    counts       jsonb       NOT NULL,
    fitted       jsonb,
    evaluations  jsonb,
    detail       jsonb,
    promoted     boolean     NOT NULL DEFAULT FALSE,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT derek_research_model_runs_one_per_day UNIQUE (run_day),
    CONSTRAINT derek_research_model_runs_outcome_ck CHECK (outcome IN (
        'INSUFFICIENT_LABELLED_FIXTURES', 'FITTED_AND_EVALUATED',
        'EVALUATED_WITHOUT_REFIT', 'FIT_REFUSED', 'LABELS_UNREADABLE')),
    CONSTRAINT derek_research_model_runs_never_promotes_ck CHECK (
        promoted = FALSE)
);

CREATE OR REPLACE FUNCTION derek_research_record_is_append_only()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: a research record is what was known '
                    'when it was written', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS derek_research_model_runs_append_only_trg
    ON derek_research_model_runs;
CREATE TRIGGER derek_research_model_runs_append_only_trg
    BEFORE UPDATE OR DELETE ON derek_research_model_runs
    FOR EACH ROW EXECUTE FUNCTION derek_research_record_is_append_only();

DROP TRIGGER IF EXISTS derek_research_observations_append_only_trg
    ON derek_research_observations;
CREATE TRIGGER derek_research_observations_append_only_trg
    BEFORE UPDATE OR DELETE ON derek_research_observations
    FOR EACH ROW EXECUTE FUNCTION derek_research_record_is_append_only();

COMMENT ON TABLE derek_research_observations IS
    'Non-funded research observations for Derek''s internal entry model: '
    'decision-time price (with its basis), payout side and Pinnacle '
    'probability per entry-experiment valuation. Never an order input: no '
    'size, verdict or plan; price_usable_for_orders is always false and '
    'execution quality is always UNKNOWN.';
COMMENT ON COLUMN derek_research_observations.price_basis IS
    'DISPLAYED_BOOK_CURRENCY_UNESTABLISHED (calibration-only valuation) or '
    'EXECUTABLE_BOOK_CURRENCY_ESTABLISHED (entry-decision valuation).';

COMMIT;
