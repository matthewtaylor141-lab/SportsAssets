-- Kalshi underdog leg, coverage rebuild (owner 2026-08-09: "this should
-- be firing every single mlb game and every single tennis match").
--
-- The queue previously only learned games the PMUS sleeve managed to
-- pick — a game that failed the PMUS band, missed its five-minute
-- window, or had no start time never reached Kalshi at all. Now the
-- worker enqueues EVERY catalogued game at discovery with its venue
-- start time, and the engine runs the T-minus-5 window itself: tasks
-- wait in the queue until their window opens, retry inside it, and only
-- go terminal after it closes.
ALTER TABLE kud_queue ADD COLUMN IF NOT EXISTS start_ts timestamptz;

CREATE INDEX IF NOT EXISTS kud_queue_start_idx
    ON kud_queue (status, start_ts);
