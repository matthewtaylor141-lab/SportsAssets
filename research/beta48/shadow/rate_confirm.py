#!/usr/bin/env python3
"""SINGLE-RATE OPERATIONAL CONFIRMATION. One rate. One run. A reachable YES.

WHAT THIS ANSWERS, AND WHAT IT DELIBERATELY DOES NOT.

    COLLECTOR_RATE_OPERATIONALLY_VALIDATED = YES | NO
        Did OUR collector, paced at ONE rate, run long enough and clean enough
        that we are willing to point it at a capture window? Measurable, and
        answerable today.

    VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED = NOT_IDENTIFIED
        What the venue's limiter actually is. Undocumented. Not answered here,
        not answered anywhere, and -- this is the point -- NOT A PRECONDITION.

The previous gate required both. It would have waited forever, because the
second question has no available evidence. Separating them is the whole reason
this module exists.

WHY A SINGLE RATE AND NOT ANOTHER LADDER. A ladder is a screen: sequential
rungs against an undocumented accounting window, where a rung may pass on
credit left by a slower one or fail on debt left by a faster one. No amount of
rungs fixes that. A confirmation is a different instrument -- one rate, in a
fresh process, with no faster traffic ahead of it in the same workflow, run
past its own evidence floor. Then the only thing the result can be about is
that rate.

    ONE RATE. ONE FRESH WORKFLOW. ONE GLOBAL PACER. NO CONCURRENCY.
    NO PRIOR HIGHER-RATE RUNGS IN THE SAME WORKFLOW.

THE EVIDENCE FLOOR IS FROZEN BEFORE THE RUN, AND SO IS THE THRESHOLD.

    MIN_REQUESTS      300
    MIN_DURATION_S    max(900, 300 / rate) -- both floors, not either. At
                      0.25 rps, 300 requests TAKE 1,200 s, so the duration
                      floor is what 300 paced requests actually cost; at a
                      fast rate the 900 s floor keeps a burst from passing in
                      three minutes.
    TARGET_429_RATE   ZERO, with one narrowly defined allowance defined HERE,
                      before any result is seen.

A confirmation that cannot reach its own floor is refused at the door rather
than run and then reinterpreted: see `dispatch_check`.

WHAT THIS MAY NEVER BE LABELLED. VENUE_MAXIMUM_SAFE_RATE. VENUE_RATE_LIMIT_KNOWN.
RATE_LIMIT_WINDOW_IDENTIFIED. A clean run at one rate says our collector was
not refused at that rate for that period. It says nothing about the ceiling,
and calling it a ceiling is the SHARES_TRADED error in a new field.

NO ORDERS. NO CREDENTIALS. GET ONLY.
"""
from __future__ import annotations

import json
import sys
import time
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rate_pilot as RP                                         # noqa: E402

NOT_IDENTIFIED = "NOT_IDENTIFIED"

READ_ONLY = True
ORDER_PATH_EXISTS = False
CREDENTIAL_PATH = "NONE"
mirror_live = False

HOST = RP.HOST

THIS_IS = "SINGLE_RATE_OPERATIONAL_CONFIRMATION"
THIS_IS_NOT = "VENUE_RATE_LIMIT_IDENTIFICATION"

# Two questions, held apart. The first never blocks the second.
VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED = NOT_IDENTIFIED
RATE_LIMIT_WINDOW_SEMANTICS = NOT_IDENTIFIED
OPERATIONAL_VALIDATION_DOES_NOT_REQUIRE_MECHANISM_IDENTIFICATION = True

# Labels this module refuses to emit whatever the result looks like.
REFUSED_LABELS = ("VENUE_MAXIMUM_SAFE_RATE", "VENUE_RATE_LIMIT_KNOWN",
                  "RATE_LIMIT_WINDOW_IDENTIFIED", "MAXIMUM_SUSTAINABLE_RATE")
WHY_REFUSED = ("a clean run at one rate bounds our own behaviour, not the "
               "venue's ceiling; no rate above the tested one was tested")

