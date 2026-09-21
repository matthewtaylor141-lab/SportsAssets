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
# THREE DEFECTS THIS SECTION EXISTS TO NOT HAVE.
#
# 1. "THE 25 OLDEST OBSERVED" CANNOT MAKE PROGRESS. A contract that
#    never resolves is permanently the oldest, so a hundred pending
#    contracts hold the batch forever and a market that settled an hour
#    ago is never read.
#
# 2. A DERIVED OUTCOME IS NOT AN AUTHORITATIVE ONE. The first fix
#    retired RESOLVED_DERIVED alongside RESOLVED, which would have
#    closed outcome collection on an INFERENCE FROM A PRICE and made
#    later confirmation through the venue's own settlement endpoint
#    impossible. A derived outcome is now PRESERVED SEPARATELY --
#    `settle_derived_outcome` and `settle_derived_at` -- and the
#    contract stays scheduled for the authoritative read. ONLY
#    `RESOLVED` completes outcome collection.
#
# 3. A HEALTHY OPEN MARKET IS NOT A FAILURE. The first fix incremented
#    one attempt counter for every result, PENDING included, and
#    abandoned the contract at twelve. A market may legitimately stay
#    open far longer than twelve checks. Successful reads and FAILED
#    reads are now counted separately:
#
#      settle_attempts   every read, successful or not (a record)
#      settle_failures   CONSECUTIVE failed reads, reset by any
#                        successful one (the escalation trigger)
#
#    A PENDING answer is a SUCCESSFUL read. It never advances the
#    failure count and can never abandon a contract. It is scheduled
#    from the event's own timing where the venue gives one, and from a
#    bounded backoff where it does not.
#
#    A run of failed reads escalates to READ_FAILURE_ESCALATED, which
#    is VISIBLE and STILL SCHEDULED -- at the maximum interval and at
#    the back of the queue, so a broken slug cannot starve a healthy
#    one, and nothing is ever silently dropped.

SETTLE_NEW = None
SETTLE_PENDING = "PENDING"
SETTLE_UNREADABLE = "UNREADABLE"
SETTLE_UNMATCHED = "UNMATCHED"
SETTLE_RESOLVED = "RESOLVED"
SETTLE_RESOLVED_DERIVED = "RESOLVED_DERIVED"
# A run of failed reads, surfaced for an operator. NOT terminal: the
# contract keeps its place in the schedule, at the longest interval.
SETTLE_READ_ESCALATED = "READ_FAILURE_ESCALATED"

# ONLY AN AUTHORITATIVE RESOLUTION COMPLETES OUTCOME COLLECTION.
# A settled price does not change, so re-reading it would spend a REST
# call on a fact we hold. Nothing else is terminal -- not a derived
# outcome, not a long-open market, not a slug we cannot read.
TERMINAL_STATUSES = (SETTLE_RESOLVED,)
AUTHORITATIVE_STATUSES = (SETTLE_RESOLVED,)
FAILED_READ_STATUSES = (SETTLE_UNREADABLE, SETTLE_UNMATCHED)
SUCCESSFUL_READ_STATUSES = (SETTLE_PENDING, SETTLE_RESOLVED_DERIVED,
                            SETTLE_RESOLVED)

# Pending markets: bounded backoff when nothing better is known.
PENDING_BASE_S = 600.0            # ten minutes
PENDING_MAX_S = 21_600.0          # never further apart than six hours
# ... and when the venue tells us when the event is, look shortly after
# it rather than walking a backoff to the cap.
EVENT_GRACE_S = 300.0

# A derived outcome means the market has almost certainly settled, so
# the authoritative endpoint should catch up soon. Re-checked on its
# own, shorter schedule -- and forever, because giving up here is
# exactly what turns an inference into a recorded outcome.
DERIVED_RECHECK_BASE_S = 900.0
DERIVED_RECHECK_MAX_S = 21_600.0

# Failed reads: their own backoff, and a visible escalation.
FAILURE_BASE_S = 600.0
FAILURE_MAX_S = 21_600.0
READ_FAILURE_ESCALATE_AT = 6      # consecutive failures


def _capped_backoff(n: int, base: float, cap: float) -> float:
    """Exponential, capped, deterministic. No jitter: two runs of the
    same scheduler must be comparable."""
    n = max(0, int(n))
    if n >= 40:                       # 2**40 overflows nothing but time
        return cap
    return min(base * (2 ** n), cap)


