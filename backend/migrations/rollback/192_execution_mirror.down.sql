-- Rollback of 192_execution_mirror.sql. Drops only the mirror's own tables;
-- no paper table is touched.
DROP TABLE IF EXISTS execmirror_snapshots;
DROP TABLE IF EXISTS execmirror_events;
DROP TABLE IF EXISTS execmirror_fills;
DROP TABLE IF EXISTS execmirror_orders;
DROP TABLE IF EXISTS execmirror_control;
