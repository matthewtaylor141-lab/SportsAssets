"""Sections 5, 6, 7, 14, 20. Did we measure the market correctly?

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
THIS MODULE CONTACTS NOTHING. It reads rows a capture already wrote.

THE ORDER OF QUESTIONS IS NOT NEGOTIABLE
----------------------------------------
Before any signal question, one question:

    DID WE ACTUALLY MEASURE THE MARKET CORRECTLY?

A microprice result computed on a series with a 40% observation hole, a
non-monotone clock and thirty crossed books is not a weak result. It is not a
result. So the quality gate runs first, and signal analysis is BLOCKED behind
it rather than merely annotated by it.

SECTION 6: A BOUND THAT DOES NOT TRAVEL
---------------------------------------
A valid polling process may MISS a real transition; it must never manufacture
one. So where the assumptions hold, OBSERVED_TRANSITIONS is a LOWER BOUND on
true transitions.

FREQUENCY PER OBSERVED TRANSITION DOES NOT INHERIT THAT BOUND. It is a ratio
whose denominator is also undercounted, and undercounting both parts of a
ratio moves it in no determined direction. The two are kept apart by name and
by function, because blurring them is how a lower bound gets quietly reported
as a rate.

SECTION 7: ROWS ARE NOT EVENTS
------------------------------
Every figure carries TIMESTAMP_ROWS and INDEPENDENT_EVENTS side by side. A
capture of three events is a pilot however many snapshots it holds.
"""

import datetime
import statistics

NOT_IDENTIFIED = "NOT_IDENTIFIED"

THIS_MODULE_CONTACTS_NOTHING = True

QUALITY_GATE_RUNS_FIRST = True
WHY_FIRST = (
    "a signal result computed on a series that failed its own integrity "
    "checks is not a weak result, it is not a result. So the gate BLOCKS the "
    "analysis rather than annotating it")


# --- Section 6. The bound, and the thing that does not inherit it. ---------

TRANSITION_CLASSES = ("OBSERVED_BOOK_CHANGES", "OBSERVED_MID_CHANGES",
                      "OBSERVED_PRICE_IMPROVEMENTS", "OBSERVED_DEPTH_CHANGES",
                      "OBSERVED_MOVE_THROUGH_EVENTS")

POLLING_MAY_MISS_MAY_NOT_MANUFACTURE = (
    "a valid polling process may MISS a real transition; it must never "
    "manufacture one")

COUNT_IS_A_LOWER_BOUND = "LOWER_BOUND_ON_TRUE_TRANSITIONS"

FREQUENCY_DOES_NOT_INHERIT_THE_BOUND = (
    "frequency per observed transition is a ratio whose DENOMINATOR is also "
    "undercounted. Undercounting both parts moves a ratio in no determined "
    "direction, so the lower-bound property of the count does not travel to "
    "the rate. Reporting a rate as a lower bound is the error this names")

BOUND_REQUIRES = (
    "the observation process is independent of the transition process, and "
    "no reconstruction step invents a transition between two observations")


def bound_label(quantity, assumptions_hold=True):
    """What may honestly be claimed about this quantity?"""
    is_count = quantity in TRANSITION_CLASSES
    if is_count and assumptions_hold:
        return {"QUANTITY": quantity, "CLAIM": COUNT_IS_A_LOWER_BOUND,
                "REQUIRES": BOUND_REQUIRES}
    if is_count:
        return {"QUANTITY": quantity, "CLAIM": "POINT_ESTIMATE_NO_BOUND",
                "WHY": "the bound's assumptions were not asserted"}
    return {"QUANTITY": quantity, "CLAIM": "NO_BOUND",
            "WHY": FREQUENCY_DOES_NOT_INHERIT_THE_BOUND}


# --- Section 7. Rows are not events. ---------------------------------------

INDEPENDENCE_UNIT = "EVENT"
PILOT_LABEL = "PILOT_PROSPECTIVE_MICROSTRUCTURE_EVIDENCE"
MIN_EVENTS_FOR_MORE_THAN_PILOT = 30
ROWS_ARE_NOT_EXPERIMENTS = (
    "10,000 snapshots are not 10,000 independent market experiments. Three "
    "events sampled densely are three events")


