-- down for 145. Refuses while any record it created exists: a correction is
-- why a booked payout changed and a contested account took exposure again,
-- an audit row is the record of who attempted it, and a report is evidence an
-- owner decided on. Dropping any of them would silently erase that.
DO $$
BEGIN
    IF (to_regclass('bettor_funded_settlement_corrections') IS NOT NULL
        AND EXISTS (SELECT 1 FROM bettor_funded_settlement_corrections))
       OR (to_regclass('bettor_funded_correction_audit') IS NOT NULL
           AND EXISTS (SELECT 1 FROM bettor_funded_correction_audit))
       OR (to_regclass('bettor_account_reconciliation_reports') IS NOT NULL
           AND EXISTS (SELECT 1 FROM bettor_account_reconciliation_reports))
       OR EXISTS (SELECT 1 FROM bettor_funded_economics
                   WHERE kind = 'SETTLEMENT_CORRECTION') THEN
        RAISE EXCEPTION 'reconciliation reports or settlement corrections are '
                        'recorded; 145 is not rolled back over them';
    END IF;
    ALTER TABLE bettor_funded_economics
        DROP CONSTRAINT IF EXISTS bettor_funded_economics_correction_id_ck;
    ALTER TABLE bettor_funded_economics
        DROP CONSTRAINT IF EXISTS bettor_funded_economics_kind_check;
    ALTER TABLE bettor_funded_economics
        ADD CONSTRAINT bettor_funded_economics_kind_check CHECK (
            kind IN ('ENTRY_COST', 'EXIT_PROCEEDS', 'FEE', 'SETTLEMENT',
                     'FEE_ADJUSTMENT'));
END $$;
DROP TABLE IF EXISTS bettor_funded_correction_audit;
DROP TABLE IF EXISTS bettor_funded_settlement_corrections;
DROP TABLE IF EXISTS bettor_account_reconciliation_reports;
DROP FUNCTION IF EXISTS bettor_funded_correction_audit_is_append_only();
DROP FUNCTION IF EXISTS bettor_funded_correction_is_append_only();
DROP FUNCTION IF EXISTS bettor_recon_report_is_append_only();
