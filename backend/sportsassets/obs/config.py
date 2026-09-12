"""Switches for the RN1 observability shadow -- run 83F.

OFF BY CODE DEFAULT. The collector does nothing at all unless
RN1_OBSERVABILITY_SHADOW is explicitly turned on, and turning it on enables
DATA COLLECTION ONLY. There is no switch in this file, or anywhere in this
package, that can cause an order: the package has no order path to enable.

The relationship to mirror_live is one-directional and worth stating plainly:
the collector runs happily while mirror_live=false, because observing costs
nothing and risks nothing. It never reads mirror_live to decide whether to act,
because it never acts.

INSTRUMENTATION FAILURE MUST NOT ENABLE TRADING. That is the twelfth acceptance
test, and this module's part in it is the absence of any coupling: no value here
is read by the trading path, so no failure here -- missing env, bad parse,
exception at import -- can change what the trading path does.
"""
from __future__ import annotations

import os

# The forward offsets, pre-registered in research/RUN83_PREREGISTRATION.md and
# frozen there BEFORE any observation is collected. They live here as data so
# the collector and the analysis read one definition; changing this tuple is an
# amendment to the pre-registration, not a configuration tweak.
OFFSETS: tuple[tuple[str, float], ...] = (
    ("0ms", 0.0),
    ("100ms", 0.100),
    ("250ms", 0.250),
    ("500ms", 0.500),
    ("1s", 1.0),
    ("2s", 2.0),
    ("5s", 5.0),
    ("10s", 10.0),
    ("30s", 30.0),
    ("60s", 60.0),
)

# A SAMPLE IS A READING AT t, NOT THE FIRST READING AFTER t.
#
# Carried from workers/price_path.py, which learned it in production: its first
# version took every overdue offset whenever the worker next ran, so a restart
# or a venue stall collapsed several offsets into one instant and produced a row
# of identical prices labelled with different times -- a flat curve by fiat,
# which is precisely what the instrument disclaims.
#
# An offset not read inside its window is NOT READ. It is recorded as
# MISSED_WINDOW with a reason, never backfilled from a neighbouring read.
#
# The tolerance is proportional for the long offsets and floored for the short
# ones: 20 ms of jitter is most of a 100 ms offset but nothing at all at 60 s.
WINDOW_FLOOR_S: float = 0.050
WINDOW_FRACTION: float = 0.20


def window_for(offset_s: float) -> float:
    """How late a reading may be and still count as a reading AT that offset."""
    return max(WINDOW_FLOOR_S, offset_s * WINDOW_FRACTION)


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def shadow_enabled() -> bool:
    """Is the observability collector switched on? Off unless explicitly set."""
    return _flag("RN1_OBSERVABILITY_SHADOW", False)


def max_inflight() -> int:
    """Cap on events being sampled concurrently.

    The collector shares a venue and an HTTP client with the rest of the process,
    and the venue has 429'd a board walk above roughly 3 req/s before. A cap here
    is the difference between an instrument and a second source of rate-limit
    incidents.
    """
    try:
        return max(1, int(os.environ.get("RN1_OBSERVABILITY_MAX_INFLIGHT", "8")))
    except ValueError:
        return 8


def reads_per_second() -> float:
    """Pacing ceiling for this collector's own book reads."""
    try:
        return max(0.1, float(os.environ.get("RN1_OBSERVABILITY_RPS", "2.0")))
    except ValueError:
        return 2.0


COLLECTOR_VERSION = "rn1-obs/1"
