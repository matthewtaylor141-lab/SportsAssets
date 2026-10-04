-- Rollback of 242 (LAB-A edge-decay SHADOW research snapshots). Refuses
-- while any snapshot exists: a recorded measurement is research evidence
-- and is never dropped as cleanup. Touches only 242's objects.
DO $$
BEGIN
    IF to_regclass('lab_edge_decay_snapshots') IS NOT NULL
       AND EXISTS (SELECT 1 FROM lab_edge_decay_snapshots) THEN
        RAISE EXCEPTION 'lab_edge_decay_snapshots holds recorded snapshots; '
                        'rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS lab_edge_decay_snapshots;
DROP FUNCTION IF EXISTS lab_edge_decay_append_only();
