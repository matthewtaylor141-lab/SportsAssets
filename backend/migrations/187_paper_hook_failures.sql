-- 187: IN-CYCLE PAPER DECISIONS THAT DID NOT HAPPEN, RECORDED.
--
-- The per-valuation hook decides each strategy on a just-written valuation.
-- When one of those decisions raised, timed out or deferred, the hook
-- returned the failure to the collector's log line and nothing else: the
-- valuation then waited for the scheduled backstop pass, which found its
-- Pinnacle probability stale (valuation 2102, 2026-10-01: ~55 s of decision
-- delay, 83 s of probability age). A missed decision must not disappear
-- silently, so every one is a row here: which valuation, which strategy,
-- which stage, how long it ran, and the error. Append-only; paper only.
CREATE TABLE IF NOT EXISTS paper_hook_failures (
    failure_id      bigserial PRIMARY KEY,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    session_id      text,
    account_id      text,
    valuation_id    bigint      NOT NULL,
    strategy        text        NOT NULL,
    stage           text        NOT NULL,
    outcome         text        NOT NULL,
    elapsed_s       double precision,
    error           text,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT paper_hook_failures_outcome_ck CHECK (
        outcome IN ('ERROR', 'TIMEOUT', 'DEFERRED'))
);
CREATE INDEX IF NOT EXISTS paper_hook_failures_valuation_idx
    ON paper_hook_failures (valuation_id);
CREATE INDEX IF NOT EXISTS paper_hook_failures_recorded_idx
    ON paper_hook_failures (recorded_at DESC);
