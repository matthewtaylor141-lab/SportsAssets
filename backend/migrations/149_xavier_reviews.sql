-- ══════════════════════════════════════════════════════════════════════
-- 149 · XAVIER'S DAILY REVIEW: WHAT WAS FORECAST, WHAT THE BOOK SAYS
--       HAPPENED, AND WHAT THE ALTERNATIVES WOULD HAVE BEEN ESTIMATED AT
-- ══════════════════════════════════════════════════════════════════════
--
-- Migration 148 is Xavier's record BEFORE acting: every considered action,
-- its economics or its blocker, the evidence and the chosen action. This is
-- the other half: once a day, after outcomes become authoritative in the
-- funded economics ledger, `bettor_xavier_review.daily_review` compares each
-- decision's forecast with the realised net, estimates the alternatives from
-- decision-time information only (labelled HYPOTHETICAL_ESTIMATE with
-- could_have_filled = UNPROVEN), checks the probability inputs against the
-- outcomes, summarises the model registry and marks every decision whose
-- outcome rests on a settlement the venue now contradicts.
--
-- ONE ROW PER UTC DAY. `review_date` is unique, so a restarted or a second
-- process cannot write a second review of the same day: the insert conflicts
-- and the writer reports "already reviewed".
--
-- IT HAS NO AUTHORITY. Nothing reads this table to authorise, size, price or
-- send anything; a review never promotes a model. It is a record.
--
-- ADDITIVE. APPEND-ONLY.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_xavier_reviews (
    review_id               text        PRIMARY KEY,
    review_date             date        NOT NULL UNIQUE,
    computed_at             timestamptz NOT NULL,
    recorded_at             timestamptz NOT NULL DEFAULT now(),
    review_version          text        NOT NULL,
    -- WHICH DECISIONS WERE READ: the decided_at window, the account/venue
    -- filter if any, how many rows were read and whether the read was cut.
    "window"                jsonb       NOT NULL DEFAULT '{}'::jsonb,
    decisions_reviewed      integer     NOT NULL CHECK (decisions_reviewed >= 0),
    decisions_with_outcomes integer     NOT NULL
        CHECK (decisions_with_outcomes >= 0),
    per_action              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- THE LABELLED ESTIMATES. Every row is HYPOTHETICAL_ESTIMATE with its fill
    -- basis and could_have_filled = UNPROVEN; blocked alternatives carry their
    -- blocker and no estimate.
    alternatives            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    calibration             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    reliability             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    errors                  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    models                  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    invalidated             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    summary                 text        NOT NULL,
    CONSTRAINT bettor_xavier_review_outcomes_ck CHECK (
        decisions_with_outcomes <= decisions_reviewed)
);

CREATE INDEX IF NOT EXISTS bettor_xavier_reviews_date_idx
    ON bettor_xavier_reviews (review_date DESC);

CREATE OR REPLACE FUNCTION bettor_xavier_review_is_a_record()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'xavier review % is a record; it is not edited or deleted',
        OLD.review_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_xavier_review_is_a_record_trg
    ON bettor_xavier_reviews;
CREATE TRIGGER bettor_xavier_review_is_a_record_trg
    BEFORE UPDATE OR DELETE ON bettor_xavier_reviews
    FOR EACH ROW EXECUTE FUNCTION bettor_xavier_review_is_a_record();

COMMIT;