# --------------------------------------------------------------------------
# FROZEN BEFORE THE RUN. Changing any of these after seeing a result is the
# thing the freeze exists to prevent.
# --------------------------------------------------------------------------
MIN_REQUESTS = 300
MIN_DURATION_FLOOR_S = 900.0
REQUESTS_FOR_DURATION_FLOOR = 300.0

TARGET_429_RATE = D("0")
# The allowance. ONE refusal is tolerated, and only when it is isolated: the
# venue asked us to wait, we waited globally, and nothing followed. A second
# 429 is not a blip -- it is a pattern with two points, and it fails.
MAX_ALLOWED_429 = 1
ALLOWANCE_CONDITIONS = (
    "AT_MOST_ONE_429_IN_THE_WHOLE_RUN",
    "RETRY_AFTER_PRESENT_AND_HONOURED_GLOBALLY",
    "NO_SECOND_429_AFTER_THE_BACKOFF",
    "NOT_INSIDE_THE_FINAL_TENTH_OF_THE_RUN",
)
WHY_THE_FINAL_TENTH = ("a refusal arriving at the end is the start of a trend "
                       "the run stopped before showing")
THRESHOLD_FROZEN_BEFORE_THE_RUN = True

# Fairness floors: a rate is not validated if it works by starving markets.
MIN_SUCCESS_SHARE_BY_MARKET_FLOOR = D("0.98")
MAX_OTHER_FAILURE_SHARE = D("0.01")

OPERATIONAL_CRITERIA = (
    "REQUESTS", "DURATION_S", "SUCCESSES", "HTTP_429", "OTHER_FAILURES",
    "HTTP_429_RATE", "P50_LATENCY", "P90_LATENCY", "P99_LATENCY",
    "RETRY_AFTER_OBSERVED", "GLOBAL_BACKOFF_EVENTS", "PER_MARKET_ATTEMPTS",
    "PER_MARKET_SUCCESSES", "MAX_SUCCESS_SHARE_BY_MARKET",
    "MIN_SUCCESS_SHARE_BY_MARKET", "POLL_ORDER_STARVATION",
)


def min_duration_s(rps):
    """max(900, 300 / rate). BOTH floors -- the binding one wins.

    At 0.25 rps the request floor costs 1,200 s, so duration binds. At 2 rps
    300 requests take 150 s, so the 900 s clock binds instead and a fast burst
    cannot buy a confirmation in three minutes.
    """
    r = float(rps)
    if r <= 0:
        raise ValueError("rate must be positive")
    return max(MIN_DURATION_FLOOR_S, REQUESTS_FOR_DURATION_FLOOR / r)


def min_requests(rps):
    """Requests needed: the fixed floor, or whatever the clock implies if more.

    At a fast rate the 900 s floor implies far more than 300 requests, and
    running 300 of them and then sitting idle would satisfy neither floor
    honestly.
    """
    return max(MIN_REQUESTS, int(round(min_duration_s(rps) * float(rps))))


def dispatch_check(rps, planned_requests, timeout_s=None):
    """Refuse, at the door, a confirmation that cannot meet its own floor.

    Running it anyway and then explaining why 180 requests were enough is the
    failure mode this exists to make impossible.
    """
    need_req = min_requests(rps)
    need_dur = min_duration_s(rps)
    plan_dur = float(planned_requests) / float(rps)
    checks = {
        "PLANNED_REQUESTS_MEET_FLOOR": planned_requests >= need_req,
        "PLANNED_DURATION_MEETS_FLOOR": plan_dur >= need_dur,
        "JOB_TIMEOUT_COVERS_THE_RUN": (
            timeout_s is None or float(timeout_s) >= plan_dur),
    }
    ok = all(checks.values())
    return {
        "RATE_RPS": str(rps),
        "PLANNED_REQUESTS": planned_requests,
        "PLANNED_DURATION_S": plan_dur,
        "MIN_REQUESTS_REQUIRED": need_req,
        "MIN_DURATION_S_REQUIRED": need_dur,
        "JOB_TIMEOUT_S": timeout_s if timeout_s is not None else NOT_IDENTIFIED,
        "CHECKS": checks,
        "FAILED_CHECKS": [k for k, v in checks.items() if not v],
        "CONFIRMATION_MAY_DISPATCH": "YES" if ok else "NO",
        "WHY": ("the planned run clears its own evidence floor" if ok else
                "a confirmation that cannot reach its floor would produce a "
                "result we would have to reinterpret afterwards"),
    }


