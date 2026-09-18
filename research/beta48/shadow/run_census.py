"""A COMPLETE run census for the clean-start gate. Fails closed.

The gate is only as good as what is handed to it. Four ways it was fed badly,
each reproduced in test_run_census.py and each refused here.

1. ONLY THE RUNNING RUN WAS SUPPLIED. A concurrency-held run is reported by
   GitHub with status `pending`, and `pending` is NOT in the Actions API's
   status filter enum (queued / in_progress / completed / requested /
   waiting). Filtering by `queued` returned nothing while a pending
   forward-capture sat in the group, so PENDING_DIRECT_CONFLICTS read 0 for
   the wrong reason.

2. A PARTIAL PAGE WAS TREATED AS THE WHOLE TRUTH.

3. COMPLETENESS WAS ARGUED FROM timeout-minutes. WITHDRAWN. `timeout-minutes`
   bounds JOB EXECUTION. It does not bound workflow age, and it does not
   bound time spent waiting for the concurrency group. Measured
   counterexample: beta48-forward-capture run 35224744353 was created
   2026-09-17T13:03:43Z and its job was not created until 16:16:18Z -- 3h12m
   of waiting -- then ran 33m18s, for a total age of 3h46m against a
   120-minute timeout. A retrieval spanning N minutes proves nothing about
   runs older than N. Completeness now means VERIFIED PAGINATION EXHAUSTION.

4. THE WORKFLOW INVENTORY WAS ONLY CHECKED IN A TEST. An empty inventory
   makes the gate read zero conflicts, so it is required, non-empty, on the
   runtime path.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# Every state in which a run holds or awaits the concurrency group. `pending`
# is the one the API's own status filter cannot express, which is exactly why
# the census is taken unfiltered and classified here.
WAITING_STATES = ("queued", "pending", "requested", "waiting")
RUNNING_STATES = ("in_progress",)
OCCUPYING_STATES = WAITING_STATES + RUNNING_STATES
TERMINAL_STATES = ("completed",)

A_STATUS_FILTER_THE_API_CANNOT_EXPRESS = (
    "the Actions API accepts queued / in_progress / completed / requested / "
    "waiting as a status filter, and reports concurrency-held runs as "
    "`pending`, which is not in that list. Asking for `queued` and getting "
    "nothing is not the same as nothing waiting. The census is taken WITHOUT "
    "a status filter and classified here")

AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN = (
    "a retrieval that hit a page limit, errored, traversed inconsistently or "
    "left pagination unresolved tells us nothing about the domain. It is "
    "refused rather than passed to the gate as a short list, because a short "
    "list reads exactly like an idle domain")

A_TIMEOUT_DOES_NOT_BOUND_A_WORKFLOW_AGE = (
    "timeout-minutes bounds JOB EXECUTION. A run waits for the concurrency "
    "group before any job exists, and that wait is unbounded: "
    "beta48-forward-capture 35224744353 waited 3h12m38s and then ran 33m18s "
    "under a 120-minute timeout. No retrieval horizon derived from a timeout "
    "is evidence of completeness")

AN_EMPTY_INVENTORY_READS_AS_AN_IDLE_DOMAIN = (
    "with no discovered venue workflows, every run fails the membership test "
    "and the gate reports zero conflicts. The inventory is required and "
    "non-empty on the runtime path, not merely asserted in a test")

AN_UNNAMED_RUN_READS_AS_AN_IDLE_DOMAIN = (
    "venue_domain.domain_idle() matches an occupying run to the domain by its "
    "WORKFLOW NAME. A run row that carries no name -- a minimal projection, a "
    "connector wrapper that drops the field, a shape we did not anticipate -- "
    "matches nothing, and the gate reports zero conflicts over a census that "
    "found the run. Every occupying row must name the workflow whose walk "
    "returned it, or the census is not complete")

BLOCKING_IS_MONOTONE = (
    "an incomplete census cannot certify an IDLE domain, because the runs it"
    "missed could be occupying it. It CAN certify a BLOCKED domain: a "
    "conflict already observed cannot be un-observed by retrieving more. "
    "Certifying blocked from partial evidence is sound; certifying idle is "
    "not")


# --- One workflow, walked to pagination exhaustion. -----------------------

def workflow_walk(workflow, pages, per_page, total_count=None, errors=()):
    """Walk one workflow's run history and verify the walk terminated.

    Exhaustion is a SHORT FINAL PAGE: the last page returned fewer rows than
    `per_page`. A full final page means there may be another, and the walk is
    unresolved. `total_count` is that workflow's own run count and is
    reconciled against the unique ids retrieved.
    """
    rows, seen, per_page_violations = [], set(), []
    pages = list(pages or ())
    for i, page in enumerate(pages):
        page = list(page or ())
        if len(page) > per_page:
            per_page_violations.append(i)
        for r in page:
            rid = (r or {}).get("id")
            if rid is None:
                per_page_violations.append(i)
                continue
            if rid in seen:
                continue
            seen.add(rid)
            rows.append(r)
    short_final = bool(pages) and len(list(pages[-1])) < per_page
    expected = total_count if isinstance(total_count, int) else None
    reconciled = expected is None or len(rows) >= expected
    problems = []
    if errors:
        problems.append("retrieval errors: %s" % "; ".join(map(str, errors)))
    if not pages:
        problems.append("no pages retrieved")
    if not short_final:
        problems.append("final page was full (%d of %d) -- pagination "
                        "unresolved" % (len(list(pages[-1])) if pages else 0,
                                        per_page))
    if per_page_violations:
        problems.append("inconsistent traversal on page(s) %s"
                        % ", ".join(map(str, sorted(set(per_page_violations)))))
    if not reconciled:
        problems.append("reconcile failed: %d unique ids vs API total %d"
                        % (len(rows), expected))
    return {
        "WORKFLOW": workflow,
        "WALK_COMPLETE": not problems,
        "RUNS": tuple(rows),
        "UNIQUE_IDS": len(rows),
        "API_TOTAL_COUNT": expected if expected is not None else NOT_IDENTIFIED,
        "PAGES_WALKED": len(pages),
        "PER_PAGE": per_page,
        "PAGINATION_EXHAUSTED": short_final,
        "PROBLEMS": tuple(problems),
    }


# --- The census over the whole discovered inventory. ----------------------

def census(walks, inventory, taken_at=None, errors=()):
    """Assemble the census from per-workflow walks over a real inventory.

    `walks` maps workflow name -> workflow_walk() result. `inventory` is the
    discovered venue-workflow list; it must be non-empty and every member
    must have a completed walk.
    """
    inventory = list(inventory or ())
    walks = dict(walks or {})
    rows, seen, walked_under = [], set(), {}
    for wf, w in walks.items():
        for r in w.get("RUNS") or ():
            rid = (r or {}).get("id")
            if rid is None or rid in seen:
                continue
            seen.add(rid)
            walked_under[rid] = wf
            rows.append(r)
    occupying = [r for r in rows
                 if (r.get("status") or "").lower() in OCCUPYING_STATES]
    # An occupying row the gate cannot attribute to a domain member reads to
    # it as no conflict at all. Fail closed on the name, not on the status.
    unattributable = []
    for r in occupying:
        name, wf = r.get("name"), walked_under.get(r.get("id"))
        if not name:
            unattributable.append("%s (no name; walked under %s)"
                                  % (r.get("id"), wf))
        elif name != wf:
            unattributable.append("%s (names %r; walked under %s)"
                                  % (r.get("id"), name, wf))
        elif name not in inventory:
            unattributable.append("%s (names %r, outside the inventory)"
                                  % (r.get("id"), name))
    unknown = sorted({(r.get("status") or NOT_IDENTIFIED) for r in rows
                      if (r.get("status") or "").lower()
                      not in OCCUPYING_STATES + TERMINAL_STATES})
    missing = [w for w in inventory if w not in walks]
    incomplete = sorted(w for w, v in walks.items()
                        if v.get("WALK_COMPLETE") is not True)
    problems = []
    if not inventory:
        problems.append("EMPTY_WORKFLOW_INVENTORY")
    if missing:
        problems.append("no walk for: %s" % ", ".join(sorted(missing)))
    if incomplete:
        problems.append("incomplete walk for: %s" % ", ".join(incomplete))
    if unknown:
        problems.append("unrecognised statuses: %s" % ", ".join(unknown))
    if unattributable:
        problems.append("occupying run(s) the gate cannot attribute to a "
                        "domain member: %s" % ", ".join(unattributable))
    if errors:
        problems.append("retrieval errors: %s" % "; ".join(map(str, errors)))
    return {
        "CENSUS_COMPLETE": not problems,
        "COMPLETENESS_BASIS": ("VERIFIED_PAGINATION_EXHAUSTION"
                               if not problems else NOT_IDENTIFIED),
        "WORKFLOW_INVENTORY": tuple(inventory),
        "INVENTORY_SIZE": len(inventory),
        "WALKS": tuple(sorted(walks)),
        "RUNS_RETRIEVED": len(rows),
        "GATE_INPUT": tuple(occupying),
        "OBSERVED_CONFLICTS": len(occupying),
        "CENSUS_TAKEN_AT": taken_at or NOT_IDENTIFIED,
        "UNRECOGNISED_STATUSES": tuple(unknown),
        "UNATTRIBUTABLE_OCCUPYING_RUNS": tuple(unattributable),
        "OCCUPYING_STATES": OCCUPYING_STATES,
        "WAITING_STATES": WAITING_STATES,
        "WHY_NOT_COMPLETE": "; ".join(problems),
        "A_STATUS_FILTER_THE_API_CANNOT_EXPRESS":
            A_STATUS_FILTER_THE_API_CANNOT_EXPRESS,
        "AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN":
            AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN,
        "A_TIMEOUT_DOES_NOT_BOUND_A_WORKFLOW_AGE":
            A_TIMEOUT_DOES_NOT_BOUND_A_WORKFLOW_AGE,
        "AN_EMPTY_INVENTORY_READS_AS_AN_IDLE_DOMAIN":
            AN_EMPTY_INVENTORY_READS_AS_AN_IDLE_DOMAIN,
        "BLOCKING_IS_MONOTONE": BLOCKING_IS_MONOTONE,
    }


MAX_CENSUS_AGE_S = 120


def may_certify(c, age_s=None, max_age_s=MAX_CENSUS_AGE_S):
    """What may this census support? Idle needs completeness; blocked does not.

    A census is also REFRESHED immediately before dispatch: an old reading
    cannot see an arrival that landed since it was taken.
    """
    if not isinstance(c, dict):
        return {"MAY_CERTIFY_IDLE": False, "MAY_CERTIFY_BLOCKED": False,
                "REASON": "NOT_A_CENSUS"}
    stale = (isinstance(age_s, (int, float)) and age_s > max_age_s)
    complete = c.get("CENSUS_COMPLETE") is True
    observed = c.get("OBSERVED_CONFLICTS") or 0
    return {
        "MAY_CERTIFY_IDLE": bool(complete and not stale),
        "MAY_CERTIFY_BLOCKED": bool(observed > 0),
        "CENSUS_COMPLETE": complete,
        "CENSUS_STALE": stale,
        "CENSUS_AGE_S": age_s if age_s is not None else NOT_IDENTIFIED,
        "MAX_CENSUS_AGE_S": max_age_s,
        "OBSERVED_CONFLICTS": observed,
        "WHY": (c.get("WHY_NOT_COMPLETE") or "") if not complete
        else ("census older than %ds" % max_age_s if stale else ""),
        "BLOCKING_IS_MONOTONE": BLOCKING_IS_MONOTONE,
        "AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN":
            AN_INCOMPLETE_CENSUS_IS_NOT_AN_IDLE_DOMAIN,
    }


# --- Acquisition: a created job for THIS run, with the expected name. -----

ACQUISITION_IS_A_CREATED_JOB = (
    "a workflow_dispatch response means GitHub accepted the request. It does "
    "not mean the run acquired the concurrency group -- the run can sit "
    "pending with no job and be cancelled by a newer arrival, which is what "
    "happened to three forward-capture runs in this window. Acquisition is "
    "evidenced by a JOB EXISTING with a created_at, belonging to THIS run id "
    "and carrying the EXPECTED job name")

A_MALFORMED_RESPONSE_IS_NOT_AN_EMPTY_ONE = (
    "reading a jobs payload the module does not recognise and calling the "
    "result 'no jobs' turns a parse failure into a confident negative. Worse "
    "in the other direction: acquired({'jobs': [job]}) raised AttributeError "
    "because .get was called on a list. The shape is normalised, and an "
    "unrecognised shape is REFUSED")

CAPTURE_JOB_NAME = "capture"


def _normalise_jobs(payload):
    """Return (jobs, shape) or (None, reason). Refuses unknown shapes."""
    if not isinstance(payload, dict):
        return None, "PAYLOAD_NOT_A_MAPPING"
    inner = payload.get("jobs")
    # Standard GitHub: {"total_count": n, "jobs": [...]}
    if isinstance(inner, list):
        return inner, "GITHUB_JOBS_LIST"
    # Connector wrapper: {"jobs": {"total_count": n, "jobs": [...]}}
    if isinstance(inner, dict):
        nested = inner.get("jobs")
        if isinstance(nested, list):
            return nested, "CONNECTOR_WRAPPED_JOBS_LIST"
        return None, "WRAPPED_PAYLOAD_HAS_NO_JOBS_LIST"
    if inner is None:
        return None, "PAYLOAD_HAS_NO_JOBS_KEY"
    return None, "JOBS_KEY_IS_%s" % type(inner).__name__.upper()


def acquired(jobs_response, expected_run_id=None,
             expected_job_name=CAPTURE_JOB_NAME):
    """Did the dispatched run actually acquire the domain, and is it OURS?"""
    jobs, shape = _normalise_jobs(jobs_response)
    if jobs is None:
        return {
            "ACQUIRED": False,
            "RESPONSE_STATUS": "MALFORMED",
            "RESPONSE_SHAPE": shape,
            "JOB_COUNT": NOT_IDENTIFIED,
            "JOB_CREATED_AT": NOT_IDENTIFIED,
            "JOB_STARTED_AT": NOT_IDENTIFIED,
            "A_MALFORMED_RESPONSE_IS_NOT_AN_EMPTY_ONE":
                A_MALFORMED_RESPONSE_IS_NOT_AN_EMPTY_ONE,
            "ACQUISITION_IS_A_CREATED_JOB": ACQUISITION_IS_A_CREATED_JOB,
        }
    matching, problems = [], []
    for j in jobs:
        if not isinstance(j, dict):
            problems.append("NON_MAPPING_JOB_RECORD")
            continue
        if expected_run_id is not None and \
                str(j.get("run_id", "")) != str(expected_run_id):
            problems.append("JOB_BELONGS_TO_RUN_%s" % j.get("run_id"))
            continue
        if expected_job_name is not None and \
                j.get("name") != expected_job_name:
            problems.append("JOB_NAMED_%s" % j.get("name"))
            continue
        matching.append(j)
    first = matching[0] if matching else None
    created = (first or {}).get("created_at")
    return {
        "ACQUIRED": bool(created),
        "RESPONSE_STATUS": "PARSED",
        "RESPONSE_SHAPE": shape,
        "JOB_COUNT": len(jobs),
        "MATCHING_JOBS": len(matching),
        "EXPECTED_RUN_ID": expected_run_id if expected_run_id is not None
        else NOT_IDENTIFIED,
        "EXPECTED_JOB_NAME": expected_job_name or NOT_IDENTIFIED,
        "REJECTED_JOBS": tuple(problems),
        "JOB_CREATED_AT": created or NOT_IDENTIFIED,
        "JOB_STARTED_AT": (first or {}).get("started_at") or NOT_IDENTIFIED,
        "A_MALFORMED_RESPONSE_IS_NOT_AN_EMPTY_ONE":
            A_MALFORMED_RESPONSE_IS_NOT_AN_EMPTY_ONE,
        "ACQUISITION_IS_A_CREATED_JOB": ACQUISITION_IS_A_CREATED_JOB,
    }
