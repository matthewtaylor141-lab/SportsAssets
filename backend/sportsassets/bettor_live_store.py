"""DURABLE STORAGE FOR THE DECISION-ONLY OBSERVATION WORKER.

WHY THIS MODULE EXISTS. The worker's previous default wrote its journal
to `/var/tmp/bettor`. Neither `fsync()` nor `os.replace()` makes that
durable: `/var/tmp` on a Render service lives in the container's
writable layer, and a REDEPLOY replaces the container. The evidence
survived a restart and died on every deploy -- which is precisely the
event most likely to follow an observation run worth keeping.

WHAT DURABLE MEANS HERE, EXACTLY:

    process restart   the container is the same     -> file survives
    service restart   the container is recreated    -> file DIES
    redeploy          a new image, new container    -> file DIES
    Postgres          a separate managed instance   -> survives all three

So the default backend is POSTGRES, on `sportsassets-db`, which the
target service already holds a connection string for. No service
change, no new credential, no new dependency.

THREE TABLES, ALL NEW, ALL OURS.

    bettor_live_journal   append-only decision and settlement records
    bettor_live_cursor    one row per market: dedup position AND the
                          settlement schedule
    bettor_live_ledger    one row per lane: the shadow ledger snapshot

NOTHING ELSE IS WRITTEN. Not `ai_trades`, not `bettor_state_*`, not
`copy_*` -- no accounting record, legacy or otherwise, is touched, and
a test asserts that over the SQL text rather than over behaviour.

LANE IS A PRIMARY-KEY COMPONENT, not a column we remember to filter on.
Retail and institutional, preproduction and production, must never
blend; keying by lane makes blending a constraint violation rather than
a code review.

THE WORKER CREATES ITS OWN SCHEMA, because the workers never run
migrations -- `start.sh` applies them and that is the API's entrypoint.
An isolated worker release that waited for an API deploy would not be
isolated. `093_bettor_live_observation.sql` carries the same DDL for
the canonical record, and a test asserts the two texts are identical so
they cannot drift.

WHAT IS BOUNDED, AND BY WHAT:

    journal rows      JOURNAL_MAX_ROWS, pruned oldest-first on a clock
    cursor rows       CURSOR_RETENTION_DAYS, plus an in-memory cap with
                      terminal-first eviction
    recovery time     O(cursors), NOT O(journal). Recovery reads the
                      cursor table and one ledger row; it never replays
                      the journal, so a month of records costs nothing
                      to restart against.
    write loss        one flush interval. Records are batched, so a
                      SIGKILL between flushes loses at most
                      FLUSH_EVERY_S of decisions. Cancellation -- which
                      is how a redeploy arrives -- flushes in a finally.
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger(__name__)

STORE_VERSION = "BETTOR_LIVE_STORE_V1"
SCHEMA_VERSION = 1

# The lane this worker writes under. Not configurable: a worker that
# could be pointed at another lane's rows is a blending hazard.
LANE = "polymarket-us/institutional/decision-only"

BACKEND_PG = "postgres"
BACKEND_FILE = "file"
BACKEND_MEMORY = "memory"

# Bounds. Every one of these is a number the operator can see.
JOURNAL_MAX_ROWS = 250_000
CURSOR_RETENTION_DAYS = 7
CURSOR_MAX_IN_MEMORY = 5_000
FLUSH_EVERY_S = 2.0
PRUNE_EVERY_S = 300.0
OUTBOX_MAX = 20_000

# ── settlement scheduling policy ─────────────────────────────────────
#
# The previous scheduler sorted by first-observed time and took the
# oldest 25. A contract that stays PENDING stays oldest, so a hundred
# never-resolving contracts hold the whole batch forever and a market
# that settled an hour ago is never read. That is not a slow scheduler;
# it is a scheduler that cannot make progress.
#
# The fix is per-contract state: attempts, a next-attempt time, and a
# terminal status. Never-attempted contracts sort first, then contracts
# whose next-attempt time has passed, oldest first. A contract that
# answers PENDING backs off, so it leaves the queue head for its own
# backoff and cannot hold it.

SETTLE_NEW = None
SETTLE_PENDING = "PENDING"
SETTLE_UNREADABLE = "UNREADABLE"
SETTLE_UNMATCHED = "UNMATCHED"
SETTLE_RESOLVED = "RESOLVED"
SETTLE_RESOLVED_DERIVED = "RESOLVED_DERIVED"
SETTLE_ABANDONED = "ABANDONED"

# AUTHORITATIVE RESOLUTIONS ARE RETIRED. A settled market's price does
# not change, so reading it again spends a REST call on a fact we hold.
TERMINAL_STATUSES = (SETTLE_RESOLVED, SETTLE_RESOLVED_DERIVED,
                     SETTLE_ABANDONED)
RETRYABLE_STATUSES = (SETTLE_PENDING, SETTLE_UNREADABLE, SETTLE_UNMATCHED)

SETTLE_BASE_S = 600.0        # first retry after ten minutes
SETTLE_MAX_S = 21_600.0      # and never further apart than six hours
SETTLE_MAX_ATTEMPTS = 12     # ~ two days of backoff, then ABANDONED


def settle_backoff_s(attempts: int) -> float:
    """Exponential, capped. Deterministic: no jitter, because two runs
    of the same scheduler must be comparable."""
    n = max(0, int(attempts))
    if n >= 40:                       # 2**40 overflows nothing but time
        return SETTLE_MAX_S
    return min(SETTLE_BASE_S * (2 ** n), SETTLE_MAX_S)


def settle_transition(status: str | None, attempts: int, *,
                      now: float) -> dict:
    """One settlement read's outcome -> the contract's next schedule.

    Returns the fields to store. A terminal status carries no next
    attempt at all, which is what retiring means.
    """
    attempts = max(0, int(attempts)) + 1
    if status in (SETTLE_RESOLVED, SETTLE_RESOLVED_DERIVED):
        return {"settle_status": status, "settle_attempts": attempts,
                "settle_next_at": None, "settle_last_at": now,
                "retired": True}
    if attempts >= SETTLE_MAX_ATTEMPTS:
        # NOT SILENT. A contract we could never read is retired with a
        # status that says so, and the count is reported.
        return {"settle_status": SETTLE_ABANDONED,
                "settle_attempts": attempts, "settle_next_at": None,
                "settle_last_at": now, "retired": True}
    return {"settle_status": status or SETTLE_PENDING,
            "settle_attempts": attempts,
            "settle_next_at": now + settle_backoff_s(attempts),
            "settle_last_at": now, "retired": False}


def due_for_settlement(cursors: dict, *, now: float, limit: int) -> list:
    """Which contracts to read this pass, in order.

    THE PROGRESS PROPERTY. A never-attempted contract (`settle_next_at`
    is None and status is None) sorts ahead of every contract that has
    been attempted, so however many contracts are permanently pending,
    a newly observed one is read on the next pass. Terminal contracts
    are not candidates at all.
    """
    due = []
    for slug, c in cursors.items():
        st = c.get("settle_status")
        if st in TERMINAL_STATUSES:
            continue
        nxt = c.get("settle_next_at")
        if st is None and nxt is None:
            due.append((0, c.get("first_seen_at") or 0.0, slug))
            continue
        if nxt is None or nxt <= now:
            due.append((1, nxt or 0.0, slug))
    due.sort()
    return [slug for _, _, slug in due[:max(0, int(limit))]]


def evict_to(cursors: dict, cap: int) -> dict:
    """Hold the cursor map under a cap. Terminal cursors go first --
    they carry no future work -- then the least recently seen.

    Evicting a cursor that is NOT terminal loses that contract's
    settlement schedule, so it is counted separately and by name.
    """
    over = len(cursors) - max(0, int(cap))
    out = {"evicted": 0, "evicted_unresolved": 0}
    if over <= 0:
        return out
    ranked = sorted(
        cursors.items(),
        key=lambda kv: (0 if kv[1].get("settle_status") in TERMINAL_STATUSES
                        else 1, kv[1].get("last_seen_at") or 0.0))
    for slug, c in ranked[:over]:
        if c.get("settle_status") not in TERMINAL_STATUSES:
            out["evicted_unresolved"] += 1
        del cursors[slug]
        out["evicted"] += 1
    return out


# ── the schema ───────────────────────────────────────────────────────
#
# ONE TEXT, TWO PLACES. `migrations/093_bettor_live_observation.sql`
# holds this verbatim for the canonical record; a test compares them
# character for character so an edit to one that misses the other fails
# the build rather than surfacing as a production column that is not
# there.

DDL = """
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
"""

OWN_TABLES = ("bettor_live_journal", "bettor_live_cursor",
              "bettor_live_ledger")

CURSOR_FIELDS = ("first_seen_at", "last_seen_at", "last_source_ts",
                 "last_decided_at", "settle_status", "settle_attempts",
                 "settle_next_at", "settle_last_at")


def _cursor_row(slug, c):
    return (LANE, slug, c.get("first_seen_at"), c.get("last_seen_at"),
            c.get("last_source_ts"), c.get("last_decided_at"),
            c.get("settle_status"), int(c.get("settle_attempts") or 0),
            c.get("settle_next_at"), c.get("settle_last_at"))


# ── backends ─────────────────────────────────────────────────────────


class MemoryStore:
    """Durable of nothing. Present so that "no durable store" is a
    NAMED backend the operator chose, never a silent default."""

    backend = BACKEND_MEMORY
    durable_across = ()

    def __init__(self):
        self.records: list = []
        self.ledger: str | None = None

    async def start(self) -> dict:
        return {"ok": True, "backend": self.backend,
                "warning": "NOTHING WRITTEN HERE SURVIVES THIS PROCESS"}

    async def flush(self, records, cursors) -> dict:
        self.records.extend(records)
        del self.records[:-OUTBOX_MAX]
        return {"records": len(records), "cursors": len(cursors),
                "failures": 0}

    async def save_ledger(self, snapshot: str) -> bool:
        self.ledger = snapshot
        return True

    async def load(self) -> dict:
        return {"cursors": {}, "ledger": None, "backend": self.backend,
                "note": "a memory store recovers nothing, by construction"}

    async def prune(self, **_) -> dict:
        return {"journal_rows_deleted": 0, "cursors_deleted": 0}

    async def close(self) -> None:
        return None


class FileStore:
    """A journal and a cursor file on a filesystem.

    DURABLE ONLY IF THE FILESYSTEM IS. On a Render service that means a
    mounted disk; on `/var/tmp` it means a restart and nothing more.
    `start()` records which of those it believes it has, from the
    declared mount, and never assumes.

    Recovery reads the CURSOR FILE, not the journal, so restart time
    does not grow with the record count. The journal is the evidence;
    the cursor file is the position.
    """

    backend = BACKEND_FILE

    def __init__(self, directory: str, *, max_bytes: int = 64 * 1024 * 1024,
                 durable_across_redeploy: bool = False):
        self.dir = directory
        self.journal = os.path.join(directory, "decisions.jsonl")
        self.cursor_path = os.path.join(directory, "cursors.json")
        self.ledger_path = os.path.join(directory, "ledger.json")
        self.max_bytes = max_bytes
        self.durable_across = (("restart", "redeploy")
                               if durable_across_redeploy else ("restart",))

    async def start(self) -> dict:
        os.makedirs(self.dir, exist_ok=True)
        return {"ok": True, "backend": self.backend, "dir": self.dir,
                "durable_across": list(self.durable_across),
                "warning": (None if "redeploy" in self.durable_across else
                            "THIS PATH IS NOT DECLARED AS A MOUNTED DISK; "
                            "a redeploy replaces the container and deletes "
                            "these files")}

    def _rotate_if_needed(self) -> int:
        try:
            if os.path.getsize(self.journal) < self.max_bytes:
                return 0
        except OSError:
            return 0
        # ONE generation kept. Two files of max_bytes is the disk bound,
        # and it is a bound rather than a hope.
        try:
            os.replace(self.journal, self.journal + ".1")
            return 1
        except OSError:
            return 0

    def _write_atomic(self, path: str, text: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    async def flush(self, records, cursors) -> dict:
        import asyncio
        return await asyncio.to_thread(self._flush_sync, records, cursors)

    def _flush_sync(self, records, cursors) -> dict:
        out = {"records": 0, "cursors": 0, "failures": 0, "rotated": 0}
        if records:
            try:
                with open(self.journal, "a") as fh:
                    for r in records:
                        fh.write(json.dumps(r, default=str) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                out["records"] = len(records)
                out["rotated"] = self._rotate_if_needed()
            except OSError as exc:  # noqa: BLE001
                out["failures"] += 1
                out["error"] = type(exc).__name__
        if cursors:
            # THE WHOLE MAP, ATOMICALLY. It is bounded by
            # CURSOR_MAX_IN_MEMORY, so rewriting it is bounded work,
            # and a partial cursor file is a corrupt position.
            try:
                self._write_atomic(self.cursor_path, json.dumps(
                    {"lane": LANE, "schema": SCHEMA_VERSION,
                     "cursors": cursors}, default=str))
                out["cursors"] = len(cursors)
            except OSError as exc:  # noqa: BLE001
                out["failures"] += 1
                out["cursor_error"] = type(exc).__name__
        return out

    async def save_ledger(self, snapshot: str) -> bool:
        import asyncio

        def _w():
            try:
                self._write_atomic(self.ledger_path, snapshot)
                return True
            except OSError:
                return False
        return await asyncio.to_thread(_w)

    async def load(self) -> dict:
        import asyncio
        return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> dict:
        out = {"cursors": {}, "ledger": None, "backend": self.backend,
               "cursor_file_read": False, "cursor_error": None}
        try:
            with open(self.cursor_path) as fh:
                blob = json.load(fh)
            if blob.get("lane") != LANE:
                out["cursor_error"] = "LANE_MISMATCH"
            else:
                out["cursors"] = dict(blob.get("cursors") or {})
                out["cursor_file_read"] = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:  # noqa: BLE001
            out["cursor_error"] = type(exc).__name__
        try:
            with open(self.ledger_path) as fh:
                out["ledger"] = fh.read()
        except FileNotFoundError:
            pass
        except OSError as exc:  # noqa: BLE001
            out["ledger_error"] = type(exc).__name__
        return out

    async def prune(self, **_) -> dict:
        # Rotation IS the prune for a file journal; cursors are pruned
        # in memory before they are written.
        return {"journal_rows_deleted": 0, "cursors_deleted": 0,
                "bound": "two files of %d bytes" % self.max_bytes}

    async def close(self) -> None:
        return None


class PgStore:
    """Postgres on the service's existing database. THE DEFAULT.

    Survives process restart, service restart and redeploy, because the
    database is a separate managed instance and not part of the
    container. No service change, no new credential, no new dependency:
    the worker process already resolves a pool through `db.get_pool()`.

    WRITES ONLY ITS OWN THREE TABLES. Every statement in this class
    names one of them, and a test greps the source for any other table
    name rather than trusting this sentence.
    """

    backend = BACKEND_PG
    durable_across = ("restart", "service_restart", "redeploy")

    def __init__(self, pool=None, *, dsn: str | None = None):
        self._pool = pool
        self._dsn = dsn
        self._own_pool = False

    async def _get_pool(self):
        if self._pool is not None:
            return self._pool
        if self._dsn:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1,
                                                   max_size=2)
            self._own_pool = True
            return self._pool
        from .db import get_pool
        self._pool = await get_pool()
        return self._pool

    async def start(self) -> dict:
        pool = await self._get_pool()
        async with pool.acquire() as con:
            # THE WORKERS NEVER RUN MIGRATIONS. This is idempotent and
            # additive: three CREATE TABLE IF NOT EXISTS and two
            # indexes, touching no existing table.
            await con.execute(DDL)
        return {"ok": True, "backend": self.backend, "lane": LANE,
                "schema_version": SCHEMA_VERSION,
                "tables": list(OWN_TABLES),
                "durable_across": list(self.durable_across)}

    async def flush(self, records, cursors) -> dict:
        out = {"records": 0, "cursors": 0, "failures": 0}
        if not records and not cursors:
            return out
        pool = await self._get_pool()
        try:
            async with pool.acquire() as con:
                async with con.transaction():
                    if records:
                        await con.executemany(
                            "INSERT INTO bettor_live_journal "
                            "(lane, loop_version, kind, market_id, "
                            " source_ts, decided_at, status, selected, "
                            " record) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)",
                            [(LANE, r.get("loop") or "", r.get("kind")
                              or "DECISION", r.get("market_id"),
                              r.get("source_ts"), r.get("decided_at"),
                              r.get("status"), r.get("selected"),
                              json.dumps(r, default=str))
                             for r in records])
                        out["records"] = len(records)
                    if cursors:
                        await con.executemany(
                            "INSERT INTO bettor_live_cursor "
                            "(lane, market_id, first_seen_at, "
                            " last_seen_at, last_source_ts, "
                            " last_decided_at, settle_status, "
                            " settle_attempts, settle_next_at, "
                            " settle_last_at) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) "
                            "ON CONFLICT (lane, market_id) DO UPDATE SET "
                            " last_seen_at = EXCLUDED.last_seen_at, "
                            " last_source_ts = EXCLUDED.last_source_ts, "
                            " last_decided_at = EXCLUDED.last_decided_at, "
                            " settle_status = EXCLUDED.settle_status, "
                            " settle_attempts = EXCLUDED.settle_attempts, "
                            " settle_next_at = EXCLUDED.settle_next_at, "
                            " settle_last_at = EXCLUDED.settle_last_at",
                            [_cursor_row(s, c) for s, c in cursors.items()])
                        out["cursors"] = len(cursors)
        except Exception as exc:  # noqa: BLE001 -- counted, never swallowed
            out["failures"] = 1
            out["error"] = type(exc).__name__
            log.warning("bettor_live_store: flush failed: %s",
                        type(exc).__name__)
        return out

    async def save_ledger(self, snapshot: str) -> bool:
        pool = await self._get_pool()
        try:
            async with pool.acquire() as con:
                await con.execute(
                    "INSERT INTO bettor_live_ledger "
                    "(lane, saved_at, loop_version, schema_version, "
                    " snapshot) VALUES ($1, now(), $2, $3, $4) "
                    "ON CONFLICT (lane) DO UPDATE SET "
                    " saved_at = now(), loop_version = EXCLUDED.loop_version,"
                    " schema_version = EXCLUDED.schema_version, "
                    " snapshot = EXCLUDED.snapshot",
                    LANE, STORE_VERSION, SCHEMA_VERSION, snapshot)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("bettor_live_store: ledger save failed: %s",
                        type(exc).__name__)
            return False

    async def load(self) -> dict:
        """RECOVERY IS O(CURSORS), NOT O(JOURNAL).

        The journal is never replayed. A month of records costs exactly
        as much to restart against as an hour of them.
        """
        t0 = time.time()
        out = {"cursors": {}, "ledger": None, "backend": self.backend}
        pool = await self._get_pool()
        try:
            async with pool.acquire() as con:
                rows = await con.fetch(
                    "SELECT market_id, first_seen_at, last_seen_at, "
                    "       last_source_ts, last_decided_at, "
                    "       settle_status, settle_attempts, "
                    "       settle_next_at, settle_last_at "
                    "  FROM bettor_live_cursor WHERE lane = $1", LANE)
                for r in rows:
                    out["cursors"][r["market_id"]] = {
                        k: r[k] for k in CURSOR_FIELDS}
                led = await con.fetchrow(
                    "SELECT snapshot, saved_at FROM bettor_live_ledger "
                    " WHERE lane = $1", LANE)
                if led:
                    out["ledger"] = led["snapshot"]
                    out["ledger_saved_at"] = str(led["saved_at"])
                out["journal_rows"] = await con.fetchval(
                    "SELECT count(*) FROM bettor_live_journal "
                    " WHERE lane = $1", LANE)
        except Exception as exc:  # noqa: BLE001
            out["error"] = type(exc).__name__
            log.warning("bettor_live_store: load failed: %s",
                        type(exc).__name__)
        out["load_seconds"] = round(time.time() - t0, 4)
        return out

    async def prune(self, *, max_rows: int = JOURNAL_MAX_ROWS,
                    cursor_retention_days: int = CURSOR_RETENTION_DAYS
                    ) -> dict:
        """BOUND THE DISK. The database this writes to filled once and
        took the platform down for thirteen hours, so a measurement
        table that grows without a ceiling is not acceptable here.
        """
        out = {"journal_rows_deleted": 0, "cursors_deleted": 0,
               "max_rows": max_rows,
               "cursor_retention_days": cursor_retention_days}
        pool = await self._get_pool()
        try:
            async with pool.acquire() as con:
                cutoff = await con.fetchval(
                    "SELECT id FROM bettor_live_journal WHERE lane = $1 "
                    " ORDER BY id DESC OFFSET $2 LIMIT 1", LANE, max_rows)
                if cutoff is not None:
                    out["journal_rows_deleted"] = int((await con.execute(
                        "DELETE FROM bettor_live_journal "
                        " WHERE lane = $1 AND id <= $2", LANE, cutoff)
                    ).split()[-1])
                horizon = time.time() - cursor_retention_days * 86400.0
                out["cursors_deleted"] = int((await con.execute(
                    "DELETE FROM bettor_live_cursor "
                    " WHERE lane = $1 AND last_seen_at IS NOT NULL "
                    "   AND last_seen_at < $2", LANE, horizon)
                ).split()[-1])
        except Exception as exc:  # noqa: BLE001
            out["error"] = type(exc).__name__
        return out

    async def close(self) -> None:
        if self._own_pool and self._pool is not None:
            await self._pool.close()
            self._pool = None


# ── selection ────────────────────────────────────────────────────────

ALLOW_EPHEMERAL_ENV = "BETTOR_LIVE_ALLOW_EPHEMERAL"


def choose(*, backend: str | None = None, directory: str | None = None,
           pool=None, dsn: str | None = None,
           disk_declared: bool = False) -> dict:
    """Which store, and whether it is allowed to be this one.

    Returns `{"ok": bool, ...}` rather than raising, because the
    worker's answer to "there is nowhere durable to write" is to REFUSE
    TO START and say which of the three reasons applied.
    """
    b = (backend or os.environ.get("BETTOR_LIVE_STATE")
         or BACKEND_PG).strip().lower()
    if b == BACKEND_PG:
        return {"ok": True, "backend": b,
                "store": PgStore(pool=pool, dsn=dsn)}
    if b == BACKEND_FILE:
        d = directory or os.environ.get("BETTOR_LIVE_STATE_DIR")
        if not d:
            return {"ok": False, "backend": b,
                    "why": "BETTOR_LIVE_STATE=file needs "
                           "BETTOR_LIVE_STATE_DIR"}
        declared = disk_declared or (
            str(os.environ.get("BETTOR_LIVE_STATE_DISK", "")).strip().lower()
            in ("1", "true", "yes"))
        if not declared and not _ephemeral_allowed():
            # THE WHOLE POINT. /var/tmp passes every fsync and loses
            # everything on the next deploy.
            return {"ok": False, "backend": b, "dir": d,
                    "why": "a file store on a path that is not a "
                           "declared persistent disk does not survive a "
                           "redeploy. Set BETTOR_LIVE_STATE_DISK=1 when "
                           "the path is a mounted disk, or "
                           "%s=1 to accept losing the evidence"
                           % ALLOW_EPHEMERAL_ENV}
        return {"ok": True, "backend": b,
                "store": FileStore(d, durable_across_redeploy=declared)}
    if b == BACKEND_MEMORY:
        if not _ephemeral_allowed():
            return {"ok": False, "backend": b,
                    "why": "a memory store keeps no evidence at all; set "
                           "%s=1 to choose that deliberately"
                           % ALLOW_EPHEMERAL_ENV}
        return {"ok": True, "backend": b, "store": MemoryStore()}
    return {"ok": False, "backend": b,
            "why": "unknown BETTOR_LIVE_STATE; expected one of %s"
                   % ", ".join((BACKEND_PG, BACKEND_FILE, BACKEND_MEMORY))}


def _ephemeral_allowed() -> bool:
    return str(os.environ.get(ALLOW_EPHEMERAL_ENV, "")).strip().lower() in (
        "1", "true", "yes")


def describe() -> dict:
    return {
        "store": STORE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "lane": LANE,
        "default_backend": BACKEND_PG,
        "tables_written": list(OWN_TABLES),
        "tables_not_written": (
            "no accounting table, legacy or otherwise: not ai_trades, "
            "not bettor_state_*, not copy_*, not trades"),
        "durability": {
            BACKEND_PG: "process restart, service restart, redeploy",
            BACKEND_FILE: "process restart always; redeploy ONLY on a "
                          "declared mounted disk",
            BACKEND_MEMORY: "nothing",
        },
        "bounds": {
            "journal_max_rows": JOURNAL_MAX_ROWS,
            "cursor_retention_days": CURSOR_RETENTION_DAYS,
            "cursor_max_in_memory": CURSOR_MAX_IN_MEMORY,
            "outbox_max": OUTBOX_MAX,
            "flush_every_s": FLUSH_EVERY_S,
            "prune_every_s": PRUNE_EVERY_S,
            "recovery_cost": "O(cursors); the journal is never replayed",
            "write_loss_on_sigkill": "at most one flush interval",
        },
        "settlement_schedule": {
            "base_s": SETTLE_BASE_S, "max_s": SETTLE_MAX_S,
            "max_attempts": SETTLE_MAX_ATTEMPTS,
            "terminal": list(TERMINAL_STATUSES),
            "retryable": list(RETRYABLE_STATUSES),
            "progress": ("never-attempted contracts sort ahead of every "
                         "attempted one, so a permanently pending "
                         "population cannot hold the batch"),
        },
    }
