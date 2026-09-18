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


def census(pages, total_count, errors=()):
    """Assemble and validate a paginated run census.

    `pages` is the list of per-page run lists as retrieved, `total_count` the
    figure the API reported. Returns the census with CENSUS_COMPLETE, which
    the caller must check before using GATE_INPUT for anything.
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
    complete = (not errors) and expected is not None and retrieved >= expected
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
                "API total unknown" if expected is None else "",
                ("retrieved %d of %d" % (retrieved, expected))
                if expected is not None and retrieved < expected else "",
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
