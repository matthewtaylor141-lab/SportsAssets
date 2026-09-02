"""ONE pacer for every MEASUREMENT read of the venue in this process
(position-mirroring review round two, 2026-09-02).

Two workers each pacing their own reads at 0.35 s do not bound the sum:
price_path (the ask at fixed offsets after a copy) and mirror_shadow
(one BBO per active market, one positions walk per tick) both run on
the copy path's venue client, and the venue 429'd a board walk above
~3 req/s. When their ticks overlap the venue saw ~5.7 req/s from
measurement alone, and the next request the limit refused was the copy
path's own quote or send.

This gate is process-wide: a call blocks until MIN_GAP_S has passed
since the last paced read ANYWHERE in the process, so however many
measurement loops exist their combined rate is one read per gap. The
money path (pre-trade quote, send, cancel, position check) does not
call it: it must not queue behind measurement.

Synchronous on purpose -- the reads it guards run in worker threads via
asyncio.to_thread, and the lock is held through the sleep so callers
form a queue instead of racing for the same gap.
"""
from __future__ import annotations

import threading
import time

MIN_GAP_S = 0.35
_lock = threading.Lock()
_last = 0.0


def pace(min_gap_s: float = MIN_GAP_S) -> float:
    """Block until `min_gap_s` has passed since the last paced read in
    this process, then claim the slot. Returns the seconds waited."""
    global _last
    with _lock:
        now = time.monotonic()
        wait = max(0.0, _last + float(min_gap_s) - now)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        return wait


__all__ = ["pace", "MIN_GAP_S"]
