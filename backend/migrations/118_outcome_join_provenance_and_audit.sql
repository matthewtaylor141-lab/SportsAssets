-- ── WHAT THE OUTCOME MEANS, AND WHERE IT CAME FROM ──────────────────
--
-- THE DEFECT THIS CORRECTS, AND IT WAS MINE. `join_outcomes` turned the
-- venue's settlement price into the held exposure's outcome by way of
-- `payout_is_complement` -- a flag that describes THE PROBABILITY's
-- source event, not the venue's side. `resolve_venue_identity` sets it
-- FALSE whenever the probability already describes the requested
-- outcome, which it does even when that outcome is the venue's SHORT
-- side. So a market whose YES side settled at 1, held short, was
-- recorded as outcome 1 while the exposure actually paid 0.
--
-- Two conversions were collapsed into one flag:
--
--   PROBABILITY conversion  is the source event the payout event, or its
--                           complement? Applied once, at valuation.
--   SETTLEMENT conversion   is the held exposure the venue's LONG side
--                           or its SHORT side? Applied once, at the
--                           join, from `buy_intent` / `ladder_side`.
--
-- They are now separate, and the columns below record which conversion
-- the stored outcome went through -- so the same mistake cannot be made
-- silently a second time.
--
--   outcome_basis       how the 0/1 was established. NULL means "not
--                       established by the corrected mapper", which is
--                       what puts a row OUT OF calibration scope.
--   outcome_side_map    the venue-side identity actually applied.
--   settlement_read     the raw value read back, unparsed, so a later
--                       reader can recheck the arithmetic.
--   outcome_audit       why this row was reopened, with its prior value
--                       preserved verbatim.
--
-- `outcome_basis IS NOT NULL` is the single condition that both excludes
-- the pre-correction joins and re-admits them once corrected. It is a
-- data question with a data answer, not a date comparison against a
-- release time.

-- A CONFIRMED VOID CANNOT BE STORED AS `outcome_known`. Migration 103's
-- `external_valuations_outcome_matches_flag` requires a known outcome to
-- be 0 or 1 with a timestamp, so the previous join's attempt to record a
-- void as "known with a NULL outcome" violated the CHECK, raised, and was
-- counted as an error -- meaning voids were re-read every run forever and
-- the comment claiming otherwise was wrong. The constraint is RIGHT: a
-- void has no 0/1 truth. So a void is recorded by its BASIS instead, with
-- `outcome_known` still false, and `settlement_read_at` says when the
-- venue was asked. `outcome_basis IS NOT NULL` is what takes it out of
-- the unjoined queue.
ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS outcome_basis      text,
    ADD COLUMN IF NOT EXISTS outcome_side_map   text,
    ADD COLUMN IF NOT EXISTS settlement_read    text,
    ADD COLUMN IF NOT EXISTS settlement_read_at timestamptz,
    ADD COLUMN IF NOT EXISTS outcome_audit      text;

COMMENT ON COLUMN external_valuations.outcome_basis IS
    'How the 0/1 was established: VENUE_SETTLEMENT_PRICE, '
    'VENUE_REPORTED_OUTCOME, or CONFIRMED_VOID. NULL means the outcome '
    'was not established by the corrected venue-side mapper and the row '
    'is therefore out of calibration scope.';

-- ── THE AUDIT OF WHAT THIS RELEASE ALREADY JOINED ───────────────────
--
-- Every row whose outcome was set while the complement mapping was in
-- force is reopened: its prior value is preserved in `outcome_audit`,
-- its outcome is cleared so `join_outcomes` will read the venue again,
-- and it stays out of calibration until the corrected mapper records a
-- basis for it. The prior value is kept as TEXT rather than in a second
-- integer column, because it is evidence about a mistake and must never
-- be mistaken for a usable outcome.
--
-- Scoped to the external lane's experiment: no other experiment wrote
-- through this join.
UPDATE external_valuations
   SET outcome_audit =
           'REOPENED_BY_MIGRATION_118: prior outcome='
           || coalesce(outcome::text, 'NULL')
           || ', prior outcome_at=' || coalesce(outcome_at::text, 'NULL')
           || '. Established by the uncorrected mapping that read '
           || 'payout_is_complement as the venue side; excluded from '
           || 'calibration until the corrected mapper re-reads the venue.',
       outcome_known = FALSE,
       outcome       = NULL,
       outcome_at    = NULL
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND outcome_known = TRUE
   AND outcome_basis IS NULL;

-- A resolved row that carries no basis cannot be scored. The index is
-- what makes the calibration read cheap enough to run every cycle.
CREATE INDEX IF NOT EXISTS external_valuations_scorable_idx
    ON external_valuations (experiment_id, outcome_known, outcome_basis);
