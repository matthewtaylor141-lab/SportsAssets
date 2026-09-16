#!/usr/bin/env python3
"""HARVEST A SEALED SINGLE-RATE CONFIRMATION. Offline. Contacts nothing.

This reads a confirmation's sealed rows off disk and re-derives the verdict from
the timestamps the run actually recorded, rather than trusting the duration the
run computed about itself.

WHY THAT DISTINCTION EARNS ITS KEEP -- THE 300/1200 OFF-BY-ONE.

At 0.25 rps the interval is 4 s. The pacer does not wait before the FIRST
request (its `last` starts at 0.0 while the monotonic clock is seconds-since-
boot, so the first gap is already in the past). So N requests span N-1
intervals, not N:

    300 requests  ->  299 x 4 s  =  1196 s        NOT 1200 s

`dispatch_check` planned the duration as N / rate = 1200.0 and passed. The
pacing semantics are (N-1) / rate. A run can therefore issue every request it
promised and still fall four seconds short of the support floor it was
dispatched against.

The floor is enforced LITERALLY here. 1196 is not 1200. A run that succeeded on
all 300 requests and elapsed 1196 s returns:

    COLLECTOR_RATE_OPERATIONALLY_VALIDATED = NO
    FAIL_REASON = DURATION_SUPPORT_FLOOR_NOT_MET

That is not pedantry about four seconds. The floor exists because a clean
twenty-minute window is the evidence; accepting a nineteen-minute-fifty-six
window because it "obviously would have" held is how a frozen criterion becomes
a criterion that moves once someone wants a particular answer.

THREE PROPERTIES, THREE VERDICTS, NEVER COLLAPSED.

    POLL_ORDER_FAIRNESS      from the ISSUED ORDERING ALONE -- cycle leads,
                             attempts, position-in-cycle. Reads no outcome at
                             all, by construction.
    SUCCESS_COVERAGE_BALANCED from OUTCOMES -- successes/attempts, 429s, other
                             failures, per market.
    POLL_ORDER_STARVATION    whether failures CONCENTRATED past what the whole
                             run is permitted in total.

A market can be scheduled perfectly fairly and still read badly because of venue
behaviour, and every market can read well while the ordering stays structurally
privileged. Reporting one of these as if it were another hides exactly the
failure mode that destroyed run 35120338223.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rate_confirm as RC                                      # noqa: E402

NOT_IDENTIFIED = "NOT_IDENTIFIED"
THIS_MODULE_CONTACTS_NOTHING = True

# RECEIPT_UTC is stamped to the second, so an elapsed span built from receipts
# carries up to a second of truncation at each end. Reported, never hidden.
TIMESTAMP_RESOLUTION_S = 1.0

DURATION_FAIL_REASON = "DURATION_SUPPORT_FLOOR_NOT_MET"
REQUESTS_FAIL_REASON = "REQUEST_SUPPORT_FLOOR_NOT_MET"

# What the pacer actually does, as opposed to what dispatch_check assumed.
PACING_SEMANTICS = "N_REQUESTS_SPAN_N_MINUS_1_INTERVALS"
FIRST_REQUEST_IS_NOT_DELAYED = True


def planned_elapsed_s(requests, rps):
    """(N-1)/rate -- the span N paced requests actually occupy."""
    return (int(requests) - 1) / float(rps)


def requests_for_duration(rps, duration_s):
    """How many requests it takes to OCCUPY duration_s at this rate."""
    return 1 + int(-(-float(duration_s) * float(rps) // 1))      # ceil


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:                                          # noqa: BLE001
        return None


def elapsed_from_rows(rows):
    """FIRST_REQUEST_TIME / LAST_REQUEST_COMPLETION_TIME from the rows."""
    stamps = [(_parse(r.get("RECEIPT_UTC")), r) for r in rows]
    stamps = [(t, r) for t, r in stamps if t is not None]
    if not stamps:
        return {"FIRST_REQUEST_TIME": NOT_IDENTIFIED,
                "LAST_REQUEST_COMPLETION_TIME": NOT_IDENTIFIED,
                "ACTUAL_ELAPSED_DURATION_S": NOT_IDENTIFIED}
    stamps.sort(key=lambda p: p[0])
    first_t, _ = stamps[0]
    last_t, last_r = stamps[-1]
    lat = float(last_r.get("LATENCY_S") or 0.0)
    span = (last_t - first_t).total_seconds() + lat
    return {
        "FIRST_REQUEST_TIME": first_t.isoformat(),
        "LAST_REQUEST_ISSUE_TIME": last_t.isoformat(),
        "LAST_REQUEST_COMPLETION_TIME": (
            last_t.isoformat() + " +%.3fs latency" % lat),
        "ACTUAL_ELAPSED_DURATION_S": span,
        "TIMESTAMP_RESOLUTION_S": TIMESTAMP_RESOLUTION_S,
        "ELAPSED_UNCERTAINTY_S": TIMESTAMP_RESOLUTION_S,
    }


def order_fairness(rows, slugs=None):
    """POLL_ORDER_FAIRNESS from the ISSUED ORDERING ALONE.

    Nothing in this function may read a status, a latency or an outcome. The
    question is whether the schedule privileged a market, which is true or
    false before the venue answers anything.
    """
    ordered = sorted(rows, key=lambda r: r.get("SEQ", 0))
    order = [r["slug"] for r in ordered]
    slugs = list(slugs or sorted(set(order)))
    n = len(slugs)
    if not n or not order:
        return {"POLL_ORDER_FAIRNESS": NOT_IDENTIFIED}

    leads = {s: 0 for s in slugs}
    attempts = {s: 0 for s in slugs}
    positions = {s: [0] * n for s in slugs}
    for i, s in enumerate(order):
        attempts[s] = attempts.get(s, 0) + 1
        pos = i % n
        if s in positions:
            positions[s][pos] += 1
        if pos == 0:
            leads[s] = leads.get(s, 0) + 1

    lead_spread = max(leads.values()) - min(leads.values())
    att_spread = max(attempts.values()) - min(attempts.values())
    pos_spread = max(max(v) - min(v) for v in positions.values())
    ok = (lead_spread <= RC.MAX_CYCLE_LEAD_IMBALANCE
          and att_spread <= RC.MAX_CYCLE_LEAD_IMBALANCE
          and pos_spread <= RC.MAX_CYCLE_LEAD_IMBALANCE)
    return {
        "ROTATION": RC.ROTATION,
        "CYCLE_LEAD_COUNTS": dict(sorted(leads.items())),
        "CYCLE_LEAD_IMBALANCE": lead_spread,
        "ATTEMPT_COUNTS": dict(sorted(attempts.items())),
        "ATTEMPT_IMBALANCE": att_spread,
        "POSITION_IN_CYCLE_COUNTS": {k: v for k, v in sorted(positions.items())},
        "POSITION_IN_CYCLE_IMBALANCE": pos_spread,
        "POLL_ORDER_FAIRNESS": "PASS" if ok else "FAIL",
        "FAIRNESS_READS_OUTCOMES": False,
    }


def success_coverage(rows):
    """SUCCESS_COVERAGE_BALANCED from OUTCOMES ALONE.

    Separate from fairness on purpose: a perfectly fair rotation can still come
    back with one market reading badly, and that is a fact about the venue, not
    about our scheduler.
    """
    per = {}
    for r in rows:
        d = per.setdefault(r["slug"], {"ATTEMPTS": 0, "SUCCESSES": 0,
                                       "HTTP_429": 0, "OTHER_FAILURES": 0})
        d["ATTEMPTS"] += 1
        st = r.get("status")
        if st == 200:
            d["SUCCESSES"] += 1
        elif st == 429:
            d["HTTP_429"] += 1
        else:
            d["OTHER_FAILURES"] += 1
    if not per:
        return {"SUCCESS_COVERAGE_BALANCED": NOT_IDENTIFIED, "PER_MARKET": {}}
    shares = {s: (D(d["SUCCESSES"]) / D(d["ATTEMPTS"]) if d["ATTEMPTS"] else D(0))
              for s, d in per.items()}
    lo, hi = min(shares.values()), max(shares.values())
    total = sum(d["ATTEMPTS"] for d in per.values())
    starv = RC._starvation(per, total)
    for s, d in per.items():
        d["SUCCESS_SHARE"] = str(shares[s])
    return {
        "PER_MARKET": {k: per[k] for k in sorted(per)},
        "PER_MARKET_ATTEMPTS": {k: per[k]["ATTEMPTS"] for k in sorted(per)},
        "PER_MARKET_SUCCESSES": {k: per[k]["SUCCESSES"] for k in sorted(per)},
        "PER_MARKET_429": {k: per[k]["HTTP_429"] for k in sorted(per)},
        "PER_MARKET_OTHER_FAILURES": {k: per[k]["OTHER_FAILURES"]
                                      for k in sorted(per)},
        "MAX_SUCCESS_SHARE_BY_MARKET": hi,
        "MIN_SUCCESS_SHARE_BY_MARKET": lo,
        "SUCCESS_SHARE_SPREAD": hi - lo,
        "SUCCESS_COVERAGE_BALANCED": ("YES" if lo >= RC.starvation_floor(
            min(d["ATTEMPTS"] for d in per.values()), total) else "NO"),
        "POLL_ORDER_STARVATION": starv["POLL_ORDER_STARVATION"],
        "STARVED_MARKETS": starv["STARVED_MARKETS"],
        "STARVATION_FLOOR": starv.get("STARVATION_FLOOR", NOT_IDENTIFIED),
        "THESE_ARE_DIFFERENT_PROPERTIES": (
            "fairness is a property of the schedule; coverage is a property of "
            "the answers; starvation is whether failures concentrated"),
    }


def harvest(outdir, rps=None):
    """The frozen field list, re-derived from the sealed evidence."""
    out = Path(outdir)
    sealed = json.loads((out / "confirm_report.json").read_text())
    rows = [json.loads(l) for l in
            (out / "confirm_rows.jsonl").read_text().splitlines() if l.strip()]
    rps = float(rps or sealed["RATE_RPS"])

    req = len(rows)
    ok = sum(1 for r in rows if r.get("status") == 200)
    n429 = sum(1 for r in rows if r.get("status") == 429)
    other = req - ok - n429
    lat = [float(r.get("LATENCY_S") or 0.0) for r in rows]

    timing = elapsed_from_rows(rows)
    actual = timing["ACTUAL_ELAPSED_DURATION_S"]
    need_dur = RC.min_duration_s(rps)
    need_req = RC.MIN_REQUESTS

    # Enforced literally. 1196 is not 1200, and it is not rounded to 1200.
    reqs_ok = req >= need_req
    dur_ok = (actual != NOT_IDENTIFIED and float(actual) >= need_dur)

    fair = order_fairness(rows)
    cover = success_coverage(rows)

    # successes after the last refusal, from the issued order
    seq = sorted(rows, key=lambda r: r.get("SEQ", 0))
    after = 0
    for r in seq:
        if r.get("status") == 429:
            after = 0
        elif r.get("status") == 200:
            after += 1

    reasons = []
    if not reqs_ok:
        reasons.append(REQUESTS_FAIL_REASON)
    if not dur_ok:
        reasons.append(DURATION_FAIL_REASON)
    if n429 > RC.MAX_ALLOWED_429:
        reasons.append("REFUSALS_EXCEED_FROZEN_ALLOWANCE")
    if cover.get("POLL_ORDER_STARVATION") == "YES":
        reasons.append("POLL_ORDER_STARVATION")
    if fair.get("POLL_ORDER_FAIRNESS") == "FAIL":
        reasons.append("POLL_ORDER_NOT_FAIR")
    if other and D(other) / D(req) > RC.MAX_OTHER_FAILURE_SHARE:
        reasons.append("OTHER_FAILURES_EXCEED_TOLERANCE")
    if n429 and not after:
        reasons.append("READS_DID_NOT_RESUME_AFTER_THE_REFUSAL")

    validated = not reasons
    report = {
        "REQUESTS": req,
        "SUCCESSES": ok,
        "HTTP_429": n429,
        "OTHER_FAILURES": other,
        "HTTP_429_RATE": (D(n429) / D(req)) if req else NOT_IDENTIFIED,
        "P50_LATENCY": RC.RP._pct(lat, 50),
        "P90_LATENCY": RC.RP._pct(lat, 90),
        "P99_LATENCY": RC.RP._pct(lat, 99),
        "VALID_RETRY_AFTER_COUNT": sealed.get("RETRY_AFTER_COUNT",
                                              NOT_IDENTIFIED),
        "RETRY_AFTER_OBSERVED": sealed.get("RETRY_AFTER_OBSERVED", []),
        "GLOBAL_BACKOFF_EVENTS": sealed.get("GLOBAL_BACKOFF_EVENTS",
                                            NOT_IDENTIFIED),
        "SUCCESSES_AFTER_LAST_429": after,

        # timing, re-derived rather than trusted
        "RUN_REPORTED_DURATION_S": sealed.get("DURATION_S", NOT_IDENTIFIED),
        "PACING_SEMANTICS": PACING_SEMANTICS,
        "FIRST_REQUEST_IS_NOT_DELAYED": FIRST_REQUEST_IS_NOT_DELAYED,
        "PLANNED_ELAPSED_BY_PACING_S": planned_elapsed_s(req, rps),
        "MIN_REQUESTS_REQUIRED": need_req,
        "MIN_DURATION_S_REQUIRED": need_dur,
        "REQUESTS_MEET_FLOOR": reqs_ok,
        "DURATION_MEETS_FLOOR": dur_ok,
        "DURATION_WAS_NOT_ROUNDED": True,
        "REQUESTS_NEEDED_FOR_THE_DURATION_FLOOR":
            requests_for_duration(rps, need_dur),

        "COLLECTOR_RATE_OPERATIONALLY_VALIDATED": ("YES" if validated else "NO"),
        "FAIL_REASON": (reasons if reasons else None),
        "PROPOSED_SUBSTANTIVE_CAPTURE_RATE": (
            "%s RPS" % sealed["RATE_RPS"] if validated else NOT_IDENTIFIED),
        "VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED": NOT_IDENTIFIED,
        "MECHANISM_DOES_NOT_GATE_THIS": True,
        "REFUSED_LABELS": list(RC.REFUSED_LABELS),
        "FASTER_RATES": "UNTESTED_IN_THIS_RUN",
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
        "ORDERS_PLACED": 0,
        "CREDENTIALS": "NONE",
        "mirror_live": False,
    }
    report.update(timing)
    report.update(fair)
    report.update(cover)
    return report


def render(r):
    """The frozen field list, ending on the two lines that are the decision."""
    L = []
    for k in ("REQUESTS", "ACTUAL_ELAPSED_DURATION_S", "SUCCESSES", "HTTP_429",
              "OTHER_FAILURES", "HTTP_429_RATE", "P50_LATENCY", "P90_LATENCY",
              "P99_LATENCY", "VALID_RETRY_AFTER_COUNT", "GLOBAL_BACKOFF_EVENTS",
              "SUCCESSES_AFTER_LAST_429"):
        L.append("%-34s = %s" % (k, r.get(k)))
    L.append("")
    for slug, d in r.get("PER_MARKET", {}).items():
        L.append("%-44s att %-4s ok %-4s 429 %-3s other %-3s share %s"
                 % (slug, d["ATTEMPTS"], d["SUCCESSES"], d["HTTP_429"],
                    d["OTHER_FAILURES"], d["SUCCESS_SHARE"]))
    L.append("")
    for k in ("POLL_ORDER_FAIRNESS", "SUCCESS_COVERAGE_BALANCED",
              "POLL_ORDER_STARVATION"):
        L.append("%-34s = %s" % (k, r.get(k)))
    if r.get("FAIL_REASON"):
        L.append("")
        for f in r["FAIL_REASON"]:
            L.append("%-34s = %s" % ("FAIL_REASON", f))
    L.append("")
    L.append("%-34s = %s" % ("VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED",
                             r["VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED"]))
    L.append("%-34s = %s" % ("COLLECTOR_RATE_OPERATIONALLY_VALIDATED",
                             r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"]))
    L.append("%-34s = %s" % ("PROPOSED_SUBSTANTIVE_CAPTURE_RATE",
                             r["PROPOSED_SUBSTANTIVE_CAPTURE_RATE"]))
    return "\n".join(L)


def _cli():                                                   # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--rate", default=None)
    a = ap.parse_args()
    print(render(harvest(a.dir, a.rate)))


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
