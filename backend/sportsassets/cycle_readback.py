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

V_NO_HEARTBEAT = "NO_SCHEDULED_CYCLE_HEARTBEAT"
V_UNREADABLE = "THE_DESK_COULD_NOT_BE_READ"
V_NOTHING_TO_EVALUATE = "NOTHING_TO_EVALUATE_THIS_CYCLE"
V_ALL_REFUSED_ACCOUNTED = "EVERY_CANDIDATE_REFUSED_AND_ACCOUNTED_FOR"
V_UNACCOUNTED = "CANDIDATES_ENTERED_THE_FUNNEL_AND_ARE_UNACCOUNTED_FOR"
V_EVALUATED = "CANDIDATES_WERE_EVALUATED"

#: Only these verdicts fail the job. A world with no fixtures is not a
#: broken system, and failing on it would train the reader to ignore the job.
FAILING = frozenset({V_NO_HEARTBEAT, V_UNREADABLE, V_UNACCOUNTED})

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


def fetch(api: str, token: str, *, timeout: float = 60.0) -> dict:
    """One authenticated GET. Never writes, never retries into the venue."""
    url = api.rstrip("/") + DESK_PATH
    req = urllib.request.Request(url, method="GET")
    req.add_header("X-Admin-Token", token)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "status": r.status,
                    "body": json.loads(r.read().decode() or "{}")}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code,
                "why": "HTTP %s from %s" % (exc.code, DESK_PATH)}
    except Exception as exc:                                   # noqa: BLE001
        # THE EXCEPTION CLASS, NOT ITS MESSAGE. A message can carry a URL
        # with a query string; the class cannot.
        return {"ok": False, "status": None,
                "why": "%s reading %s" % (type(exc).__name__, DESK_PATH)}


def census(desk: dict) -> dict:
    """The evaluation lane's own numbers, with every absent path named."""
    out = {"paths_absent": []}

    def take(name, *path):
        val, missing = _dig(desk, *path)
        if val is ABSENT:
            out["paths_absent"].append(missing)
            out[name] = None
            out[name + "_is_absent"] = True
        else:
            out[name] = val
            out[name + "_is_absent"] = False
        return val

    dec = take("decision", "last_scheduled_decision")
    d = dec if isinstance(dec, dict) else {}
    out["cycle_at"] = _dig(d, "at")[0]
    out["cycle_state"] = _dig(d, "cycle_state")[0]
    out["cycle_label"] = _dig(d, "cycle_label")[0]
    out["writer"] = _dig(d, "writer")[0]
    out["servicing_available"] = _dig(d, "available")[0]
    out["latency"] = _dig(d, "latency")[0]

    # ── THE REPAIRS OF THIS BATCH, READ BACK FROM THE RUNNING PROCESS ──
    # Which venue SDK the deployed image resolved, and whether both rate
    # controls are armed. These exist precisely so this question does not
    # have to be answered from a build log.
    for nm, key in (("venue_sdk", "venue_sdk"),
                    ("venue_rate_controls", "venue_rate_controls"),
                    ("pacer_lanes", "pacer_lanes")):
        v, missing = _dig(d, key)
        if v is ABSENT:
            # ON THE DECISION BLOCK OR NOWHERE. A build older than the one
            # that persists these writes the heartbeat without them, so
            # absent means THE SERVING BUILD -- not that the controls are off.
            out[nm] = None
            out[nm + "_is_absent"] = True
            out["paths_absent"].append("last_scheduled_decision." + key)
        else:
            out[nm] = v
            out[nm + "_is_absent"] = False
    return out


def verdict(desk: dict) -> dict:
    """What the cycle establishes, and whether that should fail a job."""
    c = census(desk)
    if c["decision_is_absent"] or not isinstance(c["decision"], dict):
        return dict(c, verdict=V_NO_HEARTBEAT, ok=False,
                    why=("the desk carries no `last_scheduled_decision`, so "
                         "this run establishes nothing about the evaluation "
                         "lane. That is an unanswered question, not an idle "
                         "lane -- reported as unanswered"))

    d = c["decision"]
    refusals = _dig(d, "refusals")[0]
    refusals = refusals if isinstance(refusals, dict) else {}
    ledger = _dig(d, "mapped_candidate_ledger")[0]
    ledger = ledger if isinstance(ledger, list) else []
    considered = _dig(d, "markets_considered")[0]
    evaluated = _dig(d, "evaluated")[0]

    def num(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    considered, evaluated = num(considered), num(evaluated)
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

    got = fetch(api, token)
    if not got.get("ok"):
        print(json.dumps({"verdict": V_UNREADABLE, "ok": False,
                          "status": got.get("status"),
                          "why": got.get("why")}, indent=2))
        return 1

    v = verdict(got["body"] or {})
    show = {k: v.get(k) for k in (
        "verdict", "ok", "why", "cycle_at", "cycle_state", "cycle_label",
        "markets_considered", "evaluated", "refusal_total",
        "mapped_candidates", "servicing_available", "paths_absent")}
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


__all__ = ["fetch", "census", "verdict", "DESK_PATH", "FAILING",
           "V_NO_HEARTBEAT", "V_UNREADABLE", "V_NOTHING_TO_EVALUATE",
           "V_ALL_REFUSED_ACCOUNTED", "V_UNACCOUNTED", "V_EVALUATED"]
