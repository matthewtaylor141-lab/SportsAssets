-- ── A SECOND HOLDING, UNDER *ONE* CAPACITY BOUND ────────────────────
--
-- Migration 126 created a unique index on the constant `(true)` over
-- `bettor_funded_intents` WHERE kind='ENTRY' AND is_open(...), so at most one
-- open ENTRY existed in the whole table. The preferred strategy is an existing
-- holding PLUS a distinct opposing contract on the same fixture, which is two
-- open ENTRY rows, and 126 rejects the second.
--
-- ── WHAT THE FIRST VERSION OF THIS FILE GOT WRONG ───────────────────
-- It created TWO INDEPENDENT bounds: one open GROUP (which may hold PRIMARY
-- and HEDGE) and, separately, one open UNGROUPED entry. Those coexist, so
-- three open entries across two economic positions were admissible while the
-- file claimed the one-position limit was preserved. An independent review was
-- right: two indexes that each permit one thing do not make one bound.
--
-- It also let a group release capacity while exposure remained. The group
-- index tested only `closed_at`, nothing tied closure to legs, orders or
-- reservations, and a closure reason I wrote myself --
-- HEDGE_ABANDONED_FIRST_LEG_RETAINED -- explicitly permitted closing with the
-- first holding still live. Repeating "close group, open another" would
-- accumulate inventory without ever tripping the one-open-group index. That is
-- worse than the constraint it replaced.
--
-- ── THE CORRECTION: ONE MECHANISM, AND CLOSURE IS EARNED ────────────
--
--   * EVERY funded ENTRY belongs to a group. There is no ungrouped path left
--     to hold a second bound: existing open entries are MIGRATED into
--     singleton groups, and a CHECK forbids new ungrouped entries. One
--     mechanism, so grouped and legacy acquisition cannot independently claim
--     the same capacity.
--   * ONE OPEN GROUP, by unique index on `(true)` over the groups table --
--     the only capacity bound in the system.
--   * A GROUP CANNOT CLOSE WHILE EXPOSURE REMAINS. Enforced by trigger
--     against the legs, their fills, outstanding orders and unresolved
--     reservations -- not by a nullable timestamp anyone may set.
--   * A CLOSED GROUP ACCEPTS NOTHING NEW. No legs, no reservations.
--   * ABANDONING A HEDGE DOES NOT RELEASE CAPACITY. The retained holding
--     keeps the group open, which is what "the first leg is still exposure"
--     means. The abandonment is recorded on the GROUP'S INTENT, not by
--     closing it.
--
-- A HEDGE ACQUISITION IS STILL NOT AN EXIT. `kind` stays 'ENTRY' for the
-- second leg, so every entry control applies to it. Labelling capital spending
-- an exit to slip past the entry rules would be the worst way to pass this.
--
-- THIS DOES NOT MAKE INDIRECT PAIRING EXECUTABLE.
-- `bettor_funded_management.EXECUTABLE_ACTIONS` is ('DIRECT_EXIT','REDUCE')
-- and FORM_INDIRECT_HEDGE has no dispatch. This file makes the second holding
-- representable, bounded and reservable. A schema that can hold a pair is not
-- a system that should buy one.
--
-- SCHEMA-QUALIFIED THROUGHOUT: an unqualified name in an index predicate
-- depends on a session `search_path` maintenance does not carry, which rolled
-- 126 back in production on PostgreSQL 17+ while passing on every 16.

BEGIN;

-- ── 1 · THE GROUP ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bettor_funded_portfolio_groups (
    group_id        text PRIMARY KEY,
    account_id      text        NOT NULL,
    venue           text        NOT NULL,
    -- THE FIXTURE EVERY LEG MUST SHARE. A pair whose legs settle on different
    -- fixtures is not a hedge; it is two positions with a story about them.
    event_key       text        NOT NULL,
    structure       text        NOT NULL,
    opened_at       timestamptz NOT NULL DEFAULT now(),
    closed_at       timestamptz,
    closure         text,
    -- THE HEDGE INTENT, SEPARATE FROM CLOSURE. Abandoning the acquisition is a
    -- decision about the SECOND leg and must not be expressible as closing the
    -- group, because the first leg is still exposure. Recorded here so it is
    -- reportable without releasing capacity.
    hedge_intent    text        NOT NULL DEFAULT 'SOUGHT',
    decision_ref    jsonb,
    CONSTRAINT bettor_funded_group_structure_ck CHECK (
        structure IN ('INDIRECT_MIDDLE', 'INDIRECT_GAP', 'SINGLE_LEG')),
    CONSTRAINT bettor_funded_group_hedge_intent_ck CHECK (
        hedge_intent IN ('SOUGHT', 'ACQUIRED', 'ABANDONED', 'NOT_APPLICABLE')),
    -- NOTE WHAT IS *NOT* HERE. There is no closure reason that permits an open
    -- holding. Every one of these requires zero exposure, and the trigger
    -- below enforces that rather than trusting the label.
    CONSTRAINT bettor_funded_group_closure_ck CHECK (
        closure IS NULL OR closure IN (
            'BOTH_LEGS_SETTLED',
            'ALL_LEGS_EXITED',
            'NEVER_HELD_ANY_INVENTORY')),
    CONSTRAINT bettor_funded_group_closed_has_reason_ck CHECK (
        (closed_at IS NULL) = (closure IS NULL))
);

