-- ROLLBACK 248 · THE COLLECTOR'S COVERAGE RECEIPTS. Refused while any receipt
-- exists: the receipts are the only record of which competitions the
-- collector served, deferred (with the slot it promised) or skipped, and of
-- the metered credits each fetch cost -- the daily envelope's ledger. They are
-- never dropped as cleanup. With none, drops 248's objects only. A second run
-- is a no-op: the guard reads the table only when it exists (EXECUTE, because
-- a PL/pgSQL `IF a AND b` still plans `b`, and planning a query on a dropped
-- table raises -- incident release, verifier finding 4; as rollback 261).
DO $$
DECLARE
    held boolean := false;
BEGIN
    IF to_regclass('collector_coverage_receipts') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM collector_coverage_receipts)'
           INTO held;
    END IF;
    IF held THEN
        RAISE EXCEPTION 'ROLLBACK_248_REFUSED: collector_coverage_receipts '
                        'holds recorded receipts';
    END IF;
END $$;
DROP TABLE IF EXISTS collector_coverage_receipts;
DROP FUNCTION IF EXISTS collector_coverage_receipts_append_only();
