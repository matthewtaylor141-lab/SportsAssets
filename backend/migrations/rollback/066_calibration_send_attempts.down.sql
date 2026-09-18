-- Rollback for 066. Drops what 066 added and nothing else.
--
-- The attempts table holds the record of which approvals were spent and
-- which sends are unresolved, so dropping it DISCARDS THAT HISTORY.
-- Export it before running this if any attempt is not RESOLVED:
--
--   \copy (SELECT * FROM calibration_send_attempts) TO 'attempts.csv' CSV HEADER
--
-- calibration_lifecycles keeps every column 065 created; only the
-- high-water column 066 added is removed.

DROP INDEX IF EXISTS calibration_attempts_unresolved;
DROP TABLE IF EXISTS calibration_send_attempts;
ALTER TABLE calibration_lifecycles DROP COLUMN IF EXISTS cash_booked_usd;
