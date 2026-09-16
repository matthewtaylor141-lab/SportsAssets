#!/usr/bin/env python3
"""READ-ONLY RATE-LIMIT PILOT. Find a sustainable request rate before capturing.

WHY THIS EXISTS. Run 35120338223 polled 24 markets at 2.0 rps and took 5,835
HTTP 429s -- 81% of every read. Six markets survived, all NFL, and they survived
because they were polled FIRST. The pipeline worked perfectly and the sample was
destroyed. Repeating that experiment at the same rate would destroy it again.

So: measure the limit before spending another capture window on it.

WHAT THIS DOES, AND THE FOUR RULES IT FOLLOWS.

    SINGLE GLOBAL SCHEDULER   one pacer, one thread, no concurrency anywhere.
    NO BURSTS                 the gap is enforced BEFORE each request, always.
    GLOBAL BACKOFF ON 429     a 429 pauses EVERY market, not the one that hit
                              it. A per-market retry loop is exactly what
                              starves the markets later in the round.
    HONOUR RETRY-AFTER        if the venue sends one, we wait that long. If it
                              does not, we back off on our own schedule and say
                              which of the two happened.

WHAT THIS REFUSES TO DO.

Headers are REPORTED, never INTERPRETED. If the venue sends something that
looks like a budget header we record its name and value verbatim and stop
there. `RATE_LIMIT_SEMANTICS = NOT_IDENTIFIED` until the venue documents them:
guessing that some `x-ratelimit-remaining` counts requests per minute, and
pacing against that guess, would be the SHARES_TRADED mistake again in a
different field.

And the rate is not chosen for being fast. `SUSTAINABLE_RATE_SELECTED` is the
fastest rate whose measured 429 share is at or under the threshold with margin,
and the reason is recorded beside it. A rate that merely completed is not
evidence that it was sustainable.

NO ORDERS. NO CREDENTIALS. GET ONLY. This module contacts the venue, and
`test_rate_pilot.py` proves the boundary by AST rather than by grep.
"""
from __future__ import annotations

import json
import sys
import time
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect as C                                            # noqa: E402

NOT_IDENTIFIED = "NOT_IDENTIFIED"

READ_ONLY = True
ORDER_PATH_EXISTS = False
CREDENTIAL_PATH = "NONE"
mirror_live = False

# The one host, imported rather than retyped -- the error that was caught
# before the first dispatch and is not going to be reintroduced here.
HOST = C.HOST

# Header names worth LOOKING for. Finding one records it; it does not license
# an interpretation.
RATE_LIMIT_HEADER_CANDIDATES = (
    "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
    "x-ratelimit-reset", "ratelimit-limit", "ratelimit-remaining",
    "ratelimit-reset", "x-rate-limit-limit", "x-rate-limit-remaining",
    "x-rate-limit-reset",
)
RATE_LIMIT_SEMANTICS = NOT_IDENTIFIED
HEADERS_ARE_REPORTED_NOT_INTERPRETED = True

# The ladder, frozen here so it is not chosen after seeing the results.
DEFAULT_RATE_LADDER = ("0.25", "0.5", "1.0", "2.0")
REQUESTS_PER_RATE = 40
ACCEPTABLE_429_SHARE = D("0.01")
MARGIN_RULE = "PICK_THE_FASTEST_PASSING_RATE_ONE_STEP_BELOW_THE_FIRST_FAILURE"

# ---------------------------------------------------------------------------
# THE RUNGS ARE NOT KNOWN TO BE INDEPENDENT, AND THAT CHANGES WHAT A PASS MEANS
# ---------------------------------------------------------------------------
#
# The ladder is tested sequentially against one gateway whose accounting window
# is undocumented. If the limiter is a rolling window or a token bucket, a
# later rung inherits whatever the earlier rungs left behind -- so a rung that
# passed may have passed on credit, and a rung that failed may have failed on
# debt. Neither reading is available to us.
RATE_LIMIT_WINDOW_SEMANTICS = NOT_IDENTIFIED
SEQUENTIAL_RUNG_CARRYOVER_POSSIBLE = "YES"
RUNG_RESULT_INDEPENDENT = NOT_IDENTIFIED
WHY_NOT_INDEPENDENT = (
    "the venue documents no accounting window, so limiter state carried from "
    "an earlier rung can neither be excluded nor measured")

