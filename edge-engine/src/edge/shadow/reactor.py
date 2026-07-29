"""Event-driven reactor: turn book pushes into immediate re-evaluations.

The polling loop re-prices everything every EDGE_CYCLE_SECONDS. That is fine
for discovery and health, but it means a mispricing that appears one instant
after a sweep waits a full cycle before anyone looks at it — and on a venue
where good asks are taken in seconds, waiting is the same as not seeing it.

The reactor closes that gap. The venue's market-data WebSocket already pushes
every book change; a listener drops the changed slug into a dirty set and
wakes the main loop, which re-evaluates ONLY those slugs. Feed quotes, event
mappings, and books are all already cached, so a reaction costs dictionary
lookups and arithmetic — no network, no fuzzy matching.

Debounce exists because books move in bursts (a single trade rewrites several
levels). Waiting a beat after the first tick coalesces a burst into one pass
instead of five.

Fail-soft: no stream (no credentials, socket down) means no ticks, `take()`
just times out, and the loop degrades exactly to the old polling behaviour.
"""

from __future__ import annotations

import threading
import time

DEBOUNCE_S = 0.25        # coalesce a burst of level updates into one pass
MAX_BATCH = 500          # slugs re-priced per reaction (bounds a burst)


class Reactor:
    """Thread-safe dirty-slug queue between the stream thread and the loop."""

    def __init__(self, debounce_s: float = DEBOUNCE_S,
                 max_batch: int = MAX_BATCH) -> None:
        self.debounce_s = debounce_s
        self.max_batch = max_batch
        self._lock = threading.Lock()
        self._dirty: dict[str, float] = {}   # slug -> first-marked timestamp
        self._wake = threading.Event()
        self.ticks = 0            # book updates seen
        self.reactions = 0        # batches handed to the engine
        self.dropped = 0          # slugs shed by max_batch back-pressure
        self._latency_sum = 0.0
        self._latency_n = 0
        self._latency_max = 0.0

    # ── producer side (stream thread) ───────────────────────────────────

    def mark(self, slug: str) -> None:
        """Record a book change. Must stay cheap — it runs on the socket."""
        if not slug:
            return
        with self._lock:
            self.ticks += 1
            self._dirty.setdefault(slug, time.time())
        self._wake.set()

    # ── consumer side (engine loop) ─────────────────────────────────────

    def take(self, timeout: float) -> set[str]:
        """Block up to `timeout` for book activity, then return the changed
        slugs (empty set on timeout). After the first tick we wait one
        debounce interval so a burst arrives as a single batch."""
        if timeout <= 0:
            return set()
        if not self._wake.wait(timeout):
            return set()
        # A burst is still landing — let it finish before we re-price.
        time.sleep(min(self.debounce_s, max(timeout, 0.0)))
        now = time.time()
        with self._lock:
            self._wake.clear()
            if not self._dirty:
                return set()
            items = sorted(self._dirty.items(), key=lambda kv: kv[1])
            batch, rest = items[:self.max_batch], items[self.max_batch:]
            # Back-pressure: whatever we cannot take this pass stays dirty
            # (it is a book we still have not looked at), and we re-arm.
            self._dirty = dict(rest)
            if rest:
                self.dropped += len(rest)
                self._wake.set()
            self.reactions += 1
            for _slug, marked_at in batch:
                lat = now - marked_at
                self._latency_sum += lat
                self._latency_n += 1
                self._latency_max = max(self._latency_max, lat)
        return {slug for slug, _ in batch}

    # ── telemetry ───────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            avg_ms = (self._latency_sum / self._latency_n * 1000
                      if self._latency_n else 0.0)
            return {"ticks": self.ticks, "reactions": self.reactions,
                    "queued": len(self._dirty), "deferred": self.dropped,
                    "avg_latency_ms": round(avg_ms, 1),
                    "max_latency_ms": round(self._latency_max * 1000, 1)}

    def reset_window(self) -> None:
        """Zero the per-report counters (called after each status post) so
        the Engine tab shows this window's activity, not lifetime totals."""
        with self._lock:
            self.ticks = 0
            self.reactions = 0
            self.dropped = 0
            self._latency_sum = 0.0
            self._latency_n = 0
            self._latency_max = 0.0
