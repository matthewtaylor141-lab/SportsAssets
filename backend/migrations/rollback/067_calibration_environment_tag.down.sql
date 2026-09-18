-- ROLLBACK for 067.
--
-- Dropping these columns DESTROYS the only record of which exchange and
-- which environment a lifecycle ran against, and of the venue's own
-- correlation identifier for an ambiguous send. After this runs, a preprod
-- row and a production row are indistinguishable again and the two can be
-- summed into one figure by anything that reads the table.
--
-- So this is a schema rollback, not a safe undo. Run it only while the
-- institutional lane holds NO unresolved attempt and NO open lifecycle --
-- otherwise the evidence a reconciliation needs goes with the columns.

DROP INDEX IF EXISTS calibration_attempts_clord_idx;
DROP INDEX IF EXISTS calibration_lifecycles_env_idx;

ALTER TABLE calibration_send_attempts
    DROP CONSTRAINT IF EXISTS calibration_send_attempts_environment_known;
ALTER TABLE calibration_send_attempts DROP COLUMN IF EXISTS venue_clord_id;
ALTER TABLE calibration_send_attempts DROP COLUMN IF EXISTS venue;
ALTER TABLE calibration_send_attempts DROP COLUMN IF EXISTS environment;

ALTER TABLE calibration_lifecycles
    DROP CONSTRAINT IF EXISTS calibration_lifecycles_environment_known;
ALTER TABLE calibration_lifecycles DROP COLUMN IF EXISTS environment;

ALTER TABLE calibration_sessions
    DROP CONSTRAINT IF EXISTS calibration_sessions_environment_known;
ALTER TABLE calibration_sessions DROP COLUMN IF EXISTS environment;
ALTER TABLE calibration_sessions DROP COLUMN IF EXISTS venue;
