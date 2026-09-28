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

#: ── THE ROUTE THAT ALREADY WORKS ────────────────────────────────────
#:
#: `/api/command/rn1x/statuses` returns the persisted heartbeat WHOLE at
#: `statuses.external_valuation.last_cycle` -- the raw row the loop wrote, not
#: a projection of it. An independent run confirmed that route answering
#: against the deployed build.
#:
#: WHY NOT THE DESK. The first version of this module read
#: `/api/command/bettor/desk`, whose Opportunities section did not carry
#: `evaluated` at all -- so basic EV diagnosis would have depended on
#: deploying a new projection, which is exactly when a diagnostic is least
#: able to wait. Reading the row whole removes that dependency: a field this
#: module wants and an older build does not write is then a MISSING FIELD on a
#: PRESENT heartbeat, which is a different fact from a missing heartbeat and
#: is reported as such.
STATUSES_PATH = "/api/command/rn1x/statuses"
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
#: The statuses route does far less work than the desk, but it still touches
#: the database on a possibly-cold service.
STATUSES_TIMEOUT_S = 90.0
#: One retry, because a cold start is the common cause and the second attempt
#: is warm. Bounded at two total: a diagnostic that hammers our own API while
#: reporting on rate control would be self-refuting.
DESK_ATTEMPTS = 2

V_NO_HEARTBEAT = "NO_SCHEDULED_CYCLE_HEARTBEAT"
V_UNREADABLE = "THE_DESK_COULD_NOT_BE_READ"
V_SERVICE_UNREACHABLE = "THE_SERVICE_ITSELF_DID_NOT_ANSWER"
V_NOTHING_TO_EVALUATE = "NOTHING_TO_EVALUATE_THIS_CYCLE"
V_ALL_REFUSED_ACCOUNTED = "EVERY_CANDIDATE_REFUSED_AND_ACCOUNTED_FOR"
#: ── A VERDICT I HAD TO ADD AFTER READING MY OWN OUTPUT ──────────────
#:
#: The first successful live run reported
#: `EVERY_CANDIDATE_REFUSED_AND_ACCOUNTED_FOR` on a cycle with
#: `markets_considered: 82` and `refusal_total: 33`. Those do not add up, and
#: the verdict asserted accounting it had never performed -- it fired whenever
#: ANY refusal was recorded, without comparing the totals.
#:
#: That is the same error as every other one in this batch: a name that claims
#: more than the measurement supports. So the reconciliation is now computed,
#: and when it cannot be closed the verdict says exactly that instead of
#: claiming completeness.
#:
#: IT IS NOT AUTOMATICALLY A DEFECT. `markets_considered` counts venue markets
#: examined; the refusal counters fire per CANDIDATE at various stages, and the
#: loop's own documentation says most of them fire before anything is
#: evaluated. The two may be different populations -- which is precisely why
#: "they differ" must be reported as NOT ESTABLISHED rather than as either a
#: clean account or a broken one.
V_REFUSALS_NAMED_NOT_RECONCILED = (
    "REFUSALS_ARE_NAMED_BUT_THE_CENSUS_DOES_NOT_RECONCILE")
V_UNACCOUNTED = "CANDIDATES_ENTERED_THE_FUNNEL_AND_ARE_UNACCOUNTED_FOR"
V_EVALUATED = "CANDIDATES_WERE_EVALUATED"

#: Only these verdicts fail the job. A world with no fixtures is not a
#: broken system, and failing on it would train the reader to ignore the job.
#:
#: `ok` AND THE EXIT CODE ARE DIFFERENT QUESTIONS, and the live run showed why
#: saying so matters: it printed `ok: false` beside "does not fail this job",
#: which reads as a contradiction.
#:
#:   ok        did this run ESTABLISH what it set out to establish?
#:   exit      should this job STOP the pipeline?
#:
#: Unreconciled coverage answers no to the first and no to the second: it is
#: UNESTABLISHED, not proven broken, so it must be visible without blocking a
#: release forever on what may be a population mismatch. The printed line now
#: states both, so neither can be read as the other.
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
          attempts: int = None, sleep=None, path: str = None) -> dict:
    """The statuses route, authenticated, with ONE bounded retry.

    RETRIED BECAUSE A COLD START IS THE COMMON CAUSE and the second attempt
    is warm. Bounded at DESK_ATTEMPTS: a diagnostic that hammered our own API
    while reporting on rate control would be self-refuting. The venue is not
    touched by this at all -- this is our own service.
    """
    import time as _t

    where = path or STATUSES_PATH
    limit = STATUSES_TIMEOUT_S if timeout is None else timeout
    tries = DESK_ATTEMPTS if attempts is None else max(1, int(attempts))
    last = None
    for i in range(tries):
        last = _get(api, where, token=token, timeout=limit)
        if last.get("ok"):
            return dict(last, attempts=i + 1, path=where)
        # A 4xx will not change on a retry -- it is an answer about our
        # credential or the route, not a cold service.
        st = last.get("status")
        if st is not None and 400 <= int(st) < 500:
            return dict(last, attempts=i + 1, path=where,
                        why_not_retried="a 4xx is an answer, not a cold start")
        if i + 1 < tries:
            (sleep or _t.sleep)(2.0)
    return dict(last or {"ok": False}, attempts=tries, path=where)


