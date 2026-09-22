"""What the run writes down, and WHERE it survives.

THE DESTINATION IS POSTGRES, AND THERE IS NO OTHER ONE. The first
version wrote JSONL under `BETTOR_INCENTIVE_DIR`, defaulting to
`/var/tmp/bettor-incentive`. That is an ephemeral filesystem on this
platform: it passes every fsync and loses everything at the next
deploy -- and a deploy is exactly what a rollback, a configuration
change or an ordinary release performs. A day of collection that dies
on the next push is not evidence, so the file path is gone rather than
demoted. `bettor_live_store` already learned this lesson and refuses a
file store on an undeclared disk; this refuses one outright.

ONE LADDER PER CHANGE, NOT ONE ROW PER SECOND. Persisting every 1 Hz
scoring instant for ten markets over a day is 864,000 rows carrying the
same unchanged book. The feed is change-driven, so the CHANGES are the
information and the instants are derivable -- but only if the
derivation has everything it needs, which is why there are five record
kinds and not one:

  LADDER          every frame, classed INITIAL_LADDER (the first for a
                  market in a connection epoch) or UPDATE, with the
                  full bids/offers arrays, the venue's transactTime at
                  source precision, and the venue state.
  EPOCH           every connection epoch, opened and closed.
  GAP             every interval we could not observe, with a reason --
                  INCLUDING the interval a process replacement created.
  PROGRAM_VERSION programme terms at arm and at every recheck.
  RUN_OPEN/CLOSE  the run's identity, allowlist, window and outcome.

THE RECONSTRUCTION KEY IS (boot_id, epoch), NEVER epoch ALONE. This is
the trap a restart sets. `MarketStream.epoch` counts connections WITHIN
ONE PROCESS and restarts at 1 in the next one, so "epoch 1" in boot A
and "epoch 1" in boot B are different connections with an unobserved
interval between them. A reconstruction keyed on the bare epoch would
see one continuous epoch, carry the last ladder of boot A across the
crash, and score the outage as a quiet book. Every row therefore
carries `boot_id`, and the rule below is keyed on the pair.

AND A BOOT GAP IS WRITTEN BEFORE ANYTHING ELSE. On a resumed run,
`open()` reads the last record of the previous boot and writes a GAP
covering `[that instant, now)` with `why=PROCESS_REPLACED`. Without it
the only evidence of the outage would be an absence of rows, and an
absence of rows on a change-driven feed is precisely what "nothing
changed" looks like.

Run:  python -m pytest backend/tests/test_bettor_incentive_release.py
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

JOURNAL_VERSION = "BETTOR_INCENTIVE_JOURNAL_V2"

R_RUN_OPEN = "RUN_OPEN"
R_RUN_CLOSE = "RUN_CLOSE"
R_LADDER = "LADDER"
R_EPOCH = "EPOCH"
R_GAP = "GAP"
R_PROGRAM = "PROGRAM_VERSION"

BOOT_GAP = "BOOT_GAP"
PROCESS_REPLACED = "PROCESS_REPLACED"

RECONSTRUCTION_RULE = (
    "A scoring instant takes the last LADDER at or before it, IF that "
    "ladder shares the instant's (boot_id, epoch) AND no GAP covers the "
    "instant. NEVER carry a ladder across a boot boundary or an epoch "
    "boundary: MarketStream.epoch restarts at 1 in each process, so the "
    "bare epoch number is not unique across boots.")

TABLE = "bettor_incentive_journal"

# Its own key, distinct from the store's 930_930_093.
SCHEMA_LOCK_KEY = 930_930_094
SCHEMA_LOCK_TIMEOUT_MS = 10_000
SCHEMA_ATTEMPTS = 3

DDL = """
CREATE TABLE IF NOT EXISTS bettor_incentive_journal (
    id       BIGSERIAL        PRIMARY KEY,
    run_id   TEXT             NOT NULL,
    boot_id  TEXT             NOT NULL,
    at       DOUBLE PRECISION NOT NULL,
    kind     TEXT             NOT NULL,
    epoch    INTEGER,
    slug     TEXT,
    payload  JSONB            NOT NULL
);
CREATE INDEX IF NOT EXISTS bettor_incentive_journal_run_at
    ON bettor_incentive_journal (run_id, at);