# ---------------------------------------------------------------------------
# TWO DIFFERENT QUESTIONS. ONE IS UNANSWERABLE; THE OTHER IS THE ONE WE NEED.
# ---------------------------------------------------------------------------
#
# An earlier version of this module required BOTH a sustained rung AND
# RUNG_INDEPENDENCE_ESTABLISHED before a capture could proceed -- while also
# stating, correctly, that independence can never be established from anything
# available to us. That is a permanent deadlock: a gate whose key does not
# exist. It was a defect, not caution.
#
# The defect came from collapsing two questions that are not the same question:
#
#   VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED   what the limiter actually is --
#       window shape, bucket size, accounting boundary, scope. Undocumented,
#       and not reverse-engineerable from headers. Stays NOT_IDENTIFIED, and
#       nothing downstream is allowed to wait on it.
#
#   COLLECTOR_RATE_OPERATIONALLY_VALIDATED  whether OUR collector, at ONE
#       chosen rate, ran long enough and clean enough to be trusted to collect
#       public books. YES or NO. Measurable. This is the gate that matters.
#
# The second can become YES while the first stays NOT_IDENTIFIED. We do not
# need to know how the venue counts in order to know that our own paced reader
# did not get refused over twenty minutes.
VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED = NOT_IDENTIFIED
OPERATIONAL_VALIDATION_DOES_NOT_REQUIRE_MECHANISM_IDENTIFICATION = True
WHY_THESE_ARE_SEPARATE = (
    "identifying the venue's limiter is not a precondition for reading public "
    "books at a rate our own collector has been observed to sustain")

# ---------------------------------------------------------------------------
# WHAT THIS LADDER MAY AND MAY NOT CONCLUDE
# ---------------------------------------------------------------------------
#
# A sequential ladder, short rungs, no proof of a clean start between them, is
# a screening instrument. It is good at one thing -- showing that a rate is
# obviously unsafe -- and it can nominate a conservative starting point. It
# cannot confirm anything, and it is not going to be rescued into confirming
# anything by adding conditions to its own output.
LADDER_MAY = ("REJECT_OBVIOUSLY_UNSAFE_RATES",
              "NOMINATE_A_CONSERVATIVE_RATE_CANDIDATE")
LADDER_MAY_NOT = ("CONFIRM_A_SUSTAINABLE_RATE",)
LADDER_CONFIRMATION_STATUS = "REFUSED_THE_LADDER_MAY_NOT_CONFIRM"
CONFIRMATION_ROUTE = "SINGLE_RATE_OPERATIONAL_CONFIRMATION"
WHY_THE_LADDER_MAY_NOT_CONFIRM = (
    "short sequential rungs against an undocumented limiter, with no proof "
    "that any rung started clean; see rate_confirm.py for the route that can "
    "return YES")

# The cooldown between rungs, in preference order. C never claims a clean
# start -- it is a wait, and a wait is not evidence the server forgot.
COOLDOWN_BASIS_RETRY_AFTER = "RETRY_AFTER_HONOURED_GLOBALLY"
COOLDOWN_BASIS_OBSERVED_RESET = "OBSERVED_RESET_TIMING"
COOLDOWN_BASIS_FROZEN = "FROZEN_CONSERVATIVE_COOLDOWN"
FROZEN_COOLDOWN_S = 90.0
CLEAN_SERVER_RATE_LIMIT_STATE_WHEN_FROZEN = "NOT_ESTABLISHED"
DO_NOT_REVERSE_ENGINEER_HEADERS_TO_CLAIM_A_CLEAN_START = True

