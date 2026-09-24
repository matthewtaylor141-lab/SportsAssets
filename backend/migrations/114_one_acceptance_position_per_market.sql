-- ONE ACCEPTANCE POSITION PER MARKET, ENFORCED BY THE DATABASE.
--
-- The seeder serialises its lookup-and-insert on an advisory lock, which
-- stops two concurrent requests from both concluding that no position
-- exists. That is a guard in one process family. This index is the guard
-- that holds regardless of which process, which release, or which code
-- path does the writing: the acceptance harness may hold at most one row
-- per (experiment, policy, condition, outcome).
--
-- WHY NOT UNIQUE ON (experiment, policy) ALONE. That would also forbid
-- ever seeding a second acceptance position after the first has been fully
-- exited and closed, which is a legitimate future action. The harm being
-- prevented is DUPLICATE INVENTORY ON THE SAME EXPOSURE, and that is what
-- the four columns name.
--
-- IT IS CREATED ONLY IF THE DATA ALREADY SATISFIES IT. A boot-time
-- migration that raises on existing duplicates would take the API down
-- instead of reporting a data problem, so duplicates are counted first and
-- the index is skipped with a warning if any exist. Skipped-with-a-warning
-- is a finding a reader can act on; a failed boot is not.

DO $$
DECLARE
    dupes integer;
BEGIN
    SELECT count(*) INTO dupes FROM (
        SELECT experiment_id, policy, condition_id, outcome_index
          FROM rn1x_positions
         WHERE policy LIKE 'ACCEPTANCE\_%'
         GROUP BY 1, 2, 3, 4
        HAVING count(*) > 1) t;

    IF dupes = 0 THEN
        CREATE UNIQUE INDEX IF NOT EXISTS rn1x_one_acceptance_position
            ON rn1x_positions (experiment_id, policy, condition_id,
                               outcome_index)
         WHERE policy LIKE 'ACCEPTANCE\_%';
    ELSE
        RAISE WARNING
            'rn1x_one_acceptance_position NOT created: % duplicate group(s) '
            'already exist. Resolve the duplicates, then re-run this '
            'migration.', dupes;
    END IF;
END $$;
