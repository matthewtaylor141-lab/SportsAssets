-- ══════════════════════════════════════════════════════════════════════
-- 157 · XAVIER: STANDING PROTECTIVE ORDERS (STRICT FALLBACK)
-- ══════════════════════════════════════════════════════════════════════
--
-- A STANDING PROTECTIVE ORDER is a resting hedge order Xavier places on ONE
-- selected hedge instrument at a PROTECTIVE PRICE -- a price at which the
-- pair's cost (primary cost + entry fees + hedge cost + hedge fees + other
-- costs + a buffer) is below the minimum combined payout over the settlement
-- states the floor classification claims. It is a MANAGEMENT policy
-- (`sportsassets.bettor_xavier_standing_orders`), not Derek's entry policy.
--
-- IT IS NOT A SECOND EXECUTION PATH. Every placement goes through the
-- existing group authority (the servicing pass's execution lock, Xavier's
-- group advisory lock, a Xavier decision row, its dispatch claim, the leg
-- reservation and `bettor_funded_execution.submit_for_decision`), and every
-- fill lands in the ONE book (`bettor_funded_intents` / `_fills` /
-- `_economics`). These tables RECORD the plan, the selection, the lifecycle
-- and the capacity the plan holds; they are never a second accounting.
--
-- STRICT FALLBACK, AND WHY A DATABASE LOCK IS NOT ENOUGH. The venue offers no
-- linked / one-cancels-other orders across markets and no cross-market
-- quantity cap (EXCHANGE_LINKED_EXCLUSIVITY = UNAVAILABLE_OR_UNVERIFIED). A
-- lock or a unique index here can stop us SENDING a second order; it cannot
-- stop two orders that were ALREADY SENT from both matching at the venue. So
-- at most ONE live-or-potentially-live hedge order may exist per group, and
-- the one-RESERVED-reservation-per-group index below is our side of that
-- rule, beside migration 131's one-open-leg-per-role index on the intents.
--
-- CAPACITY IS RELEASED ONLY ON A CONFIRMED TERMINAL STATE. A cancel request
-- or a cancel acknowledgement is not the end of an order: until the venue's
-- own record says the order is over AND the book holds exactly the quantity
-- the venue says filled, the order may still fill. The trigger below refuses
-- to release a reservation whose hedge intent is still outstanding.
--
-- ADDITIVE. Events are APPEND-ONLY; plans and selections are records.

BEGIN;

