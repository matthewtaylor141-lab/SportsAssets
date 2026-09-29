-- ══════════════════════════════════════════════════════════════════════
-- 148 · XAVIER: THE POSITION MANAGER'S PERSISTED DECISION, ONE PER POSITION
--       PER SCHEDULED REVIEW, WRITTEN BEFORE ANYTHING IS DISPATCHED, AND
--       THE APPEND-ONLY HISTORY OF WHAT EXECUTION DID WITH IT
-- ══════════════════════════════════════════════════════════════════════
--
-- Xavier is the name of the responsibility the scheduled funded servicing
-- pass already carries -- `manage` (settlement, recovery, exit valuation),
-- the pair pass (hedge discovery, one ranking, dispatch of the winner) and
-- the learning pass -- from the first fill of an entry (a partial fill
-- included) until every resulting position, outstanding order and
-- settlement obligation is reconciled. It is not a second execution lane:
-- orders still go only through the existing bound-plan dispatch and its
-- reservations.
--
-- WHAT WAS MISSING. `bettor_funded_decisions` records the pair pass's
-- ranking per ENTRY intent, but not: the responsibility state of the
-- position, the management blockers that never reached the ranking, the
-- hedge-search refusals, the unpaired residual, the execution eligibility
-- (would this have been sent, and if not which gate stopped it), the next
-- review, or which order a decision produced. An operator could not read
-- "what is Xavier doing with this position, why, and what stops it".
--
-- TWO TABLES.
--  * `bettor_xavier_decisions` -- ONE ROW PER (position, review), written
--    before dispatch, IMMUTABLE: the decision and every ranked alternative
--    are never rewritten.
--  * `bettor_xavier_execution_events` -- what execution did with a decision,
--    as it happens, APPEND-ONLY and IDEMPOTENT. Execution has many
--    subsequent facts (a claim, an acknowledgement, several partial fills, a
--    cancellation, a terminal status, a recovery of a lost acknowledgement,
--    a correction), so no single write-once field can hold it: a write-once
--    result would either block later reconciliation or leave the displayed
--    filled quantity stale forever. Every event names the decision, the
--    execution plan (digest) and the venue identities it concerns.
--
-- THE DISPATCH CLAIM. Before anything is sent, the dispatcher appends ONE
-- `DISPATCH_CLAIMED` event for the decision. The database refuses a second
-- claim for the same decision, and a second claim for the same plan digest,
-- so a retry, a restart or a concurrent review cannot submit the same
-- decision or the same plan twice. The claim must carry the digest of the
-- plan the decision chose (the database checks it): an order that is not the
-- winning plan cannot be claimed against the decision. Every other event
-- requires the claim to exist and must name the claimed plan.
--
-- ADDITIVE.

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
    CONSTRAINT bettor_xavier_state_ck CHECK (responsibility_state IN (
        'HELD', 'ORDER_OUTSTANDING', 'ORDER_UNRESOLVED',
        'SETTLEMENT_PENDING', 'CORRECTION_PENDING', 'RECONCILED')),
    -- A CHOSEN ACTION NAMES THE PLAN IT WAS RANKED WITH (HOLD has no order
    -- and so no plan); nothing else may be dispatched against this row.
    CONSTRAINT bettor_xavier_plan_ck CHECK (
        chosen_action IS NULL OR chosen_action = 'HOLD'
        OR chosen_plan_digest IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS bettor_xavier_decisions_position_idx
    ON bettor_xavier_decisions (intent_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS bettor_xavier_decisions_group_idx
    ON bettor_xavier_decisions (portfolio_group_id, decided_at DESC)
    WHERE portfolio_group_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS bettor_xavier_decisions_account_idx
    ON bettor_xavier_decisions (account_id, venue, decided_at DESC);
CREATE INDEX IF NOT EXISTS bettor_xavier_decisions_decision_idx
    ON bettor_xavier_decisions (decision_id) WHERE decision_id IS NOT NULL;

CREATE OR REPLACE FUNCTION bettor_xavier_decision_is_a_record()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'xavier decision % is a record; it is never rewritten '
                    'or deleted (execution facts are appended to '
                    'bettor_xavier_execution_events)', OLD.xavier_decision_id;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_xavier_decision_is_a_record_trg
    ON bettor_xavier_decisions;
CREATE TRIGGER bettor_xavier_decision_is_a_record_trg
    BEFORE UPDATE OR DELETE ON bettor_xavier_decisions
    FOR EACH ROW EXECUTE FUNCTION bettor_xavier_decision_is_a_record();


CREATE TABLE IF NOT EXISTS bettor_xavier_execution_events (
    event_id              bigserial   PRIMARY KEY,
    -- the caller's deterministic identity of THIS fact: a replay of the same
    -- fact reaches the same key and appends nothing
    idempotency_key       text        NOT NULL UNIQUE,
    -- sha256 of the fact's content; the same key with different content is
    -- refused by the writer (a conflicting retry), never merged
    fact_sha              text        NOT NULL,
    xavier_decision_id    text        NOT NULL
        REFERENCES bettor_xavier_decisions (xavier_decision_id),
    decision_id           text,
    plan_digest           text,
    -- the ORDER's intent (the exit / reduction / acquisition intent that
    -- carries this plan) and the POSITION it serves
    order_intent_id       text,
    position_intent_id    text        NOT NULL,
    portfolio_group_id    text,
    account_id            text        NOT NULL,
    venue                 text        NOT NULL,
    client_order_id       text,
    venue_order_id        text,
    event_kind            text        NOT NULL,
    -- the venue's CUMULATIVE filled quantity for `venue_order_id` as observed
    -- by this event (FILL, RECOVERED, CORRECTION); cumulative, not a delta,
    -- so repeated or racing reads of one fill never double-count
    cumulative_filled_qty numeric,
    avg_fill_price_cents  numeric,
    fee_usd               numeric,
    terminal_status       text,
    -- which reader observed it
    source                text        NOT NULL,
    -- the event this one corrects (CORRECTION only)
    supersedes_event_id   bigint
        REFERENCES bettor_xavier_execution_events (event_id),
    evidence              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at           timestamptz NOT NULL,
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT bettor_xavier_event_kind_ck CHECK (event_kind IN (
        'DISPATCH_CLAIMED',   -- before any send; one per decision and plan
        'NOT_SENT',           -- claimed, then refused before the send
        'ACKNOWLEDGED',       -- the venue answered with an order id
        'REFUSED',            -- the venue explicitly refused the order
        'UNKNOWN_OUTCOME',    -- the send raised / no answer: exposure counts
        'FILL',               -- a (partial) fill, as a cumulative quantity
        'CANCELLED',          -- the venue cancelled / expired the remainder
        'TERMINAL',           -- the order reached a final venue status
        'RECOVERED',          -- an investigation established an outcome
        'CORRECTION')),       -- a later authoritative fact supersedes one
    CONSTRAINT bettor_xavier_event_source_ck CHECK (source IN (
        'DISPATCHER', 'SEND_RESPONSE', 'RECOVERY_READ', 'ORDER_STATUS_READ',
        'FILLS_LEDGER', 'SETTLEMENT_CORRECTION', 'OPERATOR_RECONCILIATION')),
    CONSTRAINT bettor_xavier_event_claim_names_plan_ck CHECK (
        event_kind <> 'DISPATCH_CLAIMED' OR plan_digest IS NOT NULL),
    CONSTRAINT bettor_xavier_event_ack_names_order_ck CHECK (
        event_kind <> 'ACKNOWLEDGED' OR venue_order_id IS NOT NULL),
    CONSTRAINT bettor_xavier_event_fill_qty_ck CHECK (
        event_kind <> 'FILL' OR (venue_order_id IS NOT NULL
                                 AND cumulative_filled_qty IS NOT NULL)),
    CONSTRAINT bettor_xavier_event_qty_ck CHECK (
        cumulative_filled_qty IS NULL OR cumulative_filled_qty >= 0),
    CONSTRAINT bettor_xavier_event_terminal_ck CHECK (
        event_kind <> 'TERMINAL' OR terminal_status IS NOT NULL),
    CONSTRAINT bettor_xavier_event_correction_ck CHECK (
        (event_kind = 'CORRECTION') = (supersedes_event_id IS NOT NULL))
);

-- THE DUPLICATE-SUBMISSION GUARD: one claim per decision, one per plan.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_xavier_one_claim_per_decision
    ON bettor_xavier_execution_events (xavier_decision_id)
    WHERE event_kind = 'DISPATCH_CLAIMED';
CREATE UNIQUE INDEX IF NOT EXISTS bettor_xavier_one_claim_per_plan
    ON bettor_xavier_execution_events (plan_digest)
    WHERE event_kind = 'DISPATCH_CLAIMED';
CREATE INDEX IF NOT EXISTS bettor_xavier_events_decision_idx
    ON bettor_xavier_execution_events (xavier_decision_id, event_id);
CREATE INDEX IF NOT EXISTS bettor_xavier_events_position_idx
    ON bettor_xavier_execution_events (position_intent_id, event_id);
CREATE INDEX IF NOT EXISTS bettor_xavier_events_order_idx
    ON bettor_xavier_execution_events (order_intent_id)
    WHERE order_intent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS bettor_xavier_events_venue_order_idx
    ON bettor_xavier_execution_events (venue, venue_order_id)
    WHERE venue_order_id IS NOT NULL;

CREATE OR REPLACE FUNCTION bettor_xavier_event_is_bound()
RETURNS trigger AS $$
DECLARE
    d RECORD;
    claim RECORD;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'xavier execution event % is a record; it is never '
                        'rewritten or deleted (append a CORRECTION)',
                        OLD.event_id;
    END IF;
    SELECT * INTO d FROM bettor_xavier_decisions
     WHERE xavier_decision_id = NEW.xavier_decision_id;
    -- every event names the decision's own position, account and venue
    IF d.intent_id IS DISTINCT FROM NEW.position_intent_id
       OR d.account_id IS DISTINCT FROM NEW.account_id
       OR d.venue IS DISTINCT FROM NEW.venue THEN
        RAISE EXCEPTION 'xavier execution event names position %/%/% but '
                        'decision % is for %/%/%', NEW.position_intent_id,
                        NEW.account_id, NEW.venue, NEW.xavier_decision_id,
                        d.intent_id, d.account_id, d.venue;
    END IF;
    IF NEW.event_kind = 'DISPATCH_CLAIMED' THEN
        -- ONLY THE WINNING PLAN IS CLAIMABLE
        IF d.chosen_action IS NULL OR d.chosen_action = 'HOLD'
           OR d.chosen_plan_digest IS DISTINCT FROM NEW.plan_digest THEN
            RAISE EXCEPTION 'xavier decision % chose % with plan %; plan % '
                            'is not claimable against it',
                            NEW.xavier_decision_id, d.chosen_action,
                            d.chosen_plan_digest, NEW.plan_digest;
        END IF;
        IF d.decision_id IS DISTINCT FROM NEW.decision_id THEN
            RAISE EXCEPTION 'claim names ledger decision % but xavier '
                            'decision % recorded %', NEW.decision_id,
                            NEW.xavier_decision_id, d.decision_id;
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO claim FROM bettor_xavier_execution_events
     WHERE xavier_decision_id = NEW.xavier_decision_id
       AND event_kind = 'DISPATCH_CLAIMED';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'xavier decision % has no dispatch claim; nothing '
                        'can have been sent for it', NEW.xavier_decision_id;
    END IF;
    IF NEW.plan_digest IS DISTINCT FROM claim.plan_digest THEN
        RAISE EXCEPTION 'event names plan % but decision % claimed plan %',
                        NEW.plan_digest, NEW.xavier_decision_id,
                        claim.plan_digest;
    END IF;
    IF NEW.event_kind = 'CORRECTION' AND NOT EXISTS (
        SELECT 1 FROM bettor_xavier_execution_events
         WHERE event_id = NEW.supersedes_event_id
           AND xavier_decision_id = NEW.xavier_decision_id) THEN
        RAISE EXCEPTION 'a correction must supersede an event of the same '
                        'decision';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_xavier_event_is_bound_trg
    ON bettor_xavier_execution_events;
CREATE TRIGGER bettor_xavier_event_is_bound_trg
    BEFORE INSERT OR UPDATE OR DELETE ON bettor_xavier_execution_events
    FOR EACH ROW EXECUTE FUNCTION bettor_xavier_event_is_bound();

COMMIT;
