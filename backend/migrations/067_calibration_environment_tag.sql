-- THE LEDGER LEARNS WHICH VENUE, WHICH ENVIRONMENT AND WHICH ACCOUNT.
--
-- WHAT THIS FIXES. `calibration_lifecycles` already carries `venue` and
-- `account`. `calibration_sessions` carries NEITHER, and nothing anywhere
-- carries the ENVIRONMENT. So the budget -- the $100 cumulative ceiling,
-- the spend a restart reads back, the figure COMMAND prints -- is a single
-- untagged number that a preproduction lifecycle can move.
--
-- That is not a reporting nicety. Preproduction money is not money. A
-- preprod fill that books $3 of "spend" would consume three per cent of a
-- real allowance; a preprod exit that books cash would be summed into the
-- session total a human reads as production performance. The institutional
-- preproduction exchange is TEST-FUNDED (buyingPower "1000000" on
-- firms/.../accounts/... as read on 2026-09-10), so a single blended
-- number is not merely imprecise, it is wrong by construction.
--
-- THE SEPARATION IS STRUCTURAL, NOT ARITHMETIC. A session is BOUND to one
-- (venue, environment) pair. A ticket that names a different pair is
-- refused by name in `calibration_store.reserve`, so preprod activity
-- cannot reach a production session's row at all. There is no sum to get
-- right, because there is no shared total.
--
-- WHAT IS NOT CHANGED, DELIBERATELY:
--
--   * `calibration_one_open_lifecycle` is indexed ON ((1)) -- a constant.
--     It is already GLOBAL across every session, so a preprod lifecycle
--     and a production lifecycle cannot be open at the same time. The
--     approved "one unresolved lifecycle" boundary therefore holds ACROSS
--     environments without touching the index, and adding a preprod
--     session cannot widen it.
--   * `client_order_id` stays globally UNIQUE, so the one-attempt-per-
--     approval guarantee in migration 066 is unaffected.
--
-- THE BACKFILL IS THE TRUTH, NOT A GUESS. Every row that exists today was
-- written by the retail path against production: `calibration_evidence`
-- and `calibration_read` reach the venue through `sportsassets.pmus`,
-- which has no preproduction host. PRODUCTION is therefore the correct
-- value for existing rows, and it is what the DEFAULT writes.
--
-- Additive only. No row is deleted and no column is dropped or retyped.

-- ---------------------------------------------------------------- sessions

ALTER TABLE calibration_sessions
    ADD COLUMN IF NOT EXISTS venue TEXT NOT NULL DEFAULT 'polymarket-us';

ALTER TABLE calibration_sessions
    ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'PRODUCTION';

DO $$
BEGIN
    ALTER TABLE calibration_sessions
        ADD CONSTRAINT calibration_sessions_environment_known
        CHECK (environment IN ('PRODUCTION', 'PREPROD'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- -------------------------------------------------------------- lifecycles

ALTER TABLE calibration_lifecycles
    ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'PRODUCTION';

DO $$
BEGIN
    ALTER TABLE calibration_lifecycles
        ADD CONSTRAINT calibration_lifecycles_environment_known
        CHECK (environment IN ('PRODUCTION', 'PREPROD'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- The reporting read COMMAND makes: every lifecycle of one environment on
-- one venue, newest first. Without it "production performance" is a scan
-- that a preprod row can only be kept out of by remembering to filter.
CREATE INDEX IF NOT EXISTS calibration_lifecycles_env_idx
    ON calibration_lifecycles (environment, venue, created_at DESC);

-- ----------------------------------------------------------- send attempts
--
-- The attempt inherits the lifecycle's environment rather than deriving it:
-- a reconciliation that reads the attempts table alone must be able to say
-- which exchange an ambiguous send was made against WITHOUT a join, because
-- the join is exactly what is unavailable when the question is urgent.

ALTER TABLE calibration_send_attempts
    ADD COLUMN IF NOT EXISTS environment TEXT NOT NULL DEFAULT 'PRODUCTION';

ALTER TABLE calibration_send_attempts
    ADD COLUMN IF NOT EXISTS venue TEXT NOT NULL DEFAULT 'polymarket-us';

DO $$
BEGIN
    ALTER TABLE calibration_send_attempts
        ADD CONSTRAINT calibration_send_attempts_environment_known
        CHECK (environment IN ('PRODUCTION', 'PREPROD'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- THE VENUE'S OWN CORRELATION IDENTIFIER, recorded where the ambiguity
-- lives. Migration 066 was written for a venue with NO client-supplied
-- identifier (the retail `CreateOrderParams` has no such field), so a lost
-- response could only be reconciled against the PRE-IMAGE of resting order
-- ids. The institutional gateway DOES accept one: the REST order schema
-- carries `clordId` and the order rows echo it back. Where the venue
-- supports native correlation, the pre-image stops being the only evidence
-- and becomes the corroboration.
--
-- It is NULLABLE on purpose: on the retail venue there is no such
-- identifier, and writing the internal client_order_id here would assert a
-- correlation the venue never saw. NULL means "this venue offers none",
-- which is a different fact from "we failed to record one".
ALTER TABLE calibration_send_attempts
    ADD COLUMN IF NOT EXISTS venue_clord_id TEXT;

CREATE INDEX IF NOT EXISTS calibration_attempts_clord_idx
    ON calibration_send_attempts (venue_clord_id)
 WHERE venue_clord_id IS NOT NULL;
