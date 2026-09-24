-- THE CORRECTION RECORD. Originals are never altered.
--
-- WHY A SEPARATE TABLE RATHER THAN AN UPDATE. A correction that
-- overwrites the number it corrects destroys the evidence that a
-- correction was needed. Management asked for "an auditable correction
-- version showing changes to cash, basis, realized P&L and current
-- inventory", and an UPDATE cannot show a change -- only the new value.
-- So `bettor_desk_ledger`, `bettor_desk_orders` and
-- `bettor_desk_positions` are left exactly as written, and the
-- corrected view is the original plus the rows below.
--
-- WHAT THIS CORRECTION CAN AND CANNOT DO, recorded in the schema
-- because it is a property of the data rather than of the code:
--
--   `bettor_desk_fills` IS EMPTY. The loop never wrote it. Migration
--   094 created it, `_persist` never inserted into it, and the per-fill
--   quantity, price, liquidity role and timestamp therefore do not
--   exist for any fill the live lane has ever simulated.
--
--   PMUS rounds PER FILL (`PER_FILL_INDEPENDENT`), so a fee recomputed
--   from an order's average price is NOT the sum of its fills' fees.
--   The difference cannot be recovered without the fill count, which
--   was also never written.
--
-- Hence `status`: a correction is EXACT only when the per-fill record
-- exists. Otherwise it is BOUNDED and the accounting is INCOMPLETE,
-- which is what `bettor_desk_correction_runs.incomplete_reasons`
-- carries. Nothing here invents a missing field.

-- ONE ROW PER CORRECTED SUBJECT, and the primary key is what prevents
-- a duplicate correction. Re-running the correction is an
-- ON CONFLICT DO NOTHING no-op rather than a second adjustment applied
-- on top of the first.
CREATE TABLE IF NOT EXISTS bettor_desk_corrections (
    correction_id     TEXT PRIMARY KEY,   -- version ':' subject_kind ':' subject_id
    run_id            TEXT NOT NULL,
    version           TEXT NOT NULL,
    desk_id           TEXT NOT NULL,
    subject_kind      TEXT NOT NULL,      -- ORDER | LEG | LEDGER_EPOCH
    subject_id        TEXT NOT NULL,
    -- EXACT      every input field was present
    -- BOUNDED    computable only as an interval; see the two bounds
    -- INCOMPLETE a required field is missing and nothing is asserted
    status            TEXT NOT NULL,
    reason            TEXT NOT NULL,
    schedule_id       TEXT,               -- NULL when nothing was applied
    -- the ORIGINAL values, copied so the correction row is readable
    -- without joining back to a table that may itself be superseded
    original_fees_usd    NUMERIC,
    original_realized_usd NUMERIC,
    original_cash_usd     NUMERIC,
    original_basis_usd    NUMERIC,
    -- the correction, as an INTERVAL. lower == upper when EXACT.
    delta_fees_lower_usd     NUMERIC,
    delta_fees_upper_usd     NUMERIC,
    delta_realized_lower_usd NUMERIC,
    delta_realized_upper_usd NUMERIC,
    delta_cash_lower_usd     NUMERIC,
    delta_cash_upper_usd     NUMERIC,
    delta_basis_usd          NUMERIC,     -- fees never enter basis here
    detail            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bettor_desk_corrections_run_idx
    ON bettor_desk_corrections (run_id);
CREATE INDEX IF NOT EXISTS bettor_desk_corrections_subject_idx
    ON bettor_desk_corrections (desk_id, subject_kind, subject_id);

-- THE RUN, so a published correction can be traced to the code and the
-- moment that produced it, and so a second run is visibly a second run.
CREATE TABLE IF NOT EXISTS bettor_desk_correction_runs (
    run_id            TEXT PRIMARY KEY,
    version           TEXT NOT NULL,
    desk_id           TEXT NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    code_version      TEXT,
    subjects_seen     INTEGER NOT NULL DEFAULT 0,
    subjects_exact    INTEGER NOT NULL DEFAULT 0,
    subjects_bounded  INTEGER NOT NULL DEFAULT 0,
    subjects_incomplete INTEGER NOT NULL DEFAULT 0,
    subjects_written  INTEGER NOT NULL DEFAULT 0,   -- 0 on a repeat run
    accounting_status TEXT NOT NULL,                -- COMPLETE | INCOMPLETE
    incomplete_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    totals            JSONB NOT NULL DEFAULT '{}'::jsonb,
    reconciles        BOOLEAN,
    reconcile_detail  JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ONE VERSION IS APPLIED AT MOST ONCE PER DESK. This is the second and
-- stronger guard against a duplicate correction: even a caller that
-- generated fresh correction_ids cannot apply the same version twice.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_desk_correction_runs_version_idx
    ON bettor_desk_correction_runs (desk_id, version);

-- THE BOOK'S EPOCHS. Until now `boot_id` was written as
-- str(int(time.time())) on EVERY row, so it identified a write and not
-- a boot, and restarts were therefore not identifiable from the
-- record. A real epoch is written once per process start.
--
-- THIS MATTERS FOR ACCOUNTING, not just for tidiness. The loop
-- constructed a fresh Desk on every start and never restored cash,
-- legs or orders from Postgres, so each epoch's book began again at
-- starting_cash with no positions. A realized figure spanning two
-- epochs is therefore not a running total, and this table is what
-- makes that visible rather than inferred.
CREATE TABLE IF NOT EXISTS bettor_desk_epochs (
    epoch_id        TEXT PRIMARY KEY,
    desk_id         TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    starting_cash   NUMERIC NOT NULL,
    restored        BOOLEAN NOT NULL DEFAULT FALSE,
    restore_detail  JSONB NOT NULL DEFAULT '{}'::jsonb,
    cursor_at_start BIGINT,
    code_version    TEXT
);
CREATE INDEX IF NOT EXISTS bettor_desk_epochs_desk_idx
    ON bettor_desk_epochs (desk_id, started_at DESC);

-- Ledger rows gain the epoch so a reader can see where one book ends
-- and the next begins. Rows written before this column existed keep
-- NULL, which reads as UNSEGMENTED and must not be back-filled by
-- guessing.
ALTER TABLE bettor_desk_ledger
    ADD COLUMN IF NOT EXISTS epoch_id TEXT;
