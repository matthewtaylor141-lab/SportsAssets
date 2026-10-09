-- 366 rollback: the ACTUAL lane's aggregate exposure cap and Audrey's STALE.
--
-- The column is dropped: a release without it reads no aggregate cap (the
-- code before 366 had none; this release's code reads a missing column as
-- NULL -> max_order_usd, fail closed). Audrey's status CHECK goes back to its
-- 198 set, added NOT VALID so the STALE rows already written are kept as the
-- record they are (the previous release rewrites each group's status on its
-- next pass). Safe to apply twice.
ALTER TABLE execmirror_control
    DROP CONSTRAINT IF EXISTS execmirror_control_open_notional_ck;
ALTER TABLE execmirror_control
    DROP COLUMN IF EXISTS max_open_notional_usd;
ALTER TABLE smalllive_reconciliations
    DROP CONSTRAINT IF EXISTS smalllive_reconciliations_status_check;
ALTER TABLE smalllive_reconciliations
    ADD CONSTRAINT smalllive_reconciliations_status_check
    CHECK (status IN ('MATCHED', 'DISCREPANCY', 'PENDING', 'NOT_MIRRORED'))
    NOT VALID;
