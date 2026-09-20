-- FU_ROTATION_HEAD: which horizon led the follow-up order on this tick.
--
-- WITHOUT IT THE ROTATION CANNOT BE VERIFIED IN PRODUCTION, only
-- inferred. Per-horizon attempt counts can look even for the wrong
-- reason -- one horizon leading every tick while the others pick up
-- its leftovers gives a similar-looking spread -- and the defect this
-- column exists to catch is precisely a rotation that never reaches
-- some horizons. That defect was found offline, where the head is
-- observable; in production it was not observable at all.
--
-- NULL means the tick had no follow-up budget, so no horizon led. That
-- is a distinct state from "60s led" and must not collapse into it.
BEGIN;
ALTER TABLE bettor_capture_ticks
    ADD COLUMN IF NOT EXISTS fu_rotation_head INTEGER;
ALTER TABLE bettor_capture_ticks
    ADD COLUMN IF NOT EXISTS fu_per_horizon_cap INTEGER;
COMMIT;
