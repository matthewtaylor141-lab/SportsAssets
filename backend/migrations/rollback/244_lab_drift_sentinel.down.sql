-- Rollback of 244 (the LAB-F drift sentinel's SHADOW record). No decision,
-- order, sizing or limit path reads these tables, so nothing else changes.
-- REFUSES while any run is recorded: a recorded run is the append-only
-- record of what the sentinel measured at the time (and the work it
-- raised), and dropping it would erase that research trail. Touches only
-- 244's objects.
DO $$
BEGIN
    IF to_regclass('lab_drift_runs') IS NOT NULL
       AND EXISTS (SELECT 1 FROM lab_drift_runs) THEN
        RAISE EXCEPTION 'lab_drift_runs holds recorded runs; rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS lab_drift_tasks;
DROP TABLE IF EXISTS lab_drift_findings;
DROP TABLE IF EXISTS lab_drift_runs;
DROP FUNCTION IF EXISTS lab_drift_record_is_append_only();