def settle_backoff_s(n: int) -> float:
    return _capped_backoff(n, PENDING_BASE_S, PENDING_MAX_S)


def settle_transition(status: str | None, attempts: int, *, now: float,
                      failures: int = 0, event_at: float | None = None,
                      derived_at: float | None = None,
                      derived_outcome=None,
                      outcome=None) -> dict:
    """One settlement read's outcome -> the contract's next schedule.

    `attempts` counts every read. `failures` counts CONSECUTIVE failed
    reads and is what escalates; any successful read clears it.

    Returns the fields to store plus `retired`, which is true only for
    an AUTHORITATIVE resolution.
    """
    attempts = max(0, int(attempts)) + 1
    failures = max(0, int(failures))
    base = {"settle_attempts": attempts, "settle_last_at": now}

    if status == SETTLE_RESOLVED:
        # THE ONLY TERMINAL CASE. Outcome collection is complete for
        # this contract, and only the venue's own settlement endpoint
        # can say so.
        return dict(base, settle_status=SETTLE_RESOLVED,
                    settle_failures=0, settle_next_at=None,
                    settle_outcome=outcome, retired=True)

    if status == SETTLE_RESOLVED_DERIVED:
        # PRESERVED, NOT ACCEPTED. The derived outcome and the time we
        # first inferred it are kept; the contract stays scheduled for
        # the authoritative read that can confirm or contradict it.
        first = derived_at if derived_at is not None else now
        n_derived = 0 if derived_at is None else attempts
        return dict(base, settle_status=SETTLE_RESOLVED_DERIVED,
                    settle_failures=0,
                    settle_derived_at=first,
                    settle_derived_outcome=(derived_outcome
                                            if derived_at is not None
                                            else outcome),
                    settle_next_at=now + _capped_backoff(
                        n_derived, DERIVED_RECHECK_BASE_S,
                        DERIVED_RECHECK_MAX_S),
                    retired=False)

    if status in FAILED_READ_STATUSES:
        failures += 1
        escalated = failures >= READ_FAILURE_ESCALATE_AT
        return dict(base,
                    settle_status=(SETTLE_READ_ESCALATED if escalated
                                   else status),
                    settle_failures=failures,
                    settle_next_at=now + _capped_backoff(
                        failures, FAILURE_BASE_S, FAILURE_MAX_S),
                    retired=False)

    # PENDING, or anything else a successful read returned. A market
    # that is legitimately open is not a problem to be counted down.
    if event_at is not None and event_at + EVENT_GRACE_S > now:
        # THE EVENT'S OWN TIMING. Looking again shortly after the event
        # is expected to conclude beats walking a blind backoff to six
        # hours and then missing the resolution by five of them.
        nxt = event_at + EVENT_GRACE_S
    else:
        # Backoff on the number of PENDING reads, which is attempts
        # minus the failures already spent, and it is CAPPED -- a
        # long-open market settles into a six-hourly check and stays
        # there for as long as it stays open.
        nxt = now + _capped_backoff(max(0, attempts - failures - 1),
                                    PENDING_BASE_S, PENDING_MAX_S)
    return dict(base, settle_status=SETTLE_PENDING, settle_failures=0,
                settle_next_at=nxt, retired=False)


