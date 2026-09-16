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

import provenance as PV                                        # noqa: E402
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


def elapsed_from_rows(rows, rps):
    """The two spans, and only one of them may satisfy the support floor.

    WHY THEY ARE NOT THE SAME NUMBER. MIN_DURATION_S exists to establish
    SUSTAINED OPERATION AT THE SELECTED RATE. Two things can stretch a wall
    clock without adding a second of that evidence:

        A VENUE-ENFORCED PAUSE. A 429 backoff or an honoured Retry-After is the
        venue telling us to stop. Time spent obeying it is time we were NOT
        reading at 0.25 rps -- it is the opposite of the thing being measured.
        Counting it toward the floor would mean a refusal helps a rate pass.

        RESPONSE LATENCY. The last request's 30 ms of flight is operationally
        interesting and is reported, but measuring completion-minus-first-start
        would turn a 1,196-second pacing experiment into a "1,200-second"
        validation by arithmetic rather than by exposure.

    So the support criterion uses NOMINAL_PACED_EXPOSURE_S: the sum over
    consecutive request STARTS of min(observed gap, nominal interval). Capping
    each interval at its nominal value means no pause of any kind -- venue
    backoff, Retry-After, runner stall, GC -- can inflate it, without the
    measure needing to know why a gap was long.
    """
    interval = 1.0 / float(rps)
    stamps = [(_parse(r.get("RECEIPT_UTC")), r) for r in rows]
    stamps = [(t, r) for t, r in stamps if t is not None]
    if not stamps:
        return {"FIRST_REQUEST_START": NOT_IDENTIFIED,
                "LAST_REQUEST_START": NOT_IDENTIFIED,
                "LAST_REQUEST_COMPLETION": NOT_IDENTIFIED,
                "NOMINAL_PACED_EXPOSURE_S": NOT_IDENTIFIED,
                "WALL_CLOCK_ELAPSED_S": NOT_IDENTIFIED}
    stamps.sort(key=lambda p: (p[1].get("SEQ", 0), p[0]))
    first_t = stamps[0][0]
    last_t, last_r = stamps[-1]
    lat = float(last_r.get("LATENCY_S") or 0.0)

    exposure = 0.0
    short_gaps = 0
    for (a, _), (b, _) in zip(stamps, stamps[1:]):
        g = (b - a).total_seconds()
        if g < interval - TIMESTAMP_RESOLUTION_S:
            short_gaps += 1
        exposure += min(g, interval)

    start_span = (last_t - first_t).total_seconds()
    return {
        "FIRST_REQUEST_START": first_t.isoformat(),
        "LAST_REQUEST_START": last_t.isoformat(),
        "LAST_REQUEST_COMPLETION": (
            last_t.isoformat() + " +%.3fs latency" % lat),
        "LAST_REQUEST_LATENCY_S": lat,

        # the support criterion
        "NOMINAL_PACED_EXPOSURE_S": exposure,
        "REQUEST_INTERVAL_S": interval,
        "INTERVALS": len(stamps) - 1,

        # reported, and explicitly not usable for the floor
        "WALL_CLOCK_ELAPSED_S": start_span + lat,
        "START_TO_START_SPAN_S": start_span,
        "FORCED_SUSPENSION_S": max(0.0, start_span - exposure),
        "BACKOFF_EXCLUDED_FROM_SUPPORT": True,
        "LATENCY_EXCLUDED_FROM_SUPPORT": True,
        "WHY_BACKOFF_IS_EXCLUDED": (
            "time spent obeying a venue refusal is time we were not operating "
            "at the selected rate; a 429 may not help a rate pass"),
        "GAPS_SHORTER_THAN_NOMINAL": short_gaps,

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
    prov_path = out / "provenance.json"
    sealed_prov = (json.loads(prov_path.read_text())
                   if prov_path.is_file() else {})
    rows = [json.loads(l) for l in
            (out / "confirm_rows.jsonl").read_text().splitlines() if l.strip()]
    rps = float(rps or sealed["RATE_RPS"])

    req = len(rows)
    ok = sum(1 for r in rows if r.get("status") == 200)
    n429 = sum(1 for r in rows if r.get("status") == 429)
    other = req - ok - n429
    lat = [float(r.get("LATENCY_S") or 0.0) for r in rows]

    timing = elapsed_from_rows(rows, rps)
    exposure = timing["NOMINAL_PACED_EXPOSURE_S"]
    need_dur = RC.min_duration_s(rps)
    need_req = RC.MIN_REQUESTS

    # Enforced literally, against the PACED EXPOSURE. 1196 is not 1200, it is
    # not rounded to 1200, and no backoff or latency may carry it there.
    reqs_ok = req >= need_req
    dur_ok = (exposure != NOT_IDENTIFIED and float(exposure) >= need_dur)

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

    # -- the question THIS RUN CAN answer: was 0.25 rps operationally clean
    #    over the requests it actually issued? Outcome criteria only. The
    #    duration floor is deliberately absent here.
    operational = []
    if n429 > RC.MAX_ALLOWED_429:
        operational.append("REFUSALS_EXCEED_FROZEN_ALLOWANCE")
    if cover.get("POLL_ORDER_STARVATION") == "YES":
        operational.append("POLL_ORDER_STARVATION")
    if fair.get("POLL_ORDER_FAIRNESS") == "FAIL":
        operational.append("POLL_ORDER_NOT_FAIR")
    if cover.get("SUCCESS_COVERAGE_BALANCED") == "NO":
        operational.append("SUCCESS_COVERAGE_UNBALANCED")
    if other and D(other) / D(req) > RC.MAX_OTHER_FAILURE_SHARE:
        operational.append("OTHER_FAILURES_EXCEED_TOLERANCE")
    if n429 and not after:
        operational.append("READS_DID_NOT_RESUME_AFTER_THE_REFUSAL")
    clean = not operational

    # -- the question THIS RUN CANNOT answer: has the frozen sustained-duration
    #    criterion been met? Support floors on top of the outcome criteria.
    reasons = list(operational)
    if not reqs_ok:
        reasons.append(REQUESTS_FAIL_REASON)
    if not dur_ok:
        reasons.append(DURATION_FAIL_REASON)

    # -- the SUPPORT question, its own verdict: did the run occupy the floors?
    #    Separate from cleanliness (outcomes) and from the combined verdict, so
    #    a reader can see which of the two failed without inference.
    support_ok = reqs_ok and dur_ok

    validated = not reasons
    structural = (exposure != NOT_IDENTIFIED
                  and float(exposure) < need_dur
                  and abs(float(exposure) - planned_elapsed_s(req, rps)) < 1.5)

    # A clean-but-short run is repeatable at the SAME rate with the corrected
    # count. A run that failed on venue behaviour is not: repeating 0.25 rps
    # merely to fix our own off-by-one would be answering the arithmetic
    # question while ignoring the answer the venue already gave.
    propose = clean and not dur_ok
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
        "PLANNED_PACED_EXPOSURE_S": planned_elapsed_s(req, rps),
        "MIN_REQUESTS_REQUIRED": need_req,
        "MIN_PACED_EXPOSURE_S_REQUIRED": need_dur,
        "SUPPORT_CRITERION_USES": "NOMINAL_PACED_EXPOSURE_S",
        "REQUESTS_MEET_FLOOR": reqs_ok,
        "DURATION_MEETS_FLOOR": dur_ok,
        "DURATION_WAS_NOT_ROUNDED": True,
        "DURATION_VERDICT_IS_STRUCTURAL": structural,
        "WHY_STRUCTURAL": (
            "%d requests at %s rps span %d intervals; the exposure is fixed by "
            "the pacing before the venue answers anything"
            % (req, rps, req - 1) if structural else None),
        "REQUESTS_NEEDED_FOR_THE_EXPOSURE_FLOOR":
            requests_for_duration(rps, need_dur),

        # the two questions, kept apart
        "QUESTION_THIS_RUN_CAN_ANSWER": (
            "does %s rps appear operationally clean over the %d requests "
            "issued?" % (rps, req)),
        "OPERATIONALLY_CLEAN_OVER_REQUESTS": ("YES" if clean else "NO"),
        "OPERATIONALLY_CLEAN_OVER_THE_REQUESTS_ISSUED": ("YES" if clean
                                                         else "NO"),
        "OPERATIONAL_FAILURES": (operational if operational else None),
        "SUPPORT_FLOORS_MET": ("YES" if support_ok else "NO"),
        "SUPPORT_FAILURES": ([r for r in (REQUESTS_FAIL_REASON,
                                          DURATION_FAIL_REASON)
                              if r in reasons] or None),
        "QUESTION_THIS_RUN_CANNOT_ANSWER": (
            "has %s rps met the frozen sustained-duration validation "
            "criterion?" % rps),

        "COLLECTOR_RATE_OPERATIONALLY_VALIDATED": ("YES" if validated else "NO"),
        "FAIL_REASON": (reasons if reasons else None),
        "PROPOSED_SUBSTANTIVE_CAPTURE_RATE": (
            "%s RPS" % sealed["RATE_RPS"] if validated else NOT_IDENTIFIED),

        # the corrected re-run, offered only when the venue gave no adverse
        # answer -- never as a way to re-ask a question already answered NO
        "CORRECTED_CONFIRMATION_RATE": ("%s RPS" % sealed["RATE_RPS"]
                                        if propose else NOT_IDENTIFIED),
        "CORRECTED_CONFIRMATION_REQUESTS": (
            requests_for_duration(rps, need_dur) if propose else NOT_IDENTIFIED),
        "MIN_PACED_EXPOSURE_S": (need_dur if propose else NOT_IDENTIFIED),
        "CORRECTED_REQUEST_INTERVAL_S": (1.0 / rps if propose
                                         else NOT_IDENTIFIED),
        "PROPOSE_REPEAT_AT_THIS_RATE": ("YES" if propose else "NO"),
        "WHY_NOT_PROPOSED": (
            None if propose else
            ("the run already validated" if validated else
             "the venue gave an adverse answer at this rate; repeating it "
             "merely to fix our own off-by-one would re-ask a question that "
             "has been answered")),
        "CORRECTED_CONFIRMATION_IS_NOT_DISPATCHED": True,
        "VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED": NOT_IDENTIFIED,
        "MECHANISM_DOES_NOT_GATE_THIS": True,

        # PROVENANCE, read from the runner's own sealed record. The executed
        # SHA here is the one the RUNNER reported, never the one we expected.
        "DISPATCH_SHA": sealed_prov.get("DISPATCH_SHA", NOT_IDENTIFIED),
        "EXECUTED_SHA_ACTUAL": sealed_prov.get(
            "EXECUTED_SHA_ACTUAL", sealed_prov.get("EXECUTED_SHA",
                                                   NOT_IDENTIFIED)),
        "EXECUTED_SHA_SOURCE": sealed_prov.get("EXECUTED_SHA_SOURCE",
                                               NOT_IDENTIFIED),
        "WORKFLOW_REF_SHA": sealed_prov.get("WORKFLOW_REF_SHA", NOT_IDENTIFIED),
        "WORKFLOW_FILE_SHA": sealed_prov.get("WORKFLOW_FILE_SHA",
                                             NOT_IDENTIFIED),
        "CONFIG_SHA": sealed_prov.get("CONFIG_SHA", NOT_IDENTIFIED),
        "DATA_OUTPUT_SHA": sealed_prov.get("DATA_OUTPUT_SHA", NOT_IDENTIFIED),
        # The four aspects are RE-DERIVED here from the values the runner
        # sealed, not copied from its verdict -- an artifact that carries its
        # own PASS is not evidence of a PASS. DATA_OUTPUT_SHA is additionally
        # re-hashed off the rows file that is actually present.
        "EVIDENCE_RUN_VALIDITY": NOT_IDENTIFIED,
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

    # Provenance, re-derived from the sealed record plus the rows on disk.
    rows_sha = PV.file_sha256(out / "confirm_rows.jsonl")
    sealed_out_sha = sealed_prov.get("DATA_OUTPUT_SHA")
    prov = PV.check(
        sealed_prov.get("DISPATCH_SHA"),
        sealed_prov.get("EXECUTED_SHA_ACTUAL", sealed_prov.get("EXECUTED_SHA")),
        workflow_ref_sha=sealed_prov.get("WORKFLOW_REF_SHA"),
        workflow_file_sha=sealed_prov.get("WORKFLOW_FILE_SHA"),
        config_sha=sealed_prov.get("CONFIG_SHA"),
        data_output_sha=rows_sha if rows_sha != NOT_IDENTIFIED else None,
        executed_sha_source=sealed_prov.get(
            "EXECUTED_SHA_SOURCE",
            # the runner obtains it from `git rev-parse HEAD` in both the
            # current and the previous revision of provenance.py
            "RUNNER_GIT_REV_PARSE" if sealed_prov else None))
    report.update({k: prov[k] for k in (
        "DISPATCH_SHA", "EXECUTED_SHA_ACTUAL", "EXECUTED_SHA_SOURCE",
        "WORKFLOW_REF_SHA", "WORKFLOW_FILE_SHA", "CONFIG_SHA",
        "DATA_OUTPUT_SHA", "CODE_PROVENANCE_VALIDITY", "WORKFLOW_PROVENANCE",
        "CONFIG_PROVENANCE", "DATA_OUTPUT_PROVENANCE",
        "EVIDENCE_RUN_VALIDITY", "WORKFLOW_FILE_AND_CODE_SAME_COMMIT")})
    report["DATA_OUTPUT_SHA_MATCHES_SEALED_RECORD"] = (
        "YES" if (sealed_out_sha and rows_sha == sealed_out_sha) else
        ("NO" if sealed_out_sha else NOT_IDENTIFIED))
    report["PROVENANCE_REDERIVED_NOT_COPIED"] = True
    return report


def render(r):
    """The frozen field list, ending on the two lines that are the decision."""
    L = []
    for k in ("REQUESTS", "SUCCESSES", "HTTP_429", "OTHER_FAILURES",
              "HTTP_429_RATE", "P50_LATENCY", "P90_LATENCY", "P99_LATENCY",
              "VALID_RETRY_AFTER_COUNT", "GLOBAL_BACKOFF_EVENTS",
              "SUCCESSES_AFTER_LAST_429"):
        L.append("%-38s = %s" % (k, r.get(k)))
    L.append("")
    for k in ("FIRST_REQUEST_START", "LAST_REQUEST_START",
              "LAST_REQUEST_COMPLETION", "NOMINAL_PACED_EXPOSURE_S",
              "WALL_CLOCK_ELAPSED_S", "FORCED_SUSPENSION_S",
              "MIN_PACED_EXPOSURE_S_REQUIRED"):
        L.append("%-38s = %s" % (k, r.get(k)))
    L.append("")
    for slug, d in r.get("PER_MARKET", {}).items():
        L.append("%-44s att %-4s ok %-4s 429 %-3s other %-3s share %s"
                 % (slug, d["ATTEMPTS"], d["SUCCESSES"], d["HTTP_429"],
                    d["OTHER_FAILURES"], d["SUCCESS_SHARE"]))
    L.append("")
    for k in ("POLL_ORDER_FAIRNESS", "SUCCESS_COVERAGE_BALANCED",
              "POLL_ORDER_STARVATION"):
        L.append("%-38s = %s" % (k, r.get(k)))
    if r.get("FAIL_REASON"):
        L.append("")
        for f in r["FAIL_REASON"]:
            L.append("%-38s = %s" % ("FAIL_REASON", f))
    L.append("")
    for k in ("DISPATCH_SHA", "EXECUTED_SHA_ACTUAL", "WORKFLOW_FILE_SHA",
              "CONFIG_SHA", "DATA_OUTPUT_SHA", "CODE_PROVENANCE_VALIDITY",
              "WORKFLOW_PROVENANCE", "CONFIG_PROVENANCE",
              "DATA_OUTPUT_PROVENANCE", "EVIDENCE_RUN_VALIDITY"):
        L.append("%-38s = %s" % (k, r.get(k)))
    L.append("")
    L.append("%-38s = %s" % ("VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED",
                             r["VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED"]))
    L.append("")
    L.append("%-38s = %s" % ("OPERATIONALLY_CLEAN_OVER_REQUESTS",
                             r["OPERATIONALLY_CLEAN_OVER_REQUESTS"]))
    L.append("%-38s = %s" % ("SUPPORT_FLOORS_MET", r["SUPPORT_FLOORS_MET"]))
    L.append("%-38s = %s" % ("COLLECTOR_RATE_OPERATIONALLY_VALIDATED",
                             r["COLLECTOR_RATE_OPERATIONALLY_VALIDATED"]))
    L.append("%-38s = %s" % ("PROPOSED_SUBSTANTIVE_CAPTURE_RATE",
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