#: ── WHERE THE HEARTBEAT LIVES ON THE STATUSES ROUTE ─────────────────
#: The row WHOLE, so every field the loop wrote is reachable by its own name.
P_CYCLE = ("statuses", "external_valuation", "last_cycle")

#: Fields read off that row. Names are the loop's own, not a projection's.
F_AT = "at"
F_STATE = "state"
F_LABEL = "cycle_label"
F_WRITER = "writer"
F_CONSIDERED = "markets_considered"
F_EVALUATED = "evaluated"
F_WRITTEN = "written"
F_REFUSALS = "refusals"
F_LEDGER = "mapped_candidate_ledger"
F_VENUE_SDK = "venue_sdk"
F_RATE_CONTROLS = "venue_rate_controls"
#: NESTED, not top level. `pacer_lanes` is written INSIDE
#: `venue_rate_controls` by `_rate_control_digest`, and naming it as a
#: top-level field made the reader look somewhere the loop never writes. The
#: path-agreement test caught it before a run did, which is what it is for.
F_PACER_LANES = "venue_rate_controls.pacer_lanes"

#: A ledger row must IDENTIFY a candidate and STATE an outcome. A row with
#: neither names nothing, and counting it as coverage counts the container
#: instead of the contents.
LEDGER_ID_KEYS = ("slug", "us_market_slug", "market_slug", "condition_id",
                  "event_key", "candidate")
LEDGER_OUTCOME_KEYS = ("refusal", "reason", "first_refusal", "blocker",
                       "outcome", "state")


def _counter(value):
    """(int, None) for a valid counter, or (None, reason) for anything else.

    ONLY A NON-NEGATIVE INTEGER IS A COUNT.

      * `-1` is a corrupt field, and believing it produced
        `CANDIDATES_WERE_EVALUATED, ok=True` on a cycle that evaluated
        nothing. The same class of bug as NaN passing `float()`.
      * `True` is excluded EXPLICITLY. `isinstance(True, int)` holds in
        Python and `int(True)` is 1, so a boolean would quietly become a
        count of one.
      * A float, even 2.0, is not a candidate count; a string is not either.
        Coercing them hides a producer that changed shape.
    """
    if isinstance(value, bool):
        return None, "a boolean is not a count"
    if isinstance(value, int):
        if value < 0:
            return None, "negative (%d) is not a count" % value
        return value, None
    if value is None:
        return None, "absent"
    return None, "%s is not an integer" % type(value).__name__


