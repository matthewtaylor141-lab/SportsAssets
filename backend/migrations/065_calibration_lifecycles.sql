-- 065: MICRO_EXECUTION_CALIBRATION LEDGER (management directive
-- 2026-09-18 section A; sprint override 2026-09-18). One supervised
-- lifecycle at a time, $5 all-in per lifecycle, $100 cumulative spend
-- for the session. Until now the limits existed only as a Python
-- module: `calibration.py` computed them correctly against a dict that
-- lived for the length of one request. That is not a budget. An API
-- restart would have shown `spent = 0` and a free concurrency slot
-- while a real order was still resting at the venue, so a budget held
-- in process memory is exactly the shape of accounting defect the
-- limits exist to prevent.
--
-- TWO TABLES, BOTH APPEND-SHAPED.
--
-- calibration_sessions is the $100 ceiling. `spent_usd` is CUMULATIVE
-- CASH OUT and is written only by increment -- sale proceeds never
-- reduce it, which is what "not reusable buying power" means. There is
-- no column for proceeds here on purpose: a column that could be
-- subtracted would eventually be subtracted.
--
-- calibration_lifecycles is one row per approved ticket. `reserve_usd`
-- is the worst-case cash the ticket can still cost and is held until
-- BOTH `venue_terminal_state` is set from the venue's own read AND
-- `fills_reconciled` is true. A cancel acknowledgement satisfies
-- neither: the venue may have filled the order before it saw the
-- cancel (book 1333, book 863), so the reserve stands.
--
-- THE CONCURRENCY LIMIT IS A DATABASE CONSTRAINT, NOT A CHECK IN CODE.
-- calibration_one_open_lifecycle is a partial UNIQUE index on the open
-- states: a second concurrent lifecycle cannot be inserted even by a
-- second API instance, a retried request, or a race between the
-- preflight read and the insert. A limit enforced only by reading
-- first and writing second is not a limit.
--
-- `client_order_id` is UNIQUE so a retried approval cannot create two
-- tickets for one intent, and so a lost venue response has a durable
-- name to reconcile against.
--
-- NOTHING HERE PLACES AN ORDER. These tables are written by the
-- calibration preflight and the operator's approval path; the venue
-- adapter is untouched.

CREATE TABLE IF NOT EXISTS calibration_sessions (
    session_id     TEXT PRIMARY KEY,
    experiment     TEXT NOT NULL DEFAULT 'MICRO_EXECUTION_CALIBRATION',
    authorised_by  TEXT NOT NULL,
    max_all_in_usd NUMERIC(12, 2) NOT NULL,
    max_spend_usd  NUMERIC(12, 2) NOT NULL,
    max_open       INTEGER NOT NULL,
    spent_usd      NUMERIC(12, 2) NOT NULL DEFAULT 0,
    stopped        BOOLEAN NOT NULL DEFAULT false,   -- the operator stop
    stopped_at     TIMESTAMPTZ,
    stopped_by     TEXT,
    stop_reason    TEXT,
    opened_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT calibration_spent_never_negative CHECK (spent_usd >= 0)
);

CREATE TABLE IF NOT EXISTS calibration_lifecycles (
    id              BIGSERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES calibration_sessions (session_id),
    client_order_id TEXT NOT NULL UNIQUE,
    venue           TEXT NOT NULL,
    account         TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side = 'BUY'),  -- long only, fully funded
    order_type      TEXT NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    entry_fee_usd   NUMERIC(12, 2) NOT NULL,
    exit_fee_usd    NUMERIC(12, 2) NOT NULL,
    all_in_usd      NUMERIC(12, 2) NOT NULL,
    reserve_usd     NUMERIC(12, 2) NOT NULL,
    spent_usd       NUMERIC(12, 2) NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'APPROVED'
                    CHECK (state IN ('APPROVED', 'SUBMITTED', 'EXIT_CONSIDERED',
                                     'EXIT_SUBMITTED', 'EXIT_FILLED',
                                     'RECONCILED')),
    venue_order_id  TEXT,
    venue_terminal_state TEXT,
    fills_reconciled BOOLEAN NOT NULL DEFAULT false,
    pre_open_order_ids JSONB,   -- open ids on the market BEFORE the send:
                                -- a lost response is matched only against
                                -- orders that were not already there
    inventory_plan  TEXT NOT NULL,
    approved_by     TEXT NOT NULL,
    ticket          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reconciled_at   TIMESTAMPTZ
);

-- ONE OPEN LIFECYCLE, ENFORCED BY THE DATABASE. EXIT_FILLED counts as
-- open: a filled exit is not a reconciled position, and a late fill
-- lands exactly there.
CREATE UNIQUE INDEX IF NOT EXISTS calibration_one_open_lifecycle
    ON calibration_lifecycles ((1))
 WHERE state IN ('APPROVED', 'SUBMITTED', 'EXIT_CONSIDERED',
                 'EXIT_SUBMITTED', 'EXIT_FILLED');

CREATE INDEX IF NOT EXISTS calibration_lifecycles_session_idx
    ON calibration_lifecycles (session_id, created_at DESC);
