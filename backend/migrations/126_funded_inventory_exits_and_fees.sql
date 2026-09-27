-- A FILLED ORDER IS NOT A CLOSED POSITION, AND I HAD THEM AS THE SAME THING.
--
-- THE DEFECT. `bettor_funded_intent_is_live` excluded FILLED, and everything
-- read exposure through it. So the moment an entry filled:
--
--   * the one-open-position index released its slot, and a second entry on the
--     same event could be inserted;
--   * that holding vanished from per-event and per-market collateral;
--   * MAX_CORRELATED_EXPOSURE counted only outstanding orders.
--
-- The contracts were still there. A FILLED entry is the point of MAXIMUM
-- exposure, not zero, and the index enforced one outstanding ORDER while
-- claiming to enforce one open POSITION.
--
-- THE SEPARATION. Two independent questions, two predicates:
--
--   IS THE ORDER OUTSTANDING?   state in (INTENT_RECORDED, SEND_ATTEMPTED,
--                               ACKNOWLEDGED, PARTIALLY_FILLED, UNRESOLVED)
--   DO WE STILL HOLD IT?        residual_qty > 0 AND closed_at IS NULL
--
-- A position is OPEN if either is true, and only an EVIDENCED exit or an
-- authoritative settlement sets `closed_at`. `residual_qty` is recomputed FROM
-- THE FILL LEDGER on every write -- never incremented -- for the same reason
-- the filled quantity already is: a derived counter that drifts is worse than
-- no counter. It is denormalised onto the row only because a partial unique
-- index cannot aggregate over another table.
--
-- EXITS ARE INTENTS TOO, and they must never be blocked by the one-open-
-- position rule: an exit REDUCES exposure, and refusing it because a position
-- is open is the failure that strands inventory. `kind` separates them and the
-- index applies to ENTRY alone.
--
-- AND ESTIMATED FEES ARE NOT ACTUAL FEES. `fee_usd` held
-- `calibration_fees.expected_fee` and was read downstream as what the venue
-- charged. The venue states its own commission on the execution record
-- (`commission_usd`), so expected and observed are now separate columns with a
-- state, and a fill whose actual fee has not arrived is PROVISIONAL rather
-- than silently exact.

BEGIN;

-- ── 1 · FILLS: direction, and expected vs observed fees ─────────────

ALTER TABLE bettor_funded_fills
    ADD COLUMN IF NOT EXISTS direction text NOT NULL DEFAULT 'ENTRY',
    ADD COLUMN IF NOT EXISTS expected_fee_usd numeric,
    ADD COLUMN IF NOT EXISTS observed_fee_usd numeric,
    ADD COLUMN IF NOT EXISTS fee_state text NOT NULL DEFAULT 'PROVISIONAL',
    ADD COLUMN IF NOT EXISTS fee_reconciliation jsonb NOT NULL
        DEFAULT '{}'::jsonb;

ALTER TABLE bettor_funded_fills
    DROP CONSTRAINT IF EXISTS bettor_funded_fills_direction_ck;
ALTER TABLE bettor_funded_fills
    ADD CONSTRAINT bettor_funded_fills_direction_ck
    CHECK (direction IN ('ENTRY', 'EXIT'));

ALTER TABLE bettor_funded_fills
    DROP CONSTRAINT IF EXISTS bettor_funded_fills_fee_state_ck;
ALTER TABLE bettor_funded_fills
    ADD CONSTRAINT bettor_funded_fills_fee_state_ck
    CHECK (fee_state IN ('PROVISIONAL',   -- expected only; no venue charge yet
                         'RECONCILED',    -- the venue's charge agrees
                         'DISAGREES'));   -- the venue charged something else

-- THE BOOKED FEE MUST NAME ITS SOURCE. `fee_usd` stays as the number the
-- accounting uses, and it is the observed charge when there is one and the
-- expectation otherwise -- with `fee_state` saying which.
UPDATE bettor_funded_fills
   SET expected_fee_usd = fee_usd
 WHERE expected_fee_usd IS NULL;

COMMENT ON COLUMN bettor_funded_fills.fee_usd IS
    'The fee the accounting USES: observed_fee_usd when the venue stated one, '
    'otherwise expected_fee_usd. `fee_state` says which, and PROVISIONAL '
    'means this number is an estimate and the cash is not final.';

