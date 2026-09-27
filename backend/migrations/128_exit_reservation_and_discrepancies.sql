-- FOUR THINGS THE FUNDED LIFECYCLE NEEDED AND DID NOT HAVE.
--
-- 1 · AN EXIT HAD NO RESERVATION. `submit_exit` read `residual_qty`, then
--     inserted an EXIT row -- with no lock between the two. EXIT rows are
--     deliberately outside the one-open-position index (an exit must never be
--     refused by it), so nothing stopped two concurrent exits, or a retry
--     after an ambiguous answer, from each selling the same residual. Read,
--     read, insert, insert: both see 10 held and both sell 10.
--
--     The reservation is computed inside a transaction that takes `FOR UPDATE`
--     on the PARENT row, so the second caller queues behind the first and
--     reads a residual the first has already committed against. The helper
--     below computes what is genuinely available, which is the residual MINUS
--     the unfilled remainder of every exit that is still outstanding or
--     unresolved -- an in-flight exit has consumed no fills yet, so it does
--     not show up in the residual at all, which is exactly why it has to be
--     subtracted here.
--
-- 2 · AN OVERSELL WAS SILENTLY CLAMPED. `_recompute_residual` took
--     `max(0, in - out)`. If exits ever exceeded entries the arithmetic went
--     negative and the clamp turned it into a tidy zero -- the one number that
--     reads as "flat and finished". An oversell is a DISCREPANCY: we sold
--     contracts we could not have held, so either a fill was double-counted or
--     we are short at the venue. It is recorded, not rounded away.
--
-- 3 · A FILL'S ECONOMIC EVENTS COULD BE LOST FOREVER. `ingest_fills` inserted
--     the fill, then wrote the cash and fee events as separate statements.
--     Stop between them and the redelivery hits `ON CONFLICT DO NOTHING` on
--     the fill, concludes "already held", and never writes the events -- so
--     the contracts are in inventory and their cost is in no ledger. The fix
--     is both: one transaction, AND a repair pass that can write events for
--     any fill missing them, so an interruption is recoverable rather than
--     merely unlikely.
--
-- 4 · A POSITION'S PAYOUT EVENT WAS NOT PERSISTED. `bettor_hold_value.ev_hold`
--     requires `payout_event_held` -- the event OUR contract pays on, which is
--     NOT derivable from the order intent (a BUY_SHORT pays on the complement,
--     and on a three-way book the complement is not the opposing team). Exit
--     management cannot value a holding without it, and guessing it is how a
--     position gets valued against the wrong outcome. It is stored at entry
--     from the decision, and an intent that lacks it reports a missing input.

BEGIN;

-- ── 1 · WHAT THE DECISION KNEW, KEPT WITH THE POSITION ──────────────

ALTER TABLE bettor_funded_intents
    ADD COLUMN IF NOT EXISTS payout_event text,
    ADD COLUMN IF NOT EXISTS held_is_long boolean,
    -- The venue's own order id is the only durable handle it gives us. This
    -- records whether the venue accepted a CLIENT-supplied identity for this
    -- request, so recovery's correlation rule can state on the row whether a
    -- durable request-to-venue identity existed at all.
    ADD COLUMN IF NOT EXISTS client_identity text,
    ADD COLUMN IF NOT EXISTS client_identity_supported boolean;

COMMENT ON COLUMN bettor_funded_intents.payout_event IS
    'The event THIS CONTRACT PAYS ON, carried from the decision that sized '
    'it -- never derived from the order intent. bettor_hold_value.ev_hold '
    'refuses without it, and a guess here values the position against the '
    'wrong outcome.';

COMMENT ON COLUMN bettor_funded_intents.client_identity_supported IS
    'FALSE on this venue: polymarket_us CreateOrderParams accepts no '
    'client-supplied order identifier (marketSlug, intent, type, price, '
    'quantity, tif, participateDontInitiate, goodTillTime, cashOrderQty, '
    'manualOrderIndicator, synchronousExecution, maxBlockTime, '
    'slippageTolerance -- and nothing else). So a lost acknowledgement has NO '
    'durable request-to-venue identity to reconcile against, and recovery '
    'must leave it UNRESOLVED rather than adopt an order that merely matches '
    'our terms.';