def _starvation(per_market):
    """Did the rate work by quietly starving some markets?

    The previous capture "succeeded" at 2 rps in exactly this way: six markets
    were read and eighteen were not, and the difference was poll order.
    """
    shares = {}
    for slug, d in per_market.items():
        att = d["ATTEMPTS"]
        shares[slug] = (D(d["SUCCESSES"]) / D(att)) if att else D(0)
    if not shares:
        return {"MAX_SUCCESS_SHARE_BY_MARKET": NOT_IDENTIFIED,
                "MIN_SUCCESS_SHARE_BY_MARKET": NOT_IDENTIFIED,
                "POLL_ORDER_STARVATION": NOT_IDENTIFIED,
                "STARVED_MARKETS": []}
    lo, hi = min(shares.values()), max(shares.values())
    starved = sorted(s for s, v in shares.items()
                     if v < MIN_SUCCESS_SHARE_BY_MARKET_FLOOR)
    return {
        "SUCCESS_SHARE_BY_MARKET": {k: str(v) for k, v in sorted(shares.items())},
        "MAX_SUCCESS_SHARE_BY_MARKET": hi,
        "MIN_SUCCESS_SHARE_BY_MARKET": lo,
        "POLL_ORDER_STARVATION": ("NONE" if not starved else "PRESENT"),
        "STARVED_MARKETS": starved,
    }