-- ── 2 · INTENTS: kind, residual inventory, closure, settlement ───────

ALTER TABLE bettor_funded_intents
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'ENTRY',
    ADD COLUMN IF NOT EXISTS parent_intent_id text,
    ADD COLUMN IF NOT EXISTS residual_qty numeric NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS closed_at timestamptz,
    ADD COLUMN IF NOT EXISTS closed_reason text,
    ADD COLUMN IF NOT EXISTS settlement jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_intents_kind_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_intents_kind_ck
    CHECK (kind IN ('ENTRY', 'EXIT'));

ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_intents_residual_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_intents_residual_ck
    CHECK (residual_qty >= 0);

-- AN EXIT MUST NAME WHAT IT IS EXITING, and an entry must not.
ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_intents_parent_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_intents_parent_ck
    CHECK ((kind = 'EXIT' AND parent_intent_id IS NOT NULL)
           OR (kind = 'ENTRY' AND parent_intent_id IS NULL));

-- A CLOSURE MUST SAY WHY. `closed_at` is what removes a holding from
-- exposure, so it may never be set without an evidenced reason.
ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_intents_closure_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_intents_closure_ck
    CHECK ((closed_at IS NULL AND closed_reason IS NULL)
           OR (closed_at IS NOT NULL AND closed_reason IN (
                'EXITED_IN_THE_MARKET',
                'SETTLED_BY_THE_VENUE',
                'VOIDED_BY_THE_VENUE',
                'NEVER_HELD_ANY_INVENTORY')));

-- ── 3 · THE TWO PREDICATES, KEPT APART ──────────────────────────────
--
-- THE DROPS COME FIRST, AND THEY HAVE TO. `bettor_funded_one_live_intent` is
-- a partial index whose predicate CALLS `bettor_funded_intent_is_live`, so
-- Postgres refuses to CREATE OR REPLACE that function while the index depends
-- on it ("cannot change return type" / dependency errors). The first attempt
-- at this migration failed exactly there, silently leaving the old one-
-- outstanding-order index in place -- which is the defect this file exists to
-- fix, so it would have looked applied and changed nothing.

DROP INDEX IF EXISTS bettor_funded_one_live_intent;
DROP INDEX IF EXISTS bettor_funded_intents_event_idx;

-- IS THE ORDER STILL OUTSTANDING AT THE VENUE? FILLED is deliberately NOT
-- here: a filled order is finished. That was always right; reading it as
-- "no exposure" was the mistake.
CREATE OR REPLACE FUNCTION bettor_funded_order_is_outstanding(s text)
RETURNS boolean AS $$
    SELECT s IN ('INTENT_RECORDED', 'SEND_ATTEMPTED', 'ACKNOWLEDGED',
                 'PARTIALLY_FILLED', 'UNRESOLVED')
$$ LANGUAGE sql IMMUTABLE;

-- DO WE STILL HOLD CONTRACTS? Only an evidenced exit or an authoritative
-- settlement sets closed_at, so inventory cannot leave exposure by inference.
CREATE OR REPLACE FUNCTION bettor_funded_holds_inventory(residual numeric,
                                                         closed timestamptz)
RETURNS boolean AS $$
    SELECT coalesce(residual, 0) > 0 AND closed IS NULL
$$ LANGUAGE sql IMMUTABLE;

-- A POSITION IS OPEN IF EITHER IS TRUE.
--
-- THE INNER CALLS ARE SCHEMA-QUALIFIED, AND THAT IS NOT STYLE. This function
-- is the predicate of two partial indexes below, and PostgreSQL 17 and later
-- run maintenance work -- an index BUILD included -- under a deliberately safe
-- `search_path` of `pg_catalog, pg_temp`. A SQL function body is re-parsed when
-- the planner inlines it, so an UNQUALIFIED call inside this body cannot be
-- resolved at that moment, and the index creation fails with
--
--     function bettor_funded_order_is_outstanding(text) does not exist
--     CONTEXT: SQL function "bettor_funded_position_is_open" during inlining
--
-- Production said exactly that on 2026-09-27: the whole file rolled back, the
-- API served the previous funded schema, and the funded command-centre section
-- reported `column "residual_qty" does not exist`.
--
-- AND THE DEFECT WAS IN THIS FILE, not in that server. An unqualified name in
-- the body of a function used as an index predicate is incompatible SQL under
-- PostgreSQL 17 and later -- it depends on a session setting that maintenance
-- deliberately does not carry. It passed on every PostgreSQL 16 this file had
-- met, which is where the version comes in: 18 EXPOSED the defect, it did not
-- cause it, and "the file was byte-identical" describes how long it went
-- unnoticed rather than excusing it.
--
-- Qualifying the body fixes it at the cause. `IN` and the comparison operators
-- need no qualification: they resolve in pg_catalog, which is on the safe path.
CREATE OR REPLACE FUNCTION bettor_funded_position_is_open(s text,
                                                          residual numeric,
                                                          closed timestamptz)