-- ── 2 · THE ONE CAPACITY BOUND ──────────────────────────────────────
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_open_group
    ON bettor_funded_portfolio_groups ((true))
    WHERE closed_at IS NULL;

-- ── 3 · THE LEGS ────────────────────────────────────────────────────
ALTER TABLE bettor_funded_intents
    ADD COLUMN IF NOT EXISTS portfolio_group_id text
        REFERENCES bettor_funded_portfolio_groups(group_id),
    ADD COLUMN IF NOT EXISTS leg_role text;

ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_leg_role_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_leg_role_ck CHECK (
        leg_role IS NULL OR leg_role IN ('PRIMARY', 'HEDGE'));

ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_leg_pair_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_leg_pair_ck CHECK (
        (portfolio_group_id IS NULL) = (leg_role IS NULL));

-- ── 4 · EVERY EXISTING OPEN ENTRY JOINS THE ONE MECHANISM ───────────
--
-- MIGRATED, not grandfathered. A legacy open entry left ungrouped would be a
-- position outside the capacity bound, which is the hole the first version of
-- this file had. Each becomes a SINGLE_LEG group with role PRIMARY.
--
-- The one-open-group index means at most one of these can be created, which is
-- exactly 126's guarantee: there was at most one open ENTRY to migrate.
INSERT INTO bettor_funded_portfolio_groups
    (group_id, account_id, venue, event_key, structure, hedge_intent, closure,
     closed_at, decision_ref)
SELECT 'migrated-131:' || i.intent_id, i.account_id, i.venue, i.event_key,
       'SINGLE_LEG', 'NOT_APPLICABLE', NULL, NULL,
       jsonb_build_object('migrated_by', '131',
                          'why', 'a pre-existing open entry must not sit '
                                 'outside the one capacity bound')
FROM bettor_funded_intents i
WHERE i.kind = 'ENTRY'
  AND i.portfolio_group_id IS NULL
  AND public.bettor_funded_position_is_open(i.state, i.residual_qty,
                                            i.closed_at)
ON CONFLICT (group_id) DO NOTHING;

UPDATE bettor_funded_intents i
   SET portfolio_group_id = 'migrated-131:' || i.intent_id,
       leg_role = 'PRIMARY'
 WHERE i.kind = 'ENTRY'
   AND i.portfolio_group_id IS NULL
   AND public.bettor_funded_position_is_open(i.state, i.residual_qty,
                                             i.closed_at);

-- ── 5 · AND NO NEW ENTRY MAY BE UNGROUPED ───────────────────────────
--
-- This is what makes it ONE mechanism rather than two. A closed historical
-- entry keeps its NULL group (there is nothing to bound about it); an OPEN one
-- cannot exist outside a group at all.
ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_open_entry_has_a_group_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_open_entry_has_a_group_ck CHECK (
        kind <> 'ENTRY'
        OR portfolio_group_id IS NOT NULL
        OR NOT public.bettor_funded_position_is_open(state, residual_qty,
                                                     closed_at));

-- 126'S INDEX IS NOW REDUNDANT AND WOULD BE A SECOND BOUND. Dropped only
-- after the migration above has placed every open entry in a group, so the
-- limit is never absent.
DROP INDEX IF EXISTS bettor_funded_one_open_position;

-- ── 6 · ONE OPEN ENTRY PER ROLE PER GROUP ───────────────────────────
-- Two roles, one open entry each: at most two open legs, in ONE group, under
-- the single bound above. Exits are excluded for 126's own reason -- an exit
-- reduces exposure and must never be refused by a rule about holding it.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_open_leg_per_role
    ON bettor_funded_intents (portfolio_group_id, leg_role)
    WHERE kind = 'ENTRY'
      AND portfolio_group_id IS NOT NULL
      AND public.bettor_funded_position_is_open(state, residual_qty, closed_at);