# A rung is NOT_REFUSED or REFUSED. Neither verdict is a confirmation, and no
# combination of rungs adds up to one -- the evidence floor lives in
# rate_confirm.py, where a single standalone rate can actually clear it.
RUNG_NOT_REFUSED = "NOT_REFUSED"
RUNG_REFUSED = "REFUSED"
RUNG_MAY_CONFIRM = False

# Headroom. The objective is balanced complete data, not throughput.
HEADROOM_RULE = "ONE_LADDER_RUNG_BELOW_THE_FASTEST_PASSING_RATE"
OBJECTIVE = "BALANCED_COMPLETE_DATA_NOT_MAXIMUM_REQUEST_THROUGHPUT"

# A 429 stops everything for this long when the venue does not say otherwise.
DEFAULT_BACKOFF_S = 30.0
MAX_BACKOFF_S = 120.0


class GlobalPacer:
    """One gap for the whole run, plus a global pause after a 429.

    The pause is GLOBAL on purpose. The previous capture's per-market read
    simply moved on to the next slug after a refusal, which meant the earliest
    slugs consumed the budget every round and the later ones never got a turn.
    """

    def __init__(self, rps):
        self.gap = 1.0 / float(rps)
        self.last = 0.0
        self.paused_until = 0.0
        self.pauses = 0
        self.paused_s = 0.0

    def wait(self):
        now = time.monotonic()
        due = max(self.last + self.gap, self.paused_until)
        if now < due:
            time.sleep(due - now)
        self.last = time.monotonic()

    def back_off(self, retry_after=None):
        """Pause every market. Honour Retry-After when the venue sends one."""
        if retry_after is not None:
            try:
                wait = min(float(retry_after), MAX_BACKOFF_S)
                src = "RETRY_AFTER_HEADER"
            except (TypeError, ValueError):
                wait, src = DEFAULT_BACKOFF_S, "OUR_OWN_SCHEDULE"
        else:
            wait, src = DEFAULT_BACKOFF_S, "OUR_OWN_SCHEDULE"
        self.paused_until = time.monotonic() + wait
        self.pauses += 1
        self.paused_s += wait
        return {"BACKOFF_S": wait, "BACKOFF_SOURCE": src}


def observed_headers(headers):
    """Any rate-limit-looking header, verbatim. No meaning attached."""
    if not headers:
        return {}
    low = {str(k).lower(): v for k, v in dict(headers).items()}
    return {k: low[k] for k in RATE_LIMIT_HEADER_CANDIDATES if k in low}


def probe(http, pacer, slug):
    """One paced public GET, keeping the response headers. GET only."""
    pacer.wait()
    url = HOST + C.BOOK_PATH.format(slug=slug)
    recv = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    t0 = time.monotonic()
    try:
        r = http.get(url, timeout=20.0)
    except Exception as exc:                                   # noqa: BLE001
        return {"slug": slug, "RECEIPT_UTC": recv, "status": None,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "LATENCY_S": time.monotonic() - t0, "HEADERS": {}}
    return {
        "slug": slug, "RECEIPT_UTC": recv, "status": r.status_code,
        "error": None if r.status_code == 200 else "http_%d" % r.status_code,
        "LATENCY_S": time.monotonic() - t0,
        "HEADERS": observed_headers(getattr(r, "headers", None)),
    }


def _pct(vals, p):
    v = sorted(vals)
    if not v:
        return NOT_IDENTIFIED
    k = max(0, min(len(v) - 1, int(round((p / 100.0) * (len(v) - 1)))))
    return v[k]


