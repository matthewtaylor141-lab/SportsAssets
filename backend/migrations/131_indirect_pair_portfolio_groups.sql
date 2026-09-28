-- ── A SECOND HOLDING IS THE POINT, AND 126 FORBIDS IT ───────────────
--
-- THE CONSTRAINT THIS REPLACES. Migration 126 created
--
--     CREATE UNIQUE INDEX bettor_funded_one_open_position
--         ON bettor_funded_intents ((true))
--         WHERE kind = 'ENTRY'
--           AND bettor_funded_position_is_open(state, residual_qty, closed_at);
--
-- a unique index on the CONSTANT `(true)`, so at most ONE open ENTRY exists in
-- the whole table. That was the right bound when every entry was a standalone
-- position: it made "one funded position at a time" a database fact rather
-- than a policy someone remembers.
--
-- The preferred strategy is an EXISTING HOLDING PLUS A DISTINCT OPPOSING
-- CONTRACT on the same fixture whose payouts overlap, so both can win and the
-- verified settlement states do not permit both to lose. That is two open
-- ENTRY rows, and 126 rejects the second with
--
--     duplicate key value violates unique constraint
--     "bettor_funded_one_open_position"   DETAIL: Key ((true))=(t) already exists.
--
-- which the PostgreSQL 18 gate log shows happening repeatedly.
--
-- ── WHAT THIS FILE DOES *NOT* DO ────────────────────────────────────
-- IT DOES NOT SIMPLY DROP THE INDEX. Removing a bound because it is in the
-- way converts a deliberate limit into an accident, and the limit is real:
-- unbounded concurrent funded entries is exactly the 2026-08-11 stacking
-- incident. The bound MOVES UP A LEVEL and stays enforced by the database:
--
--   BEFORE   at most one open ENTRY row.
--   AFTER    at most one open PORTFOLIO GROUP, and at most one open ENTRY
--            per (group, leg role) -- with exactly two roles, so at most two
--            open legs, both belonging to the same group.
--
-- Concurrent funded exposure is therefore still bounded, still by an index,
-- and the number it is bounded to is stated rather than implied.
--
-- IT ALSO DOES NOT MAKE A HEDGE ACQUISITION AN EXIT. A second leg SPENDS
-- capital. `kind` stays 'ENTRY' for it, so every entry control -- collateral,
-- correlated exposure, the authority checks -- applies to it exactly as to a
-- first leg. Labelling a capital-spending acquisition an exit to slip it past
-- the entry rules would be the worst possible way to pass this constraint.
--
-- ── AND IT DOES NOT MAKE INDIRECT PAIRING EXECUTABLE ────────────────
-- `bettor_funded_management.EXECUTABLE_ACTIONS` is ('DIRECT_EXIT', 'REDUCE');
-- FORM_INDIRECT_HEDGE has no dispatch. This file makes the second holding
-- REPRESENTABLE and RESERVABLE. Discovery, verified settlement payoffs, joint
-- outcome probabilities, economic ranking, acquisition, reconciliation and
-- operator reporting are separate work, and a schema that can hold a pair is
-- not a system that should buy one.
--
-- ── SCHEMA-QUALIFIED THROUGHOUT ─────────────────────────────────────
-- Every function reference in an index predicate is `public.`-qualified. An
-- unqualified name in a predicate depends on a session `search_path` that
-- maintenance does not carry, which rolled 126 back in production on
-- PostgreSQL 17+ while passing on every 16 it had met. The same mistake is
-- not repeated here.

BEGIN;

