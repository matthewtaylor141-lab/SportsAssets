-- Restores the three-value status check. NOT VALID: rows already recorded as
-- NOT_MIRRORED are kept as written (an audit record is never rewritten to
-- satisfy a rollback); only new writes are held to the old values.
ALTER TABLE smalllive_reconciliations
    DROP CONSTRAINT IF EXISTS smalllive_reconciliations_status_check;
ALTER TABLE smalllive_reconciliations
    ADD CONSTRAINT smalllive_reconciliations_status_check
    CHECK (status IN ('MATCHED', 'DISCREPANCY', 'PENDING')) NOT VALID;
