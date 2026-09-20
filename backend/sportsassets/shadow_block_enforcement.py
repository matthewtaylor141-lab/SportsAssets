"""THE TWO ROWS THE DEPLOY TRANSITION LEFT, AND THE EPOCH THAT REPLACED
A GUESS.

Owner directive 2026-09-20, review of the V1 blocker:

    §1 "Classify the exact two rows as DEPLOY_TRANSITION_CONTAMINATION.
    They remain part of X1C_V1_ALL_RECORDED_EVIDENCE but they must be
    excluded from X1_VS_X1C_PAIRED_COMPARISON because X1 was no longer
    capable of creating corresponding positions... Never silently
    subtract the two rows from the raw ledger."

    §2 "Do not define correctness as 'no positions more than N seconds
    after blocking began.' That creates a grace interval in which
    leakage is implicitly accepted."

THE SECOND CORRECTION IS AGAINST MY OWN WORK AND IT IS RIGHT. The
alarm I wrote counted positions written more than 120 seconds after
blocking began. That number was chosen to be longer than a restart,
which is a guess dressed as a standard: it silently declares that a
leak inside 120s is acceptable, and the next deploy that takes 130s to
roll would have been reported as a fresh defect while a genuine
121-second leak would have looked identical. A window cannot tell
those apart because it is not measuring the thing that matters.

WHAT REPLACES IT. The question is not "how long after blocking began"
but "was the guarded code actually running". That has an observable
answer: the worker records its own boot, and the boot that first ran
the guarded revision is a FACT, not an interval. Everything written
from that instant onward was written by code that contained the
guard. So:

    BLOCK_ENFORCEMENT_EPOCH = the observed boot of the first worker
                              revision containing the version guard

    POSITIONS_WRITTEN_WHILE_BLOCKED_AFTER_EPOCH = 0, permanently

No grace interval exists. A single position written after the epoch
while its version is blocked is a defect in the guard, at one second
or at one day.

THE TWO KNOWN ROWS ARE EXACT HISTORICAL EXCEPTIONS, enumerated by
position id below. They are not a timing allowance and nothing else
inherits their exemption: a third row cannot become acceptable by
resembling them.
"""

from __future__ import annotations

DEPLOY_TRANSITION_CONTAMINATION = "DEPLOY_TRANSITION_CONTAMINATION"

# ── §1: the exact rows, named ────────────────────────────────────────
#
# Identified by position id, never by a time range. A range would
# re-admit anything that happened to fall inside it; a list of ids
# admits exactly these and nothing else, forever.
#
# READ FROM PRODUCTION AND FROZEN HERE (research section 28g, run
# 2026-09-20T14:24Z). Both are X1C. The first was written in the SAME
# SECOND as the guard's first refusal and the second 0.4s later, which
# is what a worker finishing its tick on the pre-guard revision looks
# like and is not something a guarded process can produce: one call
# site reaches open_position and it sits behind an unconditional early
# return on the version verdict.

CONTAMINATED_POSITION_IDS: tuple = (
    # asc-lg1-olm-psg-2026-09-20-fh-pos-2pt5, 13:11:49Z, $131.15
    "xpos_0a785213419d56d34d00e5cc33a7fd6565aa",
    # asc-lal-vil-lev-2026-09-20-neg-2pt5, 13:11:50Z, $1,000.00
    "xpos_b9ed542546c29a66b2aeb385971b68a79ff5",
)

# The ledger arithmetic these two account for, stated so the two
# figures can never drift apart unnoticed:
#     ALL   47 positions  $18,296.97
#   − these   2 positions  $ 1,131.15   (131.15 + 1000.00)
#   = CLEAN  45 positions  $17,165.82
CONTAMINATED_NOTIONAL_USD = 1131.15
X1C_ALL_POSITIONS = 47
X1C_ALL_ENTRY_NOTIONAL_USD = 18296.97
X1C_CLEAN_PAIRED_POSITIONS = 45
X1C_CLEAN_PAIRED_ENTRY_NOTIONAL_USD = 17165.82