CREATE INDEX IF NOT EXISTS bettor_funded_intents_group_idx
    ON bettor_funded_intents (portfolio_group_id)
    WHERE portfolio_group_id IS NOT NULL;

-- ── 7 · A LEG BELONGS TO ITS GROUP IN FULL ──────────────────────────
--
-- The foreign key checks only that the group EXISTS. It does not check that
-- the leg is the same account, the same venue or the same fixture -- so a leg
-- could join a group it has no economic relationship with, and the group's
-- `event_key` would be a label rather than a fact. Enforced by trigger,
-- because a CHECK cannot reference another table.
CREATE OR REPLACE FUNCTION bettor_funded_leg_matches_group()
RETURNS trigger AS $$
DECLARE g record;
BEGIN
    IF NEW.portfolio_group_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO g FROM public.bettor_funded_portfolio_groups
     WHERE group_id = NEW.portfolio_group_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'leg % names group % which does not exist',
            NEW.intent_id, NEW.portfolio_group_id;
    END IF;
    -- A CLOSED GROUP ACCEPTS NOTHING NEW.
    IF g.closed_at IS NOT NULL THEN
        RAISE EXCEPTION 'group % is closed (%); it cannot take leg %',
            g.group_id, g.closure, NEW.intent_id;
    END IF;
    IF NEW.account_id IS DISTINCT FROM g.account_id THEN
        RAISE EXCEPTION 'leg % account % does not match group % account %',
            NEW.intent_id, NEW.account_id, g.group_id, g.account_id;
    END IF;
    IF NEW.venue IS DISTINCT FROM g.venue THEN
        RAISE EXCEPTION 'leg % venue % does not match group % venue %',
            NEW.intent_id, NEW.venue, g.group_id, g.venue;
    END IF;
    IF NEW.event_key IS DISTINCT FROM g.event_key THEN
        RAISE EXCEPTION 'leg % fixture % does not match group % fixture % '
                        '-- legs on different fixtures are not a hedge',
            NEW.intent_id, NEW.event_key, g.group_id, g.event_key;
    END IF;
    -- THE TWO LEGS MUST BE DISTINCT INSTRUMENTS. Two roles on the SAME
    -- contract is same-contract netting, which is explicitly not this
    -- strategy: on this venue buying the opposite side of one market reduces
    -- the same book rather than creating a second settling holding.
    IF EXISTS (SELECT 1 FROM public.bettor_funded_intents o
                WHERE o.portfolio_group_id = NEW.portfolio_group_id
                  AND o.kind = 'ENTRY'
                  AND o.intent_id <> NEW.intent_id
                  AND o.leg_role IS DISTINCT FROM NEW.leg_role
                  AND o.us_market_slug = NEW.us_market_slug) THEN
        RAISE EXCEPTION 'leg % would pair % with itself: the other leg of '
                        'group % holds the same contract, which is netting '
                        'and not an indirect pair',
            NEW.intent_id, NEW.us_market_slug, NEW.portfolio_group_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_leg_matches_group_trg
    ON bettor_funded_intents;
CREATE TRIGGER bettor_funded_leg_matches_group_trg
    BEFORE INSERT OR UPDATE OF portfolio_group_id, leg_role, account_id,
                               venue, event_key, us_market_slug
    ON bettor_funded_intents
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_leg_matches_group();

