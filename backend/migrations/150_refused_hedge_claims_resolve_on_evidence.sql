-- ══════════════════════════════════════════════════════════════════════
-- 150 · A HEDGE THE VENUE REFUSED IN ITS ANSWER RELEASES ITS LEG CLAIM ON
--       THAT RECORDED ANSWER -- AND ONLY ON AN EXPLICIT ONE
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP (Xavier execution map Q4). A hedge whose send returned an explicit
-- refusal in the response body (`rejected`, `preview_mismatch`, ...) with no
-- order id left its intent REJECTED and its leg claim AMBIGUOUS. Nothing could
-- resolve the claim: the lost-ack investigation skips a row that is not a lost
-- answer, the operator route refuses R_NOT_A_LOST_ACK, and
-- `resolve_from_the_venue` needs a named order or a terminal-order search. The
-- claim held the leg -- and the group's capacity -- forever.
--
-- WHAT THIS ADDS. One evidence kind, `VENUE_REFUSED_THE_ORDER_IN_ITS_ANSWER`,
-- which records the venue's own answer to THIS send: no order id, no results,
-- and the refusal status it stated. `bettor_funded_reservations.
-- release_on_explicit_refusal` reads it, and additionally requires the intent
-- itself to be REJECTED with no venue order id before AMBIGUOUS -> RELEASED.
-- An exception or an unknown outcome never writes this row, so it stays
-- AMBIGUOUS and its exposure stays counted.
--
-- ADDITIVE. The CHECK is widened by one kind; nothing existing changes.

BEGIN;

DO $$
BEGIN
    IF to_regclass('bettor_funded_operation_evidence') IS NOT NULL THEN
        ALTER TABLE bettor_funded_operation_evidence
            DROP CONSTRAINT IF EXISTS bettor_funded_evidence_kind_ck;
        ALTER TABLE bettor_funded_operation_evidence
            ADD CONSTRAINT bettor_funded_evidence_kind_ck CHECK (
                kind IN ('VENUE_NAMED_THE_ORDER', 'VENUE_HAS_NO_SUCH_ORDER',
                         'READ_ESTABLISHED_NOTHING',
                         'OPERATOR_ATTESTED_NO_EXPOSURE',
                         'OPERATOR_NAMED_THE_ORDER',
                         'VENUE_REFUSED_THE_ORDER_IN_ITS_ANSWER'));
        -- A REFUSAL RECORD NAMES NO ORDER, RETURNED NOTHING, AND STATES THE
        -- STATUS THE VENUE GAVE. A row carrying an order id is not a refusal.
        ALTER TABLE bettor_funded_operation_evidence
            DROP CONSTRAINT IF EXISTS bettor_funded_evidence_refused_ck;
        ALTER TABLE bettor_funded_operation_evidence
            ADD CONSTRAINT bettor_funded_evidence_refused_ck CHECK (
                kind <> 'VENUE_REFUSED_THE_ORDER_IN_ITS_ANSWER'
                OR (venue_order_id IS NULL AND results_returned = 0
                    AND coalesce(raw ->> 'status', '') <> ''));
    END IF;
END $$;

COMMIT;
