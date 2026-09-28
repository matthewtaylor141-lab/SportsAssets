-- ══════════════════════════════════════════════════════════════════════
-- 133 · THE VENUE'S OWN ANSWER, DURABLE AND BOUND TO ONE OPERATION
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS REPLACES. `bettor_funded_reservations.resolve_from_the_venue` took a
-- BOOLEAN, `the_venue_has_the_order`, and treated it as evidence. A caller could
-- clear an AMBIGUOUS send -- the state that exists precisely because we do not
-- know whether a real order is live at the venue -- by passing False. And
-- `record_the_venue_named_it` could CONSUME a reservation with no order id at
-- all, so the row claimed the venue had named an order it could not name.
--
-- A parameter is not evidence. The venue's answer has to be written down, bound
-- to the operation it answers, and readable afterwards by someone who was not
-- there.
--
-- ── AND AN EMPTY ORDER SEARCH IS NOT PROOF OF ABSENCE ────────────────
--
-- THIS IS THE FAILURE THE TABLE IS SHAPED AROUND. The obvious recovery is: list
-- the open orders, see none of ours, conclude nothing was placed, release the
-- reservation. That is wrong in the one case that costs money -- AN ORDER THAT
-- FILLED IMMEDIATELY IS NOT OPEN. It is filled. So the search that "found
-- nothing" was searching a set the order had already left, and the conclusion
-- "no such order exists" is drawn from a query that could never have found it.
--
-- So absence-evidence must record WHAT WAS SEARCHED, and the code requires the
-- search to have been capable of returning the order:
--
--   * scoped to this account AND this instrument,
--   * covering a window that contains the send instant,
--   * and INCLUDING TERMINAL ORDERS -- filled, cancelled, rejected -- not only
--     the open book.
--
-- `covered_terminal_orders` is NOT NULL and is asserted true by the code before
-- an absence can release anything. A read that only saw open orders is stored,
-- because it is a fact about what we asked, and it is REFUSED as a basis for
-- release.
--
-- ADDITIVE ONLY: one table, no change to any existing object.
-- KEEP OUT OF PRODUCTION until its consumer is released with it.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_funded_operation_evidence (
    evidence_id     text PRIMARY KEY,
    -- BOUND TO THE OPERATION, NOT MERELY MENTIONING IT. `operation_id` is unique
    -- across the reservations table, so this reference names exactly one
    -- acquisition attempt.
    operation_id    text        NOT NULL,
    account_id      text        NOT NULL,
    venue           text        NOT NULL,
    us_market_slug  text        NOT NULL,
    intent_id       text,
    kind            text        NOT NULL,
    -- REQUIRED FOR A NAMED ORDER. A "the venue named it" record with no id names
    -- nothing.
    venue_order_id  text,
    -- ── WHAT WAS ACTUALLY ASKED ──────────────────────────────────────
    search_endpoint text        NOT NULL,
    search_scope    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    covered_terminal_orders boolean NOT NULL,
    window_from_epoch_s numeric,
    window_to_epoch_s   numeric,
    results_returned int         NOT NULL DEFAULT 0,
    raw             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    read_at         timestamptz NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT bettor_funded_evidence_kind_ck CHECK (
        kind IN ('VENUE_NAMED_THE_ORDER', 'VENUE_HAS_NO_SUCH_ORDER',
                 'READ_ESTABLISHED_NOTHING')),
    --: A NAMED ORDER CARRIES ITS ID. Enforced here rather than in the caller,
    --: because the caller was the thing that got it wrong.
    CONSTRAINT bettor_funded_evidence_named_has_id_ck CHECK (
        kind <> 'VENUE_NAMED_THE_ORDER' OR venue_order_id IS NOT NULL),
    --: AND AN ABSENCE CLAIM CARRIES A SEARCH THAT COULD HAVE FOUND IT. A
    --: `VENUE_HAS_NO_SUCH_ORDER` row whose search never looked at terminal
    --: orders is a contradiction: it asserts absence from a set the order may
    --: simply have left.
    CONSTRAINT bettor_funded_evidence_absence_searched_terminal_ck CHECK (
        kind <> 'VENUE_HAS_NO_SUCH_ORDER' OR covered_terminal_orders),
    CONSTRAINT bettor_funded_evidence_absence_found_nothing_ck CHECK (
        kind <> 'VENUE_HAS_NO_SUCH_ORDER' OR results_returned = 0)
);

CREATE INDEX IF NOT EXISTS bettor_funded_evidence_operation_idx
    ON bettor_funded_operation_evidence (operation_id, kind);

--: ONE EVIDENCE ROW PER (operation, kind, endpoint, read instant), so a replayed
--: recovery pass does not stack duplicates of the same read.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_evidence_read_uniq
    ON bettor_funded_operation_evidence
       (operation_id, kind, search_endpoint, read_at);

-- ── EVIDENCE IS NOT EDITED AFTER IT IS RECORDED ─────────────────────
-- Its whole value is that it says what the venue answered at a particular
-- instant. A row that can be rewritten afterwards records what we later wished
-- it had said.
CREATE OR REPLACE FUNCTION bettor_funded_evidence_is_not_rewritten()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'evidence % is a record of what the venue answered at %; it '
                    'is not edited afterwards',
        OLD.evidence_id, OLD.read_at;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_funded_evidence_is_not_rewritten_trg
    ON bettor_funded_operation_evidence;
CREATE TRIGGER bettor_funded_evidence_is_not_rewritten_trg
    BEFORE UPDATE ON bettor_funded_operation_evidence
    FOR EACH ROW EXECUTE FUNCTION bettor_funded_evidence_is_not_rewritten();

COMMIT;
