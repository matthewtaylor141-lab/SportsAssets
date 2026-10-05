-- Rollback of 229 (runtime loop health). The table is TELEMETRY -- one
-- current row per loop, overwritten by each start / success / failure, read
-- only by GET /api/command/loop-health -- and holds no financial, research
-- or audit record, so dropping it loses nothing a decision or a ledger
-- depends on. Touches only 229's objects (the table takes its trigger with
-- it).
DROP TABLE IF EXISTS runtime_loop_health;
DROP FUNCTION IF EXISTS runtime_loop_health_no_delete();