def rung_cooldown(last_retry_after=None, reset_timing=None):
    """How long to wait before the next rung, and on what basis.

    A. a valid Retry-After is honoured globally
    B. an OBSERVED reset timing, if one is ever reliably exposed
    C. otherwise a frozen conservative wait -- and then
       CLEAN_SERVER_RATE_LIMIT_STATE = NOT_ESTABLISHED, because waiting is not
       the same as knowing the server forgot.

    Route B is present and unused: no reset timing is exposed today, and
    guessing one out of an undocumented header to claim a clean start is the
    thing this function exists to refuse.
    """
    if last_retry_after is not None:
        try:
            return {"COOLDOWN_S": min(float(last_retry_after), MAX_BACKOFF_S),
                    "COOLDOWN_BASIS": COOLDOWN_BASIS_RETRY_AFTER,
                    "CLEAN_SERVER_RATE_LIMIT_STATE": "ASSERTED_BY_VENUE"}
        except (TypeError, ValueError):
            pass
    if reset_timing is not None:
        try:
            return {"COOLDOWN_S": float(reset_timing),
                    "COOLDOWN_BASIS": COOLDOWN_BASIS_OBSERVED_RESET,
                    "CLEAN_SERVER_RATE_LIMIT_STATE": "OBSERVED"}
        except (TypeError, ValueError):
            pass
    return {"COOLDOWN_S": FROZEN_COOLDOWN_S,
            "COOLDOWN_BASIS": COOLDOWN_BASIS_FROZEN,
            "CLEAN_SERVER_RATE_LIMIT_STATE":
                CLEAN_SERVER_RATE_LIMIT_STATE_WHEN_FROZEN}


def run_rate(http, slugs, rps, requests=REQUESTS_PER_RATE, rows=None):
    """Probe at ONE rate, rotating through the slugs. Returns the result row.

    The rotation matters even in the pilot: if a rate is marginal, we want the
    refusals spread across markets rather than concentrated on whichever slug
    happens to be last.
    """
    pacer = GlobalPacer(rps)
    ok = n429 = other = valid_ra = 0
    headers_seen, backoffs, lat = {}, [], []
    first_429_at = NOT_IDENTIFIED
    last_retry_after = None
    t0 = time.monotonic()
    for i in range(requests):
        r = probe(http, pacer, slugs[i % len(slugs)])
        if rows is not None:
            rows.append(dict(r, RATE=str(rps)))
        for k, v in r["HEADERS"].items():
            headers_seen.setdefault(k, str(v))
        lat.append(r["LATENCY_S"])
        if r["status"] == 200:
            ok += 1
        elif r["status"] == 429:
            n429 += 1
            if first_429_at == NOT_IDENTIFIED:
                first_429_at = time.monotonic() - t0
            ra = r["HEADERS"].get("retry-after")
            if ra is not None:
                try:
                    float(ra)
                    valid_ra += 1
                    last_retry_after = ra
                except (TypeError, ValueError):
                    pass
            backoffs.append(pacer.back_off(ra))
        else:
            other += 1
    share = (D(n429) / D(requests)) if requests else NOT_IDENTIFIED
    dur = time.monotonic() - t0
    passes = share != NOT_IDENTIFIED and share <= ACCEPTABLE_429_SHARE
    return {
        "RATE_RPS": str(rps),
        "REQUESTS": requests,
        "DURATION_S": dur,
        "SUCCESSFUL_REQUESTS": ok,
        "HTTP_429": n429,
        "HTTP_429_COUNT": n429,
        "OTHER_FAILURES": other,
        "HTTP_429_SHARE": share,
        "TIME_TO_FIRST_429": first_429_at,
        "VALID_RETRY_AFTER_COUNT": valid_ra,
        "P50_RESPONSE_LATENCY": _pct(lat, 50),
        "P90_RESPONSE_LATENCY": _pct(lat, 90),
        "P99_RESPONSE_LATENCY": _pct(lat, 99),
        "GLOBAL_BACKOFFS": len(backoffs),
        "BACKOFF_SOURCES": sorted({b["BACKOFF_SOURCE"] for b in backoffs}),
        "RATE_LIMIT_HEADERS_SEEN": headers_seen,
        "LAST_VALID_RETRY_AFTER": last_retry_after,

        # A rung is NOT_REFUSED or REFUSED. It is never CONFIRMED: this rung
        # is one leg of a sequential ladder with no proof of a clean start, so
        # there is no sample size at which it would start confirming.
        "PASSES": passes,
        "RUNG_VERDICT": (RUNG_NOT_REFUSED if passes else RUNG_REFUSED),
        "RUNG_MAY_CONFIRM": RUNG_MAY_CONFIRM,
        "WHY_A_RUNG_CANNOT_CONFIRM": WHY_THE_LADDER_MAY_NOT_CONFIRM,
    }


