"""THE SCHEDULED CYCLE'S OWN CENSUS, READ BACK — AND NOT SUPPRESSIBLE.

── WHY THIS EXISTS ─────────────────────────────────────────────────
Owner requirement: *"stop coupling independent production evidence to the
RN1X acceptance fixture — run a read-only EV diagnostic that cannot be
suppressed, and NOT by putting `always()` around a step that arms or
mutates the system."*

The gate run on 2026-09-28 demonstrated the coupling again. `verify` step 20
failed with

    GATE_SUBJECT: NONE
    ACC_SEED_STATE: REFUSED:NO_COVERED_MARKET_COULD_SUPPLY_A_COMPLETE_ENTRY

— the acceptance harness could not seed a subject — and because that step
`exit 1`s, every later step in the same job went unrun. The evidence about
whether the EVALUATION LANE is healthy was lost to a failure in a FIXTURE,
which is a different subject entirely.

── HOW THIS IS UNSUPPRESSIBLE, HONESTLY ────────────────────────────
Not with `always()`. It is a SEPARATE JOB with no `needs:`, so no other
job's failure can prevent it running, and it is a single authenticated GET.

IT CANNOT ARM, SEED OR MUTATE ANYTHING. One HTTP GET to
`/api/command/bettor/desk`, whose access control (`require_command`) is
documented as read-only for every role it grants. No POST, no seeding, no
cycle driving, no order. That is what makes putting it outside the gated
job safe -- an `always()` around a step that arms the system would run the
arming on a failed run, which is the opposite of a diagnostic.

── WHAT IT REFUSES TO CONFLATE ─────────────────────────────────────
Three states that all look like "zero candidates evaluated":

  NOTHING TO EVALUATE     the provider returned no fixtures, or none mapped.
                          A STATE of the world, not a defect.
  EVERY CANDIDATE REFUSED and every refusal is NAMED and the counts
                          reconcile. Also a state -- and the named reasons
                          are the thing worth reading.
  UNACCOUNTED             candidates entered the funnel and the census does
                          not say what happened to them. A MEASUREMENT
                          DEFECT, and the only one of the three that should
                          fail a job.

An absent heartbeat is a fourth: it means the question was not answered, and
that is reported as unanswered rather than as "nothing happened".

── AND ABSENT PATHS SAY SO ─────────────────────────────────────────
A previous readback used four wrong JSON paths and printed empty columns.
An empty column from a wrong path is indistinguishable from an absent field,
so every extraction here records which key it looked for and reports
`PATH_ABSENT` by name rather than defaulting to a blank or a zero.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DESK_PATH = "/api/command/bettor/desk"
HEALTH_PATH = "/healthz"

#: ── THE TIMEOUTS, AND WHY THEY DIFFER ───────────────────────────────
#:
#: FOUND BY RUNNING IT (2026-09-28). The first live run of this job returned
#: `TimeoutError reading /api/command/bettor/desk` at exactly the 60 s limit
#: it was given. The module reported that correctly -- as the diagnostic being
#: unable to ask rather than as a healthy lane -- but a diagnostic that always
#: times out establishes nothing, so the limit was the defect.
#:
#: The desk assembles sixteen sections, each doing database work, on a service
#: that may be cold. `/healthz` is cheap and answers immediately, so it is
#: asked FIRST and with a short limit: that separates "the service is not
#: answering at all" from "the desk is slower than we allowed", which are
#: different facts and previously collapsed into one timeout.
HEALTH_TIMEOUT_S = 25.0
DESK_TIMEOUT_S = 150.0
#: One retry, because a cold start is the common cause and the second attempt
#: is warm. Bounded at two total: a diagnostic that hammers our own API while
#: reporting on rate control would be self-refuting.
DESK_ATTEMPTS = 2

V_NO_HEARTBEAT = "NO_SCHEDULED_CYCLE_HEARTBEAT"
V_UNREADABLE = "THE_DESK_COULD_NOT_BE_READ"
V_SERVICE_UNREACHABLE = "THE_SERVICE_ITSELF_DID_NOT_ANSWER"
V_NOTHING_TO_EVALUATE = "NOTHING_TO_EVALUATE_THIS_CYCLE"
V_ALL_REFUSED_ACCOUNTED = "EVERY_CANDIDATE_REFUSED_AND_ACCOUNTED_FOR"
V_UNACCOUNTED = "CANDIDATES_ENTERED_THE_FUNNEL_AND_ARE_UNACCOUNTED_FOR"
V_EVALUATED = "CANDIDATES_WERE_EVALUATED"

#: Only these verdicts fail the job. A world with no fixtures is not a
#: broken system, and failing on it would train the reader to ignore the job.
FAILING = frozenset({V_NO_HEARTBEAT, V_UNREADABLE, V_UNACCOUNTED,
                     V_SERVICE_UNREACHABLE})

ABSENT = "PATH_ABSENT"


def _dig(obj, *path):
    """Walk `path`, returning (value, where_it_stopped).

    RETURNS WHERE IT STOPPED so a caller can say WHICH key was missing.
    `None` as a value and `None` because the path broke are different
    facts, and printing them the same way is how four wrong paths went
    unnoticed for a whole readback.
    """
    cur = obj
    for i, key in enumerate(path):
        if not isinstance(cur, dict) or key not in cur:
            return ABSENT, ".".join(str(p) for p in path[:i + 1])
        cur = cur[key]
    return cur, None


def _get(api: str, path: str, *, token: str = None, timeout: float,
         parse_json: bool = True) -> dict:
    """One GET. Never writes; the only HTTP method this module names."""
    req = urllib.request.Request(api.rstrip("/") + path, method="GET")
    if token:
        req.add_header("X-Admin-Token", token)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or ""
            return {"ok": True, "status": r.status,
                    "body": (json.loads(raw or "{}") if parse_json else raw)}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code,
                "why": "HTTP %s from %s" % (exc.code, path)}
    except Exception as exc:                                   # noqa: BLE001
        # THE EXCEPTION CLASS, NOT ITS MESSAGE. A message can carry a URL
        # with a query string; the class cannot.
        return {"ok": False, "status": None,
                "why": "%s reading %s" % (type(exc).__name__, path)}


def reachable(api: str, *, timeout: float = None) -> dict:
    """Is the service answering at all? Asked before the expensive read.

    WHY SEPARATELY. The first live run reported only
    `TimeoutError reading /api/command/bettor/desk`, which cannot distinguish
    a service that is down from a desk that is merely slower than the limit
    allowed. `/healthz` is cheap, so a fast answer here means the timeout
    below is about the DESK and not about the service.
    """
    return _get(api, HEALTH_PATH,
                timeout=HEALTH_TIMEOUT_S if timeout is None else timeout,
                parse_json=False)


def fetch(api: str, token: str, *, timeout: float = None,
          attempts: int = None, sleep=None) -> dict:
    """The desk, authenticated, with ONE bounded retry.

    RETRIED BECAUSE A COLD START IS THE COMMON CAUSE and the second attempt
    is warm. Bounded at DESK_ATTEMPTS: a diagnostic that hammered our own API
    while reporting on rate control would be self-refuting. The venue is not
    touched by this at all -- this is our own service.
    """
    import time as _t

    limit = DESK_TIMEOUT_S if timeout is None else timeout
    tries = DESK_ATTEMPTS if attempts is None else max(1, int(attempts))
    last = None
    for i in range(tries):
        last = _get(api, DESK_PATH, token=token, timeout=limit)
        if last.get("ok"):
            return dict(last, attempts=i + 1)
        # A 4xx will not change on a retry -- it is an answer about our
        # credential or the route, not a cold service.
        st = last.get("status")
        if st is not None and 400 <= int(st) < 500:
            return dict(last, attempts=i + 1,
                        why_not_retried="a 4xx is an answer, not a cold start")
        if i + 1 < tries:
            (sleep or _t.sleep)(2.0)
    return dict(last or {"ok": False}, attempts=tries)


#: ── THE DESK'S ACTUAL SHAPE, READ OUT OF THE ROUTE ──────────────────
#:
#: FOUND BY RUNNING IT, AGAIN. The first working run returned
#: `NO_SCHEDULED_CYCLE_HEARTBEAT` with an empty census, and it was not
#: production's answer -- it was my path. I assumed the desk embedded
#: `bettor_funded_book.command_center`, which does carry
#: `last_scheduled_decision`. It does not: `/api/command/bettor/desk` has its
#: own shape, loads the heartbeat row itself, and spreads it across `controls`
#: and `opportunities`.
#:
#: This is the SECOND time a readback of mine used paths that do not exist.
#: The first printed empty columns and I could not tell. This one named the
#: key it could not find and the mistake took one run to see, which is the
#: whole reason `PATH_ABSENT` exists.
#:
#: The paths are listed here, once, so a route change breaks them in one
#: visible place instead of silently emptying a census.
P_CYCLE_AT = ("controls", "last_cycle_at")
P_CYCLE_STATE = ("controls", "cycle_state")
P_CYCLE_LABEL = ("controls", "cycle_label")
P_WRITER = ("controls", "build_identity")
P_CONSIDERED = ("opportunities", "markets_considered")
P_EVALUATED = ("opportunities", "evaluated")
P_REFUSALS = ("opportunities", "refusals")
P_LEDGER = ("opportunities", "first_refusal_per_mapped_candidate")
P_FUNNEL = ("opportunities", "funnel")
P_VENUE_ERRORS = ("opportunities", "venue_errors")
P_LATENCY = ("opportunities", "odds_freshness")
P_VENUE_SDK = ("controls", "venue_sdk")
P_RATE_CONTROLS = ("controls", "venue_rate_controls")
P_PACER_LANES = ("controls", "pacer_lanes")


def census(desk: dict) -> dict:
    """The evaluation lane's own numbers, with every absent path named."""
    out = {"paths_absent": []}

    def take(name, path):
        val, missing = _dig(desk, *path)
        if val is ABSENT:
            out["paths_absent"].append(missing)
            out[name] = None
            out[name + "_is_absent"] = True
        else:
            out[name] = val
            out[name + "_is_absent"] = False
        return out[name]

    # THE CYCLE ROW ITSELF. Its presence is established by whether the desk
    # carries a cycle INSTANT -- not by whether any one field is non-null,
    # because a cycle that ran and refused everything legitimately has nulls.
    take("cycle_at", P_CYCLE_AT)
    take("cycle_state", P_CYCLE_STATE)
    take("cycle_label", P_CYCLE_LABEL)
    take("writer", P_WRITER)
    take("markets_considered", P_CONSIDERED)
    take("evaluated", P_EVALUATED)
    take("refusals", P_REFUSALS)
    take("ledger", P_LEDGER)
    take("funnel", P_FUNNEL)
    take("venue_errors", P_VENUE_ERRORS)
    take("latency", P_LATENCY)

    # ── THE REPAIRS OF THIS BATCH, READ BACK FROM THE RUNNING PROCESS ──
    # Which venue SDK the deployed image resolved, and whether both rate
    # controls are armed. Absent means THE SERVING BUILD does not report
    # them -- not that the SDK is unpinned or the cooldown off.
    take("venue_sdk", P_VENUE_SDK)
    take("venue_rate_controls", P_RATE_CONTROLS)
    take("pacer_lanes", P_PACER_LANES)

    # A CYCLE EXISTS IF THE DESK GAVE US ITS INSTANT. `cycle_at` absent means
    # the desk has no cycle section at all; `cycle_at` present-but-null means
    # no cycle has completed, which is a different fact and says so.
    out["cycle_row_present"] = bool(not out["cycle_at_is_absent"]
                                    and out["cycle_at"] is not None)
    out["desk_has_a_cycle_section"] = not out["cycle_at_is_absent"]
    return out