-- ── 8 · RESERVATIONS ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bettor_funded_leg_reservations (
    reservation_id  text PRIMARY KEY,
    group_id        text        NOT NULL
                        REFERENCES bettor_funded_portfolio_groups(group_id),
    leg_role        text        NOT NULL,
    us_market_slug  text        NOT NULL,
    quantity        numeric     NOT NULL,
    collateral_usd  numeric     NOT NULL,
    limit_price     numeric     NOT NULL,
    state           text        NOT NULL,
    -- ── THE OPERATION IDENTITY ───────────────────────────────────────
    -- WHAT THIS IS FOR. A HELD-uniqueness rule stops two simultaneous
    -- reservations; it does NOT stop the same acquisition being replayed after
    -- the first became CONSUMED or RELEASED. This column names the ACQUISITION
    -- ATTEMPT, and it is unique across the whole table -- so a replay of the
    -- same operation is refused whatever state the earlier row reached, while
    -- a legitimate top-up carries a NEW identity and proceeds.
    operation_id    text        NOT NULL,
    -- REFERENTIAL, not free text. It was unrestricted, so CONSUMED could name
    -- an unrelated or nonexistent intent and the link would read as evidence.
    intent_id       text        REFERENCES bettor_funded_intents(intent_id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz,
    resolution      text,
    CONSTRAINT bettor_funded_reservation_role_ck CHECK (
        leg_role IN ('PRIMARY', 'HEDGE')),
    -- THE REAL TRANSITIONS, not a two-value flag. A send that was attempted
    -- and never acknowledged is AMBIGUOUS and must remain exposure until it is
    -- resolved -- it is not 'released' and it is not 'consumed'.
    CONSTRAINT bettor_funded_reservation_state_ck CHECK (
        state IN ('HELD', 'COMMITTED', 'SEND_ATTEMPTED', 'AMBIGUOUS',
                  'CONSUMED', 'RELEASED')),
    CONSTRAINT bettor_funded_reservation_positive_ck CHECK (
        quantity > 0 AND collateral_usd >= 0
        AND limit_price > 0 AND limit_price < 1),
    -- ONLY the two terminal states are resolved. HELD, COMMITTED,
    -- SEND_ATTEMPTED and AMBIGUOUS are all live and all consume capacity.
    CONSTRAINT bettor_funded_reservation_resolution_ck CHECK (
        (state IN ('CONSUMED', 'RELEASED')) = (resolved_at IS NOT NULL)
        AND (resolved_at IS NULL) = (resolution IS NULL)),
    -- FROM COMMITTED ONWARD AN INTENT EXISTS, because the intent is what the
    -- send refers to. A CONSUMED reservation with no intent claimed an order
    -- it could not name.
    CONSTRAINT bettor_funded_reservation_committed_has_intent_ck CHECK (
        state IN ('HELD', 'RELEASED') OR intent_id IS NOT NULL)
);

--: ONE LIVE RESERVATION PER LEG. Live means anything not terminal, so an
--: unacknowledged send still blocks a second attempt.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_live_reservation_per_leg
    ON bettor_funded_leg_reservations (group_id, leg_role)
    WHERE state IN ('HELD', 'COMMITTED', 'SEND_ATTEMPTED', 'AMBIGUOUS');

--: AND ONE ROW PER ACQUISITION ATTEMPT, EVER. This is the idempotency the
--: HELD index could not provide: replaying an operation after its reservation
--: was consumed or released is refused here.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_reservation_operation_uniq
    ON bettor_funded_leg_reservations (operation_id);

CREATE INDEX IF NOT EXISTS bettor_funded_reservations_group_idx
    ON bettor_funded_leg_reservations (group_id);

-- A RESERVATION MATCHES ITS GROUP TOO, and a closed group takes none.
CREATE OR REPLACE FUNCTION bettor_funded_reservation_matches_group()
RETURNS trigger AS $$
DECLARE g record; i record;
BEGIN
    SELECT * INTO g FROM public.bettor_funded_portfolio_groups
     WHERE group_id = NEW.group_id FOR SHARE;
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
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_reservation_matches_group_trg
    ON bettor_funded_leg_reservations;
CREATE TRIGGER bettor_funded_reservation_matches_group_trg
    BEFORE INSERT OR UPDATE ON bettor_funded_leg_reservations
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_reservation_matches_group();

-- ── 9 · CURRENTLY PAIRED INVENTORY, NOT HISTORICAL VOLUME ───────────
--
-- THE DEFECT THIS REPLACES. The first version summed ENTRY fills only, so
-- buying 10 on each leg and then selling all 10 of one still reported 10
-- paired -- a number describing what was once bought, presented as what is
-- currently hedged.
--
-- The convention is the one the funded book already uses: a leg's CURRENT
-- inventory is its entry fills minus its exit fills, where exits arrive as
-- fills on CHILD EXIT intents (`parent_intent_id`). Paired inventory is then
-- the minimum of the two legs' current inventories.
--
-- HISTORICAL MATCHED VOLUME IS A DIFFERENT QUESTION and gets a different
-- function, so neither can be read as the other.
CREATE OR REPLACE FUNCTION bettor_funded_group_leg_inventory(gid text,
                                                             role text)
RETURNS numeric AS $$
    WITH legs AS (
        SELECT i.intent_id
        FROM public.bettor_funded_intents i
        WHERE i.portfolio_group_id = gid
          AND i.kind = 'ENTRY'
          AND i.leg_role = role
    ),
    -- EVERY FILL ON THE LEG *AND* ON ITS EXIT CHILDREN. An exit recorded as a
    -- child intent is still this leg's inventory leaving.
    owned AS (
        SELECT l.intent_id FROM legs l
        UNION
        SELECT c.intent_id
        FROM public.bettor_funded_intents c
        JOIN legs l ON c.parent_intent_id = l.intent_id
    )
    SELECT COALESCE(SUM(
               CASE WHEN f.direction = 'ENTRY' THEN f.qty
                    WHEN f.direction = 'EXIT'  THEN -f.qty
                    ELSE 0 END), 0)
    FROM public.bettor_funded_fills f
    WHERE f.intent_id IN (SELECT intent_id FROM owned);
$$ LANGUAGE sql STABLE;

-- CURRENTLY PAIRED. NULL for a group that does not exist, because an unknown
-- group reporting 0 is indistinguishable from a known empty one -- and in a
-- readiness report those mean different things.
CREATE OR REPLACE FUNCTION bettor_funded_group_paired_qty(gid text)
RETURNS numeric AS $$
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM public.bettor_funded_portfolio_groups
                          WHERE group_id = gid) THEN NULL
        ELSE GREATEST(0, LEAST(
            public.bettor_funded_group_leg_inventory(gid, 'PRIMARY'),
            public.bettor_funded_group_leg_inventory(gid, 'HEDGE')))
    END;
