"""Stable identity for a planned observation -- run 83.2, owner decision 6.

WHY A SLOT NEEDS A NAME. In RUN83_ACTIVATION_FAILED_V1 a missing observation and
an observation that was never scheduled looked identical: both were an absent
row. 125,270 rows existed and 337 were measurements, and no query could
distinguish "the instrument tried and failed here" from "the instrument never
came here at all". Giving every planned slot an id, written once into
rn1_obs_plan before anything is attempted, makes absence a fact about a specific
known intention.

THE ID IS DERIVED, NOT ALLOCATED. It is a SHA-256 over the canonical tuple, so:

  - the same slot always has the same id, in any process, on any day;
  - a replay of the same event cannot mint a second identity for the same
    intention (the unique key in migration 063 then refuses the duplicate);
  - the id can be computed before the row is written, so the plan row and the
    result row can be written by different code paths without passing state.

preregistration_version IS PART OF THE TUPLE. The same event observed under an
amended preregistration is a DIFFERENT intention, not a duplicate of the old
one -- otherwise the unique key would refuse the new cohort's slot and the
amendment would be silently unenforceable.
"""
from __future__ import annotations

import hashlib

# The frozen version this build collects under. Changing this string is an
# amendment to research/RUN83_PREREGISTRATION_AMENDMENT_V2.md, not a tweak.
PREREGISTRATION_VERSION = "RUN83_PREREG_V2"

_SEP = "\x1f"          # unit separator: cannot occur in an id, a channel or an int


def slot_id(*, source_event_id: str, observation_channel: str,
            target_offset_ms: int,
            preregistration_version: str = PREREGISTRATION_VERSION) -> str:
    """The deterministic id for one planned observation.

    The separator is \\x1f rather than a printable character so that no
    combination of field values can produce the same joined string as a
    different combination -- a token id containing a '|' would otherwise be able
    to collide with a different (event, channel) pair.
    """
    if not source_event_id:
        raise ValueError("slot_id needs a source_event_id")
    if not observation_channel:
        raise ValueError("slot_id needs an observation_channel")
    canonical = _SEP.join((
        preregistration_version,
        source_event_id,
        observation_channel,
        str(int(target_offset_ms)),
    ))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
