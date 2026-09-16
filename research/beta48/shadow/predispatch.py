#!/usr/bin/env python3
"""THE PRE-DISPATCH CHECK. Printed before any venue request, and it can say NO.

WHY THIS EXISTS. Run 35120338223 was authorised, dispatched, and destroyed by a
rate limit nobody had measured. Every fact that would have predicted it -- the
untested request rate, the poll order that would decide which markets survived,
the futures in the sample, the absent event map -- was knowable BEFORE the first
GET. None of it was checked at the door.

So this is the door. It runs offline, reads the frozen design, and prints the
fields that decide whether the capture may go out. If any required field is
missing or fails, CAPTURE_DISPATCH_AUTHORIZED = NO and the caller stops.

A CHECK THAT CANNOT FAIL IS DECORATION. Each one here has a way to say no:

    the rate must be OPERATIONALLY VALIDATED, not merely a candidate that
      passed a short probe
    the sample must contain ZERO season-long futures
    poll-order fairness must be PROVED on the frozen rotation, not asserted
    event identity must already be resolved -- not "we will map it afterwards"

AND A CHECK THAT CANNOT PASS IS WORSE THAN DECORATION. An earlier version of
this gate demanded a rate confidence that could only be earned by first
identifying the venue's rate-limit mechanism -- which is undocumented and stays
NOT_IDENTIFIED. That is a deadlock, and a deadlock disguised as rigour is still
a broken gate. So the two questions are held apart here, and only one of them
gates:

    VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED   reported, expected to be
        NOT_IDENTIFIED forever, and explicitly NOT a required field. Nothing
        waits on it.
    COLLECTOR_RATE_OPERATIONALLY_VALIDATED  required, and it is YES or NO.
        rate_confirm.py is the route that makes it YES.

THIS MODULE CONTACTS NOTHING. It is the gate, not the trip.
"""
from __future__ import annotations

from decimal import Decimal as D

NOT_IDENTIFIED = "NOT_IDENTIFIED"

REQUIRED_FIELDS = (
    "SELECTED_REQUEST_RATE", "RATE_SELECTION_EVIDENCE",
    "COLLECTOR_RATE_OPERATIONALLY_VALIDATED",
    "TARGET_MARKETS", "TARGET_EVENTS", "SPORT_STRATA",
    "MAX_MARKETS_PER_EVENT", "FUTURES_IN_PRIMARY_SAMPLE",
    "POLL_ORDER_FAIRNESS_TEST", "EVENT_IDENTITY_COVERAGE",
)

# Reported beside the decision, never consulted by it.
REPORTED_NOT_GATING = ("VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED",)
MECHANISM_IDENTIFICATION_IS_NOT_REQUIRED = True
WHY_MECHANISM_DOES_NOT_GATE = (
    "the venue documents no limiter; requiring its identification would block "
    "every capture forever, which is a defect and not a safeguard")

# A rate that is merely a ladder CANDIDATE is not validated. Dispatching a
# substantive capture on a candidate is what the last run did.
REQUIRED_RATE_VALIDATION = "YES"
MIN_EVENT_IDENTITY_COVERAGE = D("1.0")     # every market in the primary sample
MAX_FUTURES_IN_PRIMARY_SAMPLE = 0


def check(design):
    """Decide whether the capture may be dispatched. Offline, and able to fail."""
    missing = [f for f in REQUIRED_FIELDS
               if design.get(f) in (None, NOT_IDENTIFIED)]

    cov = design.get("EVENT_IDENTITY_COVERAGE")
    cov_ok = False
    if cov not in (None, NOT_IDENTIFIED):
        try:
            cov_ok = D(str(cov)) >= MIN_EVENT_IDENTITY_COVERAGE
        except Exception:                                      # noqa: BLE001
            cov_ok = False

    checks = {
        "ALL_REQUIRED_FIELDS_PRESENT": not missing,
        "RATE_IS_OPERATIONALLY_VALIDATED_NOT_A_CANDIDATE": (
            design.get("COLLECTOR_RATE_OPERATIONALLY_VALIDATED")
            == REQUIRED_RATE_VALIDATION),
        "RATE_SELECTION_EVIDENCE_NAMED": bool(
            design.get("RATE_SELECTION_EVIDENCE") not in
            (None, NOT_IDENTIFIED, "")),
        "NO_FUTURES_IN_PRIMARY_SAMPLE": (
            design.get("FUTURES_IN_PRIMARY_SAMPLE") ==
            MAX_FUTURES_IN_PRIMARY_SAMPLE),
        "POLL_ORDER_FAIRNESS_PASSES": (
            design.get("POLL_ORDER_FAIRNESS_TEST") == "PASS"),
        "EVENT_IDENTITY_COMPLETE": cov_ok,
        "TARGET_EVENTS_IS_MORE_THAN_ONE": (
            isinstance(design.get("TARGET_EVENTS"), int)
            and design["TARGET_EVENTS"] > 1),
        "MAX_MARKETS_PER_EVENT_IS_BOUNDED": (
            isinstance(design.get("MAX_MARKETS_PER_EVENT"), int)
            and design["MAX_MARKETS_PER_EVENT"] >= 1),
    }
    ok = all(checks.values())
    out = {f: design.get(f, NOT_IDENTIFIED) for f in REQUIRED_FIELDS}
    out.update({f: design.get(f, NOT_IDENTIFIED) for f in REPORTED_NOT_GATING})
    out.update({
        "REPORTED_NOT_GATING": list(REPORTED_NOT_GATING),
        "MECHANISM_IDENTIFICATION_IS_NOT_REQUIRED":
            MECHANISM_IDENTIFICATION_IS_NOT_REQUIRED,
        "WHY_MECHANISM_DOES_NOT_GATE": WHY_MECHANISM_DOES_NOT_GATE,
        "CHECKS": checks,
        "FAILED_CHECKS": [k for k, v in checks.items() if not v],
        "MISSING_FIELDS": missing,
        "CAPTURE_DISPATCH_AUTHORIZED": "YES" if ok else "NO",
        "WHY": ("every required field is present and every check passed"
                if ok else
                "a capture dispatched on this design would repeat a known "
                "failure; fix the named checks first"),
        "THIS_MODULE_CONTACTS_NOTHING": True,
    })
    return out


def render(report):
    """The block that gets printed before the first request, ending in the
    authorisation line so it is the last thing a reader sees."""
    lines = []
    for f in REQUIRED_FIELDS:
        lines.append("%-38s = %s" % (f, report.get(f)))
    for f in REPORTED_NOT_GATING:
        lines.append("%-38s = %s  (reported, not gating)"
                     % (f, report.get(f)))
    if report["FAILED_CHECKS"]:
        lines.append("")
        for c in report["FAILED_CHECKS"]:
            lines.append("FAILED_CHECK                 = %s" % c)
    lines.append("")
    lines.append("CAPTURE_DISPATCH_AUTHORIZED  = %s"
                 % report["CAPTURE_DISPATCH_AUTHORIZED"])
    return "\n".join(lines)
