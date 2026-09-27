-- AN EXIT COMMITS NO COLLATERAL, AND THE ENTRY'S CHECK REFUSED THAT.
--
-- `bettor_funded_intents_collateral_usd_check` was `collateral_usd > 0`, which
-- is exactly right for an ENTRY: an order that reserves nothing is an order
-- that was never sized, and letting a zero through would put a position on the
-- book that no rail had measured.
--
-- Migration 126 then made exits intents too (`kind='EXIT'`), because an exit
-- has to be committed before it is sent and reconciled afterwards like any
-- other order. An exit RELEASES collateral; it commits none. So the honest
-- number is zero, and the entry's check refused to write it -- which meant the
-- servicing path could not persist its intent at all, and the one thing that
-- must never fail is the ability to reduce exposure.
--
-- THE CHECK IS THEREFORE MADE KIND-AWARE RATHER THAN RELAXED. An ENTRY still
-- has to reserve something positive; an EXIT may state zero and may not state
-- a negative, because a negative would be a reservation running the wrong way.

BEGIN;

ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_intents_collateral_usd_check;

ALTER TABLE bettor_funded_intents
    DROP CONSTRAINT IF EXISTS bettor_funded_intents_collateral_ck;
ALTER TABLE bettor_funded_intents
    ADD CONSTRAINT bettor_funded_intents_collateral_ck
    CHECK ((kind = 'ENTRY' AND collateral_usd > 0)
           OR (kind = 'EXIT' AND collateral_usd >= 0));

COMMENT ON COLUMN bettor_funded_intents.collateral_usd IS
    'What the venue takes, in the space the venue takes it: price x qty on a '
    'BUY_LONG and (1 - price) x qty on a BUY_SHORT. Positive and required on '
    'an ENTRY -- an unsized order is one no rail measured. Zero on an EXIT, '
    'which releases collateral rather than committing it.';

COMMIT;