def select_rate(ladder_results, rung_independence=NOT_IDENTIFIED):
    """The THREE things this ladder is allowed to say, and nothing more.

      UNSAFE_RATES               every rung the venue refused. This is the
                                 ladder's real product: a rejection is robust
                                 to carryover in the direction that matters --
                                 a rung that was refused is not a rate we are
                                 going to argue our way back to.
      CANDIDATE_RATE             the fastest rung that was NOT refused, taken
                                 BEFORE the first failure. A nomination. A
                                 starting point for a confirmation, not a
                                 finding.
      RECOMMENDED_HEADROOM_RATE  one measured rung BELOW the candidate. If the
                                 candidate is the slowest rung on the ladder
                                 there is no such rung, and this comes back
                                 NOT_IDENTIFIED rather than quietly resolving
                                 to the candidate itself -- "no headroom
                                 available" is a result, and dropping it is
                                 how a candidate becomes an operating rate.

    CAPTURE_MAY_PROCEED is False here ALWAYS, and the reason names a route that
    can actually return YES. That is the difference between a gate and a
    deadlock: this one says what unlocks it.
    """
    chosen, reason, idx = None, "", None
    unsafe = [row["RATE_RPS"] for row in ladder_results if not row["PASSES"]]
    for i, row in enumerate(ladder_results):
        if row["PASSES"]:
            chosen, idx = row["RATE_RPS"], i
        else:
            reason = ("stopped at the first rate whose 429 share exceeded "
                      "%s (%s rps)" % (ACCEPTABLE_429_SHARE, row["RATE_RPS"]))
            break
    base = {
        "LADDER_MAY": list(LADDER_MAY),
        "LADDER_MAY_NOT": list(LADDER_MAY_NOT),
        "SUSTAINABLE_RATE_CONFIRMED": LADDER_CONFIRMATION_STATUS,
        "WHY_THE_LADDER_MAY_NOT_CONFIRM": WHY_THE_LADDER_MAY_NOT_CONFIRM,
        "CONFIRMATION_ROUTE": CONFIRMATION_ROUTE,

        "UNSAFE_RATES": unsafe,
        "RATE_LIMIT_WINDOW_SEMANTICS": RATE_LIMIT_WINDOW_SEMANTICS,
        "VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED":
            VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED,
        "COLLECTOR_RATE_OPERATIONALLY_VALIDATED": "NO",
        "WHY_NOT_OPERATIONALLY_VALIDATED":
            "no standalone single-rate confirmation has been run yet",
        "OPERATIONAL_VALIDATION_DOES_NOT_REQUIRE_MECHANISM_IDENTIFICATION":
            OPERATIONAL_VALIDATION_DOES_NOT_REQUIRE_MECHANISM_IDENTIFICATION,
        "SEQUENTIAL_RUNG_CARRYOVER_POSSIBLE": SEQUENTIAL_RUNG_CARRYOVER_POSSIBLE,
        "RUNG_RESULT_INDEPENDENT": RUNG_RESULT_INDEPENDENT,
        "PILOT_RUNG_INDEPENDENCE": rung_independence,
        "WHY_NOT_INDEPENDENT": WHY_NOT_INDEPENDENT,
        "INDEPENDENCE_IS_NOT_A_PRECONDITION_OF_VALIDATION": True,
        "OBJECTIVE": OBJECTIVE,
        "HEADROOM_RULE": HEADROOM_RULE,
        "MARGIN_RULE": MARGIN_RULE,
        "NOT_CHOSEN_FOR_BEING_FASTEST": True,
        "CAPTURE_MAY_PROCEED": False,
        "WHY_CAPTURE_MAY_NOT_PROCEED": (
            "a screening ladder cannot confirm a rate; run the standalone "
            "single-rate confirmation in rate_confirm.py, then re-check"),
    }
    if chosen is None:
        base.update({
            "CANDIDATE_RATE": NOT_IDENTIFIED,
            "SUSTAINABLE_RATE_CANDIDATE": NOT_IDENTIFIED,
            "RECOMMENDED_HEADROOM_RATE": NOT_IDENTIFIED,
            "RECOMMENDED_CAPTURE_RATE": NOT_IDENTIFIED,
            "SUSTAINABLE_RATE_SELECTED": NOT_IDENTIFIED,
            "RECOMMENDED_RATE_HAS_HEADROOM": False,
            "SELECTION_REASON": ("no tested rate held the 429 share at or "
                                 "under %s; the ladder needs a slower rung"
                                 % ACCEPTABLE_429_SHARE),
        })
        return base

    below = (ladder_results[idx - 1]["RATE_RPS"] if idx and idx > 0 else None)
    base.update({
        "CANDIDATE_RATE": chosen,
        "SUSTAINABLE_RATE_CANDIDATE": chosen,
        "RECOMMENDED_HEADROOM_RATE": (below if below is not None
                                      else NOT_IDENTIFIED),
        "RECOMMENDED_CAPTURE_RATE": (below if below is not None
                                     else NOT_IDENTIFIED),
        "RECOMMENDED_RATE_HAS_HEADROOM": below is not None,
        "HEADROOM_NOTE": (None if below is not None else
                          "the candidate is the ladder's slowest MEASURED "
                          "rung, so no rung below it was tested; a headroom "
                          "rate would have to come from extending the ladder "
                          "downward, which is a new measurement, not an "
                          "inference from this one"),
        # The old field name, kept pointing at the CANDIDATE so nothing silently
        # reads a confirmation that was never made.
        "SUSTAINABLE_RATE_SELECTED": chosen,
        "SELECTION_REASON": (reason or
                             "every tested rate passed; the ladder's top rung "
                             "is the candidate and a faster one is UNTESTED"),
    })
    return base


