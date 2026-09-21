-- 093: durable storage for the decision-only observation worker.
--
-- THE SAME TEXT AS `bettor_live_store.DDL`, character for character.
-- `tests/test_bettor_live_store.py` compares them, because the worker
-- creates these tables itself (the workers never run migrations --
-- start.sh applies them and that is the API's entrypoint) and a
-- migration that drifted from the code would describe a schema that
-- production does not have.
--
-- Additive and idempotent. No existing table is read, altered or
-- dropped by this file. Nothing here touches an accounting record.
--
-- LANE IS PART OF EVERY KEY. Retail and institutional, preproduction
-- and production, must never blend; keying by lane makes blending a
-- constraint violation rather than a matter of remembering a WHERE.
--
-- UNIQUE (lane, record_key) IS THE RETRY GUARD. A commit can succeed
-- and its acknowledgment be lost; the retry that follows must be a
-- no-op rather than a second copy of every record, or an observation
-- count silently inflates and every rate derived from it with it.
--
-- settle_failures IS SEPARATE FROM settle_attempts, and
-- settle_derived_outcome IS SEPARATE FROM settle_outcome. A market
-- that is legitimately open is not a failing read, and an inference
-- from converged prices is not the venue's own answer.
--
-- THE ADVISORY LOCK BELOW IS NOT DECORATION. `CREATE TABLE IF NOT
-- EXISTS` checks the catalog before taking its lock, so concurrent
-- creators race: measured, twelve sessions running this DDL at once
-- gave 2 successes and 10 failures (DuplicateTableError,
-- UniqueViolationError on pg_type_typname_nsp_index,
-- DuplicateObjectError). The API's migration runner applies this file
-- while the worker calls bettor_live_store.PgStore.start() in the SAME
-- DEPLOYMENT, and both take THIS key. The runner already wraps each
-- migration in a transaction, so the lock is released with it.

SELECT pg_advisory_xact_lock(930930093);

CREATE TABLE IF NOT EXISTS bettor_live_journal (
    id            BIGSERIAL   PRIMARY KEY,
    lane          TEXT        NOT NULL,
    boot_id       TEXT,
    record_key    TEXT        NOT NULL,
    written_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    loop_version  TEXT        NOT NULL,
    kind          TEXT        NOT NULL,
    market_id     TEXT,
    source_ts     TEXT,
    decided_at    TEXT,
    status        TEXT,
    selected      TEXT,
    record        JSONB       NOT NULL,
    UNIQUE (lane, record_key)
);

CREATE INDEX IF NOT EXISTS bettor_live_journal_lane_id_idx
    ON bettor_live_journal (lane, id DESC);

CREATE TABLE IF NOT EXISTS bettor_live_cursor (
    lane                   TEXT        NOT NULL,
    market_id              TEXT        NOT NULL,
    first_seen_at          DOUBLE PRECISION,
    last_seen_at           DOUBLE PRECISION,
    last_source_ts         TEXT,
    last_decided_at        TEXT,
    event_at               DOUBLE PRECISION,
    settle_status          TEXT,
    settle_attempts        INTEGER     NOT NULL DEFAULT 0,
    settle_failures        INTEGER     NOT NULL DEFAULT 0,
    settle_next_at         DOUBLE PRECISION,
    settle_last_at         DOUBLE PRECISION,
    settle_derived_at      DOUBLE PRECISION,
    settle_derived_outcome TEXT,
    settle_outcome         TEXT,
    PRIMARY KEY (lane, market_id)
);

CREATE INDEX IF NOT EXISTS bettor_live_cursor_seen_idx
    ON bettor_live_cursor (lane, last_seen_at);

CREATE INDEX IF NOT EXISTS bettor_live_cursor_due_idx
    ON bettor_live_cursor (lane, settle_next_at)
    WHERE settle_status IS DISTINCT FROM 'RESOLVED';

CREATE TABLE IF NOT EXISTS bettor_live_ledger (
    lane          TEXT        PRIMARY KEY,
    saved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    boot_id       TEXT,
    loop_version  TEXT        NOT NULL,
    schema_version INTEGER    NOT NULL,
    snapshot      TEXT        NOT NULL
);