def census(payload: dict) -> dict:
    """The cycle row and its fields, with every absence named for what it is.

    THREE KINDS OF ABSENCE, KEPT APART -- because an older build and an
    unwritten row and a failed read are three different situations:

      NO SECTION      the route does not carry the cycle path at all.
      NO ROW          the section is there and the row is null: nothing has
                      been written.
      UNREADABLE      the route itself reported it could not read the row.
      MISSING FIELD   the row is present and this build did not write that
                      field. NOT a missing heartbeat, and not a claim about
                      the thing the field would have described.
    """
    out = {"paths_absent": [], "invalid_fields": []}
    row, missing = _dig(payload, *P_CYCLE)

    if row is ABSENT:
        out.update(cycle=None, desk_has_a_cycle_section=False,
                   cycle_row_present=False, cycle_row_unreadable=False)
        out["paths_absent"].append(missing)
        return out
    out["desk_has_a_cycle_section"] = True
    if not isinstance(row, dict) or not row:
        # `{}` AND `None` BOTH LAND HERE, and neither is a measured empty
        # funnel. An empty object supports no claim about the world at all;
        # reporting it as NOTHING_TO_EVALUATE was a positive claim built on
        # nothing.
        out.update(cycle=None, cycle_row_present=False,
                   cycle_row_unreadable=False,
                   cycle_row_why=("the cycle row is %s -- present as a key and "
                                  "carrying no measurement"
                                  % ("an empty object" if row == {}
                                     else type(row).__name__)))
        return out
    if row.get("unreadable"):
        out.update(cycle=row, cycle_row_present=False,
                   cycle_row_unreadable=True,
                   cycle_row_why=("the route could not read the row: %s"
                                  % row.get("unreadable")))
        return out

    out["cycle"] = row
    out["cycle_row_unreadable"] = False

    def take(name, key):
        if key not in row:
            out["paths_absent"].append("last_cycle." + key)
            out[name] = None
            out[name + "_is_absent"] = True
        else:
            out[name] = row[key]
            out[name + "_is_absent"] = False

    for name, key in (("cycle_at", F_AT), ("cycle_state", F_STATE),
                      ("cycle_label", F_LABEL), ("writer", F_WRITER),
                      ("refusals", F_REFUSALS), ("ledger", F_LEDGER),
                      ("venue_sdk", F_VENUE_SDK),
                      ("venue_rate_controls", F_RATE_CONTROLS)):
        take(name, key)
    # THE NESTED ONE, read from where it is actually written.
    rc = out.get("venue_rate_controls")
    if isinstance(rc, dict) and "pacer_lanes" in rc:
        out["pacer_lanes"] = rc["pacer_lanes"]
        out["pacer_lanes_is_absent"] = False
    else:
        out["pacer_lanes"] = None
        out["pacer_lanes_is_absent"] = True
        out["paths_absent"].append("last_cycle." + F_PACER_LANES)

    # COUNTERS ARE VALIDATED, not coerced. An invalid one is named and the
    # value becomes None, so nothing downstream can treat it as a number.
    for name, key in (("markets_considered", F_CONSIDERED),
                      ("evaluated", F_EVALUATED), ("written", F_WRITTEN)):
        if key not in row:
            out["paths_absent"].append("last_cycle." + key)
            out[name] = None
            out[name + "_is_absent"] = True
            continue
        out[name + "_is_absent"] = False
        val, why = _counter(row[key])
        out[name] = val
        if val is None and why != "absent":
            out["invalid_fields"].append("%s (%s)" % (key, why))

    # A CYCLE EXISTS IF IT HAS AN INSTANT. Counters can legitimately be zero
    # or absent; an instant cannot be, because the loop writes it first.
    at_val, at_why = (None, None)
    if not out["cycle_at_is_absent"]:
        try:
            at_val = float(out["cycle_at"])
            if at_val != at_val or at_val <= 0:      # NaN or non-positive
                at_val, at_why = None, "not a positive instant"
        except (TypeError, ValueError):
            at_val, at_why = None, "not a number"
    out["cycle_at_epoch_s"] = at_val
    if at_why:
        out["invalid_fields"].append("%s (%s)" % (F_AT, at_why))
    out["cycle_row_present"] = bool(at_val is not None)
    return out


def _refusal_total(refusals):
    """Sum the refusal counters, validating each. Returns (total, invalid)."""
    bad = []
    total = 0
    if not isinstance(refusals, dict):
        return None, (["refusals is not a mapping"] if refusals is not None
                      else [])
    for k, v in refusals.items():
        n, why = _counter(v)
        if n is None:
            bad.append("refusals.%s (%s)" % (k, why))
        else:
            total += n
    return total, bad


def _ledger_quality(ledger):
    """How many ledger rows actually identify a candidate AND an outcome.

    A BLANK ROW IS NOT COVERAGE. `[{}]` was read as "this candidate is
    accounted for", which counted the existence of a row as the content of
    one. A usable row names WHICH candidate hit WHICH blocker; anything less
    is a row, not an account.
    """
    if not isinstance(ledger, list):
        return {"rows": 0, "usable": 0, "blank": 0,
                "not_a_list": ledger is not None}
    usable = blank = 0
    for r in ledger:
        if not isinstance(r, dict):
            blank += 1
            continue
        has_id = any(r.get(k) for k in LEDGER_ID_KEYS)
        has_outcome = any(r.get(k) for k in LEDGER_OUTCOME_KEYS)
        if has_id and has_outcome:
            usable += 1
        else:
            blank += 1
    return {"rows": len(ledger), "usable": usable, "blank": blank,
            "not_a_list": False}


