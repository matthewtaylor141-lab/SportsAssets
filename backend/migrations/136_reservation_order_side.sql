-- ══════════════════════════════════════════════════════════════════════
-- 136 · A RESERVATION NAMES THE SIDE IT CLAIMS
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT WAS MISSING. A leg reservation recorded the instrument, the quantity,
-- the limit price and the collateral -- and NOT which of the instrument's two
-- outcome tokens it claimed. `bettor_funded_reservations.PLAN_FIELDS` compared
-- those four and nothing else, and 131's triggers fixed those four and nothing
-- else.
--
-- WHY THAT IS A REAL HOLE AND NOT A TIDINESS POINT. One PMUS instrument carries
-- two outcome tokens, and on this venue the price on the wire is always the
-- YES-denominated price. A LONG at wire price w posts w x q of collateral; a
-- SHORT at the same wire price posts (1 - w) x q. At w = 0.50 the two are
-- IDENTICAL on every field this table held:
--
--     slug          same instrument
--     quantity      same
--     limit_price   0.50 = 0.50
--     collateral    0.50 x q = (1 - 0.50) x q
--
-- So a reservation for one side and an order for the other matched, and the
-- rails counted them as one acquisition. The ranking chooses a SIDE -- the
-- candidate identity is `slug#SIDE` -- and that choice has to survive to the
-- venue; a reservation that cannot state it cannot defend it.
--
-- WHAT THIS DOES.
--
--   1. `order_intent` is added, NULLABLE. Existing rows predate the column and
--      nothing is invented for them. Production held zero reservations when this
--      was written (the funded book held zero intents), so no live row is left
--      without a side; the NULL case exists for correctness, not for a backlog.
--   2. It may only name one of the venue's two intents.
--   3. It is part of the reservation's FIXED IDENTITY. A different side is a
--      different acquisition and needs its own operation_id, exactly as a
--      different price or quantity already does.
--   4. WHEN THE RESERVATION IS COMMITTED TO AN INTENT, THE TWO SIDES MUST AGREE.
--      This is the boundary that matters: it is where the claim becomes an
--      order. A reservation that states a side refuses an intent on the other
--      one -- at the database, so no caller can route around it.
--
-- The two functions below are 131's, reproduced in full with the side added.
-- Nothing else in them changes.