CONTAMINATION_RECORD = {
    "classification": DEPLOY_TRANSITION_CONTAMINATION,
    "experimentId": "X1C_NULL_CONTROL",
    "positionIds": list(CONTAMINATED_POSITION_IDS),
    "count": 2,
    "notionalUsd": 1131.15,
    "markets": ["asc-lg1-olm-psg-2026-09-20-fh-pos-2pt5",
                "asc-lal-vil-lev-2026-09-20-neg-2pt5"],
    "writtenAt": ["2026-09-20T13:11:49Z", "2026-09-20T13:11:50Z"],
    "guardFirstRefusedAt": "2026-09-20T13:11:49Z",
    "why": (
        "written by a worker process that had not yet restarted onto "
        "the revision containing the version guard. One call site "
        "reaches open_position and it sits behind an unconditional "
        "early return on the guard, so no code path in the guarded "
        "revision can produce these rows"),
    "whyExcludedFromPairing": (
        "X1 was already blocked from creating positions when these "
        "were written, so no X1 position exists that could correspond "
        "to them. A paired comparison that included them would compare "
        "a control entry against a candidate entry that the frozen "
        "contract had already forbidden"),
    "remainsPartOf": "X1C_V1_ALL_RECORDED_EVIDENCE",
    "excludedFrom": "X1_VS_X1C_PAIRED_COMPARISON",
    "isNotATimingAllowance": (
        "these two ids are exact historical exceptions. They are not a "
        "grace interval and nothing else inherits their exemption"),
}


# ── §2: the epoch, observed rather than assumed ──────────────────────

EPOCH_NOT_ESTABLISHED = "BLOCK_ENFORCEMENT_EPOCH_NOT_ESTABLISHED"

# The revision that first carried the version guard. Recorded as a
# literal so that "was the guard running" is answered by a commit, not
# by a duration.
GUARD_REVISION = "c95e3f2"
GUARD_REVISION_SUBJECT = (
    "Close X1 V1 to new positions and freeze a complete successor "
    "contract")


def enforcement_epoch(worker_boot_at=None) -> dict:
    """When the guard began actually running, from an observed boot.

    Returns NOT_ESTABLISHED rather than a guess when no boot has been
    observed. A missing epoch must never default to "now" or to the
    first refusal: both would silently exempt whatever came before.
    """
    if worker_boot_at is None:
        return {
            "BLOCK_ENFORCEMENT_EPOCH": EPOCH_NOT_ESTABLISHED,
            "established": False,
            "guardRevision": GUARD_REVISION,
            "why": ("no worker boot carrying the guard revision has "
                    "been observed yet; the epoch is not inferred from "
                    "the first refusal, because a refusal proves the "
                    "code was running by then and says nothing about "
                    "when it started"),
        }
    return {
        "BLOCK_ENFORCEMENT_EPOCH": worker_boot_at,
        "established": True,
        "guardRevision": GUARD_REVISION,
        "rule": ("POSITIONS_WRITTEN_WHILE_BLOCKED_AFTER_EPOCH = 0, "
                 "permanently. No grace interval."),
        "historicalExceptions": list(CONTAMINATED_POSITION_IDS),
        "why": ("everything written from this instant was written by "
                "code containing the guard, so a position written "
                "while its version is blocked is a defect in the "
                "guard -- at one second or at one day"),
    }


def leak_verdict(*, epoch, positions_after_epoch,
                 contaminated_ids=None) -> dict:
    """Did anything leak, excluding only the named historical rows."""
    known = set(contaminated_ids or CONTAMINATED_POSITION_IDS)
    leaked = [p for p in (positions_after_epoch or ()) if p not in known]
    established = epoch not in (None, EPOCH_NOT_ESTABLISHED)
    return {
        "BLOCK_ENFORCEMENT_EPOCH": epoch,
        "epochEstablished": established,
        "POST_EPOCH_BLOCK_LEAKS": len(leaked),
        "leakedPositionIds": leaked,
        "clean": established and not leaked,
        "why": (None if established else
                "no epoch established, so no leak claim can be made "
                "in either direction"),
    }
