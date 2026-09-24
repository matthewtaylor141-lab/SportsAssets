-- THE CURSOR COLUMN, ADDED FORWARD.
--
-- WHAT HAPPENED. Migration 094 was written with
-- `cursor_decision_ts`/`cursor_decision_id`, deployed, and applied at
-- API boot by `start.sh`. It was THEN edited in place to
-- `cursor_event_id` -- the right column, for the right reason: a
-- timestamp repeats and arrives out of order across ingestion lanes,
-- so a cursor built on one can go backwards and replay work.
--
-- But `CREATE TABLE IF NOT EXISTS` does not reshape a table that
-- already exists. The edit therefore changed the file and not the
-- database, and the live loop died on its first read with
-- `column "cursor_event_id" does not exist`.
--
-- EDITING AN APPLIED MIGRATION IS THE MISTAKE, and this file is the
-- correction: migrations move forward, they are not rewritten. 094
-- keeps its history; 095 states the change.
--
-- The old columns are left in place rather than dropped. They hold
-- nothing, dropping them buys nothing, and a DROP on a live table is a
-- risk taken for tidiness.

ALTER TABLE bettor_desk_state
    ADD COLUMN IF NOT EXISTS cursor_event_id BIGINT;