-- ── 1 · THE PLAN: one row per standing order Xavier decided to place ──
CREATE TABLE IF NOT EXISTS bettor_standing_order_plans (
    plan_id                  text        PRIMARY KEY,   -- the plan digest
    account_id               text        NOT NULL,
    venue                    text        NOT NULL,
    group_id                 text        NOT NULL,
    primary_intent_id        text        NOT NULL,
    -- the Xavier decision that chose THIS order (its chosen plan digest is
    -- plan_id) and the group review it was decided inside
    xavier_decision_id       text        NOT NULL
        REFERENCES bettor_xavier_decisions (xavier_decision_id),
    group_review_xavier_decision_id text,
    decision_id              text,
    policy_key               text        NOT NULL,
    policy_version           text        NOT NULL,
    policy                   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    mode                     text        NOT NULL,
    exchange_linked_exclusivity text     NOT NULL,
    candidate_id             text        NOT NULL,
    venue_slug               text        NOT NULL,
    order_intent             text        NOT NULL,
    wire_limit_price         numeric     NOT NULL,
    cost_price               numeric     NOT NULL,
    quantity                 integer     NOT NULL CHECK (quantity > 0),
    tif                      text        NOT NULL,
    good_till_time           timestamptz,
    capacity                 jsonb       NOT NULL,
    floor_class              text        NOT NULL,
    floor                    jsonb       NOT NULL,
    fee_schedule_identity    text        NOT NULL,
    settlement_identity      text        NOT NULL,
    desirability             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- the hedge ENTRY intent the send created; set ONCE (see the trigger)
    hedge_intent_id          text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT bettor_standing_plan_mode_ck CHECK (mode = 'STRICT_FALLBACK'),
    CONSTRAINT bettor_standing_plan_capability_ck CHECK (
        exchange_linked_exclusivity = 'UNAVAILABLE_OR_UNVERIFIED'),
    CONSTRAINT bettor_standing_plan_floor_ck CHECK (floor_class IN (
        'POSITIVE_FLOOR_ALL_ESTABLISHED_STATES',
        'POSITIVE_FLOOR_CONDITIONAL_ON_ORDINARY_SETTLEMENT',
        'CONTROLLED_LOSS_PAIR', 'FLOOR_NOT_ESTABLISHABLE')),
    CONSTRAINT bettor_standing_plan_tif_ck CHECK (tif IN (
        'TIME_IN_FORCE_GOOD_TILL_DATE', 'TIME_IN_FORCE_GOOD_TILL_CANCEL'))
);
CREATE INDEX IF NOT EXISTS bettor_standing_plans_group_idx
    ON bettor_standing_order_plans (group_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS bettor_standing_plans_intent_idx
    ON bettor_standing_order_plans (hedge_intent_id)
    WHERE hedge_intent_id IS NOT NULL;

CREATE OR REPLACE FUNCTION bettor_standing_plan_is_a_record()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'standing order plan % is a record; it is never '
                        'deleted', OLD.plan_id;
    END IF;
    -- THE ONLY PERMITTED CHANGE: binding the hedge intent, once.
    IF OLD.hedge_intent_id IS NOT NULL
       OR NEW.hedge_intent_id IS NULL
       OR (to_jsonb(NEW) - 'hedge_intent_id')
          IS DISTINCT FROM (to_jsonb(OLD) - 'hedge_intent_id') THEN
        RAISE EXCEPTION 'standing order plan % is a record; only its hedge '
                        'intent may be bound, once', OLD.plan_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS bettor_standing_plan_is_a_record_trg
    ON bettor_standing_order_plans;
CREATE TRIGGER bettor_standing_plan_is_a_record_trg
    BEFORE UPDATE OR DELETE ON bettor_standing_order_plans
    FOR EACH ROW EXECUTE FUNCTION bettor_standing_plan_is_a_record();


-- ── 2 · THE GROUP'S SELECTED HEDGE INSTRUMENT ────────────────────────
-- Selected by the FIRST FILL of a hedge order (or by a separately evaluated
-- TRANSITION). One current selection per group: the highest selection_seq.
CREATE TABLE IF NOT EXISTS bettor_hedge_group_selection (
    group_id           text        NOT NULL,
    selection_seq      integer     NOT NULL CHECK (selection_seq >= 1),
    account_id         text        NOT NULL,
    venue              text        NOT NULL,
    primary_intent_id  text        NOT NULL,
    candidate_id       text        NOT NULL,
    venue_slug         text        NOT NULL,
    order_intent       text        NOT NULL,
    hedge_intent_id    text,
    plan_id            text,
    selected_by        text        NOT NULL
        CHECK (selected_by IN ('FIRST_FILL', 'TRANSITION')),
    first_fill_id      text,
    evidence           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    selected_at        timestamptz NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, selection_seq),
    CONSTRAINT bettor_hedge_selection_first_fill_ck CHECK (
        selected_by <> 'FIRST_FILL' OR first_fill_id IS NOT NULL)
);

CREATE OR REPLACE FUNCTION bettor_hedge_selection_is_a_record()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'hedge selection %/% is a record; a change of instrument '
                    'is a new TRANSITION row, never an edit',
                    OLD.group_id, OLD.selection_seq;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS bettor_hedge_selection_is_a_record_trg
    ON bettor_hedge_group_selection;
CREATE TRIGGER bettor_hedge_selection_is_a_record_trg
    BEFORE UPDATE OR DELETE ON bettor_hedge_group_selection
    FOR EACH ROW EXECUTE FUNCTION bettor_hedge_selection_is_a_record();


