"""What the run writes down, and why it is these records and not others.

ONE LADDER PER CHANGE, NOT ONE ROW PER SECOND. Persisting every 1 Hz
scoring instant for ten markets over a day is 864,000 rows carrying
the same unchanged book over and over. The feed is change-driven, so
the CHANGES are the information and the instants are derivable from
them -- but only if the derivation has everything it needs, which is
why this journal records four things rather than one:

  LADDER          every frame, classed INITIAL_LADDER (the first for a
                  market in a connection epoch) or UPDATE, with the
                  full bids/offers arrays, the venue's transactTime at
                  source precision, and the venue state.
  EPOCH           every connection epoch, opened and closed. Without
                  these a reconstruction cannot tell "the book did not
                  change" from "we were not connected", and those two
                  produce opposite answers.
  GAP             every transition into and out of an interval we
                  could not observe, with the reason.
  PROGRAM_VERSION the programme terms as captured at arm and at every
                  recheck. A reward computed against terms we never
                  recorded is not reproducible.

THE RECONSTRUCTION RULE, STATED HERE SO IT TRAVELS WITH THE DATA. A
scoring instant takes the last LADDER at or before it, PROVIDED that
ladder is in the same epoch as the instant and no GAP covers the
instant. Forward-filling is valid because the feed is change-driven
and silence within a healthy connected epoch means the book did not
change. It is NOT valid across an epoch boundary: the missed interval
cannot be replayed, and the first frame after a reconnect is an
INITIAL_LADDER precisely so a reconstruction can see where it may not
carry state across.

DURABILITY IS DECLARED, NOT ASSUMED. `open()` records whether the
directory is a declared persistent disk. On an ephemeral path the run
still writes -- and says, in the run record and in the log, that its
evidence dies at the next deploy.

Run:  python -m pytest backend/tests/test_bettor_incentive_journal.py
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

JOURNAL_VERSION = "BETTOR_INCENTIVE_JOURNAL_V1"

DIR_ENV = "BETTOR_INCENTIVE_DIR"
DISK_ENV = "BETTOR_INCENTIVE_DIR_DISK"

R_RUN_OPEN = "RUN_OPEN"
R_RUN_CLOSE = "RUN_CLOSE"
R_LADDER = "LADDER"
R_EPOCH = "EPOCH"
R_GAP = "GAP"
R_PROGRAM = "PROGRAM_VERSION"
R_NOTE = "NOTE"

# How often the file is flushed to the OS and fsynced. Every record is
# written immediately; fsync is batched because a per-record fsync on a
# change-driven feed is the one thing that could make the writer the
# bottleneck.
FSYNC_EVERY_S = 5.0


def _iso(t=None) -> str:
    return datetime.fromtimestamp(t if t is not None else time.time(),
                                  tz=timezone.utc).isoformat()


class Journal:
    """Append-only JSONL for one run. Thread-safe.

    The stream's callback runs on the socket thread and the loop runs
    on the event loop, so both write here and the lock is not
    decorative.
    """

    def __init__(self, directory: str, *, run_id: str,
                 disk_declared: bool | None = None) -> None:
        self.dir = directory
        self.run_id = run_id
        self.path = os.path.join(directory, "run-%s.jsonl" % run_id)
        self.disk_declared = (
            str(os.environ.get(DISK_ENV, "")).strip().lower()
            in ("1", "true", "yes") if disk_declared is None
            else bool(disk_declared))
        self._fh = None
        self._lock = threading.Lock()
        self._last_sync = 0.0
        self.counts: dict = {}
        self.bytes_written = 0
        self.write_errors = 0

    # ── lifecycle ────────────────────────────────────────────────────

    def open(self) -> dict:
        os.makedirs(self.dir, exist_ok=True)
        try:
            self._fh = open(self.path, "a", encoding="utf-8")
        except Exception as exc:            # noqa: BLE001
            return {"ok": False, "why": "JOURNAL_UNWRITABLE",
                    "detail": type(exc).__name__, "path": self.path}
        if not self.disk_declared:
            log.warning("bettor_incentive_journal: %s is NOT a declared "
                        "persistent disk; this run's evidence does not "
                        "survive a redeploy", self.dir)
        return {"ok": True, "path": self.path,
                "durable_across_redeploy": self.disk_declared,
                "journal": JOURNAL_VERSION}

    def close(self) -> dict:
        with self._lock:
            if self._fh is None:
                return {"closed": False, "why": "never opened"}
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._fh.close()
                ok = True
            except Exception as exc:        # noqa: BLE001
                log.error("bettor_incentive_journal: close failed (%s)",
                          type(exc).__name__)
                ok = False
            self._fh = None
        return {"closed": ok, "path": self.path, "counts": dict(self.counts),
                "bytes": self.bytes_written, "write_errors": self.write_errors}

    # ── the one write ────────────────────────────────────────────────

    def write(self, kind: str, payload: dict) -> bool:
        rec = {"t": time.time(), "iso": _iso(), "kind": kind,
               "run_id": self.run_id, **payload}
        line = json.dumps(rec, default=str, sort_keys=True) + "\n"
        with self._lock:
            if self._fh is None:
                self.write_errors += 1
                return False
            try:
                self._fh.write(line)
                self.bytes_written += len(line)
                self.counts[kind] = self.counts.get(kind, 0) + 1
                now = time.time()
                if now - self._last_sync >= FSYNC_EVERY_S:
                    self._fh.flush()
                    os.fsync(self._fh.fileno())
                    self._last_sync = now
                return True
            except Exception as exc:        # noqa: BLE001
                self.write_errors += 1
                log.error("bettor_incentive_journal: write failed (%s)",
                          type(exc).__name__)
                return False

    # ── the four record kinds, named rather than stringly-typed ──────

    def run_open(self, **kw) -> bool:
        return self.write(R_RUN_OPEN, dict(
            journal=JOURNAL_VERSION,
            durable_across_redeploy=self.disk_declared,
            reconstruction_rule=(
                "a scoring instant takes the last LADDER at or before it, "
                "IF that ladder is in the same EPOCH and no GAP covers the "
                "instant. Never carry a ladder across an epoch boundary."),
            **kw))

    def run_close(self, **kw) -> bool:
        return self.write(R_RUN_CLOSE, kw)

    def ladder(self, classified: dict, book: dict) -> bool:
        """The full ladder, both sides, with the venue's own clock."""
        return self.write(R_LADDER, {
            "slug": classified["slug"],
            "epoch": classified["epoch"],
            "ladder_class": classified["ladder_class"],
            "ladder_seq": classified["ladder_seq"],
            # SOURCE PRECISION, NOT ROUNDED. The 1 Hz is a scoring
            # resample computed later; nothing is truncated on the way
            # to disk.
            "source_ts": classified.get("source_ts"),
            "received_at": classified.get("received_at"),
            "venue_state": classified.get("venue_state"),
            "replacement": classified.get("replacement"),
            "bids": (book or {}).get("bids") or [],
            "offers": (book or {}).get("offers") or [],
        })

    def epoch(self, *, epoch, event: str, **kw) -> bool:
        return self.write(R_EPOCH, {"epoch": epoch, "event": event, **kw})

    def gap(self, *, event: str, why: str, **kw) -> bool:
        return self.write(R_GAP, {"event": event, "why": why, **kw})

    def program_version(self, *, phase: str, programs: dict, **kw) -> bool:
        """Terms as captured. `phase` is ARM or RECHECK."""
        return self.write(R_PROGRAM, {"phase": phase, "programs": programs,
                                      **kw})

    def report(self) -> dict:
        with self._lock:
            return {"journal": JOURNAL_VERSION, "path": self.path,
                    "counts": dict(self.counts), "bytes": self.bytes_written,
                    "write_errors": self.write_errors,
                    "durable_across_redeploy": self.disk_declared}


def directory_for(run_id: str) -> str:
    base = os.environ.get(DIR_ENV) or "/var/tmp/bettor-incentive"
    return os.path.join(base, run_id.replace(":", "_"))


def describe() -> dict:
    return {
        "journal": JOURNAL_VERSION,
        "records": [R_RUN_OPEN, R_PROGRAM, R_EPOCH, R_LADDER, R_GAP,
                    R_RUN_CLOSE],
        "one_row_per": "BOOK CHANGE, not per scoring instant",
        "instants_are": "reconstructed offline from LADDER + EPOCH + GAP",
        "never_carries_a_ladder_across_an_epoch": True,
    }