def due_for_settlement(cursors: dict, *, now: float, limit: int) -> list:
    """Which contracts to read this pass, in order.

    THE PROGRESS PROPERTY. A never-attempted contract sorts ahead of
    every attempted one, so however many contracts are pending, a newly
    observed one is read on the next pass.

    THE STARVATION PROPERTY. A contract whose reads keep failing sorts
    BEHIND every healthy one, so a broken slug spends only the budget
    left over -- and it is still in the queue, because dropping it
    would be losing an obligation quietly.

    Only an AUTHORITATIVE resolution is excluded. A derived outcome is
    still a candidate: confirming it is the whole point.
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
        if nxt is not None and nxt > now:
            continue
        rank = 2 if st == SETTLE_READ_ESCALATED else 1
        due.append((rank, nxt if nxt is not None else 0.0, slug))
    due.sort()
    return [slug for _, _, slug in due[:max(0, int(limit))]]


def has_outstanding_obligation(c: dict) -> bool:
    """Does this cursor still owe us an authoritative outcome?

    Everything that is not an authoritative resolution does, including
    a derived one. Used by the pruner, which must never delete an
    obligation because it is old -- an old unresolved market is the
    case retention exists FOR.
    """
    return c.get("settle_status") not in TERMINAL_STATUSES


def evict_to(cursors: dict, cap: int, *, dirty: set = None) -> dict:
    """Hold the IN-MEMORY cursor map under a cap.

    THIS IS A CACHE, NOT THE RECORD. The settlement queue is read from
    the STORE (`store.due_for_settlement`), so dropping a cursor from
    memory loses no future work -- the row is still there and comes
    back when it is due. What it must never drop is a cursor whose
    updates have NOT been flushed yet, because that would lose the
    update rather than the cache entry.

    Order: authoritative-resolved first (no future work at all), then
    least recently seen. Unflushed cursors are never evicted.
    """
    dirty = dirty or set()
    out = {"evicted": 0, "evicted_unresolved": 0, "kept_dirty": 0}
    over = len(cursors) - max(0, int(cap))
    if over <= 0:
        return out
    candidates = [(s, c) for s, c in cursors.items() if s not in dirty]
    out["kept_dirty"] = len(cursors) - len(candidates)
    ranked = sorted(
        candidates,
        key=lambda kv: (0 if not has_outstanding_obligation(kv[1]) else 1,
                        kv[1].get("last_seen_at") or 0.0))
    for slug, c in ranked[:over]:
        if has_outstanding_obligation(c):
            # A CACHE MISS, NOT A LOSS. Counted because a persistently
            # full cache means the working set is larger than the cap
            # and every settlement pass pays a store read for it.
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
"""

OWN_TABLES = ("bettor_live_journal", "bettor_live_cursor",
              "bettor_live_ledger")

CURSOR_FIELDS = ("first_seen_at", "last_seen_at", "last_source_ts",
                 "last_decided_at", "event_at", "settle_status",
                 "settle_attempts", "settle_failures", "settle_next_at",
                 "settle_last_at", "settle_derived_at",
                 "settle_derived_outcome", "settle_outcome")

_INT_CURSOR_FIELDS = ("settle_attempts", "settle_failures")


def new_cursor(now: float) -> dict:
    c = {k: None for k in CURSOR_FIELDS}
    c.update(first_seen_at=now, last_seen_at=now,
             settle_attempts=0, settle_failures=0)
    return c


def _cursor_row(slug, c):
    return (LANE, slug, c.get("first_seen_at"), c.get("last_seen_at"),
            c.get("last_source_ts"), c.get("last_decided_at"),
            c.get("event_at"), c.get("settle_status"),
            int(c.get("settle_attempts") or 0),
            int(c.get("settle_failures") or 0),
            c.get("settle_next_at"), c.get("settle_last_at"),
            c.get("settle_derived_at"),
            _txt(c.get("settle_derived_outcome")),
            _txt(c.get("settle_outcome")))


def _txt(v):
    return None if v is None else str(v)


def record_key(rec: dict) -> str:
    """A STABLE IDENTITY FOR ONE RECORD, so a retry cannot double it.

    A commit can succeed and its acknowledgment be lost -- a dropped
    connection after COMMIT looks exactly like a failed write. The
    retry that follows must not insert the record a second time, or
    every such event silently inflates the observation count and every
    rate derived from it.

    The key is content-derived, so the retry of a record computes the
    same key the first attempt did, and `ON CONFLICT DO NOTHING` makes
    the second insert a no-op.
    """
    import hashlib
    parts = [LANE, str(rec.get("kind") or "DECISION"),
             str(rec.get("market_id") or ""),
             str(rec.get("source_ts") or ""),
             str(rec.get("decided_at") or rec.get("at") or ""),
             str(rec.get("status") or ""),
             str(rec.get("observation_id") or "")]
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()


# ── backends ─────────────────────────────────────────────────────────