-- ── 1 · THE GROUP ───────────────────────────────────────────────────
--
-- A group is a bounded set of legs held as ONE economic position. It exists so
-- the "one at a time" bound has something to count that is not a row.
CREATE TABLE IF NOT EXISTS bettor_funded_portfolio_groups (
    group_id        text PRIMARY KEY,
    account_id      text        NOT NULL,
    venue           text        NOT NULL,
    -- THE FIXTURE BOTH LEGS MUST SHARE. A pair whose legs settle on different
    -- fixtures is not a hedge; it is two positions with a story about them.
    event_key       text        NOT NULL,
    structure       text        NOT NULL,
    opened_at       timestamptz NOT NULL DEFAULT now(),
    closed_at       timestamptz,
    -- WHY IT CLOSED, because "closed" alone cannot distinguish a completed
    -- pair from an abandoned acquisition, and those have different P&L.
    closure         text,
    -- The decision that created it, so a group is traceable to its reasoning.
    decision_ref    jsonb,
    CONSTRAINT bettor_funded_group_structure_ck CHECK (
        structure IN ('INDIRECT_MIDDLE', 'INDIRECT_GAP', 'SINGLE_LEG')),
    CONSTRAINT bettor_funded_group_closure_ck CHECK (
        closure IS NULL OR closure IN (
            -- Both legs held to their settlement.
            'BOTH_LEGS_SETTLED',
            -- The pair was unwound deliberately.
            'BOTH_LEGS_EXITED',
            -- The first leg is held and the hedge was never acquired. NOT a
            -- failure state: abandoning a hedge whose economics moved is the
            -- correct outcome, and it must be nameable.
            'HEDGE_ABANDONED_FIRST_LEG_RETAINED',
            -- Opened and nothing was ever filled.
            'NEVER_HELD_ANY_INVENTORY')
    ),
    CONSTRAINT bettor_funded_group_closed_has_reason_ck CHECK (
        (closed_at IS NULL) = (closure IS NULL))
);

-- ── 2 · AT MOST ONE OPEN GROUP, WHICH IS 126'S BOUND, MOVED ─────────
--
-- The same `(true)` trick 126 used, on the table where one row IS one
-- position. `(true)` is deliberate and is the whole mechanism: a unique index
-- on a constant admits exactly one matching row.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_open_group
    ON bettor_funded_portfolio_groups ((true))
    WHERE closed_at IS NULL;

-- ── 3 · THE LEGS ────────────────────────────────────────────────────
ALTER TABLE bettor_funded_intents
    ADD COLUMN IF NOT EXISTS portfolio_group_id text
        REFERENCES bettor_funded_portfolio_groups(group_id),
    ADD COLUMN IF NOT EXISTS leg_role text;

-- EXACTLY TWO ROLES. The cap on legs per group is expressed as a role
-- vocabulary plus a unique index, so it needs no trigger and cannot drift:
-- two possible roles, one open entry each, therefore at most two open legs.
ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_leg_role_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_leg_role_ck CHECK (
        leg_role IS NULL OR leg_role IN ('PRIMARY', 'HEDGE'));

-- A LEG WITHOUT A GROUP, OR A GROUP WITHOUT A ROLE, IS NEITHER. Both or
-- neither, so an ungrouped row cannot silently become a leg.
ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_leg_pair_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_leg_pair_ck CHECK (
        (portfolio_group_id IS NULL) = (leg_role IS NULL));

-- ── 4 · ONE OPEN ENTRY PER ROLE PER GROUP ───────────────────────────
--
-- This is what admits the second holding while keeping it bounded. Note
-- `kind = 'ENTRY'`: exits are excluded for 126's own reason -- an exit reduces
-- exposure and must never be refused by a rule about holding it.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_open_leg_per_role
    ON bettor_funded_intents (portfolio_group_id, leg_role)
    WHERE kind = 'ENTRY'
      AND portfolio_group_id IS NOT NULL
      AND public.bettor_funded_position_is_open(state, residual_qty, closed_at);

-- ── 5 · AN UNGROUPED ENTRY IS STILL LIMITED TO ONE ──────────────────
--
-- 126's index is REPLACED, not deleted: the same bound now applies to entries
-- that belong to no group, so the old behaviour is preserved exactly for every
-- row that existed before this file. Dropping 126's index without this would
-- have removed the bound from the legacy path.
DROP INDEX IF EXISTS bettor_funded_one_open_position;
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_open_ungrouped_position
    ON bettor_funded_intents ((true))
    WHERE kind = 'ENTRY'
      AND portfolio_group_id IS NULL
      AND public.bettor_funded_position_is_open(state, residual_qty, closed_at);

