#!/usr/bin/env python3
"""ORDER RECONCILIATION. Five sources, one truth, and a freeze on disagreement.

WHY THIS IS BUILT BEFORE THE FIRST LIVE ORDER RATHER THAN AFTER THE FIRST
SURPRISE. Every incident this desk has had on the live side was a reconciliation
failure wearing a different hat: a placement whose response was lost and whose
order filled anyway; two IOC takes two seconds apart that both filled while the
ledger booked one; a position the venue said we held and our ledger did not. In
each case the system kept trading while its own account of reality was wrong.

So the rule here is blunt: ANY unreconciled mismatch FREEZES further execution
for that position. Not a warning, not a retry -- a freeze that a later
reconciliation, or a human, has to clear.

THE FIVE SOURCES, and none of them is privileged by default:
    INTENT      what BETTOR decided to do
    ACK         what the venue said it accepted
    OPEN_ORDERS what the venue says is still working
    FILLS       what the venue says traded
    LEDGER      what BETTOR believes it holds

An absent source is NOT agreement. A missing acknowledgement is its own
mismatch class, because "we never heard back" and "it was rejected" are
different facts with different recoveries, and the expensive mistake is
treating the first as the second.

This module contacts nothing and can place no order.
"""
import json
from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"

SOURCES = ("INTENT", "ACK", "OPEN_ORDERS", "FILLS", "LEDGER")

MISMATCH_CLASSES = (
    "MISSING_ACKNOWLEDGEMENT",
    "PARTIAL_FILL",
    "OVERFILL",
    "UNEXPECTED_FILL",
    "DUPLICATE_FILL",
    "CANCEL_RACE",
    "LATE_FILL_AFTER_CANCEL_REQUEST",
    "POSITION_MISMATCH",
)

FREEZING_CLASSES = (
    "MISSING_ACKNOWLEDGEMENT",
    "OVERFILL",
    "UNEXPECTED_FILL",
    "DUPLICATE_FILL",
    "CANCEL_RACE",
    "LATE_FILL_AFTER_CANCEL_REQUEST",
    "POSITION_MISMATCH",
)
# PARTIAL_FILL is expected behaviour for a resting maker order, not a defect.
# It is reported and it does not freeze.

ABSENCE_IS_NOT_AGREEMENT = (
    "a source that did not answer has not confirmed anything; silence is a "
    "mismatch class of its own, not a quiet yes")
ANY_MISMATCH_FREEZES = (
    "a system whose account of its own position is wrong must stop acting on "
    "that position, not act more carefully")


def _d(x):
    if x in (None, NOT_IDENTIFIED, ""):
        return None
    try:
        return D(str(x))
    except Exception:                                         # noqa: BLE001
        return None