class MemoryStore:
    """Durable of nothing. Present so that "no durable store" is a
    NAMED backend the operator chose, never a silent default."""

    backend = BACKEND_MEMORY
    durable_across = ()
    boot_id = None

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

    async def due_for_settlement(self, *, now: float, limit: int) -> dict:
        return {"slugs": [], "cursors": {}, "outstanding": 0,
                "note": "a memory store keeps no schedule"}

    async def outcome_report(self) -> dict:
        return {"by_status": {}, "authoritative": 0,
                "awaiting_authoritative": 0, "read_escalated": 0,
                "outstanding": 0}

    async def prune(self, **_) -> dict:
        return {"journal_rows_deleted": 0, "cursors_deleted": 0,
                "cursors_retained_unresolved": 0}

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
                 durable_across_redeploy: bool = False,
                 boot_id: str | None = None):
        self.boot_id = boot_id
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
            # MERGED, THEN WRITTEN WHOLE AND ATOMICALLY.
            #
            # The caller hands us the DIRTY cursors, not all of them --
            # it cannot hand us all of them, because the in-memory map
            # is a bounded cache. Writing what we were handed replaced
            # the file with a subset and silently deleted every
            # contract that had not changed in this batch, which is a
            # settlement obligation lost to a flush.
            try:
                existing = self._load_sync().get("cursors") or {}
                existing.update(cursors)
                self._write_atomic(self.cursor_path, json.dumps(
                    {"lane": LANE, "schema": SCHEMA_VERSION,
                     "cursors": existing}, default=str))
                out["cursors"] = len(cursors)
                out["cursors_total"] = len(existing)
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

    async def due_for_settlement(self, *, now: float, limit: int) -> dict:
        """The same queue, over the cursor file. One implementation of
        the ORDERING (`due_for_settlement`, the pure function); two
        implementations of where the rows come from."""
        loaded = await self.load()
        cursors = loaded.get("cursors") or {}
        slugs = due_for_settlement(cursors, now=now, limit=limit)
        out = {"slugs": slugs,
               "cursors": {s: cursors[s] for s in slugs},
               "outstanding": sum(1 for c in cursors.values()
                                  if has_outstanding_obligation(c))}
        if loaded.get("cursor_error"):
            out["error"] = loaded["cursor_error"]
        return out

    async def outcome_report(self) -> dict:
        cursors = (await self.load()).get("cursors") or {}
        by: dict = {}
        for c in cursors.values():
            k = c.get("settle_status") or "NEVER_ATTEMPTED"
            by[k] = by.get(k, 0) + 1
        return {"by_status": by,
                "authoritative": by.get(SETTLE_RESOLVED, 0),
                "awaiting_authoritative": by.get(SETTLE_RESOLVED_DERIVED, 0),
                "read_escalated": by.get(SETTLE_READ_ESCALATED, 0),
                "outstanding": sum(n for st, n in by.items()
                                   if st != SETTLE_RESOLVED)}

    async def prune(self, *, max_rows: int = JOURNAL_MAX_ROWS,
                    cursor_retention_days: int = CURSOR_RETENTION_DAYS
                    ) -> dict:
        """Rotation IS the prune for a file journal. Cursors age out
        ONLY when outcome collection is complete for them -- the same
        rule the Postgres backend applies, because deleting an
        unresolved obligation because it is old deletes exactly the
        markets retention exists for."""
        import asyncio

        def _go():
            loaded = self._load_sync()
            cursors = loaded.get("cursors") or {}
            horizon = time.time() - cursor_retention_days * 86400.0
            keep, dropped = {}, 0
            for slug, c in cursors.items():
                seen = c.get("last_seen_at")
                if (c.get("settle_status") == SETTLE_RESOLVED
                        and seen is not None and seen < horizon):
                    dropped += 1
                    continue
                keep[slug] = c
            if dropped:
                self._write_atomic(self.cursor_path, json.dumps(
                    {"lane": LANE, "schema": SCHEMA_VERSION,
                     "cursors": keep}, default=str))
            return {"journal_rows_deleted": 0, "cursors_deleted": dropped,
                    "cursors_retained_unresolved": sum(
                        1 for c in keep.values()
                        if has_outstanding_obligation(c)),
                    "bound": "two files of %d bytes" % self.max_bytes}

        return await asyncio.to_thread(_go)

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

    def __init__(self, pool=None, *, dsn: str | None = None,
                 boot_id: str | None = None):
        self._pool = pool
        self._dsn = dsn
        self._own_pool = False
        self.boot_id = boot_id

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
        """Create the schema, then VERIFY it.

        `CREATE TABLE IF NOT EXISTS` is a no-op against a table that
        already exists with an OLDER SHAPE -- it adds no column and
        raises nothing, so a schema change would half-apply and the
        first write would fail at runtime instead of at startup. The
        verification below turns that into a refusal to start, which
        `main()` reports as STORE_START_REFUSED.
        """
        pool = await self._get_pool()
        async with pool.acquire() as con:
            # THE WORKERS NEVER RUN MIGRATIONS. Idempotent and
            # additive: three CREATE TABLE IF NOT EXISTS and three
            # indexes, touching no existing table.
            await con.execute(DDL)
            missing = await self._missing_columns(con)
        if missing:
            return {"ok": False, "backend": self.backend,
                    "why": "SCHEMA_MISMATCH", "missing": missing,
                    "remedy": ("these tables exist with an older shape. "
                               "Apply migrations/093_bettor_live_"
                               "observation.sql, or drop the three "
                               "bettor_live_* tables if they hold "
                               "nothing worth keeping")}
        return {"ok": True, "backend": self.backend, "lane": LANE,
                "schema_version": SCHEMA_VERSION,
                "tables": list(OWN_TABLES),
                "durable_across": list(self.durable_across)}

    async def _missing_columns(self, con) -> list:
        want = {
            "bettor_live_journal": ("lane", "boot_id", "record_key",
                                    "kind", "market_id", "source_ts",
                                    "record"),
            "bettor_live_cursor": ("lane", "market_id") + CURSOR_FIELDS,
            "bettor_live_ledger": ("lane", "boot_id", "snapshot",
                                   "schema_version"),
        }
        rows = await con.fetch(
            "SELECT table_name, column_name FROM information_schema.columns "
            " WHERE table_schema = current_schema() "
            "   AND table_name = ANY($1::text[])", list(want))
        have: dict = {}
        for r in rows:
            have.setdefault(r["table_name"], set()).add(r["column_name"])
        return sorted("%s.%s" % (t, c) for t, cols in want.items()
                      for c in cols if c not in have.get(t, set()))

    async def flush(self, records, cursors) -> dict:
        out = {"records": 0, "cursors": 0, "failures": 0}
        if not records and not cursors:
            return out
        pool = await self._get_pool()
        try:
            async with pool.acquire() as con:
                async with con.transaction():
                    if records:
                        # ON CONFLICT DO NOTHING, against the CONTENT
                        # KEY. A commit whose acknowledgment was lost
                        # is retried by the loop, and without this the
                        # retry would insert every record a second
                        # time -- inflating the observation count and
                        # every rate derived from it.
                        await con.executemany(
                            "INSERT INTO bettor_live_journal "
                            "(lane, boot_id, record_key, loop_version, "
                            " kind, market_id, source_ts, decided_at, "
                            " status, selected, record) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,"
                            "        $11::jsonb) "
                            "ON CONFLICT (lane, record_key) DO NOTHING",
                            [(LANE, r.get("boot_id"),
                              r.get("record_key") or record_key(r),
                              r.get("loop") or "", r.get("kind")
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
                            " last_decided_at, event_at, settle_status, "
                            " settle_attempts, settle_failures, "
                            " settle_next_at, settle_last_at, "
                            " settle_derived_at, settle_derived_outcome, "
                            " settle_outcome) "
                            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,"
                            "        $11,$12,$13,$14,$15) "
                            "ON CONFLICT (lane, market_id) DO UPDATE SET "
                            " last_seen_at = EXCLUDED.last_seen_at, "
                            " last_source_ts = EXCLUDED.last_source_ts, "
                            " last_decided_at = EXCLUDED.last_decided_at, "
                            " event_at = COALESCE(EXCLUDED.event_at, "
                            "                     bettor_live_cursor.event_at), "
                            " settle_status = EXCLUDED.settle_status, "
                            " settle_attempts = EXCLUDED.settle_attempts, "
                            " settle_failures = EXCLUDED.settle_failures, "
                            " settle_next_at = EXCLUDED.settle_next_at, "
                            " settle_last_at = EXCLUDED.settle_last_at, "
                            " settle_derived_at = COALESCE("
                            "     bettor_live_cursor.settle_derived_at, "
                            "     EXCLUDED.settle_derived_at), "
                            " settle_derived_outcome = COALESCE("
                            "     bettor_live_cursor.settle_derived_outcome, "
                            "     EXCLUDED.settle_derived_outcome), "
                            " settle_outcome = COALESCE("
                            "     EXCLUDED.settle_outcome, "
                            "     bettor_live_cursor.settle_outcome)",
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
                    "(lane, saved_at, boot_id, loop_version, "
                    " schema_version, snapshot) "
                    "VALUES ($1, now(), $2, $3, $4, $5) "
                    "ON CONFLICT (lane) DO UPDATE SET "
                    " saved_at = now(), boot_id = EXCLUDED.boot_id, "
                    " loop_version = EXCLUDED.loop_version,"
                    " schema_version = EXCLUDED.schema_version, "
                    " snapshot = EXCLUDED.snapshot",
                    LANE, self.boot_id, STORE_VERSION, SCHEMA_VERSION,
                    snapshot)
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
                    "       last_source_ts, last_decided_at, event_at, "
                    "       settle_status, settle_attempts, "
                    "       settle_failures, settle_next_at, "
                    "       settle_last_at, settle_derived_at, "
                    "       settle_derived_outcome, settle_outcome "
                    "  FROM bettor_live_cursor WHERE lane = $1 "
                    " ORDER BY last_seen_at DESC NULLS LAST LIMIT $2",
                    LANE, CURSOR_MAX_IN_MEMORY)
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
                # AN OBLIGATION IS NEVER DELETED BY AGE. The previous
                # version deleted any cursor older than the retention
                # window regardless of settlement status, which would
                # have thrown away exactly the markets that had been
                # open longest -- the unresolved ones retention exists
                # FOR. Only an AUTHORITATIVELY RESOLVED contract, whose
                # outcome collection is complete, can age out.
                out["cursors_deleted"] = int((await con.execute(
                    "DELETE FROM bettor_live_cursor "
                    " WHERE lane = $1 AND last_seen_at IS NOT NULL "
                    "   AND last_seen_at < $2 "
                    "   AND settle_status = $3",
                    LANE, horizon, SETTLE_RESOLVED)
                ).split()[-1])
                out["cursors_retained_unresolved"] = await con.fetchval(
                    "SELECT count(*) FROM bettor_live_cursor "
                    " WHERE lane = $1 AND last_seen_at < $2 "
                    "   AND settle_status IS DISTINCT FROM $3",
                    LANE, horizon, SETTLE_RESOLVED)
        except Exception as exc:  # noqa: BLE001
            out["error"] = type(exc).__name__
        return out

    async def due_for_settlement(self, *, now: float, limit: int) -> dict:
        """THE SCHEDULE LIVES IN THE STORE, NOT IN MEMORY.

        The in-memory cursor map is a bounded CACHE. If the settlement
        queue were read from it, hitting the cap would silently drop
        obligations -- a market would stop being checked because the
        process was busy, which is not a reason.

        Same ordering as the pure policy: never-attempted first, then
        due-by-backoff, escalated last. Only an AUTHORITATIVE
        resolution is excluded, so a derived outcome is still queued
        for the confirmation that can complete it.
        """
        pool = await self._get_pool()
        out = {"slugs": [], "cursors": {}}
        try:
            async with pool.acquire() as con:
                rows = await con.fetch(
                    "SELECT market_id, first_seen_at, last_seen_at, "
                    "       last_source_ts, last_decided_at, event_at, "
                    "       settle_status, settle_attempts, "
                    "       settle_failures, settle_next_at, "
                    "       settle_last_at, settle_derived_at, "
                    "       settle_derived_outcome, settle_outcome "
                    "  FROM bettor_live_cursor "
                    " WHERE lane = $1 "
                    "   AND settle_status IS DISTINCT FROM $2 "
                    "   AND (settle_next_at IS NULL "
                    "        OR settle_next_at <= $3) "
                    " ORDER BY (CASE "
                    "             WHEN settle_status IS NULL "
                    "                  AND settle_next_at IS NULL THEN 0 "
                    "             WHEN settle_status = $4 THEN 2 "
                    "             ELSE 1 END), "
                    "          COALESCE(settle_next_at, first_seen_at, 0) "
                    " LIMIT $5",
                    LANE, SETTLE_RESOLVED, now, SETTLE_READ_ESCALATED,
                    max(0, int(limit)))
                for r in rows:
                    out["slugs"].append(r["market_id"])
                    out["cursors"][r["market_id"]] = {
                        k: r[k] for k in CURSOR_FIELDS}
                out["outstanding"] = await con.fetchval(
                    "SELECT count(*) FROM bettor_live_cursor "
                    " WHERE lane = $1 AND settle_status IS DISTINCT FROM $2",
                    LANE, SETTLE_RESOLVED)
        except Exception as exc:  # noqa: BLE001
            out["error"] = type(exc).__name__
            log.warning("bettor_live_store: due_for_settlement failed: %s",
                        type(exc).__name__)
        return out

    async def outcome_report(self) -> dict:
        """What outcome collection has and has not completed.

        DERIVED IS NOT DONE. `awaiting_authoritative` counts contracts
        whose only outcome is an inference from converged prices; they
        stay in the queue until the venue's own endpoint answers.
        """
        pool = await self._get_pool()
        out = {}
        try:
            async with pool.acquire() as con:
                rows = await con.fetch(
                    "SELECT settle_status, count(*) AS n "
                    "  FROM bettor_live_cursor WHERE lane = $1 "
                    " GROUP BY settle_status", LANE)
                out["by_status"] = {(r["settle_status"] or "NEVER_ATTEMPTED"):
                                    r["n"] for r in rows}
                out["authoritative"] = out["by_status"].get(SETTLE_RESOLVED, 0)
                out["awaiting_authoritative"] = out["by_status"].get(
                    SETTLE_RESOLVED_DERIVED, 0)
                out["read_escalated"] = out["by_status"].get(
                    SETTLE_READ_ESCALATED, 0)
                out["outstanding"] = sum(
                    n for st, n in out["by_status"].items()
                    if st != SETTLE_RESOLVED)
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
           pool=None, dsn: str | None = None, boot_id: str | None = None,
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
                "store": PgStore(pool=pool, dsn=dsn, boot_id=boot_id)}
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
                "store": FileStore(d, durable_across_redeploy=declared,
                                   boot_id=boot_id)}
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
            "pending": {"base_s": PENDING_BASE_S, "max_s": PENDING_MAX_S,
                        "from_event_timing": "next check at event_at + "
                                             "%g s where the venue gives "
                                             "one" % EVENT_GRACE_S,
                        "never_abandoned": (
                            "a PENDING answer is a SUCCESSFUL read. A "
                            "market may legitimately stay open far "
                            "longer than any attempt count")},
            "derived": {"base_s": DERIVED_RECHECK_BASE_S,
                        "max_s": DERIVED_RECHECK_MAX_S,
                        "terminal": False,
                        "why": ("a derived outcome is an INFERENCE FROM "
                                "A PRICE. It is preserved in "
                                "settle_derived_outcome and the contract "
                                "stays scheduled, because only the "
                                "venue's settlement endpoint can "
                                "complete outcome collection")},
            "failed_reads": {"base_s": FAILURE_BASE_S,
                             "max_s": FAILURE_MAX_S,
                             "escalate_at": READ_FAILURE_ESCALATE_AT,
                             "escalated_status": SETTLE_READ_ESCALATED,
                             "policy": ("consecutive failures escalate to "
                                        "a VISIBLE status and keep their "
                                        "place in the queue, at the "
                                        "longest interval and at the "
                                        "back, so a broken slug cannot "
                                        "starve a healthy one and "
                                        "nothing is dropped")},
            "terminal": list(TERMINAL_STATUSES),
            "completes_outcome_collection": "RESOLVED, and only RESOLVED",
            "counters": {"settle_attempts": "every read",
                         "settle_failures": "CONSECUTIVE failed reads, "
                                            "cleared by any successful "
                                            "one"},
            "queue_lives_in": ("the STORE, not the in-memory cache: "
                               "store.due_for_settlement(). An in-memory "
                               "cap must never erase future work"),
            "retention": ("only an AUTHORITATIVELY RESOLVED cursor ages "
                          "out. An unresolved obligation is retained "
                          "regardless of age"),
            "progress": ("never-attempted contracts sort ahead of every "
                         "attempted one, so a permanently pending "
                         "population cannot hold the batch"),
        },
    }