-- ── 3 · THE ORDER LIFECYCLE, APPEND-ONLY ─────────────────────────────
CREATE TABLE IF NOT EXISTS bettor_standing_order_events (
    event_id           bigserial   PRIMARY KEY,
    -- a replay of the same fact reaches the same key and appends nothing
    idempotency_key    text        NOT NULL UNIQUE,
    account_id         text        NOT NULL,
    venue              text        NOT NULL,
    group_id           text        NOT NULL,
    primary_intent_id  text,
    plan_id            text,
    hedge_intent_id    text,
    venue_order_id     text,
    xavier_decision_id text,
    event_kind         text        NOT NULL CHECK (event_kind ~ '^[A-Z_]+$'),
    source             text        NOT NULL CHECK (source IN (
        'SCHEDULED_SERVICING', 'VENUE_ORDER_EVENT', 'VENUE_MARKET_EVENT',
        'RECONCILIATION_READ', 'DISPATCHER', 'OPERATOR')),
    lifecycle_state    text,
    primary_qty        numeric,
    filled_qty         numeric,
    fill_capable_qty   numeric,
    evidence           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at        timestamptz NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bettor_standing_events_group_idx
    ON bettor_standing_order_events (group_id, event_id);
CREATE INDEX IF NOT EXISTS bettor_standing_events_intent_idx
    ON bettor_standing_order_events (hedge_intent_id)
    WHERE hedge_intent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS bettor_standing_events_account_idx
    ON bettor_standing_order_events (account_id, venue, occurred_at DESC);

CREATE OR REPLACE FUNCTION bettor_standing_event_is_a_record()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'standing order event % is a record; it is never '
                    'rewritten or deleted', OLD.event_id;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS bettor_standing_event_is_a_record_trg
    ON bettor_standing_order_events;
CREATE TRIGGER bettor_standing_event_is_a_record_trg
    BEFORE UPDATE OR DELETE ON bettor_standing_order_events
    FOR EACH ROW EXECUTE FUNCTION bettor_standing_event_is_a_record();


-- ── 4 · CAPACITY HELD WHILE AN ORDER IS FILL-CAPABLE ──────────────────
CREATE TABLE IF NOT EXISTS bettor_standing_capacity_reservations (
    reservation_id          text        PRIMARY KEY,
    account_id              text        NOT NULL,
    venue                   text        NOT NULL,
    group_id                text        NOT NULL,
    plan_id                 text        NOT NULL
        REFERENCES bettor_standing_order_plans (plan_id),
    primary_intent_id       text        NOT NULL,
    hedge_intent_id         text,
    reserved_qty            numeric     NOT NULL CHECK (reserved_qty > 0),
    reserved_collateral_usd numeric     NOT NULL CHECK (
        reserved_collateral_usd >= 0),
    state                   text        NOT NULL
        CHECK (state IN ('RESERVED', 'RELEASED')),
    opened_at               timestamptz NOT NULL,
    released_at             timestamptz,
    release_reason          text CHECK (release_reason IS NULL OR
        release_reason IN ('TERMINAL_CONFIRMED_BY_THE_VENUE', 'NEVER_SENT')),
    release_evidence        jsonb,
    CONSTRAINT bettor_standing_reservation_release_ck CHECK (
        (state = 'RELEASED') = (release_reason IS NOT NULL
                                AND released_at IS NOT NULL))
);
-- STRICT FALLBACK, OUR SIDE OF IT: one reserved (live or potentially live)
-- standing hedge order per group.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_standing_one_reserved_per_group
    ON bettor_standing_capacity_reservations (group_id)
    WHERE state = 'RESERVED';

CREATE OR REPLACE FUNCTION bettor_standing_reservation_releases_on_terminal()
RETURNS trigger AS $$
DECLARE
    st text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'capacity reservation % is a record; it is released, '
                        'never deleted', OLD.reservation_id;
    END IF;
    IF OLD.state = 'RELEASED' THEN
        RAISE EXCEPTION 'capacity reservation % is already released',
                        OLD.reservation_id;
    END IF;
    IF NEW.reservation_id IS DISTINCT FROM OLD.reservation_id
       OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
       OR NEW.group_id IS DISTINCT FROM OLD.group_id
       OR NEW.reserved_qty IS DISTINCT FROM OLD.reserved_qty
       OR NEW.reserved_collateral_usd IS DISTINCT FROM
          OLD.reserved_collateral_usd THEN
        RAISE EXCEPTION 'capacity reservation % may only bind its intent or '
                        'be released', OLD.reservation_id;
    END IF;
    IF OLD.hedge_intent_id IS NOT NULL
       AND NEW.hedge_intent_id IS DISTINCT FROM OLD.hedge_intent_id THEN
        RAISE EXCEPTION 'capacity reservation % is bound to intent %',
                        OLD.reservation_id, OLD.hedge_intent_id;
    END IF;
    IF NEW.state = 'RELEASED' THEN
        IF NEW.release_reason = 'TERMINAL_CONFIRMED_BY_THE_VENUE' THEN
            SELECT i.state INTO st FROM public.bettor_funded_intents i
             WHERE i.intent_id = NEW.hedge_intent_id;
            IF st IS NULL OR public.bettor_funded_order_is_outstanding(st) THEN
                RAISE EXCEPTION 'capacity reservation % cannot be released: '
                                'its order (intent %, state %) is not '
                                'confirmed terminal -- a cancel request is '
                                'not the end of an order',
                                OLD.reservation_id, NEW.hedge_intent_id, st;
            END IF;
        ELSIF NEW.release_reason = 'NEVER_SENT' THEN
            IF NEW.hedge_intent_id IS NOT NULL THEN
                SELECT i.state INTO st FROM public.bettor_funded_intents i
                 WHERE i.intent_id = NEW.hedge_intent_id;
                IF st IS NOT NULL AND st NOT IN ('ABANDONED', 'REJECTED') THEN
                    RAISE EXCEPTION 'capacity reservation % names intent % '
                                    '(state %): it was sent',
                                    OLD.reservation_id, NEW.hedge_intent_id,
                                    st;
                END IF;
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS bettor_standing_reservation_releases_on_terminal_trg
    ON bettor_standing_capacity_reservations;
CREATE TRIGGER bettor_standing_reservation_releases_on_terminal_trg
    BEFORE UPDATE OR DELETE ON bettor_standing_capacity_reservations
    FOR EACH ROW EXECUTE FUNCTION
        bettor_standing_reservation_releases_on_terminal();

COMMIT;