RETURNS boolean AS $$
    SELECT public.bettor_funded_order_is_outstanding(s)
        OR public.bettor_funded_holds_inventory(residual, closed)
$$ LANGUAGE sql IMMUTABLE;

-- The old name is kept as an alias so nothing that still calls it silently
-- changes meaning -- it now answers the OUTSTANDING-ORDER question, which is
-- what it always computed.
-- Qualified for the same reason: it was an index predicate before this file
-- and may be one again in any database that has not yet applied this.
CREATE OR REPLACE FUNCTION bettor_funded_intent_is_live(s text)
RETURNS boolean AS $$
    SELECT public.bettor_funded_order_is_outstanding(s)
$$ LANGUAGE sql IMMUTABLE;

-- ── 4 · ONE OPEN *POSITION* AT A TIME ───────────────────────────────

-- ENTRY only: an exit reduces exposure and must never be refused by this.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_open_position
    ON bettor_funded_intents ((true))
    WHERE kind = 'ENTRY'
      AND bettor_funded_position_is_open(state, residual_qty, closed_at);

CREATE INDEX IF NOT EXISTS bettor_funded_intents_event_idx
    ON bettor_funded_intents (event_key)
    WHERE bettor_funded_position_is_open(state, residual_qty, closed_at);

CREATE INDEX IF NOT EXISTS bettor_funded_intents_parent_idx
    ON bettor_funded_intents (parent_intent_id)
    WHERE parent_intent_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS bettor_funded_fills_direction_idx
    ON bettor_funded_fills (intent_id, direction);

-- ── 5 · REALISED ECONOMIC EVENTS, SO P&L IS NOT A CONSTANT ──────────
--
-- `pnl()` returned a hardcoded 0.0 for realised P&L and `check_rails()` a
-- hardcoded 0.0 for drawdown. "Nothing has settled yet" describes today; it
-- cannot implement a loss stop tomorrow. Every economic event that moves cash
-- is recorded here, so realised P&L and drawdown are SUMS over rows.

CREATE TABLE IF NOT EXISTS bettor_funded_economics (
    event_id        text PRIMARY KEY,
    intent_id       text NOT NULL REFERENCES bettor_funded_intents
                        (intent_id),
    at              timestamptz NOT NULL,
    kind            text NOT NULL
        CHECK (kind IN ('ENTRY_COST',      -- cash out to open
                        'EXIT_PROCEEDS',   -- cash in from an exit
                        'FEE',             -- a charge, expected or observed
                        'SETTLEMENT',      -- the venue's payout
                        'FEE_ADJUSTMENT')),-- observed minus expected
    -- SIGNED, in dollars: negative is money leaving the account.
    amount_usd      numeric NOT NULL,
    qty             numeric,
    basis           text NOT NULL,
    provisional     boolean NOT NULL DEFAULT FALSE,
    evidence        jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE bettor_funded_economics IS
    'Every cash movement of the funded pilot, signed, with its basis and '
    'whether it is provisional. Realised P&L is the SUM of amount_usd and '
    'drawdown is derived from it -- neither is a constant, and a provisional '
    'row makes the total explicitly incomplete rather than quietly wrong.';

CREATE INDEX IF NOT EXISTS bettor_funded_economics_intent_idx
    ON bettor_funded_economics (intent_id, kind);
CREATE INDEX IF NOT EXISTS bettor_funded_economics_provisional_idx
    ON bettor_funded_economics (provisional) WHERE provisional;

COMMIT;