def validate(result):
    """COLLECTOR_RATE_OPERATIONALLY_VALIDATED, from the frozen criteria only.

    Every check can fail, and none of them is "did we identify the limiter".
    """
    rps = float(result["RATE_RPS"])
    n429 = result["HTTP_429"]
    dur = result["DURATION_S"]
    req = result["REQUESTS"]
    other_share = (D(result["OTHER_FAILURES"]) / D(req)) if req else D(1)

    allowance_ok = True
    allowance_why = None
    if n429:
        if n429 > MAX_ALLOWED_429:
            allowance_ok, allowance_why = False, (
                "%d refusals: past one, this is a pattern, not a blip" % n429)
        elif not result.get("RETRY_AFTER_OBSERVED"):
            allowance_ok, allowance_why = False, (
                "the one refusal carried no Retry-After we could honour")
        elif result.get("LAST_429_AT_S") is not None and dur and (
                float(result["LAST_429_AT_S"]) > 0.9 * float(dur)):
            allowance_ok, allowance_why = False, (
                "the refusal landed in the final tenth: %s" % WHY_THE_FINAL_TENTH)

    checks = {
        "REQUESTS_MEET_FLOOR": req >= min_requests(rps),
        "DURATION_MEETS_FLOOR": dur >= min_duration_s(rps),
        "REFUSALS_WITHIN_FROZEN_ALLOWANCE": allowance_ok,
        "OTHER_FAILURES_WITHIN_TOLERANCE": other_share <= MAX_OTHER_FAILURE_SHARE,
        "NO_POLL_ORDER_STARVATION": result.get("POLL_ORDER_STARVATION") == "NONE",
        "EVERY_MARKET_READ_ABOVE_FLOOR": (
            result.get("MIN_SUCCESS_SHARE_BY_MARKET") not in
            (None, NOT_IDENTIFIED)
            and result["MIN_SUCCESS_SHARE_BY_MARKET"]
            >= MIN_SUCCESS_SHARE_BY_MARKET_FLOOR),
        "SINGLE_RATE_RUN": result.get("RATES_IN_THIS_RUN") == 1,
        "NO_PRIOR_HIGHER_RATE_IN_THIS_WORKFLOW": bool(
            result.get("NO_PRIOR_HIGHER_RATE_IN_THIS_WORKFLOW")),
    }
    ok = all(checks.values())
    return {
        "THIS_IS": THIS_IS,
        "THIS_IS_NOT": THIS_IS_NOT,
        "COLLECTOR_RATE_OPERATIONALLY_VALIDATED": ("YES" if ok else "NO"),
        "VALIDATED_RATE_RPS": (result["RATE_RPS"] if ok else NOT_IDENTIFIED),
        "VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED":
            VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED,
        "RATE_LIMIT_WINDOW_SEMANTICS": RATE_LIMIT_WINDOW_SEMANTICS,
        "OPERATIONAL_VALIDATION_DOES_NOT_REQUIRE_MECHANISM_IDENTIFICATION":
            OPERATIONAL_VALIDATION_DOES_NOT_REQUIRE_MECHANISM_IDENTIFICATION,
        "REFUSED_LABELS": list(REFUSED_LABELS),
        "WHY_THOSE_LABELS_ARE_REFUSED": WHY_REFUSED,
        "FASTER_RATES": "UNTESTED_IN_THIS_RUN",
        "CHECKS": checks,
        "FAILED_CHECKS": [k for k, v in checks.items() if not v],
        "ALLOWANCE_FAILURE_REASON": allowance_why,
        "TARGET_429_RATE": TARGET_429_RATE,
        "MAX_ALLOWED_429": MAX_ALLOWED_429,
        "ALLOWANCE_CONDITIONS": list(ALLOWANCE_CONDITIONS),
        "THRESHOLD_FROZEN_BEFORE_THE_RUN": THRESHOLD_FROZEN_BEFORE_THE_RUN,
        "MIN_REQUESTS_REQUIRED": min_requests(rps),
        "MIN_DURATION_S_REQUIRED": min_duration_s(rps),
        "WHAT_THIS_VALIDATES": (
            "our paced collector at %s rps over this period" % result["RATE_RPS"]),
        "WHAT_THIS_DOES_NOT_VALIDATE": (
            "any faster rate, any other time of day, and the venue's limiter"),
    }


