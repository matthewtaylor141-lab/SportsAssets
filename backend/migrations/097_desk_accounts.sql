-- SHADOW ACCOUNTS. A durable book identity, separate from a boot id.
--
-- WHY THE TWO IDS ARE DIFFERENT, and why conflating them was the bug.
--
--   account_id  the BOOK. Survives restarts. A restart resumes the
--               same account; only an explicit, once-ever creation
--               makes a new one.
--   epoch_id    the PROCESS. New on every start, useful for tracing a
--               write to the process that made it, and meaningless as
--               an accounting boundary.
--
-- Until now there was no account id at all, and `boot_id` was written
-- as str(int(time.time())) per ROW -- so it identified neither. The
-- loop restarted several times without restoring its book, leaving
-- `bettor_desk_positions` holding legs from several abandoned books
-- while `bettor_desk_state.cash` reflected only the last. When the
-- restore arrived it adopted all of them against that one cash figure
-- and the identity drifted +$2,367.73 on 96 legs where the running
-- book had 52.
--
-- NOTHING IS DELETED AND NOTHING IS COMPENSATED. The old rows stay
-- exactly as written, carry account_id IS NULL, and are excluded from
-- every live read by that fact alone. The discrepancy is recorded, not
-- absorbed; the inventory of that period is UNATTRIBUTABLE, which is
-- not the same as closed.

CREATE TABLE IF NOT EXISTS bettor_desk_accounts (
    account_id       TEXT PRIMARY KEY,
    desk_id          TEXT NOT NULL,
    -- ACTIVE                  the one book now trading
    -- CLOSED_UNATTRIBUTABLE   preserved, never reopened, not summed
    status           TEXT NOT NULL,
    opening_balance  NUMERIC NOT NULL,
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ,
    note             TEXT NOT NULL,
    provenance       JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- EXACTLY ONE ACTIVE ACCOUNT PER DESK, enforced by the database rather
-- than by the caller remembering. This is also what makes "create the
-- new account exactly once" true under concurrent starts: the second
-- insert fails.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_desk_accounts_one_active_idx
    ON bettor_desk_accounts (desk_id) WHERE status = 'ACTIVE';

-- THE RUNNING STATE, KEYED ON THE ACCOUNT. The old
-- `bettor_desk_state` is keyed on desk_id and is left untouched as
-- part of the preserved record; upserting a new account's cash into it
-- would have overwritten the closed account's final figure.
--
-- CASH AND CURSOR MOVE TOGETHER IN ONE ROW so that one UPDATE inside
-- the persist transaction commits both. A cursor that advanced without
-- its cash, or the reverse, is the interrupted-write failure.
CREATE TABLE IF NOT EXISTS bettor_desk_account_state (
    account_id        TEXT PRIMARY KEY
                          REFERENCES bettor_desk_accounts (account_id),
    desk_id           TEXT NOT NULL,
    cursor_event_id   BIGINT,
    cash_usd          NUMERIC NOT NULL,
    starting_cash_usd NUMERIC NOT NULL,
    realized_pnl_usd  NUMERIC NOT NULL DEFAULT 0,
    fees_usd          NUMERIC NOT NULL DEFAULT 0,
    epoch_id          TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- THE ACCOUNT ON EVERY RECORD. NULL means "written before accounts
-- existed" -- unassigned, and excluded from every live read. It is
-- never back-filled: assigning those rows to a book would be inventing
-- the very attribution that does not exist.
ALTER TABLE bettor_desk_decisions ADD COLUMN IF NOT EXISTS account_id TEXT;
ALTER TABLE bettor_desk_orders    ADD COLUMN IF NOT EXISTS account_id TEXT;
ALTER TABLE bettor_desk_fills     ADD COLUMN IF NOT EXISTS account_id TEXT;
ALTER TABLE bettor_desk_positions ADD COLUMN IF NOT EXISTS account_id TEXT;
ALTER TABLE bettor_desk_ledger    ADD COLUMN IF NOT EXISTS account_id TEXT;
-- The consumption ledger too. It was never persisted at all, which is
-- why resting orders had to be expired on every restart rather than
-- resumed: without it, re-arming an order could allocate evidence the
-- previous process had already spent.
ALTER TABLE bettor_desk_consumption
    ADD COLUMN IF NOT EXISTS account_id TEXT;
-- The evidence id alone was the primary key, which would have made one
-- print unusable by a second account. Scope it.
ALTER TABLE bettor_desk_consumption
    DROP CONSTRAINT IF EXISTS bettor_desk_consumption_pkey;
ALTER TABLE bettor_desk_consumption
    ADD PRIMARY KEY (evidence_id, account_id);

CREATE INDEX IF NOT EXISTS bettor_desk_positions_account_idx
    ON bettor_desk_positions (account_id) WHERE account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS bettor_desk_orders_account_idx
    ON bettor_desk_orders (account_id, state);
CREATE INDEX IF NOT EXISTS bettor_desk_ledger_account_idx
    ON bettor_desk_ledger (account_id, at DESC);
CREATE INDEX IF NOT EXISTS bettor_desk_decisions_account_idx
    ON bettor_desk_decisions (account_id, decided_at DESC);

-- THE INCIDENT RECORD. One row, written once, carrying the measured
-- discrepancy and the counts of what is being preserved. It is what
-- the command centre shows beside the new account, and it is why the
-- previously reported P&L for that period is marked unreliable rather
-- than restated.
CREATE TABLE IF NOT EXISTS bettor_desk_incidents (
    incident_id      TEXT PRIMARY KEY,
    desk_id          TEXT NOT NULL,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind             TEXT NOT NULL,
    summary          TEXT NOT NULL,
    measured         JSONB NOT NULL DEFAULT '{}'::jsonb,
    preserved_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    pnl_reliability  TEXT NOT NULL,
    detail           JSONB NOT NULL DEFAULT '{}'::jsonb
);
