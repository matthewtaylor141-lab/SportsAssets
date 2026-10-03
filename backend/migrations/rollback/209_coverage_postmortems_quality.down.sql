-- Refuses while any coverage alert, postmortem or improvement deficit
-- exists: they are Audrey's audit trail and are never dropped as cleanup.
-- Funnel snapshots alone are re-derivable from the production tables and
-- do not block the rollback.
DO $$
BEGIN
    IF to_regclass('coverage_collapse_alerts') IS NOT NULL
       AND EXISTS (SELECT 1 FROM coverage_collapse_alerts) THEN
        RAISE EXCEPTION 'coverage_collapse_alerts holds records; rollback refused';
    END IF;
    IF to_regclass('position_postmortems') IS NOT NULL
       AND EXISTS (SELECT 1 FROM position_postmortems) THEN
        RAISE EXCEPTION 'position_postmortems holds records; rollback refused';
    END IF;
    IF to_regclass('improvement_deficits') IS NOT NULL
       AND EXISTS (SELECT 1 FROM improvement_deficits) THEN
        RAISE EXCEPTION 'improvement_deficits holds records; rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS improvement_deficits;
DROP TABLE IF EXISTS position_postmortems;
DROP TABLE IF EXISTS coverage_collapse_alerts;
DROP TABLE IF EXISTS coverage_funnel_snapshots;