def confirm(outdir, slugs, http, rps, requests=None, no_prior_higher_rate=True):
    """Run ONE rate to its floor, rotating fairly, and report the criteria."""
    rps_f = float(rps)
    requests = int(requests or min_requests(rps_f))
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    pacer = RP.GlobalPacer(rps_f)
    per_market = {s: {"ATTEMPTS": 0, "SUCCESSES": 0, "HTTP_429": 0,
                      "OTHER_FAILURES": 0} for s in slugs}
    rows, lat, backoffs = [], [], []
    ok = n429 = other = 0
    retry_after_seen = []
    first_429 = last_429 = None
    t0 = time.monotonic()
    for i in range(requests):
        slug = slugs[i % len(slugs)]
        r = RP.probe(http, pacer, slug)
        rows.append(dict(r, RATE=str(rps)))
        lat.append(r["LATENCY_S"])
        m = per_market[slug]
        m["ATTEMPTS"] += 1
        if r["status"] == 200:
            ok += 1
            m["SUCCESSES"] += 1
        elif r["status"] == 429:
            n429 += 1
            m["HTTP_429"] += 1
            at = time.monotonic() - t0
            first_429 = at if first_429 is None else first_429
            last_429 = at
            ra = r["HEADERS"].get("retry-after")
            if ra is not None:
                retry_after_seen.append(str(ra))
            backoffs.append(pacer.back_off(ra))
        else:
            other += 1
            m["OTHER_FAILURES"] += 1
    dur = time.monotonic() - t0

    with (out / "confirm_rows.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")

    result = {
        "RATE_RPS": str(rps),
        "RATES_IN_THIS_RUN": 1,
        "NO_PRIOR_HIGHER_RATE_IN_THIS_WORKFLOW": bool(no_prior_higher_rate),
        "REQUESTS": requests,
        "DURATION_S": dur,
        "SUCCESSES": ok,
        "SUCCESSFUL_REQUESTS": ok,
        "HTTP_429": n429,
        "HTTP_429_COUNT": n429,
        "OTHER_FAILURES": other,
        "HTTP_429_RATE": (D(n429) / D(requests)) if requests else NOT_IDENTIFIED,
        "P50_LATENCY": RP._pct(lat, 50),
        "P90_LATENCY": RP._pct(lat, 90),
        "P99_LATENCY": RP._pct(lat, 99),
        "RETRY_AFTER_OBSERVED": sorted(set(retry_after_seen)),
        "GLOBAL_BACKOFF_EVENTS": len(backoffs),
        "BACKOFF_SOURCES": sorted({b["BACKOFF_SOURCE"] for b in backoffs}),
        "FIRST_429_AT_S": first_429 if first_429 is not None else NOT_IDENTIFIED,
        "LAST_429_AT_S": last_429,
        "PER_MARKET_ATTEMPTS": {s: d["ATTEMPTS"]
                                for s, d in sorted(per_market.items())},
        "PER_MARKET_SUCCESSES": {s: d["SUCCESSES"]
                                 for s, d in sorted(per_market.items())},
        "PER_MARKET_429": {s: d["HTTP_429"] for s, d in sorted(per_market.items())},
        "SCHEDULER": "SINGLE_GLOBAL_PACER_NO_CONCURRENCY",
        "BACKOFF": "GLOBAL_ON_429_HONOURING_RETRY_AFTER_WHEN_PRESENT",
        "PER_MARKET_RETRY_LOOP": "NONE_BY_CONSTRUCTION",
        "OPERATIONAL_CRITERIA": list(OPERATIONAL_CRITERIA),
        "ORDERS_PLACED": 0,
        "CREDENTIALS": CREDENTIAL_PATH,
        "mirror_live": mirror_live,
    }
    result.update(_starvation(per_market))
    result.update(validate(result))
    with (out / "confirm_report.json").open("w") as fh:
        json.dump(result, fh, indent=1, sort_keys=True, default=str)
    return result


def render(report):
    """The operational block, ending on the one line that is the decision."""
    lines = []
    for k in OPERATIONAL_CRITERIA:
        v = report.get(k, NOT_IDENTIFIED)
        if isinstance(v, dict):
            v = ", ".join("%s=%s" % kv for kv in sorted(v.items()))
        lines.append("%-30s = %s" % (k, v))
    lines.append("")
    lines.append("%-30s = %s" % ("VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED",
                                 report["VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED"]))
    for c in report["FAILED_CHECKS"]:
        lines.append("%-30s = %s" % ("FAILED_CHECK", c))
    lines.append("")
    lines.append("%-30s = %s" % ("COLLECTOR_RATE_OPERATIONALLY_VALIDATED",
                                 report["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"]))
    return "\n".join(lines)


def _cli():                                                   # pragma: no cover
    import argparse
    import httpx
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rate", required=True)
    ap.add_argument("--markets", type=int, default=6)
    ap.add_argument("--requests", type=int, default=0)
    ap.add_argument("--timeout-s", type=float, default=None)
    a = ap.parse_args()
    rps = float(a.rate)
    req = a.requests or min_requests(rps)

    gate = dispatch_check(rps, req, a.timeout_s)
    print(json.dumps(gate, indent=1, sort_keys=True, default=str))
    if gate["CONFIRMATION_MAY_DISPATCH"] != "YES":
        raise SystemExit("CONFIRMATION_MAY_DISPATCH = NO")

    u = json.loads(Path(a.universe).read_text())
    slugs = [m["slug"] for m in u["MARKETS"]][:a.markets]
    with httpx.Client(headers={"accept": "application/json"}) as http:
        rep = confirm(a.out, slugs, http, rps, req)
    print(render(rep))


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
