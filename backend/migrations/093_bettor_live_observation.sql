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

CREATE TABLE IF NOT EXISTS bettor_live_journal (
    id            BIGSERIAL   PRIMARY KEY,
    lane          TEXT        NOT NULL,
    written_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    loop_version  TEXT        NOT NULL,
    kind          TEXT        NOT NULL,
    market_id     TEXT,
    source_ts     TEXT,
    decided_at    TEXT,
    status        TEXT,
    selected      TEXT,
    record        JSONB       NOT NULL
);

CREATE INDEX IF NOT EXISTS bettor_live_journal_lane_id_idx
    ON bettor_live_journal (lane, id DESC);

CREATE TABLE IF NOT EXISTS bettor_live_cursor (
    lane            TEXT        NOT NULL,
    market_id       TEXT        NOT NULL,
    first_seen_at   DOUBLE PRECISION,
    last_seen_at    DOUBLE PRECISION,
    last_source_ts  TEXT,
    last_decided_at TEXT,
    settle_status   TEXT,
    settle_attempts INTEGER     NOT NULL DEFAULT 0,
    settle_next_at  DOUBLE PRECISION,
    settle_last_at  DOUBLE PRECISION,
    PRIMARY KEY (lane, market_id)
);

CREATE INDEX IF NOT EXISTS bettor_live_cursor_seen_idx
    ON bettor_live_cursor (lane, last_seen_at);

CREATE TABLE IF NOT EXISTS bettor_live_ledger (
    lane          TEXT        PRIMARY KEY,
    saved_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    loop_version  TEXT        NOT NULL,
    schema_version INTEGER    NOT NULL,
    snapshot      TEXT        NOT NULL
);
