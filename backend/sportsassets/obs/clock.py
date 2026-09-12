"""Two clock families, kept apart on purpose -- run 83B.

WHY THIS MODULE EXISTS. Run 82 found that nothing in the retained data can
measure the time between RN1's fill and BETTOR seeing it, because every such
boundary crosses from a clock we do not control to one we do, with no record of
the offset between them. The visible symptom was that 92.04% of the chain lane's
detected_at - ts differences are NEGATIVE -- an elapsed time cannot be negative,
so those numbers were never elapsed times. No later analysis can repair that.

So this module makes two rules mechanical rather than aspirational:

  1. A DURATION IS COMPUTED FROM MONOTONIC READINGS, NEVER FROM WALL CLOCKS.
     time.monotonic() cannot step, cannot go backwards, and is unaffected by NTP
     corrections. datetime.now() can do all three.

  2. A MONOTONIC READING IS MEANINGLESS OUTSIDE ITS PROCESS. Python's monotonic
     origin is arbitrary and moves on every restart, so subtracting two readings
     from different processes produces a plausible-looking number with no
     meaning. Every reading here carries PROCESS_BOOT_ID, and elapsed_between()
     REFUSES to subtract across a boundary rather than returning a wrong answer.

The wall clock is still recorded, on every reading, because it is what lets a row
be correlated with a venue log, a support ticket or another system. It is for
correlation and audit. It is not for arithmetic.
"""
from __future__ import annotations

import os
import platform
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

# Identifies THIS process instance. Generated once at import: every monotonic
# value produced here is comparable only against others carrying this same id.
PROCESS_BOOT_ID: str = str(uuid.uuid4())
PROCESS_IDENTITY: str = f"{socket.gethostname()}:{os.getpid()}"

# The wall clock at the moment the monotonic origin was captured. Together these
# two let a monotonic reading be placed approximately on the wall timeline --
# approximately, because the wall clock may have stepped since.
_BOOT_WALL: datetime = datetime.now(tz=timezone.utc)
_BOOT_MONOTONIC: float = time.monotonic()


class ClockDomainError(ValueError):
    """Raised when two readings from different processes would be subtracted."""


@dataclass(frozen=True)
class Instant:
    """One instant, read in both families at once.

    Both readings are taken as close together as the interpreter allows, and the
    monotonic one is taken FIRST so that any delay between them inflates the wall
    reading rather than the durations that matter.
    """

    monotonic: float
    wall: datetime
    process_boot_id: str = PROCESS_BOOT_ID
    process_identity: str = PROCESS_IDENTITY

    def __sub__(self, other: "Instant") -> float:
        return elapsed_between(other, self)


def now() -> Instant:
    """The current instant in both clock families."""
    mono = time.monotonic()
    wall = datetime.now(tz=timezone.utc)
    return Instant(monotonic=mono, wall=wall)


def elapsed_between(start: Instant, end: Instant) -> float:
    """Seconds from start to end.

    RAISES rather than guesses when the two readings come from different process
    instances. That is the entire point: a silent wrong answer here is exactly
    the failure this module exists to prevent, and a caller that hits this
    exception has found a real defect in its own bookkeeping.
    """
    if start.process_boot_id != end.process_boot_id:
        raise ClockDomainError(
            "refusing to subtract monotonic readings across process instances "
            f"({start.process_boot_id} vs {end.process_boot_id}); the monotonic "
            "origin moves on restart, so the difference would have no meaning"
        )
    return end.monotonic - start.monotonic


def since(start: Instant) -> float:
    """Seconds elapsed since `start`, measured monotonically."""
    return elapsed_between(start, now())


# ---------------------------------------------------------------- provenance
# A timestamp travels with the name of the clock that produced it. The absence
# of exactly this is why chain.py's silent wall-clock fallback is invisible in
# every row it ever wrote.

class ClockDomain:
    """Names for the clock that produced a value. Not an enum, so an unexpected
    lane cannot be silently coerced into a known one."""

    SOURCE_CHAIN = "C1_SOURCE_CHAIN"        # Polygon block timestamp
    SOURCE_VENUE = "C4_SOURCE_VENUE"        # a venue's own server time
    BETTOR_WALL = "C2_BETTOR_WALL"          # our process wall clock
    BETTOR_MONOTONIC = "C2_BETTOR_MONOTONIC"  # our process monotonic clock
    POSTGRES = "C3_POSTGRES"                # the database server's now()
    UNKNOWN = "UNKNOWN"


class TimestampStatus:
    SUPPLIED = "SUPPLIED"
    MISSING = "MISSING"
    FALLBACK_SUBSTITUTED = "FALLBACK_SUBSTITUTED"


@dataclass(frozen=True)
class SourceTimestamp:
    """A source-supplied timestamp, or an honest record that there wasn't one.

    THE INVARIANT, enforced in __post_init__ and again by a CHECK constraint in
    migration 062: MISSING carries no value, and no value means MISSING. There is
    no path through this class that produces a BETTOR clock reading wearing a
    source timestamp's name.
    """

    value: datetime | None
    provenance: str
    clock_domain: str
    status: str = TimestampStatus.SUPPLIED
    fallback: bool = False
    sync_status: str | None = None

    def __post_init__(self) -> None:
        if self.status not in (TimestampStatus.SUPPLIED, TimestampStatus.MISSING,
                               TimestampStatus.FALLBACK_SUBSTITUTED):
            raise ValueError(f"unknown source timestamp status: {self.status!r}")
        if (self.status == TimestampStatus.MISSING) != (self.value is None):
            raise ValueError(
                "source timestamp status and value disagree: MISSING must carry "
                "None and None must be declared MISSING -- a substituted local "
                "clock must be declared FALLBACK_SUBSTITUTED, never passed off "
                "as SUPPLIED"
            )
        if self.status == TimestampStatus.FALLBACK_SUBSTITUTED and not self.fallback:
            raise ValueError("FALLBACK_SUBSTITUTED must set fallback=True")

    @classmethod
    def missing(cls, provenance: str, clock_domain: str = ClockDomain.UNKNOWN
                ) -> "SourceTimestamp":
        """The source gave us nothing. Say so; do not substitute our own clock."""
        return cls(value=None, provenance=provenance, clock_domain=clock_domain,
                   status=TimestampStatus.MISSING)


def boot_reference() -> dict:
    """What this process's monotonic origin corresponds to on the wall clock."""
    return {
        "process_boot_id": PROCESS_BOOT_ID,
        "process_identity": PROCESS_IDENTITY,
        "boot_wall": _BOOT_WALL,
        "boot_monotonic": _BOOT_MONOTONIC,
        "platform": platform.platform(),
        "monotonic_resolution_s": time.get_clock_info("monotonic").resolution,
        "wall_resolution_s": time.get_clock_info("time").resolution,
        "monotonic_is_monotonic": time.get_clock_info("monotonic").monotonic,
        "wall_is_adjustable": time.get_clock_info("time").adjustable,
    }
