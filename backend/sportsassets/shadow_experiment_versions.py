"""WHICH EXPERIMENT VERSIONS MAY STILL CREATE POSITIONS, AND WHY NOT.

Owner directive 2026-09-20, "STOP NEW X1 V1 POSITION CREATION":

    "Do not discard observations or decisions. X1 V1 may continue
    producing observational/model evidence if useful. But it should not
    create additional positions under an exit policy we already know is
    incomplete. Use a named prospective blocker:
    EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE. This is not a
    performance-based stop."

WHY THIS IS A SEPARATE MODULE AND NOT A FIELD ON THE DECLARATION.
`readiness` lives INSIDE the declaration, and the declaration is
hashed: X1 V1's hash 77c930248609d057 is stored on four production
positions and on every decision row it ever wrote. Flipping its
readiness to RETIRED would change that hash, and `verify_all()` would
then report X1 as an experiment whose rules were edited under a live
run -- which is precisely the alarm that must stay meaningful. The
frozen declaration says what the experiment IS; this module says what
the OPERATOR currently permits. Those are different facts and they are
stored in different places.

THE BLOCKER IS NOT ABOUT PERFORMANCE, and the distinction is load
bearing. X1 V1 lost money. That is not why it is blocked. It is
blocked because its frozen contract cannot complete a position
lifecycle -- a position opened under it can never reach
POSITION_CLOSED, no matter what the market does. A profitable
experiment with the same defect would be blocked on the same day for
the same reason, and the code below cannot even read a P&L.
"""

from __future__ import annotations

from . import shadow_experiments as xp

# ── the blockers, by name ────────────────────────────────────────────

EXIT_SEMANTICS_INCOMPLETE = "EXPERIMENT_VERSION_EXIT_SEMANTICS_INCOMPLETE"
AWAITING_REVIEW = "EXPERIMENT_VERSION_AWAITING_OWNER_REVIEW"
# Reviewed and rejected. Distinct from AWAITING_REVIEW because the
# answer is known: this version will never open a position, and saying
# so is different from saying nobody has looked yet.
NOT_APPROVED = "EXPERIMENT_VERSION_REVIEW_NOT_APPROVED"

PERMITTED = "POSITION_CREATION_PERMITTED"

# A blocker stops POSITIONS, never observations, decisions or markouts.
# §1: "Do not discard observations or decisions." The experimental tape
# keeps recording what the model would have decided; what stops is the
# creation of a position whose lifecycle the frozen contract cannot
# finish.
BLOCKS = "POSITION_CREATION_ONLY"

POSITION_CREATION_BLOCKED = {
    "X1_SHORT_HORIZON_DIRECTION": {
        "blocker": EXIT_SEMANTICS_INCOMPLETE,
        "since": "2026-09-20",
        "why": (
            "the frozen V1 contract answers WHEN, WHAT and WHAT PRICE "
            "but not HOW MUCH when the book is thin, and declares no "
            "tolerance for 'the book observed at that instant'. A "
            "position opened under it can never reach POSITION_CLOSED. "
            "Choosing those rules now would define realised economics "
            "after the first cohort's outcome is already known, so the "
            "version is closed to new positions instead"),
        "performanceBased": False,
        "supersededBy": "X1_SHORT_HORIZON_DIRECTION_V2",
    },
    "X1_SHORT_HORIZON_DIRECTION_V2": {
        "blocker": NOT_APPROVED,
        "since": "2026-09-20",
        "why": (
            "reviewed and rejected: its exit-delay bound was derived "
            "from the collector's 60s evidence trail rather than from "
            "the in-memory book the execution path reads, was "
            "contradicted by contemporaneous measurement of that same "
            "trail, was recomputed from replaceable telemetry at "
            "import, and its declared start preceded its own freeze by "
            "more than eight hours. Preserved unmutated; superseded"),
        "performanceBased": False,
        "supersededBy": "X1_SHORT_HORIZON_DIRECTION_V3",
    },
    "X1C_NULL_CONTROL_V2": {
        "blocker": NOT_APPROVED,
        "since": "2026-09-20",
        "why": "carries V2's exit contract; rejected with its candidate",
        "performanceBased": False,
        "supersededBy": "X1C_NULL_CONTROL_V3",
    },
    "X1_SHORT_HORIZON_DIRECTION_V3": {
        "blocker": NOT_APPROVED,
        "since": "2026-09-20",
        "why": (
            "reviewed and rejected: its timing contract conflated BOOK "
            "FRESHNESS with TARGET-TO-OBSERVATION DELAY. A book "
            "received 20s after the target whose own age is 1s is "
            "fresh and admissible, and V3's [TARGET, TARGET+5s] window "
            "would have refused it for a reason that was never about "
            "staleness. Preserved unmutated; superseded"),
        "performanceBased": False,
        "supersededBy": "X1_SHORT_HORIZON_DIRECTION_V4",
    },
    "X1C_NULL_CONTROL_V3": {
        "blocker": NOT_APPROVED,
        "since": "2026-09-20",
        "why": "carries V3's exit contract; rejected with its candidate",
        "performanceBased": False,
        "supersededBy": "X1C_NULL_CONTROL_V4",
    },
    "X1C_NULL_CONTROL": {
        "blocker": EXIT_SEMANTICS_INCOMPLETE,
        "since": "2026-09-20",
        "why": (
            "the control carries the SAME frozen exit rule as X1 V1 and "
            "has the same incomplete lifecycle. A control that kept "
            "opening positions after its candidate stopped would no "
            "longer be a control -- it would be a second experiment on "
            "a different population"),
        "performanceBased": False,
        "supersededBy": "X1C_NULL_CONTROL_V2",
    },
}


