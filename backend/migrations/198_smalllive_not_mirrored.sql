-- 198 · SMALL-LIVE RECONCILIATION: NOT_MIRRORED (2026-10-03).
--
-- Audrey reconciles every paper group the execution mirror has seen,
-- including groups whose every mirror row was EXCLUDED before submission
-- (an exploration or other paper-only strategy, a below-minimum quantity).
-- Such a group has NO actual leg by design. Recording it as MATCHED read as
-- "the actual position matches the paper position" when no actual position
-- was ever intended. NOT_MIRRORED says what is true: paper only, nothing sent
-- to the venue, and no live order or fill found against it. A live order or
-- fill on such a group is still a DISCREPANCY.
ALTER TABLE smalllive_reconciliations
    DROP CONSTRAINT IF EXISTS smalllive_reconciliations_status_check;
ALTER TABLE smalllive_reconciliations
    ADD CONSTRAINT smalllive_reconciliations_status_check
    CHECK (status IN ('MATCHED', 'DISCREPANCY', 'PENDING', 'NOT_MIRRORED'));
