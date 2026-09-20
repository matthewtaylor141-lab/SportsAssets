-- A VERSION THAT CANNOT CLOSE A POSITION MAY NOT OPEN ONE.
--
-- Owner directive 2026-09-20, "STOP NEW X1 V1 POSITION CREATION":
--
--     "It should not create additional positions under an exit policy
--      we already know is incomplete. Use a named prospective blocker:
--      EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE. This is not a
--      performance-based stop."
--
-- The decision is still recorded -- "Do not discard observations or
-- decisions", and X1 V1 may keep producing model evidence. What stops
-- is the POSITION. So the decision row needs a status that says the
-- entry was blocked by its own version's incomplete contract, and it
-- must carry no position id and no economics, for exactly the reason
-- migration 086 gave for the re-entry refusal: every query that counts
-- "decisions that became positions" counts `position_id IS NOT NULL`,
-- and a blocked entry holding a position id would be counted as a
-- trade that never happened.
--
-- THE INVARIANT IS WIDENED TO COVER BOTH REFUSAL KINDS rather than
-- duplicated, so a third kind added later cannot quietly escape it.
--
-- NOTHING HISTORICAL CHANGES. No existing row carries this status and
-- none is given it.

BEGIN;

ALTER TABLE bettor_experimental_decisions
    DROP CONSTRAINT IF EXISTS bettor_exp_execution_status;

ALTER TABLE bettor_experimental_decisions
    ADD CONSTRAINT bettor_exp_execution_status
        CHECK (execution_status IN (
            'EXECUTED', 'PARTIAL', 'UNFILLED', 'NOT_IDENTIFIED',
            'BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE',
            'BLOCKED_INPUT_INVALID', 'NO_EXECUTION_INTENDED',
            'REFUSED_REENTRY_INSIDE_HORIZON',
            'BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE'));

ALTER TABLE bettor_experimental_decisions
    DROP CONSTRAINT IF EXISTS bettor_exp_refusal_has_no_position;

ALTER TABLE bettor_experimental_decisions
    ADD CONSTRAINT bettor_exp_refusal_has_no_position
        CHECK (execution_status NOT IN (
                   'REFUSED_REENTRY_INSIDE_HORIZON',
                   'BLOCKED_EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE')
               OR (position_id IS NULL
                   AND executed_notional_usd IS NULL
                   AND filled_qty IS NULL));

COMMIT;