def reconcile(intent=None, ack=None, open_orders=None, fills=None,
              ledger=None, cancel_requested_at=None):
    """Compare the five sources for ONE order intent and classify every gap.

    `fills` is a list of {id, qty, price, at}. Duplicates are detected by fill
    id, because the same fill arriving twice through two delivery paths is the
    exact shape that once doubled a whale's position in our reading of it.
    """
    findings = []
    intent = intent or {}
    req = _d(intent.get("QUANTITY"))
    cid = intent.get("CLIENT_ORDER_ID", NOT_IDENTIFIED)

    present = {
        "INTENT": bool(intent),
        "ACK": ack is not None,
        "OPEN_ORDERS": open_orders is not None,
        "FILLS": fills is not None,
        "LEDGER": ledger is not None,
    }
    missing_sources = [s for s, ok in present.items() if not ok]

    if intent and ack is None:
        findings.append({
            "CLASS": "MISSING_ACKNOWLEDGEMENT",
            "DETAIL": ("an intent exists with no venue acknowledgement; "
                       "'never heard back' is not 'rejected', and the order "
                       "may be working or filled"),
            "CLIENT_ORDER_ID": cid})

    seen, dupes, total = set(), [], D("0")
    for f in (fills or ()):
        fid = f.get("id", NOT_IDENTIFIED)
        q = _d(f.get("qty")) or D("0")
        if fid in seen:
            dupes.append(fid)
        else:
            seen.add(fid)
            total += q
    if dupes:
        findings.append({"CLASS": "DUPLICATE_FILL",
                         "DETAIL": "the same fill id arrived more than once",
                         "FILL_IDS": dupes})

    acked = _d((ack or {}).get("ACKNOWLEDGED_SIZE"))
    if req is not None and acked is not None and acked > req:
        findings.append({"CLASS": "OVERFILL",
                         "DETAIL": "the venue acknowledged more than we asked",
                         "REQUESTED": str(req), "ACKNOWLEDGED": str(acked)})
    if req is not None and total > req:
        findings.append({"CLASS": "OVERFILL",
                         "DETAIL": "filled quantity exceeds the intent",
                         "REQUESTED": str(req), "FILLED": str(total)})
    elif req is not None and D("0") < total < req:
        findings.append({"CLASS": "PARTIAL_FILL",
                         "DETAIL": "expected for a resting maker order",
                         "REQUESTED": str(req), "FILLED": str(total),
                         "FREEZES": False})

    if not intent and fills:
        findings.append({"CLASS": "UNEXPECTED_FILL",
                         "DETAIL": ("fills exist with no intent of ours; a "
                                    "hand order or a foreign position")})

    if cancel_requested_at:
        late = [f for f in (fills or ())
                if f.get("at") and str(f["at"]) > str(cancel_requested_at)]
        if late:
            findings.append({
                "CLASS": "LATE_FILL_AFTER_CANCEL_REQUEST",
                "DETAIL": ("the venue filled after we asked to cancel; the "
                           "cancel lost the race and we own the result"),
                "FILLS": [f.get("id") for f in late]})
        still_open = [o for o in (open_orders or ())
                      if o.get("CLIENT_ORDER_ID") == cid]
        if still_open and not late:
            findings.append({
                "CLASS": "CANCEL_RACE",
                "DETAIL": ("cancel requested and the order is still working; "
                           "state is undecided until the venue settles it")})

    led = _d((ledger or {}).get("POSITION"))
    if led is not None and fills is not None and led != total:
        findings.append({"CLASS": "POSITION_MISMATCH",
                         "DETAIL": "our ledger and the venue's fills disagree",
                         "LEDGER": str(led), "VENUE_FILLS": str(total)})

    freezing = [f for f in findings
                if f["CLASS"] in FREEZING_CLASSES]
    reconciled = not findings
    return {
        "CLIENT_ORDER_ID": cid,
        "SOURCES_PRESENT": present,
        "SOURCES_MISSING": missing_sources,
        "ABSENCE_IS_NOT_AGREEMENT": ABSENCE_IS_NOT_AGREEMENT,
        "FINDINGS": findings,
        "MISMATCH_CLASSES_CHECKED": list(MISMATCH_CLASSES),
        "FREEZING_FINDINGS": [f["CLASS"] for f in freezing],
        "RECONCILED": reconciled,
        "EXECUTION_FROZEN": bool(freezing),
        "FURTHER_EXECUTION": ("FROZEN_UNTIL_RECONCILED" if freezing
                              else "PERMITTED_BY_RECONCILIATION_ONLY"),
        "ANY_MISMATCH_FREEZES": ANY_MISMATCH_FREEZES,
        "PARTIAL_FILL_IS_NOT_A_DEFECT": (
            "a resting maker order filling in pieces is the mechanism working"),
        "RECONCILIATION_IS_NOT_PERMISSION": (
            "a clean reconciliation says our books agree; it does not say an "
            "order may be sent"),
    }


def render(r):
    L = ["%-34s = %s" % ("CLIENT_ORDER_ID", r["CLIENT_ORDER_ID"]),
         "%-34s = %s" % ("RECONCILED", r["RECONCILED"]),
         "%-34s = %s" % ("EXECUTION_FROZEN", r["EXECUTION_FROZEN"]),
         "%-34s = %s" % ("FURTHER_EXECUTION", r["FURTHER_EXECUTION"])]
    for f in r["FINDINGS"]:
        L.append("    %-32s %s" % (f["CLASS"], f["DETAIL"]))
    return "\n".join(L)


def to_json(r):
    return json.dumps(r, indent=1, sort_keys=True, default=str)
