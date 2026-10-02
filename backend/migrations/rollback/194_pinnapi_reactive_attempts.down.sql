-- Rollback of 194. The audit is evidence: export it before dropping, and set
-- PINNAPI_REACTIVE_PAPER=off first (the worker refuses without its table).
DROP TABLE IF EXISTS pinnapi_reactive_attempts;
