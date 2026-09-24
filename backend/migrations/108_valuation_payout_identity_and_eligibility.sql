-- 108 — THE PAYOUT IDENTITY, ON THE ROW, AND EVERY EXISTING ROW HELD.
--
-- WHY. `resolve_venue_identity` read the payout event off the venue's
-- ORDER INTENT:
--
--     short = intent == "ORDER_INTENT_BUY_SHORT"
--     payout_event = "NOT(outcome)" if short else outcome
--
-- which is wrong. `premap.resolve` is asked for a specific OUTCOME and
-- returns the row matching it together with the intent that BUYS it, so a
-- BUY_SHORT result for "Chicago Cubs" means Cubs is the venue's SHORT
-- side and the contract STILL PAYS ON CUBS. The intent selects which
-- ladder pays for the contract; it does not name the payout event.
--
-- The consequence was a fabricated edge: p(Cubs)=0.30 against an
-- acquisition cost of 0.40 is -0.10, and the defect computed
-- 0.70 - 0.40 = +0.30.
--
-- WHY EVERY EXISTING ROW IS HELD, AND WHY THAT IS EXACT RATHER THAN
-- CAUTIOUS. `external_valuations` records `contract_selection` and
-- `probability` but NOT the buy intent, so a row whose probability
-- describes NOT(selection) is indistinguishable from a correct one by the
-- stored columns alone. The window, however, is known: acceptance run 28
-- read `census evaluated 0` on build a5436a1 at 13:38-13:46Z, and the
-- first rows appear under 42a68c4. So every row in this table was written
-- by the defective build and none of them carries the evidence needed to
-- clear it. They are held, not deleted: the refusals on them are still
-- the deliverable, and a discarded row cannot be audited.
--
-- Rows written from here carry their own payout identity and are
-- ELIGIBLE by construction.

BEGIN;

ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS payout_event text;
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS payout_event_basis text;
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS probability_event text;
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS payout_is_complement boolean;
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS buy_intent text;
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS matched_side_norm text;
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS resolver_asked_for text;
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS ladder_side text;

ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS eligibility text;
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS ineligible_reason text;

COMMENT ON COLUMN external_valuations.payout_event IS
    'The event THIS CONTRACT PAYS ON, established from the requested '
    'outcome and the venue side the resolver matched -- never inferred '
    'from the order intent.';
COMMENT ON COLUMN external_valuations.probability_event IS
    'The event the stored `probability` describes. Equal to payout_event '
    'unless payout_is_complement is true.';
COMMENT ON COLUMN external_valuations.ladder_side IS
    'BID for a short acquisition, ASK for a long. This is the ONLY thing '
    'the order intent decides.';
COMMENT ON COLUMN external_valuations.eligibility IS
    'ELIGIBLE, or held. A held row is kept and excluded from every '
    'downstream claim; its refusals remain readable.';

-- EVERY PRE-EXISTING ROW IS HELD. No row written before this migration
-- records its payout identity, so none can be cleared.
UPDATE external_valuations
   SET eligibility = 'INELIGIBLE_PAYOUT_IDENTITY_UNVERIFIED',
       ineligible_reason =
           'written before migration 108. The payout event was derived '
           'from the order intent, which is wrong for a venue side whose '
           'exposure is the short leg, and the row does not record the '
           'intent so it cannot be re-checked. Held, not deleted.'
 WHERE eligibility IS NULL;

ALTER TABLE external_valuations
    ALTER COLUMN eligibility SET DEFAULT 'ELIGIBLE';

-- A held row must say why, and an eligible one must not pretend to.
ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuation_eligibility_explained;
ALTER TABLE external_valuations
    ADD CONSTRAINT external_valuation_eligibility_explained
    CHECK (eligibility IS NULL
           OR eligibility = 'ELIGIBLE'
           OR ineligible_reason IS NOT NULL);

-- AND A COMPLEMENT MUST BE DECLARED, NOT IMPLIED. If the probability
-- describes a different event from the payout, both names must be
-- present, so a future reader can check the relationship instead of
-- trusting it.
ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuation_complement_is_named;
ALTER TABLE external_valuations
    ADD CONSTRAINT external_valuation_complement_is_named
    CHECK (payout_is_complement IS NOT TRUE
           OR (payout_event IS NOT NULL AND probability_event IS NOT NULL))
    NOT VALID;

COMMIT;