-- ── 6 · TRANSACTIONAL RESERVATIONS FOR THE SECOND LEG ───────────────
--
-- WHY A RESERVATION AND NOT A CHECK-THEN-ACT. Between "there is room for this
-- hedge" and "the order is acknowledged" the venue may fill, the book may
-- move, and this process may die. A reservation is a ROW, taken in the same
-- transaction that decides, so a restart finds it and knows an acquisition was
-- in flight rather than inferring from an absence.
--
-- ONE LIVE RESERVATION PER (GROUP, ROLE), by unique index, so a retry after a
-- lost acknowledgement cannot open a second one and buy the hedge twice.
CREATE TABLE IF NOT EXISTS bettor_funded_leg_reservations (
    reservation_id  text PRIMARY KEY,
    group_id        text        NOT NULL
                        REFERENCES bettor_funded_portfolio_groups(group_id),
    leg_role        text        NOT NULL,
    us_market_slug  text        NOT NULL,
    -- WHAT IS RESERVED. Quantity and the collateral it commits, so the
    -- exposure of an in-flight acquisition is countable before it fills.
    quantity        numeric     NOT NULL,
    collateral_usd  numeric     NOT NULL,
    limit_price     numeric     NOT NULL,
    state           text        NOT NULL,
    -- The intent it became, once one exists. NULL while the reservation is
    -- held and no order has been recorded.
    intent_id       text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz,
    resolution      text,
    CONSTRAINT bettor_funded_reservation_role_ck CHECK (
        leg_role IN ('PRIMARY', 'HEDGE')),
    CONSTRAINT bettor_funded_reservation_state_ck CHECK (
        state IN ('HELD', 'CONSUMED', 'RELEASED')),
    CONSTRAINT bettor_funded_reservation_positive_ck CHECK (
        quantity > 0 AND collateral_usd >= 0
        AND limit_price > 0 AND limit_price < 1),
    -- A RESOLVED RESERVATION MUST SAY WHY, for the same reason a closed group
    -- must: "released" cannot distinguish a hedge we chose not to buy from one
    -- whose order was refused, and those are different facts about the system.
    CONSTRAINT bettor_funded_reservation_resolution_ck CHECK (
        (state = 'HELD') = (resolved_at IS NULL)
        AND (resolved_at IS NULL) = (resolution IS NULL)),
    CONSTRAINT bettor_funded_reservation_consumed_has_intent_ck CHECK (
        state <> 'CONSUMED' OR intent_id IS NOT NULL)
);

-- THE ANTI-DUPLICATE-SUBMISSION INVARIANT. At most one HELD reservation per
-- (group, role): the second attempt to reserve the same leg fails in the
-- database rather than producing a second order.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_held_reservation_per_leg
    ON bettor_funded_leg_reservations (group_id, leg_role)
    WHERE state = 'HELD';

CREATE INDEX IF NOT EXISTS bettor_funded_reservations_group_idx
    ON bettor_funded_leg_reservations (group_id);

CREATE INDEX IF NOT EXISTS bettor_funded_intents_group_idx
    ON bettor_funded_intents (portfolio_group_id)
    WHERE portfolio_group_id IS NOT NULL;

-- ── 7 · WHAT IS PAIRED IS WHAT FILLED ───────────────────────────────
--
-- ONLY FILLED MATCHED QUANTITIES COUNT AS PAIRED. A reservation is not a
-- holding and an acknowledged order is not a fill, so the paired quantity is
-- the MINIMUM of the two legs' filled quantities -- never the intended sizes,
-- and never the larger leg. A partial fill on the hedge therefore pairs only
-- what it actually bought, and the remainder of the first leg is unhedged and
-- must read as unhedged.
-- FROM THE FILL LEDGER, NOT FROM THE INTENT. `bettor_funded_intents` carries
-- `quantity` (what was ORDERED) and `residual_qty` (what is still held); there
-- is no filled column on it, and the first version of this function invented
-- one. Applying the file to a real database is what caught it: `column
-- i.filled_qty does not exist`.
--
-- The filled quantity is `SUM(f.qty) WHERE direction = 'ENTRY'`, which is the
-- same source 126 requires for `residual_qty` -- recomputed from the ledger,
-- never incremented, because a derived counter that drifts is worse than no
-- counter.
CREATE OR REPLACE FUNCTION bettor_funded_group_paired_qty(gid text)
RETURNS numeric AS $$
    WITH leg_fills AS (
        SELECT i.leg_role,
               COALESCE(SUM(f.qty) FILTER (WHERE f.direction = 'ENTRY'), 0)
                   AS entered
        FROM public.bettor_funded_intents i
        LEFT JOIN public.bettor_funded_fills f ON f.intent_id = i.intent_id
        WHERE i.portfolio_group_id = gid
          AND i.kind = 'ENTRY'
          AND i.leg_role IS NOT NULL
        GROUP BY i.leg_role
    )
    SELECT COALESCE(LEAST(
        COALESCE((SELECT entered FROM leg_fills
                  WHERE leg_role = 'PRIMARY'), 0),
        COALESCE((SELECT entered FROM leg_fills
                  WHERE leg_role = 'HEDGE'), 0)), 0);
$$ LANGUAGE sql STABLE;

COMMIT;