$$ LANGUAGE sql STABLE;

-- HISTORICAL MATCHED VOLUME, kept separate and named for what it is.
CREATE OR REPLACE FUNCTION bettor_funded_group_matched_volume(gid text)
RETURNS numeric AS $$
    WITH e AS (
        SELECT i.leg_role,
               COALESCE(SUM(f.qty) FILTER (WHERE f.direction = 'ENTRY'), 0)
                   AS entered
        FROM public.bettor_funded_intents i
        LEFT JOIN public.bettor_funded_fills f ON f.intent_id = i.intent_id
        WHERE i.portfolio_group_id = gid AND i.kind = 'ENTRY'
          AND i.leg_role IS NOT NULL
        GROUP BY i.leg_role
    )
    SELECT COALESCE(LEAST(
        COALESCE((SELECT entered FROM e WHERE leg_role = 'PRIMARY'), 0),
        COALESCE((SELECT entered FROM e WHERE leg_role = 'HEDGE'), 0)), 0);
$$ LANGUAGE sql STABLE;

-- ── 10 · CLOSURE IS EARNED, NOT DECLARED ────────────────────────────
--
-- THE HOLE THIS CLOSES. The one-open-group index tested `closed_at` alone, and
-- nothing connected closure to exposure. Repeating "close the group, open
-- another" would accumulate live inventory while the index reported one open
-- group throughout. Abandoning a hedge must leave the RETAINED HOLDING
-- consuming capacity, which means it cannot be expressed by closing the group.
CREATE OR REPLACE FUNCTION bettor_funded_group_closure_is_earned()
RETURNS trigger AS $$
DECLARE
    inv numeric;
    live_orders int;
    live_res int;
BEGIN
    IF NEW.closed_at IS NULL OR OLD.closed_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    -- ANY residual inventory on ANY leg, entry or exit child.
    SELECT COALESCE(SUM(GREATEST(0,
               public.bettor_funded_group_leg_inventory(NEW.group_id, r))), 0)
      INTO inv
      FROM (VALUES ('PRIMARY'), ('HEDGE')) AS t(r);
    IF inv > 0 THEN
        RAISE EXCEPTION 'group % still holds % contract(s); closure would '
                        'release capacity while exposure remains',
            NEW.group_id, inv;
    END IF;
    -- ANY outstanding or ambiguous order on any leg or its children.
    SELECT count(*) INTO live_orders
      FROM public.bettor_funded_intents i
     WHERE (i.portfolio_group_id = NEW.group_id
            OR i.parent_intent_id IN (
                 SELECT intent_id FROM public.bettor_funded_intents
                  WHERE portfolio_group_id = NEW.group_id))
       AND public.bettor_funded_order_is_outstanding(i.state);
    IF live_orders > 0 THEN
        RAISE EXCEPTION 'group % has % outstanding or ambiguous order(s); '
                        'closure would abandon them', NEW.group_id, live_orders;
    END IF;
    -- ANY unresolved reservation.
    SELECT count(*) INTO live_res
      FROM public.bettor_funded_leg_reservations r
     WHERE r.group_id = NEW.group_id
       AND r.state IN ('HELD', 'COMMITTED', 'SEND_ATTEMPTED', 'AMBIGUOUS');
    IF live_res > 0 THEN
        RAISE EXCEPTION 'group % has % unresolved reservation(s); closure '
                        'would release capacity an acquisition still claims',
            NEW.group_id, live_res;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_group_closure_is_earned_trg
    ON bettor_funded_portfolio_groups;
CREATE TRIGGER bettor_funded_group_closure_is_earned_trg
    BEFORE UPDATE OF closed_at, closure ON bettor_funded_portfolio_groups
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_group_closure_is_earned();

COMMIT;
