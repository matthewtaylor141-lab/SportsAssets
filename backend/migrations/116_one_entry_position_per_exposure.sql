-- ONE AUTONOMOUS-ENTRY POSITION PER EXPOSURE, ENFORCED BY THE DATABASE.
--
-- `rn1x_positions` is UNIQUE on (experiment_id, policy, source_trade_id),
-- which protects the SEEDED lane: every seeded position comes from one
-- observed on-chain trade and that trade's id is the key.
--
-- AN AUTONOMOUS ENTRY HAS NO SOURCE TRADE. The column is NULL, and in
-- Postgres NULLs do not collide under a unique constraint, so two
-- entries into the same market on two successive cycles would both be
-- accepted. The lane derives its ids from the exposure and writes with
-- ON CONFLICT DO NOTHING, which makes a re-run idempotent -- but that is
-- a guard in one code path, and the harm being prevented is DUPLICATE
-- INVENTORY ON THE SAME EXPOSURE regardless of which path writes it.
--
-- WHY THESE FOUR COLUMNS AND NOT (experiment_id, policy). Keying on the
-- lane alone would forbid ever entering a second, different market --
-- which is the lane's normal operation. The exposure is the market and
-- the side, so it is the market and the side that may not be held twice.
--
-- IT IS CREATED ONLY IF THE DATA ALREADY SATISFIES IT, for the reason
-- migration 114 gives: a boot-time migration that raises on existing
-- duplicates takes the API down instead of reporting a data problem.
-- Skipped-with-a-warning is a finding somebody can act on.

DO $$
DECLARE
    dupes integer;
BEGIN
    SELECT count(*) INTO dupes FROM (
        SELECT experiment_id, policy, condition_id, outcome_index
          FROM rn1x_positions
         WHERE policy LIKE 'EXT\_%ENTRY\_%'
         GROUP BY 1, 2, 3, 4
        HAVING count(*) > 1) t;

    IF dupes = 0 THEN
        CREATE UNIQUE INDEX IF NOT EXISTS rn1x_one_entry_position
            ON rn1x_positions (experiment_id, policy, condition_id,
                               outcome_index)
         WHERE policy LIKE 'EXT\_%ENTRY\_%';
    ELSE
        RAISE WARNING
            'rn1x_one_entry_position NOT created: % duplicate group(s) '
            'already exist. Resolve the duplicates, then re-run this '
            'migration.', dupes;
    END IF;
END $$;
