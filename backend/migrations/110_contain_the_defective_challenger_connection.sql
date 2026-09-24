-- 110 — HOLD EVERY CHALLENGER DECISION THE BROKEN CONNECTION PRODUCED.
--
-- Independent inspection of deployed commit 5b19bc5 found four defects in
-- the production connection, all of them upstream of the ranking, so a
-- decision taken under that build can be wrong even though the selector,
-- the venue model and the accounting were all working correctly.
--
-- WHAT WAS WRONG, AND WHY EACH ONE INVALIDATES A DECISION.
--
--  1 EXIT PRICES WERE ACQUISITION COSTS, TRANSPOSED.
--    `challenger_inputs_for` asked `acquisition_ladder` for the opposite
--    intent and passed its acquisition price through as `bid`. On YES
--    bid .60 / ask .63 it supplied, for a held long, bid .40 and
--    complement_ask .63. The true pair is exit .60 / complement .40. So
--    the ranking compared a hold value against an exit price that was
--    roughly the complement of the real one, and it also saw an invented
--    spread between DIRECT_EXIT and TAKE_COMPLEMENT -- which on this
--    venue are the same order at the same price. Any selection made on
--    those numbers is unsound, in either direction.
--
--  2 THE PAYOUT IDENTITY CHECK COULD NOT FAIL. `payout_event_held` was
--    read off the valuation row and compared against that same row, so
--    every row for the condition passed -- including one describing the
--    OPPOSING exposure, which is a probability for the event the
--    position LOSES on. condition_id alone does not distinguish the two
--    sides of one market.
--
--  3 FRESHNESS PERMITTED 1,800 s WITH NO PRE-MATCH RESTRICTION, so a
--    half-hour-old moneyline could price a hold during play, and the
--    hold value was computed once per position rather than re-aged at
--    each decision.
--
--  4 A POSITION WAS DECIDED ONCE AND NEVER REVISITED, so the stored
--    decision is not a management record at all.
--
-- HELD, NOT DELETED. The refusals and the reasons on these rows are the
-- evidence about the connection, and a discarded row cannot be audited.
-- They are marked so no downstream read, comparison or report can treat
-- them as sound decisions.
--
-- SCOPE IS EXACT. Only the challenger arm ran through this builder. The
-- frozen MANAGEMENT_PAIR_091_STOP_16_V1 benchmark never touched
-- `challenger_inputs_for`, `bettor_hold_value` or the exit ladder -- it
-- reads its bid from its own caller and its own policy -- so its rows are
-- NOT affected and are deliberately left alone.

BEGIN;

ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS eligibility text;
ALTER TABLE rn1x_decisions
    ADD COLUMN IF NOT EXISTS ineligible_reason text;

COMMENT ON COLUMN rn1x_decisions.eligibility IS
    'ELIGIBLE, or held. A held decision is kept and excluded from every '
    'downstream claim; its inputs and refusals stay readable.';

UPDATE rn1x_decisions d
   SET eligibility = 'INELIGIBLE_BROKEN_PRODUCTION_CONNECTION',
       ineligible_reason =
           'taken under deployed 5b19bc5, whose input builder supplied '
           'the opposite side''s ACQUISITION cost as the exit price '
           '(held long: .40 for a .60 bid) and the same side''s '
           'acquisition as the complement cost; whose payout-identity '
           'check compared the valuation row against itself and so could '
           'not reject a row describing the opposing exposure; and whose '
           'freshness bound admitted a 1,800 s probability in play. Held, '
           'not deleted: the inputs and refusals on the row are the '
           'evidence about the connection.'
  FROM rn1x_positions p
 WHERE p.position_id = d.position_id
   AND d.eligibility IS NULL
   AND p.policy = 'SHADOW_CHALLENGER_HOLD_RANKED_V1';

-- Rows written from here are eligible by construction. The default is
-- applied AFTER the UPDATE so it cannot clear the rows being held.
ALTER TABLE rn1x_decisions
    ALTER COLUMN eligibility SET DEFAULT 'ELIGIBLE';

-- The benchmark's decisions are eligible and say so explicitly, rather
-- than carrying a NULL that a later reader has to interpret.
UPDATE rn1x_decisions d
   SET eligibility = 'ELIGIBLE'
  FROM rn1x_positions p
 WHERE p.position_id = d.position_id
   AND d.eligibility IS NULL
   AND p.policy <> 'SHADOW_CHALLENGER_HOLD_RANKED_V1';

ALTER TABLE rn1x_decisions
    DROP CONSTRAINT IF EXISTS rn1x_decision_eligibility_explained;
ALTER TABLE rn1x_decisions
    ADD CONSTRAINT rn1x_decision_eligibility_explained
    CHECK (eligibility IS NULL
           OR eligibility = 'ELIGIBLE'
           OR ineligible_reason IS NOT NULL)
    NOT VALID;

COMMIT;