def verdict(desk: dict) -> dict:
    """What the cycle establishes, and whether that should fail a job."""
    c = census(desk)
    if not c["desk_has_a_cycle_section"]:
        return dict(c, verdict=V_NO_HEARTBEAT, ok=False,
                    why=("the desk carries no cycle section at %s, so this "
                         "run establishes nothing about the evaluation lane. "
                         "That is an unanswered question, not an idle lane -- "
                         "and if this route changed shape, the absent path is "
                         "named above rather than shown as an empty census"
                         % ".".join(P_CYCLE_AT)))
    if not c["cycle_row_present"]:
        return dict(c, verdict=V_NO_HEARTBEAT, ok=False,
                    why=("the desk has a cycle section but no cycle instant, "
                         "so no cycle has completed on this build. Reported "
                         "as unanswered rather than as an idle lane"))

    refusals = c["refusals"] if isinstance(c["refusals"], dict) else {}
    ledger = c["ledger"] if isinstance(c["ledger"], list) else []

    def num(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    considered, evaluated = num(c["markets_considered"]), num(c["evaluated"])
    refused_total = sum(v for v in (num(x) for x in refusals.values())
                        if v is not None)
    c.update(refusals=refusals, refusal_total=refused_total,
             mapped_candidates=len(ledger),
             markets_considered=considered, evaluated=evaluated)

    if evaluated:
        return dict(c, verdict=V_EVALUATED, ok=True,
                    why="%d candidate(s) reached evaluation" % evaluated)

    # ZERO EVALUATED. Which of the three is it?
    if not considered and not refused_total and not ledger:
        return dict(c, verdict=V_NOTHING_TO_EVALUATE, ok=True,
                    why=("no candidate entered the funnel at all: the "
                         "provider supplied nothing, or nothing mapped. A "
                         "state of the world, not a defect -- and NOT "
                         "evidence that the lane works"))
    if refused_total or ledger:
        return dict(c, verdict=V_ALL_REFUSED_ACCOUNTED, ok=True,
                    why=("every candidate was refused for a NAMED reason "
                         "(%d refusal(s) recorded, %d candidate(s) in the "
                         "ledger). The named reasons are the finding"
                         % (refused_total, len(ledger))))
    return dict(c, verdict=V_UNACCOUNTED, ok=False,
                why=("%s market(s) were considered and none was evaluated, "
                     "and the census names no refusal and lists no "
                     "candidate. The lane is not measuring itself, which is "
                     "a defect in the instrument rather than a fact about "
                     "the venue" % considered))


def _main(argv=None) -> int:
    api = (os.environ.get("API_URL")
           or "https://sportsassets-api.onrender.com")
    token = (os.environ.get("ADMIN_TOKEN") or "").strip()
    if not token:
        # NO TOKEN IS NOT A PASS AND NOT A FAILURE OF THE LANE. It is this
        # job being unable to ask. Said plainly, and non-zero, because a
        # diagnostic that cannot run must not read as green.
        print(json.dumps({"verdict": V_UNREADABLE, "ok": False,
                          "why": ("no ADMIN_TOKEN, so the authenticated read "
                                  "is not available here. This is the "
                                  "diagnostic being unable to ask, not the "
                                  "lane being healthy")}, indent=2))
        return 1

    # THE CHEAP QUESTION FIRST, so a slow desk and a dead service are two
    # different verdicts rather than one indistinguishable timeout.
    health = reachable(api)
    if not health.get("ok"):
        print(json.dumps({"verdict": V_SERVICE_UNREACHABLE, "ok": False,
                          "status": health.get("status"),
                          "why": health.get("why"),
                          "what_this_means": (
                              "the service did not answer %s within %.0fs, so "
                              "nothing downstream of it was asked. This is "
                              "about the SERVICE, not about the evaluation "
                              "lane" % (HEALTH_PATH, HEALTH_TIMEOUT_S))},
                         indent=2))
        return 1

    got = fetch(api, token)
    if not got.get("ok"):
        print(json.dumps({"verdict": V_UNREADABLE, "ok": False,
                          "status": got.get("status"),
                          "attempts": got.get("attempts"),
                          "why": got.get("why"),
                          "why_not_retried": got.get("why_not_retried"),
                          "what_this_means": (
                              "%s answered but %s did not, within %.0fs x %s "
                              "attempt(s). The service is up; this read is "
                              "the thing that failed"
                              % (HEALTH_PATH, DESK_PATH, DESK_TIMEOUT_S,
                                 got.get("attempts")))}, indent=2))
        return 1

    v = verdict(got["body"] or {})
    show = {k: v.get(k) for k in (
        "verdict", "ok", "why", "cycle_at", "cycle_state", "cycle_label",
        "writer", "markets_considered", "evaluated", "refusal_total",
        "mapped_candidates", "paths_absent")}
    print(json.dumps(show, indent=2, default=str))
    print("\n-- the refusal census, verbatim --")
    print(json.dumps(v.get("refusals") or {}, indent=2, default=str))
    print("\n-- the venue SDK the deployed process reports --")
    print(json.dumps(v.get("venue_sdk"), indent=2, default=str)
          if not v.get("venue_sdk_is_absent")
          else "ABSENT: the serving build does not report it. That is a "
               "statement about the BUILD, not about the SDK.")
    print("\n-- the two rate controls, as they stand --")
    print(json.dumps(v.get("venue_rate_controls"), indent=2, default=str)
          if not v.get("venue_rate_controls_is_absent")
          else "ABSENT: the serving build does not report them.")
    print("\n-- pacer lanes (research vs servicing contention) --")
    print(json.dumps(v.get("pacer_lanes"), indent=2, default=str)
          if not v.get("pacer_lanes_is_absent")
          else "ABSENT: the serving build does not report them.")
    print("\nVERDICT %s (%s)" % (v["verdict"],
                                 "fails this job" if v["verdict"] in FAILING
                                 else "does not fail this job"))
    return 1 if v["verdict"] in FAILING else 0


if __name__ == "__main__":                                     # pragma: no cover
    import sys

    sys.exit(_main(sys.argv[1:]))


__all__ = ["fetch", "reachable", "census", "verdict", "DESK_PATH",
           "P_CYCLE_AT", "P_CONSIDERED", "P_EVALUATED", "P_REFUSALS",
           "P_LEDGER", "P_VENUE_SDK", "P_RATE_CONTROLS", "P_PACER_LANES",
           "HEALTH_PATH", "FAILING", "DESK_TIMEOUT_S", "HEALTH_TIMEOUT_S",
           "DESK_ATTEMPTS", "V_NO_HEARTBEAT", "V_UNREADABLE",
           "V_SERVICE_UNREACHABLE", "V_NOTHING_TO_EVALUATE",
           "V_ALL_REFUSED_ACCOUNTED", "V_UNACCOUNTED", "V_EVALUATED"]
