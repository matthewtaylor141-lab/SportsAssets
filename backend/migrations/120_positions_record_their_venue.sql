-- ── WHICH VENUE'S POSITION MODEL APPLIES TO THIS POSITION ───────────
--
-- THE DEFECT THIS CORRECTS, AND IT WAS MINE. The settlement consumer
-- replayed a position's ledger in the TWO-TOKEN vocabulary: an opposite
-- side acquisition became a SECOND inventory leg, and both legs were
-- settled. On Polymarket US that is wrong, and `bettor_venue_position_model`
-- exists to say so:
--
--     docs/rn1-two-sided-design.md §1 -- "Polymarket US keeps one signed
--     netPosition per market slug ... Buying the complement of something
--     you hold is not a second position; it is a sale of the first."
--
-- Booking it as a second leg records a matched pair the venue says does
-- not exist, inflates gross inventory, and then settles quantity that was
-- already flattened. I had written a test asserting exactly that wrong
-- behaviour, which is worse than having no test: it made the defect look
-- deliberate.
--
-- `bettor_venue_position_model.model_for` REFUSES an unknown venue rather
-- than defaulting, for the same reason -- "assuming the two-token model on
-- a netting venue records a matched pair the venue would have flattened,
-- and assuming the netting model on a two-token venue reports a capital
-- release that never happened". A consumer can only honour that refusal if
-- it knows which venue the position is on, and `rn1x_positions` did not
-- record one.
--
-- NULL IS A REFUSAL, NOT A DEFAULT. A position with no venue recorded and
-- no source valuation to read one from is not settled at all.

ALTER TABLE rn1x_positions
    ADD COLUMN IF NOT EXISTS venue text;

COMMENT ON COLUMN rn1x_positions.venue IS
    'The venue whose position model governs this position -- PMUS nets one '
    'signed position per market, the global CLOB holds both tokens. NULL '
    'means it was not recorded, and a consumer must resolve it from the '
    'source valuation or refuse; it is never defaulted, because guessing '
    'the model either invents a matched pair or invents a capital release.';

-- ── AND HOW ITS OPENING INVENTORY CAME TO EXIST ─────────────────────
--
-- THE SECOND DEFECT. `replay` decided "this position was assigned its
-- inventory" by testing whether the ledger happened to contain zero BUY
-- fills, and it applied that seed AFTER replaying the fills. Both halves
-- are wrong:
--
--   * an ASSIGNED position that has since SOLD part of its inventory has
--     SELL fills and no BUY fills, so the sell was replayed against an
--     empty book and `Portfolio.sell` raised -- the review's own case
--     (assigned 100 at .60, sell 40 at .80, settle 60) could not run; and
--   * a position that was acquisition-filled and then FULLY EXITED also
--     ends with no held quantity, so seeding it would have resurrected
--     inventory that was deliberately closed.
--
-- The distinction is PROVENANCE, which `rn1x_positions.provenance` already
-- records and migration 117 already enumerates:
--
--   RN1_SIGNAL_DERIVED                    assigned -- seeded from someone
--                                         else's observed trade, booked at
--                                         zero fee because no execution of
--                                         ours occurred
--   ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY   assigned -- the acceptance
--                                         harness's synthetic position
--   AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_  acquisition-filled -- this
--   SHADOW                                system's own order and fill are
--                                         in the ledger
--
-- The mapping lives in `bettor_entry_settlement.OPENING_BY_PROVENANCE`
-- beside the code that uses it, and an unrecognised provenance refuses.
-- Nothing is added to the schema for it: the column already exists and
-- already carries the answer.
