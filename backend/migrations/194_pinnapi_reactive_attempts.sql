-- 194 · PINNAPI REACTIVE PAPER ATTEMPTS (audit of WebSocket-triggered paper
-- evaluations; research/pinnapi-reactive-20261002/install.sql)
--
-- One row per attempt, written STARTED before the evaluation begins and then
-- COMPLETED / REFUSED / TIMEOUT / ERROR / CANCELLED with its trigger, queue,
-- start and finish clocks, the feed version, counters and the valuation IDs
-- it produced. The worker refuses to evaluate when the STARTED row cannot be
-- written. STARTED rows left by a crash stay visible and are never replayed.
-- Applies before PINNAPI_REACTIVE_PAPER can be switched on; no account,
-- control, order or funded table is touched.
CREATE TABLE IF NOT EXISTS pinnapi_reactive_attempts (
    attempt_id text PRIMARY KEY,
    event_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    state text NOT NULL CHECK (state IN
        ('STARTED','COMPLETED','REFUSED','TIMEOUT','ERROR','CANCELLED')),
    detail jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS pinnapi_reactive_attempts_created
    ON pinnapi_reactive_attempts (created_at DESC);
