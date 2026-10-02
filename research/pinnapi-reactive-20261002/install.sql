-- Incorporate into the next unused numbered migration on the release branch.
-- Apply before enabling PINNAPI_REACTIVE_PAPER. No account/control changes.
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
-- STARTED rows left by a process crash remain visible; never auto-replay them.
