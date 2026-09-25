-- ── A POSITION MUST CARRY THE IDENTITY IT WAS OPENED UNDER ──────────
--
-- THE DEFECT THIS CORRECTS, AND IT WAS MINE. `bettor_entry_settlement`
-- found the venue contract for a position by taking "the latest
-- admissible valuation for the same experiment and condition". That is
-- not a link to the decision that created the position. It is a guess
-- that happens to be right while exactly one admissible valuation exists
-- per condition, and the settlement it produces is irreversible.
--
-- Worse, nothing checked that the valuation described the side the
-- position HOLDS. A condition has two sides; a valuation naming the other
-- one would have settled the position against the opposite outcome, and
-- the "one distinct identity" fallback does not help -- every available
-- valuation could describe the wrong side.
--
-- So the identity is PERSISTED ON THE POSITION, by the writer that knows
-- it, at the moment it knows it:
--
--   venue_market_slug    the venue-native contract the order was for
--   venue_buy_intent     ORDER_INTENT_BUY_LONG / _BUY_SHORT
--   venue_ladder_side    ASK / BID -- the same fact, independently stated
--   payout_event         the event this position pays on, by name
--   source_valuation_id  the external_valuations row that admitted it
--
-- `payout_event` beside `outcome_index` is deliberate redundancy: the
-- index is the global catalogue's ordering and the name is the event, and
-- a consumer can check one against the other through `market_tokens`
-- rather than trusting either alone. That cross-check is what makes a
-- settlement auditable instead of merely recorded.
--
-- NULL IS A MEANINGFUL VALUE HERE. Positions created before this
-- migration -- including the acceptance position, which was never opened
-- through the entry lane -- carry NULL, and the settlement consumer must
-- then resolve the held outcome independently from `market_tokens` and
-- refuse when it cannot. A backfill would be a guess wearing a column.

ALTER TABLE rn1x_positions
    ADD COLUMN IF NOT EXISTS venue_market_slug   text,
    ADD COLUMN IF NOT EXISTS venue_buy_intent    text,
    ADD COLUMN IF NOT EXISTS venue_ladder_side   text,
    ADD COLUMN IF NOT EXISTS payout_event        text,
    ADD COLUMN IF NOT EXISTS source_valuation_id bigint;

COMMENT ON COLUMN rn1x_positions.venue_market_slug IS
    'The venue-native contract this position was opened on, recorded by '
    'the writer that opened it. NULL means the position predates this '
    'column or was not opened through the entry lane, and a consumer must '
    'resolve the identity independently rather than assume one.';

COMMENT ON COLUMN rn1x_positions.payout_event IS
    'The event this position pays on, by name, so it can be cross-checked '
    'against outcome_index through market_tokens instead of either being '
    'trusted alone.';

-- ── A TERMINAL SETTLEMENT MUST TAKE A POSITION OUT OF MANAGEMENT ─────
--
-- `rn1x_outcomes` already existed and already carried `outcome_basis`,
-- and two very different things are recorded under it:
--
--   OBSERVED_PAYOUT_SCORING_ONLY  a payout observed for SCORING. The
--                                 position may still hold inventory.
--   the settlement consumer's own bases -- a TERMINAL settlement: the
--                                 contract no longer exists, the residual
--                                 is closed and the exposure is released.
--
-- `store.OPEN_POSITIONS_SQL` asks only whether SELL fills have released
-- the seed quantity, so a terminally settled position with no SELL fills
-- stayed "open" forever: the manager kept selecting it, kept valuing a
-- contract that no longer exists, and the outcome row said residual zero
-- while the open query said otherwise. Excluding EVERY outcome row would
-- have been wrong in the other direction -- it would stop managing a live
-- challenger position the moment a scoring observation was recorded.
--
-- So terminality is a property of the BASIS. The list itself lives in
-- `bettor_rn1x_store.TERMINAL_OUTCOME_BASES` and is interpolated into the
-- queries that need it, NOT read from the view below: a query that depends
-- on a view has to have that view present in every database any test
-- builds, and it fails as "relation does not exist" a long way from the
-- cause. The view exists for reporting and ad-hoc reads only, and nothing
-- in the application depends on it.
CREATE OR REPLACE VIEW rn1x_terminal_settlements AS
    SELECT position_id, outcome_basis, settled_at, written_at,
           residual_qty, net_usd
      FROM rn1x_outcomes
     WHERE outcome_basis IN (
        'VENUE_AUTHORITATIVE_SETTLEMENT_OF_THE_HELD_CONTRACT',
        'VENUE_CONFIRMED_VOID_STAKES_RETURNED');

COMMENT ON VIEW rn1x_terminal_settlements IS
    'Outcome rows that END a position: the venue settled the contract we '
    'held, so the residual is closed and the exposure released. Excludes '
    'OBSERVED_PAYOUT_SCORING_ONLY, which is a payout observed for scoring '
    'while the position may still hold inventory.';

CREATE INDEX IF NOT EXISTS rn1x_outcomes_basis_idx
    ON rn1x_outcomes (outcome_basis);