-- `IF EXISTS` ON THE TABLE, because the runner applies every file at boot and
-- a migration that raises takes the API down (see 114's header). The table is
-- 131's, and the two always ship together; this only guarantees that a
-- database without 131 does not fail here instead of saying nothing.
ALTER TABLE IF EXISTS bettor_funded_leg_reservations
    ADD COLUMN IF NOT EXISTS order_intent text;

ALTER TABLE IF EXISTS bettor_funded_leg_reservations
    DROP CONSTRAINT IF EXISTS bettor_funded_reservation_side_ck;
ALTER TABLE IF EXISTS bettor_funded_leg_reservations
    ADD CONSTRAINT bettor_funded_reservation_side_ck CHECK (
        order_intent IS NULL
        OR order_intent IN ('ORDER_INTENT_BUY_LONG', 'ORDER_INTENT_BUY_SHORT'));

-- ── 4 · THE SIDE IS CHECKED WHERE THE CLAIM BECOMES AN ORDER ─────────
CREATE OR REPLACE FUNCTION bettor_funded_reservation_matches_group()
RETURNS trigger AS $$
DECLARE g record; i record;
BEGIN
    -- `FOR NO KEY UPDATE` FOR THE SAME REASON AS THE LEG TRIGGER: a SHARE lock
    -- lets two connections reserve the same leg role of the same group
    -- concurrently, each blind to the other's uncommitted row.
    SELECT * INTO g FROM public.bettor_funded_portfolio_groups
     WHERE group_id = NEW.group_id FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reservation % names group % which does not exist',
            NEW.reservation_id, NEW.group_id;
    END IF;
    IF g.closed_at IS NOT NULL
       AND NEW.state NOT IN ('CONSUMED', 'RELEASED') THEN
        RAISE EXCEPTION 'group % is closed (%); it cannot hold reservation %',
            g.group_id, g.closure, NEW.reservation_id;
    END IF;
    IF NEW.intent_id IS NOT NULL THEN
        SELECT * INTO i FROM public.bettor_funded_intents
         WHERE intent_id = NEW.intent_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'reservation % names intent % which does not exist',
                NEW.reservation_id, NEW.intent_id;
        END IF;
        -- THE INTENT MUST BE *THIS* LEG. A referential column alone would
        -- accept any intent in the table, including the other leg's or another
        -- group's, and the link would still read as evidence of this
        -- acquisition.
        IF i.portfolio_group_id IS DISTINCT FROM NEW.group_id
           OR i.leg_role IS DISTINCT FROM NEW.leg_role THEN
            RAISE EXCEPTION 'reservation % (group %, role %) names intent % '
                            'which belongs to group % role %',
                NEW.reservation_id, NEW.group_id, NEW.leg_role,
                i.intent_id, i.portfolio_group_id, i.leg_role;
        END IF;
        IF i.us_market_slug IS DISTINCT FROM NEW.us_market_slug THEN
            RAISE EXCEPTION 'reservation % reserved % and names intent % on %',
                NEW.reservation_id, NEW.us_market_slug, i.intent_id,
                i.us_market_slug;
        END IF;
        -- AND THE SAME SIDE OF IT. Added by 136. A reservation that stated no
        -- side (a row older than this column) is not compared: nothing is
        -- invented for it.
        IF NEW.order_intent IS NOT NULL
           AND i.order_intent IS DISTINCT FROM NEW.order_intent THEN
            RAISE EXCEPTION 'reservation % claimed % of % and names intent % '
                            'which is for %: a claim on one side is not an '
                            'order on the other',
                NEW.reservation_id, NEW.order_intent, NEW.us_market_slug,
                i.intent_id, i.order_intent;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── 3 · THE SIDE IS PART OF THE FIXED IDENTITY ───────────────────────
CREATE OR REPLACE FUNCTION bettor_funded_reservation_transitions_are_legal()
RETURNS trigger AS $$
DECLARE legal boolean;
BEGIN
    IF NEW.state = OLD.state THEN
        legal := true;
    ELSE
        legal := CASE OLD.state
            WHEN 'HELD'           THEN NEW.state IN ('COMMITTED', 'RELEASED')
            WHEN 'COMMITTED'      THEN NEW.state IN ('SEND_ATTEMPTED',
                                                     'RELEASED')
            WHEN 'SEND_ATTEMPTED' THEN NEW.state IN ('CONSUMED', 'AMBIGUOUS')
            WHEN 'AMBIGUOUS'      THEN NEW.state IN ('CONSUMED', 'RELEASED')
            ELSE false
        END;
    END IF;
    IF NOT legal THEN
        RAISE EXCEPTION 'reservation % cannot go from % to %: %',
            OLD.reservation_id, OLD.state, NEW.state,
            CASE
              WHEN OLD.state IN ('CONSUMED','RELEASED')
                THEN 'it is already resolved, and a resolved acquisition is '
                     'not re-opened'
              WHEN OLD.state = 'SEND_ATTEMPTED' AND NEW.state = 'RELEASED'
                THEN 'a request that may have left cannot be declared not to '
                     'have left; resolve it through AMBIGUOUS on the venue''s '
                     'own answer'
              WHEN OLD.state = 'HELD' AND NEW.state IN ('SEND_ATTEMPTED',
                                                        'AMBIGUOUS','CONSUMED')
                THEN 'nothing has been committed to an intent yet, so there is '
                     'no order for this to refer to'
              ELSE 'that is not a transition this machine has'
            END;
    END IF;
    IF NEW.operation_id IS DISTINCT FROM OLD.operation_id
       OR NEW.group_id IS DISTINCT FROM OLD.group_id
       OR NEW.leg_role IS DISTINCT FROM OLD.leg_role
       OR NEW.us_market_slug IS DISTINCT FROM OLD.us_market_slug
       OR NEW.quantity IS DISTINCT FROM OLD.quantity
       OR NEW.limit_price IS DISTINCT FROM OLD.limit_price
       OR NEW.collateral_usd IS DISTINCT FROM OLD.collateral_usd
       OR NEW.order_intent IS DISTINCT FROM OLD.order_intent THEN
        RAISE EXCEPTION 'reservation % identity is fixed: a different group, '
                        'role, contract, side, quantity, price or collateral '
                        'is a DIFFERENT acquisition and needs its own '
                        'operation_id',
            OLD.reservation_id;
    END IF;
    IF OLD.intent_id IS NOT NULL
       AND NEW.intent_id IS DISTINCT FROM OLD.intent_id THEN
        RAISE EXCEPTION 'reservation % is already committed to intent %; it '
                        'cannot be re-pointed at %',
            OLD.reservation_id, OLD.intent_id, NEW.intent_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
