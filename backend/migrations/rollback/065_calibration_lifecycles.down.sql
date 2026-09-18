-- ROLLBACK FOR 065 (recorded before the release of 2026-09-18).
--
-- READ THIS BEFORE RUNNING IT.
--
-- 065 is PURELY ADDITIVE: two new tables and two new indexes. It alters
-- no existing table, drops no column and rewrites no row, so the API can
-- be rolled back to any earlier build with this migration left in place
-- and nothing will notice. THAT IS THE PREFERRED ROLLBACK: revert the
-- code, leave the tables.
--
-- WHAT THIS FILE DOES IS DESTRUCTIVE. Dropping calibration_lifecycles
-- destroys the record of every approved ticket and every reserve, and
-- dropping calibration_sessions destroys the cumulative $100 spend. That
-- history is the audit trail of a money experiment: once it is gone,
-- "how much has this session spent?" has no answer and the ceiling
-- cannot be enforced against what already happened.
--
-- SO IT IS NOT RUN AS PART OF AN ORDINARY ROLLBACK. Run it only if
-- management explicitly asks for the calibration ledger to be removed,
-- and only after exporting both tables:
--
--     \copy calibration_sessions   TO 'calibration_sessions.csv'   CSV HEADER
--     \copy calibration_lifecycles TO 'calibration_lifecycles.csv' CSV HEADER
--
-- If any lifecycle is still in an open state, an order may still be
-- resting at the venue. Reconcile it first; dropping the row does not
-- cancel anything.
--
--     SELECT client_order_id, state, reserve_usd, venue_order_id
--       FROM calibration_lifecycles
--      WHERE state <> 'RECONCILED';

DROP INDEX IF EXISTS calibration_lifecycles_session_idx;
DROP INDEX IF EXISTS calibration_one_open_lifecycle;
DROP TABLE IF EXISTS calibration_lifecycles;
DROP TABLE IF EXISTS calibration_sessions;