def position_creation(experiment_id: str, *, declaration=None) -> dict:
    """May this version open a NEW position right now, and if not, why.

    Checks the operator's list first, then the declaration's own
    lifecycle completeness. The second check is the backstop: a version
    added later whose exit semantics are incomplete is blocked even if
    nobody remembered to list it here.
    """
    listed = POSITION_CREATION_BLOCKED.get(experiment_id)
    if listed:
        return {"decision": listed["blocker"], "permitted": False,
                "blocks": BLOCKS, **listed}

    if declaration is not None:
        readiness = declaration.get("readiness")
        if readiness == xp.DECLARED_AWAITING_REVIEW:
            return {
                "decision": AWAITING_REVIEW, "permitted": False,
                "blocks": BLOCKS, "performanceBased": False,
                "why": ("the version is completely specified but has "
                        "not been reviewed; being fully specified and "
                        "being allowed to trade are different facts")}
        completeness = xp.lifecycle_completeness(declaration)
        if completeness != xp.LIFECYCLE_COMPLETE:
            # A VERSION THAT CANNOT CLOSE A POSITION MAY NOT OPEN ONE.
            # Derived from the declaration itself, so it holds for a
            # version nobody thought to add to the list above.
            return {
                "decision": EXIT_SEMANTICS_INCOMPLETE, "permitted": False,
                "blocks": BLOCKS, "performanceBased": False,
                "why": ("the declaration does not freeze a complete "
                        "exit contract, so a position opened under it "
                        "could never reach POSITION_CLOSED")}

    return {"decision": PERMITTED, "permitted": True}


def blocked_ids() -> tuple:
    return tuple(sorted(POSITION_CREATION_BLOCKED))


def report(experiments) -> dict:
    """What COMMAND shows about version lifecycle (§8)."""
    rows = []
    for e in experiments or ():
        verdict = position_creation(e["experimentId"], declaration=e)
        rows.append({
            "experimentId": e["experimentId"],
            "readiness": e.get("readiness"),
            "lifecycle": xp.lifecycle_completeness(e),
            "positionCreation": verdict["decision"],
            "newPositionsPermitted": verdict["permitted"],
            "performanceBased": verdict.get("performanceBased", False),
            "supersedes": e.get("supersedes"),
            "supersededBy": verdict.get("supersededBy"),
            "why": verdict.get("why"),
        })
    return {
        "versions": rows,
        "blockedFromNewPositions": [r["experimentId"] for r in rows
                                    if not r["newPositionsPermitted"]],
        "note": ("a blocker stops POSITION CREATION only; observations, "
                 "decisions and markouts continue to be recorded, and "
                 "existing positions keep their own history"),
        "noBlockerIsPerformanceBased": all(
            not r["performanceBased"] for r in rows),
    }