def verdict(payload: dict) -> dict:
    """What the cycle establishes, and whether that should fail a job.

    ── THE RULE THE OLD VERSION BROKE ───────────────────────────────
    The presence of A refusal, or A ledger row, does not establish that every
    candidate is accounted for. One refusal out of a hundred candidates is one
    accounted candidate and ninety-nine unknown ones. The verdict now
    RECONCILES before it claims completeness, and when it cannot close the
    sum it says so instead.

    ── AND IT DOES NOT MANUFACTURE A RECONCILIATION ─────────────────
    `markets_considered` counts venue markets examined; the refusal counters
    fire per candidate, most before scoring. They may be different
    populations, so a difference is UNESTABLISHED -- neither a clean account
    nor a defect. Summing overlapping reasons to make the totals meet would
    be inventing the answer.
    """
    c = census(payload)
    if not c.get("desk_has_a_cycle_section"):
        return dict(c, verdict=V_NO_HEARTBEAT, ok=False,
                    why=("the response carries no cycle at %s, so this run "
                         "establishes nothing about the evaluation lane. If "
                         "the route changed shape, the absent path is named "
                         "above rather than shown as an empty census"
                         % ".".join(P_CYCLE)))
    if c.get("cycle_row_unreadable"):
        return dict(c, verdict=V_UNREADABLE, ok=False,
                    why=c.get("cycle_row_why"))
    if not c.get("cycle_row_present"):
        return dict(c, verdict=V_NO_HEARTBEAT, ok=False,
                    why=(c.get("cycle_row_why")
                         or "the cycle row carries no valid instant, so no "
                            "cycle has been recorded. Unanswered, not idle"))
    if c["invalid_fields"]:
        # A CORRUPT COUNTER FAILS BEFORE ANY VERDICT IS DRAWN FROM IT.
        return dict(c, verdict=V_UNACCOUNTED, ok=False,
                    why=("the cycle row carries field(s) that are not valid "
                         "counters: %s. No verdict is drawn from a number "
                         "that is not one" % ", ".join(c["invalid_fields"])))

    refused, bad = _refusal_total(c["refusals"])
    if bad:
        c["invalid_fields"].extend(bad)
        return dict(c, verdict=V_UNACCOUNTED, ok=False,
                    refusal_total=None,
                    why=("the refusal census carries invalid counter(s): %s"
                         % ", ".join(bad)))
    lq = _ledger_quality(c["ledger"])
    c.update(refusal_total=refused,
             ledger_rows=lq["rows"], ledger_rows_usable=lq["usable"],
             ledger_rows_blank=lq["blank"])

    considered, evaluated = c["markets_considered"], c["evaluated"]

    # ── THE RECONCILIATION ───────────────────────────────────────────
    reconciled = False
    unexplained = None
    why_not = None
    if considered is None:
        why_not = ("`markets_considered` is absent on this build, so the "
                   "population the refusals should cover is unknown")
    elif evaluated is None:
        why_not = ("`evaluated` is absent on this build, so considered minus "
                   "refused cannot be closed: the remainder could be "
                   "evaluations or could be unaccounted candidates")
    elif refused is None:
        why_not = "the refusal census is unreadable"
    else:
        unexplained = considered - evaluated - refused
        reconciled = (unexplained == 0)
        if not reconciled:
            why_not = (
                "%d considered - %d evaluated - %d refused = %d unexplained. "
                "These may also be DIFFERENT POPULATIONS: "
                "`markets_considered` counts venue markets examined while the "
                "refusal counters fire per candidate, mostly before scoring. "
                "So this is UNESTABLISHED coverage rather than a proven gap, "
                "and it is not closed by summing overlapping reasons"
                % (considered, evaluated, refused, unexplained))
    c.update(reconciled=reconciled, unexplained=unexplained,
             why_not_reconciled=why_not)

    # ── NOTHING AT ALL ENTERED THE FUNNEL ────────────────────────────
    if (considered == 0 and (evaluated in (0, None)) and not refused
            and lq["rows"] == 0):
        return dict(c, verdict=V_NOTHING_TO_EVALUATE, ok=True,
                    why=("nothing entered the funnel: the provider supplied "
                         "no fixtures, or nothing mapped. A state of the "
                         "world, and NOT evidence that the lane works"))

    # ── EVALUATIONS HAPPENED ─────────────────────────────────────────
    if evaluated:
        if reconciled:
            return dict(c, verdict=V_EVALUATED, ok=True,
                        why=("%d candidate(s) reached evaluation and the "
                             "census reconciles" % evaluated))
        return dict(c, verdict=V_REFUSALS_NAMED_NOT_RECONCILED, ok=False,
                    why=("%d candidate(s) reached evaluation but the census "
                         "does not reconcile: %s" % (evaluated, why_not)))

    # ── ZERO EVALUATED, SOMETHING IN THE FUNNEL ──────────────────────
    if reconciled:
        return dict(c, verdict=V_ALL_REFUSED_ACCOUNTED, ok=True,
                    why=("every candidate was refused for a NAMED reason and "
                         "the counts reconcile (%d considered = 0 evaluated + "
                         "%d refused). The named reasons are the finding"
                         % (considered, refused)))
    if refused or lq["usable"]:
        # NAMED, BUT NOT COMPLETE. Reported as unestablished coverage, which
        # is the honest middle the old code collapsed into a green.
        return dict(c, verdict=V_REFUSALS_NAMED_NOT_RECONCILED, ok=False,
                    why=("refusals are named (%s refused, %d usable ledger "
                         "row(s)) but coverage of the candidate population is "
                         "NOT established: %s"
                         % (refused, lq["usable"], why_not)))
    return dict(c, verdict=V_UNACCOUNTED, ok=False,
                why=("%s market(s) were considered, none was evaluated, and "
                     "the census names no refusal and carries no usable "
                     "ledger row (%d blank of %d). The lane is not measuring "
                     "itself, which is a defect in the instrument rather "
                     "than a fact about the venue"
                     % (considered, lq["blank"], lq["rows"])))


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

    # THE CHEAP QUESTION FIRST, so a slow route and a dead service are two
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
                          "route": got.get("path"),
                          "why": got.get("why"),
                          "why_not_retried": got.get("why_not_retried")},
                         indent=2))
        return 1

    v = verdict(got["body"] or {})
    show = {k: v.get(k) for k in (
        "verdict", "ok", "why", "cycle_at", "cycle_state", "cycle_label",
        "writer", "markets_considered", "evaluated", "written",
        "refusal_total", "ledger_rows", "ledger_rows_usable",
        "ledger_rows_blank", "reconciled", "unexplained",
        "why_not_reconciled", "invalid_fields", "paths_absent")}
    print(json.dumps(show, indent=2, default=str))
    print("\n-- the refusal census, verbatim --")
    print(json.dumps(v.get("refusals") or {}, indent=2, default=str))
    for label, key in (("the venue SDK the deployed process reports",
                        "venue_sdk"),
                       ("the two rate controls, as they stand",
                        "venue_rate_controls"),
                       ("pacer lanes (research vs servicing contention)",
                        "pacer_lanes")):
        print("\n-- %s --" % label)
        if v.get(key + "_is_absent"):
            print("ABSENT: the heartbeat is present and this SERVING BUILD "
                  "does not write this field. That is a statement about the "
                  "BUILD -- not that the dependency is unpinned, and not "
                  "that rate control is off.")
        else:
            print(json.dumps(v.get(key), indent=2, default=str))
    fails = v["verdict"] in FAILING
    print("\nVERDICT %s" % v["verdict"])
    print("  establishes what it set out to establish : %s" % bool(v["ok"]))
    print("  stops this job                           : %s" % fails)
    if not v["ok"] and not fails:
        print("  the two differ on purpose: coverage is UNESTABLISHED rather "
              "than proven broken, so it is reported and not used to block a "
              "release on what may be a population mismatch. A green job here "
              "is NOT trading acceptance and does not become one by repeating.")
    return 1 if fails else 0


if __name__ == "__main__":                                     # pragma: no cover
    import sys

    sys.exit(_main(sys.argv[1:]))


__all__ = ["fetch", "reachable", "census", "verdict", "STATUSES_PATH",
           "DESK_PATH", "HEALTH_PATH", "P_CYCLE", "FAILING",
           "STATUSES_TIMEOUT_S", "DESK_TIMEOUT_S", "HEALTH_TIMEOUT_S",
           "DESK_ATTEMPTS", "LEDGER_ID_KEYS", "LEDGER_OUTCOME_KEYS",
           "V_NO_HEARTBEAT", "V_UNREADABLE", "V_SERVICE_UNREACHABLE",
           "V_NOTHING_TO_EVALUATE", "V_ALL_REFUSED_ACCOUNTED",
           "V_REFUSALS_NAMED_NOT_RECONCILED", "V_UNACCOUNTED", "V_EVALUATED"]