def pilot(outdir, slugs, http, ladder=DEFAULT_RATE_LADDER,
          requests=REQUESTS_PER_RATE, cooldown=True):
    """The whole pilot: ascending ladder, a COOLDOWN between rungs, a selection.

    The cooldown is what makes a rung even arguably a standalone measurement.
    It does not make the rungs independent -- nothing available to us does --
    but without it a later rung is certainly reading the earlier rung's
    leftovers, and PILOT_RUNG_INDEPENDENCE would be NOT_ESTABLISHED for a
    second, avoidable reason.
    """
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    rows, ladder_results, cooldowns = [], [], []
    for i, rps in enumerate(ladder):
        if i and cooldown:
            prev = ladder_results[-1]
            cd = rung_cooldown(prev.get("LAST_VALID_RETRY_AFTER"))
            cooldowns.append(dict(cd, BEFORE_RATE=str(rps)))
            time.sleep(cd["COOLDOWN_S"])
        ladder_results.append(run_rate(http, slugs, rps, requests, rows))
    with (out / "pilot_rows.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")

    total = sum(r["REQUESTS"] for r in ladder_results)
    ok = sum(r["SUCCESSFUL_REQUESTS"] for r in ladder_results)
    n429 = sum(r["HTTP_429_COUNT"] for r in ladder_results)
    headers = {}
    for r in ladder_results:
        headers.update(r["RATE_LIMIT_HEADERS_SEEN"])

    # Independence is NEVER "ESTABLISHED" here. A cooldown is a wait, not a
    # proof that the limiter reset, and the window semantics are undocumented.
    independence = ("NOT_ESTABLISHED" if cooldown
                    else "NOT_ESTABLISHED_NO_COOLDOWN_BETWEEN_RUNGS")

    report = {
        "PILOT_REQUESTS": total,
        "SUCCESSFUL_REQUESTS": ok,
        "HTTP_429_COUNT": n429,
        "HTTP_429_RATE": (D(n429) / D(total)) if total else NOT_IDENTIFIED,
        "OTHER_FAILURES": sum(r["OTHER_FAILURES"] for r in ladder_results),
        "TESTED_REQUEST_RATES": [r["RATE_RPS"] for r in ladder_results],
        "PER_RATE": ladder_results,
        "MARKETS_PROBED": len(slugs),

        # Headers: recorded, not interpreted.
        "RATE_LIMIT_HEADERS_SEEN": headers,
        "RATE_LIMIT_HEADERS_FOUND": bool(headers),
        "RATE_LIMIT_SEMANTICS": RATE_LIMIT_SEMANTICS,
        "HEADERS_ARE_REPORTED_NOT_INTERPRETED":
            HEADERS_ARE_REPORTED_NOT_INTERPRETED,

        "ACCEPTABLE_429_SHARE": ACCEPTABLE_429_SHARE,

        # Between-rung separation, and what it does and does not buy.
        "COOLDOWN_BETWEEN_RUNGS": bool(cooldown),
        "COOLDOWNS": cooldowns,
        "CLEAN_SERVER_RATE_LIMIT_STATE": (
            "NOT_ESTABLISHED" if not cooldowns else
            sorted({c["CLEAN_SERVER_RATE_LIMIT_STATE"] for c in cooldowns})),
        "PILOT_RUNG_INDEPENDENCE": independence,
        "DO_NOT_REVERSE_ENGINEER_HEADERS_TO_CLAIM_A_CLEAN_START":
            DO_NOT_REVERSE_ENGINEER_HEADERS_TO_CLAIM_A_CLEAN_START,

        "SCHEDULER": "SINGLE_GLOBAL_PACER_NO_CONCURRENCY",
        "BACKOFF": "GLOBAL_ON_429_HONOURING_RETRY_AFTER_WHEN_PRESENT",
        "PER_MARKET_RETRY_LOOP": "NONE_BY_CONSTRUCTION",

        "ORDERS_PLACED": 0,
        "CREDENTIALS": CREDENTIAL_PATH,
        "mirror_live": mirror_live,
    }
    report.update(select_rate(ladder_results, independence))
    with (out / "pilot_report.json").open("w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True, default=str)
    return report


def _cli():                                                   # pragma: no cover
    import argparse
    import httpx
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--markets", type=int, default=6)
    ap.add_argument("--requests", type=int, default=REQUESTS_PER_RATE)
    a = ap.parse_args()
    u = json.loads(Path(a.universe).read_text())
    slugs = [m["slug"] for m in u["MARKETS"]][:a.markets]
    with httpx.Client(headers={"accept": "application/json"}) as http:
        rep = pilot(a.out, slugs, http, requests=a.requests)
    print(json.dumps({k: str(v) for k, v in rep.items()
                      if k != "PER_RATE"}, indent=1, sort_keys=True))
    for r in rep["PER_RATE"]:
        print(" RATE", r["RATE_RPS"], "ok", r["SUCCESSFUL_REQUESTS"],
              "429", r["HTTP_429_COUNT"], "share", str(r["HTTP_429_SHARE"]),
              "passes", r["PASSES"])


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