-- ── 2 · WHAT IS ACTUALLY AVAILABLE TO SELL ──────────────────────────

-- `coalesce` WRAPS THE WHOLE THING because a SQL function whose body selects
-- over a non-matching row returns NULL, not 0 -- and a NULL available quantity
-- compared against a requested one is false in every direction, which would
-- have read as "no room" for a real position and as "fine" for a typo.
CREATE OR REPLACE FUNCTION bettor_funded_available_to_exit(parent text)
RETURNS numeric AS $$
    SELECT coalesce((
    SELECT GREATEST(0, coalesce(p.residual_qty, 0) - coalesce((
        -- THE UNFILLED REMAINDER OF EVERY EXIT STILL IN FLIGHT.
        -- An outstanding exit has consumed no fills, so its quantity is not
        -- yet reflected in the residual; an UNRESOLVED exit may have
        -- executed at the venue without us knowing, so it counts at its
        -- full unfilled size too. Both are reservations against the same
        -- inventory.
        SELECT sum(GREATEST(0, c.quantity - coalesce((
                     SELECT sum(f.qty) FROM bettor_funded_fills f
                      WHERE f.intent_id = c.intent_id
                        AND f.direction = 'EXIT'), 0)))
          FROM bettor_funded_intents c
         WHERE c.parent_intent_id = p.intent_id
           AND c.kind = 'EXIT'
           AND bettor_funded_order_is_outstanding(c.state)), 0))
      FROM bettor_funded_intents p
     WHERE p.intent_id = parent), 0)
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION bettor_funded_available_to_exit(text) IS
    'The residual a NEW exit may sell: held contracts minus the unfilled '
    'remainder of every exit already outstanding or unresolved. Called inside '
    'the transaction that holds FOR UPDATE on the parent, which is what makes '
    'two concurrent exits impossible rather than unlikely.';

-- ── 3 · DISCREPANCIES, RECORDED RATHER THAN CLAMPED ─────────────────

CREATE TABLE IF NOT EXISTS bettor_funded_discrepancies (
    discrepancy_id  text PRIMARY KEY,
    intent_id       text,
    kind            text NOT NULL,
    at              timestamptz NOT NULL DEFAULT now(),
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    resolved_at     timestamptz,
    resolution      text
);

COMMENT ON TABLE bettor_funded_discrepancies IS
    'Things that cannot be true at once and were not rounded away: an exit '
    'that sold more than was held, a fill whose economic events were missing, '
    'a venue charge the schedule did not predict, an order the venue holds '
    'that no intent owns. Each is a row an operator must close, and NONE of '
    'them is a number that quietly became zero.';

CREATE INDEX IF NOT EXISTS bettor_funded_discrepancies_open_idx
    ON bettor_funded_discrepancies (kind, at)
    WHERE resolved_at IS NULL;

-- ── 4 · A FILL KNOWS WHETHER ITS ECONOMICS WERE WRITTEN ─────────────
--
-- The repair pass needs to find a fill whose events are missing WITHOUT
-- re-deriving them from the ledger every time, and an interrupted write must
-- be distinguishable from a completed one. The flag is set in the SAME
-- transaction as the events, so it can only be TRUE if they exist.

ALTER TABLE bettor_funded_fills
    ADD COLUMN IF NOT EXISTS economics_written boolean NOT NULL DEFAULT FALSE;

-- Rows that predate this column had their events written by the old
-- non-atomic path; whether they landed is established by asking the ledger,
-- so they start FALSE and the repair pass settles it.
CREATE INDEX IF NOT EXISTS bettor_funded_fills_unwritten_idx
    ON bettor_funded_fills (intent_id)
    WHERE NOT economics_written;

COMMIT;