"""

REQUIRED_COLUMNS = ("id", "run_id", "boot_id", "at", "kind", "epoch",
                    "slug", "payload")

# Rows are buffered and written in batches: the socket thread produces
# frames faster than a per-row round trip, and a writer that became the
# bottleneck would distort the very timing being measured.
FLUSH_EVERY_S = 2.0
FLUSH_AT_ROWS = 200
OUTBOX_MAX = 20_000


def _iso(t=None) -> str:
    return datetime.fromtimestamp(t if t is not None else time.time(),
                                  tz=timezone.utc).isoformat()


class PgJournal:
    """Append-only, in Postgres, batched. Thread-safe.

    The stream's callback runs on the socket thread and the loop runs
    on the event loop; both enqueue here, and only the loop flushes.
    """

    def __init__(self, pool, *, run_id: str, boot_id: str) -> None:
        self.pool = pool
        self.run_id = run_id
        self.boot_id = boot_id
        self._outbox: list = []
        self._lock = threading.Lock()
        self._last_flush = 0.0
        self.counts: dict = {}
        self.rows_written = 0
        self.rows_dropped = 0
        self.write_errors = 0
        self.boot_gap: dict | None = None

    async def _get_pool(self):
        if self.pool is not None:
            return self.pool
        from .db import get_pool
        return await get_pool()

    # ── lifecycle ────────────────────────────────────────────────────

    async def open(self) -> dict:
        """Create and VERIFY the schema, then record any boot gap.

        Safe under concurrency for the same reason `bettor_live_store`
        is: `CREATE TABLE IF NOT EXISTS` checks the catalog before it
        takes a lock, so twelve concurrent creators produce two
        successes and ten errors. One advisory lock, bounded retries,
        and a verification that accepts a complete schema however it
        got there.
        """
        try:
            pool = await self._get_pool()
        except Exception as exc:            # noqa: BLE001
            return {"ok": False, "why": "JOURNAL_NO_POOL",
                    "detail": type(exc).__name__}

        last_error = None
        for attempt in range(SCHEMA_ATTEMPTS):
            try:
                async with pool.acquire() as con:
                    async with con.transaction():
                        await con.execute("SET LOCAL lock_timeout = '%dms'"
                                          % SCHEMA_LOCK_TIMEOUT_MS)
                        await con.execute("SELECT pg_advisory_xact_lock($1)",
                                          SCHEMA_LOCK_KEY)
                        await con.execute(DDL)
            except Exception as exc:        # noqa: BLE001
                last_error = type(exc).__name__
                log.warning("bettor_incentive_journal: schema attempt "
                            "%d/%d failed (%s)", attempt + 1,
                            SCHEMA_ATTEMPTS, last_error)
            # VERIFY REGARDLESS -- the other party may have created it.
            try:
                cols = await pool.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = $1", TABLE)
                have = {r["column_name"] for r in cols}
                if set(REQUIRED_COLUMNS) <= have:
                    break
                last_error = "missing columns: %s" % sorted(
                    set(REQUIRED_COLUMNS) - have)
            except Exception as exc:        # noqa: BLE001
                last_error = type(exc).__name__
        else:
            return {"ok": False, "why": "JOURNAL_SCHEMA_UNAVAILABLE",
                    "detail": last_error}

        gap = await self._boot_gap(pool)
        return {"ok": True, "journal": JOURNAL_VERSION, "table": TABLE,
                "durable_across_redeploy": True,
                "destination": "postgres",
                "boot_gap": gap}

    async def _boot_gap(self, pool) -> dict | None:
        """The interval a process replacement left unobserved.

        Written BEFORE any record of this boot, so a reconstruction
        reading the run in order meets the gap before it meets the
        ladders that follow it. Absence of rows is what an unchanged
        book looks like on this feed; without this record the outage
        would be indistinguishable from a quiet market.
        """
        try:
            row = await pool.fetchrow(
                "SELECT max(at) AS last_at FROM " + TABLE +
                " WHERE run_id = $1 AND boot_id <> $2",
                self.run_id, self.boot_id)
        except Exception as exc:            # noqa: BLE001
            log.error("bettor_incentive_journal: could not read the "
                      "previous boot (%s)", type(exc).__name__)
            return None
        last_at = row["last_at"] if row else None
        if last_at is None:
            return None                     # first boot of this run
        now = time.time()
        self.boot_gap = {"event": BOOT_GAP, "why": PROCESS_REPLACED,
                         "from": float(last_at), "to": now,
                         "from_iso": _iso(float(last_at)), "to_iso": _iso(now),
                         "duration_s": round(now - float(last_at), 3),
                         "detail": "no observation exists in this interval; "
                                   "it is UNOBSERVED, not unchanged"}
        self.write(R_GAP, dict(self.boot_gap))
        await self.flush(force=True)
        log.warning("bettor_incentive_journal: BOOT GAP of %.1fs recorded "
                    "for run %s", self.boot_gap["duration_s"], self.run_id)
        return self.boot_gap

    # ── the one write ────────────────────────────────────────────────

    def write(self, kind: str, payload: dict) -> bool:
        """Enqueue one record. Never blocks the socket thread."""
        rec = (self.run_id, self.boot_id,
               float(payload.get("at") or time.time()), kind,
               _int_or_none(payload.get("epoch")), payload.get("slug"),
               json.dumps({"iso": _iso(), **payload}, default=str))
        with self._lock:
            if len(self._outbox) >= OUTBOX_MAX:
                # BOUNDED, AND THE LOSS IS COUNTED. A buffer that grows
                # without limit turns a slow database into an OOM, and
                # an OOM loses far more than the rows it refused.
                self.rows_dropped += 1
                return False
            self._outbox.append(rec)
            self.counts[kind] = self.counts.get(kind, 0) + 1
            return True

    async def flush(self, *, force: bool = False) -> int:
        now = time.time()
        with self._lock:
            if not self._outbox:
                return 0
            if not force and len(self._outbox) < FLUSH_AT_ROWS and \
                    now - self._last_flush < FLUSH_EVERY_S:
                return 0
            batch, self._outbox = self._outbox, []
            self._last_flush = now
        try:
            pool = await self._get_pool()
            await pool.executemany(
                "INSERT INTO " + TABLE +
                " (run_id, boot_id, at, kind, epoch, slug, payload)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)", batch)
            self.rows_written += len(batch)
            return len(batch)
        except Exception as exc:            # noqa: BLE001
            self.write_errors += 1
            # PUT THEM BACK. A failed flush is a retry, not a loss --
            # the bound above is what stops that becoming unbounded.
            with self._lock:
                self._outbox = batch + self._outbox
            log.error("bettor_incentive_journal: flush of %d rows failed "
                      "(%s); retained for retry", len(batch),
                      type(exc).__name__)
            return 0

    async def close(self) -> dict:
        for _ in range(3):
            if await self.flush(force=True) == 0:
                break
        with self._lock:
            unwritten = len(self._outbox)
        return {"journal": JOURNAL_VERSION, "table": TABLE,
                "destination": "postgres", "durable_across_redeploy": True,
                "counts": dict(self.counts), "rows_written": self.rows_written,
                "rows_unwritten_at_close": unwritten,
                "rows_dropped": self.rows_dropped,
                "write_errors": self.write_errors,
                "boot_gap": self.boot_gap}

    # ── the record kinds, named rather than stringly-typed ───────────

    def run_open(self, **kw) -> bool:
        return self.write(R_RUN_OPEN, dict(
            journal=JOURNAL_VERSION, boot_id=self.boot_id,
            durable_across_redeploy=True, destination="postgres",
            reconstruction_rule=RECONSTRUCTION_RULE,
            boot_gap=self.boot_gap, **kw))

    def run_close(self, **kw) -> bool:
        return self.write(R_RUN_CLOSE, kw)

    def ladder(self, classified: dict, book: dict) -> bool:
        return self.write(R_LADDER, {
            "slug": classified["slug"], "epoch": classified["epoch"],
            "ladder_class": classified["ladder_class"],
            "ladder_seq": classified["ladder_seq"],
            "at": classified.get("received_at"),
            # SOURCE PRECISION, NOT ROUNDED. The 1 Hz is a scoring
            # resample computed later; nothing is truncated here.
            "source_ts": classified.get("source_ts"),
            "venue_state": classified.get("venue_state"),
            "replacement": classified.get("replacement"),
            "bids": (book or {}).get("bids") or [],
            "offers": (book or {}).get("offers") or []})

    def epoch(self, *, epoch, event: str, **kw) -> bool:
        return self.write(R_EPOCH, {"epoch": epoch, "event": event, **kw})

    def gap(self, *, event: str, why: str, **kw) -> bool:
        return self.write(R_GAP, {"event": event, "why": why, **kw})

    def program_version(self, *, phase: str, programs: dict, **kw) -> bool:
        return self.write(R_PROGRAM, {"phase": phase, "programs": programs,
                                      **kw})

    def report(self) -> dict:
        with self._lock:
            pending = len(self._outbox)
        return {"journal": JOURNAL_VERSION, "table": TABLE,
                "destination": "postgres", "durable_across_redeploy": True,
                "counts": dict(self.counts), "rows_written": self.rows_written,
                "rows_pending": pending, "rows_dropped": self.rows_dropped,
                "write_errors": self.write_errors, "boot_gap": self.boot_gap}


def _int_or_none(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# ── reading it back ──────────────────────────────────────────────────

async def read_run(pool, run_id: str) -> list:
    """Every record of a run, in order, across every boot."""
    p = pool
    if p is None:
        from .db import get_pool
        p = await get_pool()
    rows = await p.fetch(
        "SELECT boot_id, at, kind, epoch, slug, payload FROM " + TABLE +
        " WHERE run_id = $1 ORDER BY at, id", run_id)
    out = []
    for r in rows:
        pay = r["payload"]
        out.append({"boot_id": r["boot_id"], "at": r["at"], "kind": r["kind"],
                    "epoch": r["epoch"], "slug": r["slug"],
                    "payload": json.loads(pay) if isinstance(pay, (str, bytes))
                    else pay})
    return out


def segments(records: list) -> list:
    """Contiguous OBSERVABLE segments, keyed on (boot_id, epoch).

    A reconstruction may forward-fill a ladder only WITHIN one of
    these. Every interval not covered by one is unobserved, and this
    function is what makes that mechanical rather than a matter of care.

    A SEGMENT DOES NOT END AT ITS LAST LADDER. That was the first
    version and it repeats, in the reconstruction, the very error the
    sampling side exists to avoid: on a change-driven feed a quiet book
    sends nothing, so ending the segment at the final frame would mark
    every quiet stretch before a disconnect as unobserved -- and quiet
    stretches are exactly what a qualifying-uptime measurement is made
    of. The segment runs until the CONNECTION stops being evidence:
    the last record of that boot before its next epoch begins. EPOCH
    and RUN_CLOSE records carry that instant, which is why they are
    journalled at all.
    """
    first: dict = {}
    ladders: dict = {}
    by_boot: dict = {}
    for r in records:
        by_boot.setdefault(r["boot_id"], []).append(r)
        if r["kind"] != R_LADDER:
            continue
        key = (r["boot_id"], r["epoch"])
        if key not in first:
            first[key] = r["at"]
            ladders[key] = {"n": 0, "slugs": set()}
        ladders[key]["n"] += 1
        ladders[key]["slugs"].add(r["slug"])

    out = []
    for (boot_id, epoch), start in first.items():
        # The next epoch of the SAME BOOT bounds this one; a later boot
        # never does, because a boot boundary is itself a gap.
        later = [s for (b, e), s in first.items()
                 if b == boot_id and s > start]
        next_start = min(later) if later else None
        end = start
        for r in by_boot.get(boot_id, []):
            if r["at"] < start:
                continue
            if next_start is not None and r["at"] >= next_start:
                continue
            end = max(end, r["at"])
        out.append({"boot_id": boot_id, "epoch": epoch,
                    "start": start, "end": end,
                    "ladders": ladders[(boot_id, epoch)]["n"],
                    "slugs": sorted(x for x in
                                    ladders[(boot_id, epoch)]["slugs"] if x),
                    "end_basis": ("the last record of this boot before its "
                                  "next epoch -- NOT the last ladder, "
                                  "because a quiet book sends nothing")})
    return sorted(out, key=lambda s: s["start"])


def covers(records: list, t: float) -> dict:
    """Is instant `t` observed? The answer a gap must be able to give.

    Returns the segment covering `t`, or a refusal naming why not. An
    instant inside a recorded GAP is UNOBSERVED even if ladders exist
    on both sides of it, and an instant between two boots is unobserved
    even though the bare epoch numbers may match.
    """
    for g in records:
        if g["kind"] != R_GAP:
            continue
        p = g["payload"]
        a, b = p.get("from"), p.get("to")
        if a is not None and b is not None and float(a) <= t < float(b):
            return {"observed": False, "why": p.get("why") or "GAP",
                    "gap": {"from": a, "to": b}}
    for s in segments(records):
        if s["start"] <= t <= s["end"]:
            return {"observed": True, "segment": s}
    return {"observed": False, "why": "NO_SEGMENT_COVERS_THIS_INSTANT"}


def describe() -> dict:
    return {
        "journal": JOURNAL_VERSION,
        "destination": "postgres table %s" % TABLE,
        "ephemeral_file_backend": "REMOVED -- /var/tmp does not survive a "
                                  "deploy, and a deploy is what a rollback "
                                  "performs",
        "records": [R_RUN_OPEN, R_PROGRAM, R_EPOCH, R_LADDER, R_GAP,
                    R_RUN_CLOSE],
        "one_row_per": "BOOK CHANGE, not per scoring instant",
        "reconstruction_key": "(boot_id, epoch) -- never epoch alone",
        "reconstruction_rule": RECONSTRUCTION_RULE,
        "boot_gap": "written before any record of a resumed boot",
    }
