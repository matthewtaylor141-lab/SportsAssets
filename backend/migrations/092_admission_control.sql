-- CAPACITY-AWARE ADMISSION: the counters that make it auditable.
--
-- Admitting an observation creates one follow-up task per horizon.
-- W3 measured intake creating 5.85 eligible tasks/min against 2.03
-- completed, with 4.02/min expiring unread -- 65% of the work the
-- collector made for itself was discarded. Admission control throttles
-- intake to what the follow-up reserve can actually service.
--
-- THESE COLUMNS EXIST SO THE THROTTLE CANNOT HIDE. Without them a
-- capped tick is indistinguishable from a tick that found no work, and
-- "we admitted less" is indistinguishable from "we collected less".
--
--   obs_skipped_admission  offered by the rotation, declined by the cap.
--                          NOT a loss: the market stays in the rotation.
--                          Counted apart from obs_skipped_budget, which
--                          IS a loss.
--   backlog_tasks          outstanding eligible follow-up tasks at the
--                          moment the tick decided. A LEVEL, not a sum.
--   admit_cap              how many new observations this tick allowed.
--   admission_saturated    true when the queue was too deep to admit
--                          anything and the whole budget went to drain.
--   admission_version      which admission rule produced the row.
BEGIN;
ALTER TABLE bettor_capture_ticks
    ADD COLUMN IF NOT EXISTS obs_skipped_admission INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bettor_capture_ticks
    ADD COLUMN IF NOT EXISTS backlog_tasks INTEGER;
ALTER TABLE bettor_capture_ticks
    ADD COLUMN IF NOT EXISTS admit_cap INTEGER;
ALTER TABLE bettor_capture_ticks
    ADD COLUMN IF NOT EXISTS admission_saturated BOOLEAN;
ALTER TABLE bettor_capture_ticks
    ADD COLUMN IF NOT EXISTS admission_version TEXT;
COMMIT;
