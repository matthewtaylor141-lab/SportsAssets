-- ROLLBACK 248 · THE COLLECTOR'S COVERAGE RECEIPTS. Refused while any receipt
-- exists: the receipts are the only record of which competitions the
-- collector served, deferred (with the slot it promised) or skipped, and of
-- the metered credits each fetch cost -- the daily envelope's ledger. They are
-- never dropped as cleanup. With none, drops 248's objects only.
DO $$
BEGIN
    IF to_regclass('collector_coverage_receipts') IS NOT NULL
       AND EXISTS (SELECT 1 FROM collector_coverage_receipts) THEN
        RAISE EXCEPTION 'ROLLBACK_248_REFUSED: collector_coverage_receipts '
                        'holds recorded receipts';
    END IF;
END $$;
DROP TABLE IF EXISTS collector_coverage_receipts;
DROP FUNCTION IF EXISTS collector_coverage_receipts_append_only();
