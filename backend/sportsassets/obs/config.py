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
    """Cap on SIMULTANEOUS NETWORK OPERATIONS. Not on pending observations.

    THIS CAP MEANT SOMETHING ELSE IN V1, AND THAT IS WHAT BROKE THE INSTRUMENT.
    collector.py::run held one semaphore slot per event for the event's whole
    60-second horizon, so the cap was really `events being tracked at once` and
    the collector's ceiling was 8 / 60 s = 0.133 events/s -- against an RN1-only
    arrival rate of 0.187 to 0.255/s. It failed by 1.4x to 1.9x even after the
    population was corrected, which is why the scheduler was replaced and not
    just the filter (owner decision 6).

    A slot is now held only while an HTTP request is actually executing. By
    Little's law the sustained requirement is arrival x duration: at the design
    rate of 3 reads/s and the measured p95 duration of 0.227 s that is 0.68
    concurrent, so 8 is roughly twelve times what the secondary channel needs.

    The "roughly 3 req/s" figure the old docstring cited here has been removed:
    `git log -S` traced it to run 83's own commits and nowhere else, and no 429
    against clob.polymarket.com is recorded anywhere in this repository. The
    limit is UNESTABLISHED and is reported as such rather than quoted as if
    measured.
    """
    try:
        return max(1, int(os.environ.get("RN1_OBSERVABILITY_MAX_INFLIGHT", "8")))
    except ValueError:
        return 8


def reads_per_second() -> float:
    """Pacing ceiling for the collector's own HTTP book reads.

    Governs the cache refresher and the secondary diagnostic channel. It does
    NOT govern the primary channel at all: a primary sample reads local state
    and makes no request, which is the whole of owner decision 2.
    """
    try:
        return max(0.1, float(os.environ.get("RN1_OBSERVABILITY_RPS", "2.0")))
    except ValueError:
        return 2.0


# ------------------------------------------------------- the local-state cache
# The primary channel samples a continuously maintained local cache. These
# govern how that cache is kept, and therefore the instrument's real
# short-horizon resolution: TWO OFFSETS CLOSER TOGETHER THAN THE REFRESH
# INTERVAL RETURN THE SAME STATE. That is a property of the mechanism, not a
# tuning parameter to be optimised against interim data, and it is why every
# sample carries cache_age_ms and the venue's book hash.

def cache_refresh_interval_s() -> float:
    """Target interval between refreshes of one HOT token."""
    try:
        return max(0.05, float(
            os.environ.get("RN1_OBSERVABILITY_CACHE_REFRESH_S", "0.25")))
    except ValueError:
        return 0.25


def cache_batch_size() -> int:
    """Tokens per POST /books request.

    The batch endpoint is what makes a maintained cache affordable: refreshing
    N tokens costs one request rather than N. Established from
    py-clob-client 0.34.6 -- GET_ORDER_BOOKS = "/books", posted a list of
    {"token_id": ...}. The batch SIZE the venue will accept is NOT documented
    anywhere reachable, so this starts small and is raised only on evidence.
    """
    try:
        return max(1, int(os.environ.get("RN1_OBSERVABILITY_CACHE_BATCH", "20")))
    except ValueError:
        return 20


def cache_hot_tokens() -> int:
    """How many tokens may be in the hot set at once."""
    try:
        return max(1, int(os.environ.get("RN1_OBSERVABILITY_HOT_TOKENS", "40")))
    except ValueError:
        return 40


def cache_hot_ttl_s() -> float:
    """How long a token stays hot after the last event that promoted it.

    Slightly over the 60 s horizon so a token stays warm for the whole of its
    own event's schedule and a little beyond.
    """
    try:
        return max(1.0, float(os.environ.get("RN1_OBSERVABILITY_HOT_TTL_S", "75")))
    except ValueError:
        return 75.0


def cache_stale_tolerance_s() -> float:
    """Beyond this age, local state is STALE_BEYOND_TOLERANCE, not a reading.

    A sample older than this is recorded with its age and marked invalid rather
    than being served as though it were the state at the scheduled instant.
    """
    try:
        return max(0.1, float(
            os.environ.get("RN1_OBSERVABILITY_STALE_TOLERANCE_S", "5.0")))
    except ValueError:
        return 5.0


def legacy_http_channel_enabled() -> bool:
    """Is the SECONDARY diagnostic HTTP channel collecting?

    Off by code default. The primary curve must never depend on REST capacity
    (owner decision 3), and the cheapest way to guarantee that is for the
    secondary channel to be absent unless somebody switches it on.
    """
    return _flag("RN1_OBSERVABILITY_LEGACY_HTTP", False)


COLLECTOR_VERSION = "rn1-obs/2"


def clob_base() -> str:
    """Read-only book endpoint.

    Defaulted to the same host copy_probe.py reads, because U2 -- the sealed
    population every historical run measured -- was built from that endpoint's
    ladders. Reading anywhere else would make the forward curve a different
    quantity from the one it is meant to be compared against.
    """
    return os.environ.get("CLOB_BASE_URL", "https://clob.polymarket.com").rstrip("/")
