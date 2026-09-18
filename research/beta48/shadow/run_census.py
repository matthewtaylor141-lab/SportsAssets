"""A COMPLETE run census for the clean-start gate. Fails closed.

The gate is only as good as what is handed to it. Two ways it was fed badly
in this session, both of which this module now refuses:

1. ONLY THE RUNNING RUN WAS SUPPLIED. A concurrency-held run is reported by
   GitHub with status `pending`, and `pending` is NOT in the Actions API's
   status filter enum (queued / in_progress / completed / requested /
   waiting). Filtering by `queued` returned nothing while a pending
   forward-capture was sitting in the group, so PENDING_DIRECT_CONFLICTS
   read 0 for the wrong reason.

2. A PARTIAL PAGE WAS TREATED AS THE WHOLE TRUTH. A census that did not
   retrieve everything it was told exists is not evidence of an idle domain.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# Every state in which a run holds or awaits the concurrency group. `pending`
# is the one the API's own status filter cannot express, which is exactly why
# the census is taken unfiltered and classified here.
WAITING_STATES = ("queued", "pending", "requested", "waiting")
RUNNING_STATES = ("in_progress",)
OCCUPYING_STATES = WAITING_STATES + RUNNING_STATES

A_STATUS_FILTER_THE_API_CANNOT_EXPRESS = (
    "the Actions API accepts queued / in_progress / completed / requested / "
    "waiting as a status filter, and reports concurrency-held runs as "
    "`pending`, which is not in that list. Asking for `queued` and getting "
    "nothing is not the same as nothing waiting. The census is taken WITHOUT "
    "a status filter and classified here")

AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN = (
    "a retrieval that returned fewer runs than the API said exist, or that "
    "errored on any page, tells us nothing about the domain. It is refused "
    "rather than passed to the gate as a short list, because a short list "
    "reads exactly like an idle domain")


# The longest timeout-minutes on any workflow in the venue domain. A run
# created earlier than this before `now` CANNOT still be live, which is the
# only completeness argument available here: the API's own total_count is the
# repository's entire run history (thousands), not the number of live runs,
# and no status filter can express `pending`.
DOMAIN_MAX_TIMEOUT_MINUTES = 340

A_HISTORY_COUNT_IS_NOT_A_LIVE_COUNT = (
    "list_workflow_runs reports total_count for the repository's whole run "
    "history. Comparing a page against it can never succeed and says nothing "
    "about live runs. Completeness here means: every run created within the "
    "last DOMAIN_MAX_TIMEOUT_MINUTES has been retrieved, because nothing "
    "older can still be holding or awaiting the group")


def census(pages, total_count=None, errors=(), covers_minutes=None,
           max_timeout_minutes=DOMAIN_MAX_TIMEOUT_MINUTES):
    """Assemble and validate a paginated run census.

    Completeness is established EITHER by `covers_minutes` -- the age of the
    oldest retrieved run, which must exceed the domain's longest timeout --
    OR, for a closed set such as a single-workflow listing, by `total_count`.
    Neither supplied is not complete.
    """
    runs, seen = [], set()
    for page in pages or ():
        for r in page or ():
            rid = (r or {}).get("id")
            if rid is None or rid in seen:
                continue
            seen.add(rid)
            runs.append(r)
    retrieved = len(runs)
    expected = total_count if isinstance(total_count, int) else None
    by_horizon = (isinstance(covers_minutes, (int, float))
                  and covers_minutes > max_timeout_minutes)
    by_count = expected is not None and retrieved >= expected
    complete = (not errors) and (by_horizon or by_count)
    occupying = [r for r in runs
                 if (r.get("status") or "").lower() in OCCUPYING_STATES]
    unknown = sorted({(r.get("status") or NOT_IDENTIFIED)
                      for r in runs
                      if (r.get("status") or "").lower() not in
                      OCCUPYING_STATES + ("completed",)})
    return {
        "CENSUS_COMPLETE": bool(complete) and not unknown,
        "RUNS_RETRIEVED": retrieved,
        "RUNS_REPORTED_BY_API": expected if expected is not None
        else NOT_IDENTIFIED,
        "COMPLETENESS_BASIS": ("OLDEST_RUN_PREDATES_MAX_TIMEOUT" if by_horizon
                               else "API_TOTAL_COUNT" if by_count
                               else NOT_IDENTIFIED),
        "COVERS_MINUTES": covers_minutes if covers_minutes is not None
        else NOT_IDENTIFIED,
        "DOMAIN_MAX_TIMEOUT_MINUTES": max_timeout_minutes,
        "A_HISTORY_COUNT_IS_NOT_A_LIVE_COUNT":
            A_HISTORY_COUNT_IS_NOT_A_LIVE_COUNT,
        "PAGES": len(list(pages or ())),
        "RETRIEVAL_ERRORS": tuple(errors or ()),
        "UNRECOGNISED_STATUSES": tuple(unknown),
        "GATE_INPUT": tuple(occupying),
        "OCCUPYING_STATES": OCCUPYING_STATES,
        "WAITING_STATES": WAITING_STATES,
        "A_STATUS_FILTER_THE_API_CANNOT_EXPRESS":
            A_STATUS_FILTER_THE_API_CANNOT_EXPRESS,
        "AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN":
            AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN,
        "WHY_NOT_COMPLETE": (
            "" if complete and not unknown else
            "; ".join(filter(None, [
                "retrieval errors" if errors else "",
                ("no completeness basis: neither covers_minutes > %d nor an "
                 "API total" % max_timeout_minutes)
                if not by_horizon and expected is None else "",
                ("retrieved %d of %d" % (retrieved, expected))
                if not by_horizon and expected is not None
                and retrieved < expected else "",
                ("unrecognised statuses: %s" % ", ".join(unknown))
                if unknown else "",
            ]))),
    }


def may_evaluate_gate(c):
    """The gate may only be evaluated on a COMPLETE census. Fails closed."""
    if not isinstance(c, dict) or c.get("CENSUS_COMPLETE") is not True:
        return {"MAY_EVALUATE_GATE": False,
                "REASON": "CENSUS_INCOMPLETE",
                "WHY": (c or {}).get("WHY_NOT_COMPLETE", NOT_IDENTIFIED),
                "AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN":
                    AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN}
    return {"MAY_EVALUATE_GATE": True,
            "GATE_INPUT_RUNS": len(c["GATE_INPUT"])}


ACQUISITION_IS_A_CREATED_JOB = (
    "a workflow_dispatch response means GitHub accepted the request. It does "
    "not mean the run acquired the concurrency group -- the run can sit "
    "pending with no job and be cancelled by a newer arrival, which is what "
    "happened to three forward-capture runs in this window. Acquisition is "
    "evidenced by a JOB EXISTING with a created_at, and by nothing else")


def acquired(jobs_response):
    """Did the dispatched run actually acquire the domain?"""
    jobs = ((jobs_response or {}).get("jobs") or {}).get("jobs")
    if jobs is None:
        jobs = (jobs_response or {}).get("jobs") or []
    first = jobs[0] if jobs else None
    created = (first or {}).get("created_at")
    return {
        "ACQUIRED": bool(created),
        "JOB_COUNT": len(jobs),
        "JOB_CREATED_AT": created or NOT_IDENTIFIED,
        "JOB_STARTED_AT": (first or {}).get("started_at") or NOT_IDENTIFIED,
        "ACQUISITION_IS_A_CREATED_JOB": ACQUISITION_IS_A_CREATED_JOB,
    }
