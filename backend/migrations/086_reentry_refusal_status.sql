-- A REFUSED RE-ENTRY IS A DECISION THAT NEVER BECAME A POSITION.
--
-- Owner directive 2026-09-20 §10/§11:
--
--     "X1 has zero re-entry violations... but the rule must become
--      actual enforcement rather than accidental compliance caused by
--      the ~65s tick cadence. Do not let polling cadence serve as the
--      risk control."
--
-- THE DEFECT THIS EXISTS TO PREVENT, which the enforcement itself
-- would otherwise have created:
--
--   `bettor_experimental_decisions.position_id` is written from the
--   reconstruction's own id. Once the guard refuses an entry, the
--   position row is never inserted -- so a decision row would carry a
--   position_id pointing at nothing, and every query that counts
--   "decisions that became positions" by `position_id IS NOT NULL`
--   would count a refusal as a trade. The enforcement would show up
--   in COMMAND as an EXTRA position rather than a refused one.
--
-- So the refusal gets its own execution_status and the row carries no
-- position_id and no economics. It is not a fill, not an unfill, and
-- not a NOT_IDENTIFIED: the arrival book may have been perfectly
-- readable. The frozen rule forbade the ENTRY.
--
-- NOTHING HISTORICAL CHANGES. §7: "Do not rewrite them." No existing
-- row has this status and none is given it; the constraint is only
-- widened so that a future refusal can be recorded truthfully instead
-- of being squeezed into a status that means something else.

BEGIN;

ALTER TABLE bettor_experimental_decisions
    DROP CONSTRAINT IF EXISTS bettor_exp_execution_status;

ALTER TABLE bettor_experimental_decisions
    ADD CONSTRAINT bettor_exp_execution_status
        CHECK (execution_status IN (
            'EXECUTED', 'PARTIAL', 'UNFILLED', 'NOT_IDENTIFIED',
            'BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE',
            'BLOCKED_INPUT_INVALID', 'NO_EXECUTION_INTENDED',
            'REFUSED_REENTRY_INSIDE_HORIZON'));

-- AND THE INVARIANT THE REFUSAL MUST KEEP: a refused entry carries no
-- position and no executed notional. Stated in the database rather
-- than only in the writer, because the writer is the thing most
-- likely to be changed by someone who has not read this file.
ALTER TABLE bettor_experimental_decisions
    DROP CONSTRAINT IF EXISTS bettor_exp_refusal_has_no_position;

ALTER TABLE bettor_experimental_decisions
    ADD CONSTRAINT bettor_exp_refusal_has_no_position
        CHECK (execution_status <> 'REFUSED_REENTRY_INSIDE_HORIZON'
               OR (position_id IS NULL
                   AND executed_notional_usd IS NULL
                   AND filled_qty IS NULL));

COMMIT;