def scale_label(independent_events):
    n = int(independent_events or 0)
    if n >= MIN_EVENTS_FOR_MORE_THAN_PILOT:
        return {"LABEL": "PROSPECTIVE_MICROSTRUCTURE_EVIDENCE",
                "INDEPENDENT_EVENTS": n}
    return {"LABEL": PILOT_LABEL, "INDEPENDENT_EVENTS": n,
            "WHY": ROWS_ARE_NOT_EXPERIMENTS,
            "MIN_EVENTS_FOR_MORE_THAN_PILOT": MIN_EVENTS_FOR_MORE_THAN_PILOT}


# --- Section 5. The quality report. ----------------------------------------

def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _num(v):
    if v in (None, NOT_IDENTIFIED, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify_error(row):
    """A failure is named by KIND, never pooled into one 'errors' number."""
    status = row.get("status")
    err = str(row.get("error") or "").lower()
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = None
    if status == 429:
        return "HTTP_429"
    if "timeout" in err or "timed out" in err:
        return "TIMEOUTS"
    if "json" in err or "parse" in err or "decode" in err:
        return "PARSE_FAILURES"
    if status is not None and status >= 400:
        return "OTHER_HTTP_FAILURES"
    if err:
        return "OTHER_HTTP_FAILURES"
    return "OTHER_HTTP_FAILURES"


# ---------------------------------------------------------------------------
# FROZEN QUALITY THRESHOLDS
# ---------------------------------------------------------------------------
# Fixed 2026-09-17, BEFORE any substantive-capture data existed. Every value is
# declared here by value, hashed, and copied by value into the CAPTURE_SPEC
# record and the CAPTURE_MANIFEST at dispatch.
#
# The capture parameters they are derived from (substantive_capture.py):
#   REQUEST_INTERVAL_S = 4.0, MARKETS = 6  ->  NOMINAL_REVISIT_S = 24.0
#   CAPTURE_SECONDS = 5400, EXPECTED_OBS_PER_MARKET = 225
#
# THESE MAY NOT BE CHANGED AFTER OBSERVING A RESULT. See THRESHOLD_CHANGE_RULE.

FROZEN_AT = "2026-09-17"
FROZEN_BEFORE_ANY_CAPTURE_DATA = True

FROZEN_QUALITY_THRESHOLDS = {
    "MIN_OBSERVATION_COVERAGE_PCT": 90.0,

    # Rates are fractions of ATTEMPTED_POLLS.
    "MAX_HTTP_429_RATE": 0.02,
    "MAX_OTHER_FAILURE_RATE": 0.05,
    "MAX_PARSE_FAILURE_RATE": 0.005,

    # Rates are fractions of SUCCESSFUL_POLLS. A price outside [0,1] or a bid
    # above its own ask is a defect, not noise, so the tolerance is zero.
    "MAX_INVALID_BOOK_RATE": 0.0,
    "MAX_CROSSED_BOOK_RATE": 0.0,

    # Absolute seconds, against a 24.0 s nominal revisit.
    "MAX_MEDIAN_OBSERVATION_GAP_S": 36.0,
    "MAX_P90_OBSERVATION_GAP_S": 60.0,
    "MAX_MAXIMUM_OBSERVATION_GAP_S": 300.0,

    # Completeness.
    "MIN_OBSERVATIONS_PER_MARKET": 200,
    "COMPLETE_SERIES_FRACTION": 0.90,
    "MIN_MARKETS_WITH_SUFFICIENT_SERIES": 5,
    "MIN_EVENTS_WITH_SUFFICIENT_SERIES": 3,

    "CLOCK_MONOTONICITY_REQUIRED": True,
    "MAX_NON_MONOTONE_STEPS": 0,
    "MAX_DUPLICATE_SNAPSHOTS": 0,
}

THRESHOLD_RATIONALE = {
    "MIN_OBSERVATION_COVERAGE_PCT": (
        "below 90% of planned polls the series has holes large enough to "
        "change a short-horizon result by which observations survived"),
    "MAX_HTTP_429_RATE": (
        "the pacer runs at the confirmed 0.25 rps, deliberately conservative. "
        "More than 2% rate-limited means the rate assumption is wrong, which "
        "is a finding about the collector, not a nuisance to average over"),
    "MAX_PARSE_FAILURE_RATE": (
        "a parse failure is a schema mismatch, not transport noise. Near-zero "
        "tolerance because the alternative is silently dropping a field"),
    "MAX_INVALID_BOOK_RATE": (
        "a contract price outside [0,1] cannot be a price. One is a defect"),
    "MAX_CROSSED_BOOK_RATE": (
        "a bid above its own ask is either a venue anomaly worth naming or a "
        "read error. Either way it is not data to average through"),
    "MAX_MEDIAN_OBSERVATION_GAP_S": (
        "1.5x the 24.0 s nominal revisit. A typical gap worse than this means "
        "the sampling grid did not hold"),
    "MAX_P90_OBSERVATION_GAP_S": "2.5x nominal, for the tail",
    "MAX_MAXIMUM_OBSERVATION_GAP_S": (
        "a single 5-minute hole destroys the 300 s horizon outright"),
    "MIN_OBSERVATIONS_PER_MARKET": (
        "matches the existing SUPPORT_MIN_OBS_PER_MARKET floor of 200 against "
        "225 expected, so the quality gate and the support gate agree"),
    "MIN_MARKETS_WITH_SUFFICIENT_SERIES": (
        "5 of 6, NOT 6 of 6. A market that halts mid-capture because its game "
        "ended is a legitimate VENUE state, not a collection defect, and "
        "requiring all six would fail the capture on the former. The event "
        "floor below is what protects the scientific unit"),
    "MIN_EVENTS_WITH_SUFFICIENT_SERIES": (
        "all 3. The independent event is the unit of inference, so every "
        "event must retain at least one sufficient market. Losing an event is "
        "not a partial result, it is a smaller experiment"),
    "CLOCK_MONOTONICITY_REQUIRED": (
        "a backwards clock makes every gap, horizon and ordering statistic "
        "unreadable. There is no partial credit"),
}

THRESHOLDS_ARE_PREDECLARED = (
    "these floors are fixed before the capture is scored. A gate whose "
    "threshold is chosen after seeing the data is not a gate")

THRESHOLD_CHANGE_RULE = (
    "a frozen threshold MAY NOT be modified after observing a result. If one "
    "is later judged scientifically wrong: (1) the current capture is still "
    "evaluated under the FROZEN rule and its verdict stands, (2) the reason "
    "is documented, (3) the corrected rule applies ONLY to the NEXT "
    "experiment. Re-scoring a finished capture under a revised threshold is "
    "not a correction, it is choosing the answer")

# Revisions accumulate here for the NEXT experiment. This list does not and
# may not affect the current capture's verdict.
NEXT_EXPERIMENT_THRESHOLD_REVISIONS = ()
REVISIONS_DO_NOT_APPLY_TO_THE_CURRENT_CAPTURE = True


def _thresholds_sha():
    import hashlib
    import json
    return hashlib.sha256(json.dumps(
        FROZEN_QUALITY_THRESHOLDS, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


FROZEN_QUALITY_THRESHOLDS_SHA = _thresholds_sha()
FROZEN_QUALITY_THRESHOLDS_STATUS = "FROZEN_AND_HASHED"


def thresholds_block():
    """The by-value record embedded in CAPTURE_SPEC and CAPTURE_MANIFEST."""
    return {
        "FROZEN_QUALITY_THRESHOLDS": dict(FROZEN_QUALITY_THRESHOLDS),
        "FROZEN_QUALITY_THRESHOLDS_SHA": FROZEN_QUALITY_THRESHOLDS_SHA,
        "FROZEN_QUALITY_THRESHOLDS_STATUS": FROZEN_QUALITY_THRESHOLDS_STATUS,
        "FROZEN_AT": FROZEN_AT,
        "FROZEN_BEFORE_ANY_CAPTURE_DATA": FROZEN_BEFORE_ANY_CAPTURE_DATA,
        "THRESHOLD_CHANGE_RULE": THRESHOLD_CHANGE_RULE,
    }


def thresholds_intact():
    """Has the frozen block been edited since it was hashed?"""
    got = _thresholds_sha()
    ok = got == FROZEN_QUALITY_THRESHOLDS_SHA
    return {"INTACT": ok, "RECORDED_SHA": FROZEN_QUALITY_THRESHOLDS_SHA,
            "RECOMPUTED_SHA": got,
            "REASON": None if ok else "THRESHOLDS_EDITED_AFTER_HASHING"}


# Back-compatible names, reading from the frozen block so there is exactly one
# source of truth.
COVERAGE_PCT_FLOOR = FROZEN_QUALITY_THRESHOLDS["MIN_OBSERVATION_COVERAGE_PCT"]
COMPLETE_SERIES_FRACTION = FROZEN_QUALITY_THRESHOLDS["COMPLETE_SERIES_FRACTION"]
CROSSED_BOOK_TOLERANCE = 0
CLOCK_MUST_BE_MONOTONE = FROZEN_QUALITY_THRESHOLDS[
    "CLOCK_MONOTONICITY_REQUIRED"]
MAX_GAP_MULTIPLE_OF_NOMINAL = 4.0   # retained for the per-market series label


def quality(tick_rows, error_rows=(), planned_polls=None,
            nominal_revisit_s=24.0, event_of=None, market_of=None):
    """Section 5. The full integrity report, and the gate verdict.

    `tick_rows` are successful observations; `error_rows` are the TICK_ERROR
    lines. Both are counted, because attempted-minus-successful is the only
    honest denominator for coverage.
    """
    market_of = market_of or (lambda r: r.get("MARKET_SLUG") or r.get("slug"))
    event_of = event_of or (lambda r: r.get("EVENT_KEY")
                            or r.get("EVENT_ID") or NOT_IDENTIFIED)

    ticks = list(tick_rows or ())
    errs = list(error_rows or ())

    by_market = {}
    events = set()
    dup = 0
    seen = set()
    crossed = missing_bid = missing_ask = invalid = 0
    for r in ticks:
        m = market_of(r)
        by_market.setdefault(m, []).append(r)
        ev = event_of(r)
        if ev != NOT_IDENTIFIED:
            events.add(ev)
        key = (m, str(r.get("RECEIPT_UTC")), r.get("seq"))
        if key in seen:
            dup += 1
        seen.add(key)
        bid, ask = _num(r.get("BID")), _num(r.get("ASK"))
        if bid is None:
            missing_bid += 1
        if ask is None:
            missing_ask += 1
        if bid is not None and ask is not None:
            if bid > ask:
                crossed += 1
            if not (0.0 <= bid <= 1.0) or not (0.0 <= ask <= 1.0):
                invalid += 1
        elif bid is not None and not (0.0 <= bid <= 1.0):
            invalid += 1
        elif ask is not None and not (0.0 <= ask <= 1.0):
            invalid += 1

    # Failures, named.
    fail = {"HTTP_429": 0, "OTHER_HTTP_FAILURES": 0, "TIMEOUTS": 0,
            "PARSE_FAILURES": 0}
    for r in errs:
        fail[classify_error(r)] += 1

    attempted = len(ticks) + len(errs)
    successful = len(ticks)
    # An unknown plan may NOT default to `attempted`: that would make coverage
    # 100% by construction and pass the gate's main threshold on a capture
    # nobody planned. An unknown denominator is NOT_IDENTIFIED, and the gate
    # treats that as a failure rather than a pass.
    planned = int(planned_polls) if planned_polls else None
    coverage = (100.0 * successful / planned) if planned else NOT_IDENTIFIED

    # Gaps and clock, per market.
    gaps = []
    non_monotone = 0
    complete = partial = 0
    sufficient_markets = 0
    sufficient_events = set()
    per_market = {}
    for m, rows in by_market.items():
        stamped = [(_parse(r.get("RECEIPT_UTC")), r) for r in rows]
        stamped = [(t, r) for t, r in stamped if t is not None]
        stamped.sort(key=lambda x: (x[1].get("seq", 0)))
        times = [t for t, _ in stamped]
        for a, b in zip(times, times[1:]):
            if b < a:
                non_monotone += 1
        ordered = sorted(times)
        mg = [(b - a).total_seconds() for a, b in zip(ordered, ordered[1:])]
        gaps.extend(mg)
        # With no plan there is no denominator, so completeness is UNKNOWN --
        # never COMPLETE by default.
        want = (planned / len(by_market)) if (planned and by_market) else None
        biggest = max(mg) if mg else 0.0
        if want is None:
            verdict = "UNKNOWN_PLAN"
            ok = False
        else:
            ok = (len(rows) >= COMPLETE_SERIES_FRACTION * want
                  and biggest <= MAX_GAP_MULTIPLE_OF_NOMINAL * nominal_revisit_s)
            verdict = "COMPLETE" if ok else "PARTIAL"
        complete += 1 if ok else 0
        partial += 0 if ok else 1
        # SUFFICIENT is the frozen-threshold judgement and is deliberately
        # separate from COMPLETE: a market may fall short of the planned count
        # yet still carry enough observations to analyse.
        suff = (len(rows) >= FROZEN_QUALITY_THRESHOLDS[
            "MIN_OBSERVATIONS_PER_MARKET"]
            and biggest <= FROZEN_QUALITY_THRESHOLDS[
                "MAX_MAXIMUM_OBSERVATION_GAP_S"])
        sufficient_markets += 1 if suff else 0
        if suff:
            ev = event_of(rows[0])
            if ev != NOT_IDENTIFIED:
                sufficient_events.add(ev)
        per_market[m] = {"OBSERVATIONS": len(rows), "MAX_GAP_S": biggest,
                         "SERIES": verdict,
                         "SUFFICIENT": suff}

    def _q(vals, p):
        if not vals:
            return NOT_IDENTIFIED
        s = sorted(vals)
        i = min(int(p * (len(s) - 1)), len(s) - 1)
        return s[i]

    rep = {
        "TIMESTAMP_ROWS": successful,
        "INDEPENDENT_EVENTS": len(events),
        "MARKETS": len(by_market),

        "PLANNED_POLLS": planned if planned else NOT_IDENTIFIED,
        "ATTEMPTED_POLLS": attempted,
        "SUCCESSFUL_POLLS": successful,

        "HTTP_429": fail["HTTP_429"],
        "OTHER_HTTP_FAILURES": fail["OTHER_HTTP_FAILURES"],
        "TIMEOUTS": fail["TIMEOUTS"],
        "PARSE_FAILURES": fail["PARSE_FAILURES"],

        "OBSERVATION_COVERAGE_PCT": (round(coverage, 3)
                                     if planned else NOT_IDENTIFIED),

        "MEDIAN_OBSERVATION_GAP": (round(statistics.median(gaps), 3)
                                   if gaps else NOT_IDENTIFIED),
        "P90_OBSERVATION_GAP": (round(_q(gaps, 0.90), 3) if gaps
                                else NOT_IDENTIFIED),
        "MAX_OBSERVATION_GAP": round(max(gaps), 3) if gaps else NOT_IDENTIFIED,

        "MARKETS_WITH_COMPLETE_SERIES": complete,
        "MARKETS_WITH_PARTIAL_SERIES": partial,
        "MARKETS_WITH_SUFFICIENT_SERIES": sufficient_markets,
        "EVENTS_WITH_SUFFICIENT_SERIES": len(sufficient_events),

        "CLOCK_MONOTONICITY_STATUS": ("MONOTONE" if non_monotone == 0
                                      else "NON_MONOTONE"),
        "CLOCK_NON_MONOTONE_STEPS": non_monotone,

        "DUPLICATE_SNAPSHOT_COUNT": dup,
        "INVALID_BOOK_COUNT": invalid,
        "CROSSED_BOOK_COUNT": crossed,
        "MISSING_BID_COUNT": missing_bid,
        "MISSING_ASK_COUNT": missing_ask,

        "PER_MARKET": per_market,
        "SCALE": scale_label(len(events)),
        "THRESHOLDS_ARE_PREDECLARED": THRESHOLDS_ARE_PREDECLARED,
    }
    rep.update(thresholds_block())
    rep.update(gate(rep, nominal_revisit_s))
    return rep


def gate(rep, nominal_revisit_s=24.0):
    """May signal analysis proceed? Every frozen threshold is evaluated.

    Each check reports the OBSERVED value beside the FROZEN limit, so the
    verdict can be audited without re-running anything. An unmeasurable
    quantity fails; it does not pass by absence.
    """
    T = FROZEN_QUALITY_THRESHOLDS
    fails, checks = [], []

    def _check(name, observed, limit, ok, note=None):
        checks.append({"CHECK": name, "OBSERVED": observed,
                       "FROZEN_LIMIT": limit, "PASS": bool(ok),
                       "NOTE": note})
        if not ok:
            fails.append(name)

    integrity = thresholds_intact()
    if not integrity["INTACT"]:
        fails.append("FROZEN_THRESHOLDS_EDITED_AFTER_HASHING")

    attempted = rep.get("ATTEMPTED_POLLS", 0) or 0
    successful = rep.get("SUCCESSFUL_POLLS", 0) or 0

    _check("SUCCESSFUL_POLLS", successful, "> 0", successful > 0)

    cov = rep.get("OBSERVATION_COVERAGE_PCT")
    if cov == NOT_IDENTIFIED or cov is None:
        _check("MIN_OBSERVATION_COVERAGE_PCT", NOT_IDENTIFIED,
               T["MIN_OBSERVATION_COVERAGE_PCT"], False,
               "PLANNED_POLLS_NOT_IDENTIFIED -- an unknown denominator fails")
    else:
        _check("MIN_OBSERVATION_COVERAGE_PCT", cov,
               T["MIN_OBSERVATION_COVERAGE_PCT"],
               cov >= T["MIN_OBSERVATION_COVERAGE_PCT"])

    for key, field, denom_n, denom_name in (
            ("MAX_HTTP_429_RATE", "HTTP_429", attempted, "ATTEMPTED_POLLS"),
            ("MAX_OTHER_FAILURE_RATE", "OTHER_HTTP_FAILURES", attempted,
             "ATTEMPTED_POLLS"),
            ("MAX_PARSE_FAILURE_RATE", "PARSE_FAILURES", attempted,
             "ATTEMPTED_POLLS"),
            ("MAX_INVALID_BOOK_RATE", "INVALID_BOOK_COUNT", successful,
             "SUCCESSFUL_POLLS"),
            ("MAX_CROSSED_BOOK_RATE", "CROSSED_BOOK_COUNT", successful,
             "SUCCESSFUL_POLLS")):
        n = rep.get(field, 0) or 0
        rate = (n / denom_n) if denom_n else (0.0 if n == 0 else 1.0)
        _check(key, round(rate, 6), T[key], rate <= T[key],
               "%d / %d %s" % (n, denom_n, denom_name))

    for key, field in (("MAX_MEDIAN_OBSERVATION_GAP_S",
                        "MEDIAN_OBSERVATION_GAP"),
                       ("MAX_P90_OBSERVATION_GAP_S", "P90_OBSERVATION_GAP"),
                       ("MAX_MAXIMUM_OBSERVATION_GAP_S",
                        "MAX_OBSERVATION_GAP")):
        v = rep.get(field)
        if isinstance(v, (int, float)):
            _check(key, v, T[key], v <= T[key])
        else:
            _check(key, NOT_IDENTIFIED, T[key], False,
                   "no measurable gap series")

    suff_m = rep.get("MARKETS_WITH_SUFFICIENT_SERIES")
    _check("MIN_MARKETS_WITH_SUFFICIENT_SERIES", suff_m,
           T["MIN_MARKETS_WITH_SUFFICIENT_SERIES"],
           isinstance(suff_m, int)
           and suff_m >= T["MIN_MARKETS_WITH_SUFFICIENT_SERIES"])

    suff_e = rep.get("EVENTS_WITH_SUFFICIENT_SERIES")
    _check("MIN_EVENTS_WITH_SUFFICIENT_SERIES", suff_e,
           T["MIN_EVENTS_WITH_SUFFICIENT_SERIES"],
           isinstance(suff_e, int)
           and suff_e >= T["MIN_EVENTS_WITH_SUFFICIENT_SERIES"])

    if T["CLOCK_MONOTONICITY_REQUIRED"]:
        steps = rep.get("CLOCK_NON_MONOTONE_STEPS", 0) or 0
        _check("CLOCK_MONOTONICITY_REQUIRED", steps,
               T["MAX_NON_MONOTONE_STEPS"],
               rep.get("CLOCK_MONOTONICITY_STATUS") == "MONOTONE"
               and steps <= T["MAX_NON_MONOTONE_STEPS"])

    dup = rep.get("DUPLICATE_SNAPSHOT_COUNT", 0) or 0
    _check("MAX_DUPLICATE_SNAPSHOTS", dup, T["MAX_DUPLICATE_SNAPSHOTS"],
           dup <= T["MAX_DUPLICATE_SNAPSHOTS"])

    ok = not fails
    return {
        "CAPTURE_QUALITY_GATE": "PASS" if ok else "FAIL",
        "QUALITY_GATE_FAILURES": fails,
        "QUALITY_GATE_CHECKS": checks,
        "SIGNAL_ANALYSIS_MAY_PROCEED": ok,
        "FROZEN_QUALITY_THRESHOLDS_SHA": FROZEN_QUALITY_THRESHOLDS_SHA,
        "FROZEN_QUALITY_THRESHOLDS_STATUS": FROZEN_QUALITY_THRESHOLDS_STATUS,
        "THRESHOLDS_INTACT": integrity["INTACT"],
        "THRESHOLD_CHANGE_RULE": THRESHOLD_CHANGE_RULE,
        "EVALUATED_UNDER": "THE_FROZEN_RULE",
        "WHY_FIRST": WHY_FIRST,
    }


# --- Section 14. How much is the poll rate missing? ------------------------

VERSION_FIELDS = ("VERSION_ID", "SEQUENCE", "VENUE_VERSION", "UPDATED_AT")

NO_VERSION_MEANS_UNKNOWN = (
    "where the venue exposes no sequence or version, unobserved transitions "
    "are NOT_IDENTIFIED. An absent counter is not a count of zero")


def _version_of(row):
    for f in VERSION_FIELDS:
        v = row.get(f)
        if v not in (None, NOT_IDENTIFIED, ""):
            return v
    return None


def version_advance(tick_rows, market_of=None, state_of=None):
    """Compare venue version advances against our observed state changes.

    Two failures are distinguishable and both matter:
      - the venue advanced but our captured state is identical  -> we sampled
        too slowly to see what moved, or the move was invisible in our fields
      - our state changed with no venue advance                 -> our state
        definition is reading something the version does not cover
    """
    market_of = market_of or (lambda r: r.get("MARKET_SLUG") or r.get("slug"))
    state_of = state_of or (lambda r: (r.get("BID"), r.get("ASK"),
                                       r.get("BID_SIZE"), r.get("ASK_SIZE")))
    by = {}
    for r in tick_rows or ():
        by.setdefault(market_of(r), []).append(r)

    have_version = False
    v_adv = s_chg = v_same_state = s_no_v = 0
    for rows in by.values():
        rows = sorted(rows, key=lambda r: r.get("seq", 0))
        for a, b in zip(rows, rows[1:]):
            va, vb = _version_of(a), _version_of(b)
            sa, sb = state_of(a), state_of(b)
            if va is None or vb is None:
                continue
            have_version = True
            moved_v = va != vb
            moved_s = sa != sb
            v_adv += 1 if moved_v else 0
            s_chg += 1 if moved_s else 0
            if moved_v and not moved_s:
                v_same_state += 1
            if moved_s and not moved_v:
                s_no_v += 1
    if not have_version:
        return {
            "VENUE_VERSION_AVAILABLE": False,
            "VENUE_VERSION_ADVANCES": NOT_IDENTIFIED,
            "BETTOR_OBSERVED_DECISION_STATE_CHANGES": NOT_IDENTIFIED,
            "VENUE_CHANGES_WITH_IDENTICAL_CAPTURED_STATE": NOT_IDENTIFIED,
            "OBSERVED_STATE_CHANGES_WITHOUT_VENUE_CHANGE": NOT_IDENTIFIED,
            "POTENTIAL_UNOBSERVED_TRANSITIONS": NOT_IDENTIFIED,
            "NO_VERSION_MEANS_UNKNOWN": NO_VERSION_MEANS_UNKNOWN,
        }
    return {
        "VENUE_VERSION_AVAILABLE": True,
        "VENUE_VERSION_ADVANCES": v_adv,
        "BETTOR_OBSERVED_DECISION_STATE_CHANGES": s_chg,
        "VENUE_CHANGES_WITH_IDENTICAL_CAPTURED_STATE": v_same_state,
        "OBSERVED_STATE_CHANGES_WITHOUT_VENUE_CHANGE": s_no_v,
        "POTENTIAL_UNOBSERVED_TRANSITIONS": v_same_state,
        "WHAT_THIS_TELLS_US": (
            "whether the capture frequency is adequate for later execution "
            "research. A large VENUE_CHANGES_WITH_IDENTICAL_CAPTURED_STATE "
            "means the rate is too slow or the state fields too narrow"),
    }


# --- Section 20. The harvest decision. -------------------------------------

DECISIONS = ("GO_TO_LARGER_PROSPECTIVE_CAPTURE",
             "REPAIR_CAPTURE_AND_REPEAT",
             "STOP_THIS_MICROSTRUCTURE_PATH")

STOP_REQUIRES_A_STRUCTURAL_NEGATIVE = (
    "with a handful of independent events, STOP requires an extremely strong "
    "STRUCTURAL negative -- not an underpowered null. An experiment that "
    "could not have detected the effect has not falsified it")

DO_NOT_OVERSTATE = (
    "a null on three events is a null on three events. It is evidence about "
    "the pilot's power, not about the market")


def harvest_decision(quality_report, signal_findings=None,
                     structural_negative=False, independent_events=None):
    """Return exactly one of the three decisions, with its evidence.

    STOP is deliberately hard to reach: it requires an explicit structural
    negative AND enough events for that negative to mean anything.
    """
    n = (independent_events if independent_events is not None
         else (quality_report or {}).get("INDEPENDENT_EVENTS", 0))
    q_ok = bool((quality_report or {}).get("SIGNAL_ANALYSIS_MAY_PROCEED"))
    findings = list(signal_findings or ())

    if not q_ok:
        return {
            "HARVEST_DECISION": "REPAIR_CAPTURE_AND_REPEAT",
            "EVIDENCE": "collection or data-quality defects prevent a "
                        "meaningful inference",
            "QUALITY_GATE_FAILURES":
                (quality_report or {}).get("QUALITY_GATE_FAILURES", []),
            "INDEPENDENT_EVENTS": n,
            "DECISIONS": DECISIONS,
        }
    if structural_negative and n >= MIN_EVENTS_FOR_MORE_THAN_PILOT:
        return {
            "HARVEST_DECISION": "STOP_THIS_MICROSTRUCTURE_PATH",
            "EVIDENCE": "a scientifically adequate prospective experiment "
                        "falsified the useful hypotheses",
            "INDEPENDENT_EVENTS": n,
            "STOP_REQUIRES_A_STRUCTURAL_NEGATIVE":
                STOP_REQUIRES_A_STRUCTURAL_NEGATIVE,
            "DECISIONS": DECISIONS,
        }
    if structural_negative:
        # Asked for STOP, but the sample cannot carry it.
        return {
            "HARVEST_DECISION": "GO_TO_LARGER_PROSPECTIVE_CAPTURE",
            "EVIDENCE": "a structural negative was asserted, but at %d "
                        "independent events the experiment lacks the power to "
                        "falsify anything. STOP is refused" % n,
            "INDEPENDENT_EVENTS": n,
            "STOP_REFUSED_FOR_INSUFFICIENT_EVENTS": True,
            "STOP_REQUIRES_A_STRUCTURAL_NEGATIVE":
                STOP_REQUIRES_A_STRUCTURAL_NEGATIVE,
            "DO_NOT_OVERSTATE": DO_NOT_OVERSTATE,
            "DECISIONS": DECISIONS,
        }
    return {
        "HARVEST_DECISION": "GO_TO_LARGER_PROSPECTIVE_CAPTURE",
        "EVIDENCE": ("data quality is sufficient and %d finding(s) warrant "
                     "more independent events" % len(findings)),
        "FINDINGS": findings,
        "INDEPENDENT_EVENTS": n,
        "DO_NOT_OVERSTATE": DO_NOT_OVERSTATE,
        "DECISIONS": DECISIONS,
    }


def describe():
    return {
        "QUALITY_GATE_RUNS_FIRST": QUALITY_GATE_RUNS_FIRST,
        "WHY_FIRST": WHY_FIRST,
        "TRANSITION_CLASSES": TRANSITION_CLASSES,
        "POLLING_MAY_MISS_MAY_NOT_MANUFACTURE":
            POLLING_MAY_MISS_MAY_NOT_MANUFACTURE,
        "COUNT_IS_A_LOWER_BOUND": COUNT_IS_A_LOWER_BOUND,
        "FREQUENCY_DOES_NOT_INHERIT_THE_BOUND":
            FREQUENCY_DOES_NOT_INHERIT_THE_BOUND,
        "INDEPENDENCE_UNIT": INDEPENDENCE_UNIT,
        "PILOT_LABEL": PILOT_LABEL,
        "ROWS_ARE_NOT_EXPERIMENTS": ROWS_ARE_NOT_EXPERIMENTS,
        "COVERAGE_PCT_FLOOR": COVERAGE_PCT_FLOOR,
        "MAX_GAP_MULTIPLE_OF_NOMINAL": MAX_GAP_MULTIPLE_OF_NOMINAL,
        "THRESHOLDS_ARE_PREDECLARED": THRESHOLDS_ARE_PREDECLARED,
        "DECISIONS": DECISIONS,
        "STOP_REQUIRES_A_STRUCTURAL_NEGATIVE":
            STOP_REQUIRES_A_STRUCTURAL_NEGATIVE,
        "NO_VERSION_MEANS_UNKNOWN": NO_VERSION_MEANS_UNKNOWN,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
    }
