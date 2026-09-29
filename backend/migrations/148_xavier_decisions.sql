-- ══════════════════════════════════════════════════════════════════════
-- 148 · XAVIER: THE POSITION MANAGER'S PERSISTED DECISION, ONE PER POSITION
--       PER SCHEDULED REVIEW, WRITTEN BEFORE ANYTHING IS DISPATCHED
-- ══════════════════════════════════════════════════════════════════════
--
-- Xavier is the name of the responsibility the scheduled funded servicing
-- pass already carries -- `manage` (settlement, recovery, exit valuation),
-- the pair pass (hedge discovery, one ranking, dispatch of the winner) and
-- the learning pass -- from the first fill of an entry (a partial fill
-- included) until every resulting position, outstanding order and
-- settlement obligation is reconciled. It is not a second execution lane:
-- orders still go only through the existing bound-plan dispatch.
--
-- WHAT WAS MISSING. `bettor_funded_decisions` records the pair pass's
-- ranking per ENTRY intent, but not: the responsibility state of the
-- position, the management blockers that never reached the ranking, the
-- hedge-search refusals, the unpaired residual, the execution eligibility
-- (would this have been sent, and if not which gate stopped it), the next
-- review, or which order a decision produced. An operator could not read
-- "what is Xavier doing with this position, why, and what stops it".
--
-- ONE ROW PER (position, review). Written before dispatch; the only field
-- that may change afterwards is `dispatch_result`, once, from NULL -- the
-- record of what the venue actually did with the winner.
--
-- ADDITIVE. APPEND-ONLY apart from that single transition.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_xavier_decisions (
    xavier_decision_id   text        PRIMARY KEY,
    -- the pair pass's ledger row this review produced, when it produced one
    decision_id          text,
    account_id           text        NOT NULL,
    venue                text        NOT NULL,
    -- THE POSITION under responsibility: the ENTRY intent, and its group
    intent_id            text        NOT NULL,
    portfolio_group_id   text,
    us_market_slug       text,
    decided_at           timestamptz NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    xavier_version       text        NOT NULL,
    responsibility_state text        NOT NULL,
    -- NULL when nothing was selectable; then `alternatives` says why
    chosen_action        text,
    chosen_plan_digest   text,
    execution_eligibility text       NOT NULL,
    -- EVERY considered action: economics when rankable, the exact blocker
    -- when not. Never only the winner.
    alternatives         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    reasoning            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    expected_economics   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    residual_exposure    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- probability sources, model ids and versions, feature shas, book and
    -- settlement evidence the decision rested on
    evidence             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    obligations          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    next_review_at       timestamptz,
    dispatch_result      jsonb,
    dispatch_recorded_at timestamptz,
    CONSTRAINT bettor_xavier_state_ck CHECK (responsibility_state IN (
        'HELD', 'ORDER_OUTSTANDING', 'ORDER_UNRESOLVED',
        'SETTLEMENT_PENDING', 'CORRECTION_PENDING', 'RECONCILED')),
    -- A CHOSEN ACTION NAMES THE PLAN IT WAS RANKED WITH (HOLD has no order
    -- and so no plan); nothing else may be dispatched against this row.
    CONSTRAINT bettor_xavier_plan_ck CHECK (
        chosen_action IS NULL OR chosen_action = 'HOLD'
        OR chosen_plan_digest IS NOT NULL),
    CONSTRAINT bettor_xavier_dispatch_ck CHECK (
        (dispatch_result IS NULL) = (dispatch_recorded_at IS NULL))
);

CREATE INDEX IF NOT EXISTS bettor_xavier_decisions_position_idx
    ON bettor_xavier_decisions (intent_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS bettor_xavier_decisions_account_idx
    ON bettor_xavier_decisions (account_id, venue, decided_at DESC);
CREATE INDEX IF NOT EXISTS bettor_xavier_decisions_decision_idx
    ON bettor_xavier_decisions (decision_id) WHERE decision_id IS NOT NULL;

CREATE OR REPLACE FUNCTION bettor_xavier_decision_is_a_record()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'xavier decision % is a record; it is not deleted',
            OLD.xavier_decision_id;
    END IF;
    -- THE ONE PERMITTED CHANGE: what dispatch did, recorded once.
    IF OLD.dispatch_result IS NULL AND NEW.dispatch_result IS NOT NULL
       AND (NEW.xavier_decision_id, NEW.decision_id, NEW.account_id,
            NEW.venue, NEW.intent_id, NEW.portfolio_group_id,
            NEW.us_market_slug, NEW.decided_at, NEW.recorded_at,
            NEW.xavier_version, NEW.responsibility_state, NEW.chosen_action,
            NEW.chosen_plan_digest, NEW.execution_eligibility,
            NEW.alternatives, NEW.reasoning, NEW.expected_economics,
            NEW.residual_exposure, NEW.evidence, NEW.obligations,
            NEW.next_review_at)
           IS NOT DISTINCT FROM
           (OLD.xavier_decision_id, OLD.decision_id, OLD.account_id,
            OLD.venue, OLD.intent_id, OLD.portfolio_group_id,
            OLD.us_market_slug, OLD.decided_at, OLD.recorded_at,
            OLD.xavier_version, OLD.responsibility_state, OLD.chosen_action,
            OLD.chosen_plan_digest, OLD.execution_eligibility,
            OLD.alternatives, OLD.reasoning, OLD.expected_economics,
            OLD.residual_exposure, OLD.evidence, OLD.obligations,
            OLD.next_review_at)
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'xavier decision % is a record; only its dispatch result '
                    'may be recorded, once', OLD.xavier_decision_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_xavier_decision_is_a_record_trg
    ON bettor_xavier_decisions;
CREATE TRIGGER bettor_xavier_decision_is_a_record_trg
    BEFORE UPDATE OR DELETE ON bettor_xavier_decisions
    FOR EACH ROW EXECUTE FUNCTION bettor_xavier_decision_is_a_record();

COMMIT;
