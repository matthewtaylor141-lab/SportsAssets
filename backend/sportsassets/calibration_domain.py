"""COORDINATION WITH PROTECTED RESEARCH, using the EXISTING machinery.

WHAT WAS WRONG. `calibration_evidence.domain_isolation` walked two status
filters -- in_progress and queued -- and decided membership by a
`beta48-` name prefix. Three defects, all of the class this codebase
keeps being bitten by:

  * A STATUS FILTER THE API CANNOT EXPRESS. `?status=queued` does not
    return every waiting state, and a run held by the concurrency group
    can sit in a state that filter never shows. A member that is
    invisible reads as an idle domain.
  * MEMBERSHIP BY NAME PREFIX. `run85-phase2-capture` does not start
    with `beta48-` and it IS a member of pmus-public-read-global. The
    shortcut would have read an executing run85 as an idle domain --
    exactly the collector this coordination exists to protect.
  * A SECOND, COMPETING CENSUS. The repository already has one, built
    and tested for this: `run_census.workflow_walk` / `run_census.census`
    over the inventory `venue_domain` discovers from the workflow files
    themselves, plus `venue_domain.isolation` for the verdict. Writing a
    second one guarantees the two disagree.

So this module DELEGATES. It discovers the domain inventory from the
repository, walks every member's run history to exhaustion, assembles
the census with the existing code, and asks `venue_domain.isolation` for
the verdict. It adds no rule of its own.

A CLEAR SNAPSHOT IS NOT A LOCK, and this module says so rather than
implying otherwise. A census taken at 17:10:00Z says nothing about
17:10:05Z: a scheduled collector can arrive in between. The only
mechanism that actually EXCLUDES the collectors is the one they already
obey -- the GitHub concurrency group `pmus-public-read-global`. Evidence
gathering that must not overlap a capture therefore runs as a job in
that group (`.github/workflows/calibration-evidence.yml`), and the
reservation is the group's own serialisation, not a snapshot this
process took.

`reservation_state()` reports which of the two a caller actually holds,
and `RESERVATION_HELD` is only ever true when the process is running
inside the group. A caller outside it gets `SNAPSHOT_ONLY` and the
evidence command treats that as a blocker unless the operator has
explicitly accepted it for a non-production read.

THIS MODULE CONTACTS NO VENUE. It reads the GitHub Actions API and the
repository's own workflow files.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

# Verdict vocabulary. Never a bare boolean: an unreadable domain must not
# be spendable as an idle one.
CLEAR = "CLEAR"
ACTIVE = "ACTIVE"
UNKNOWN = "UNKNOWN"

B_DOMAIN = "PROTECTED_RESEARCH_WINDOW_ACTIVE"
B_DOMAIN_UNREADABLE = "DOMAIN_STATE_NOT_ESTABLISHED"
B_NO_MACHINERY = "CENSUS_MACHINERY_UNAVAILABLE"
B_NO_RESERVATION = "NO_EXECUTION_RESERVATION_HELD"

AN_UNESTABLISHED_DOMAIN_IS_NOT_IDLE = (
    "a domain state we could not read is not an idle one. UNKNOWN refuses "
    "exactly as ACTIVE does")

A_SNAPSHOT_IS_NOT_A_LOCK = (
    "a census is true at the instant it was taken. A scheduled collector "
    "can arrive a second later. The only thing that EXCLUDES the "
    "collectors is the concurrency group they already obey, so evidence "
    "gathering that must not overlap a capture runs inside it")

# The group every venue-touching workflow shares. Read from venue_domain
# rather than repeated here.
RESERVATION_ENV = "PMUS_DOMAIN_RESERVATION"

MAX_PAGES = 40
PER_PAGE = 100

# The repository does not change under a running process, and both the
# module load and the workflow-file scan are expensive enough to matter
# when every gather pays for them.
_MACHINERY_CACHE = {}
_INVENTORY_CACHE = {}


class MachineryUnavailable(Exception):
    """The repository's census code could not be loaded.

    Raised rather than falling back to a simpler check: a simpler check is
    how the name-prefix shortcut got written in the first place.
    """


def _repo_root(root=None) -> pathlib.Path:
    if root:
        return pathlib.Path(root)
    # backend/sportsassets/calibration_domain.py -> repo root
    return pathlib.Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise MachineryUnavailable("cannot load %s from %s" % (name, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


def machinery(root=None):
    """`run_census` and `venue_domain`, loaded from the repository.

    They live in research/beta48/shadow, outside this package, and they
    are the tested implementations. Importing them by path is deliberate:
    the alternative is a copy, and a copy drifts.
    """
    key = str(_repo_root(root))
    if key in _MACHINERY_CACHE:
        return _MACHINERY_CACHE[key]
    shadow = _repo_root(root) / "research" / "beta48" / "shadow"
    if not shadow.is_dir():
        raise MachineryUnavailable("no census machinery at %s" % shadow)
    sys.path.insert(0, str(shadow)) if str(shadow) not in sys.path else None
    try:
        got = (_load("run_census", shadow / "run_census.py"),
               _load("venue_domain", shadow / "venue_domain.py"))
        _MACHINERY_CACHE[key] = got
        return got
    except MachineryUnavailable:
        raise
    except Exception as exc:                                   # noqa: BLE001
        raise MachineryUnavailable("%s: %s" % (type(exc).__name__, exc)) from exc


def inventory(root=None):
    """The venue-touching workflows, discovered from the files themselves.

    `venue_domain.audit` reads every workflow in the repository and names
    the ones that can reach the venue. It is not a list anyone maintains
    by hand, which is the point: run85-phase2-capture is a member and
    does not carry the beta48 prefix.
    """
    key = str(_repo_root(root))
    if key in _INVENTORY_CACHE:
        return list(_INVENTORY_CACHE[key])
    _rc, vd = machinery(root)
    a = vd.audit(key)
    names = a.get("KNOWN_VENUE_TOUCHING_WORKFLOWS") or []
    out = []
    for n in names:
        out.append(n["workflow"] if isinstance(n, dict) else str(n))
    _INVENTORY_CACHE[key] = sorted(set(out))
    return list(_INVENTORY_CACHE[key])


def _walk_workflow(fetch, repo, workflow, rc):
    """Every run of one workflow, paged to exhaustion, through run_census.

    NO STATUS FILTER. `run_census` documents why: a filter the API cannot
    express hides the states a concurrency-held member sits in. The walk
    takes the whole history and lets the census classify it.
    """
    pages, total = [], None
    for page_no in range(1, MAX_PAGES + 1):
        body = fetch("https://api.github.com/repos/%s/actions/workflows/%s/runs"
                     "?per_page=%d&page=%d" % (repo, workflow, PER_PAGE, page_no))
        if not isinstance(body, dict):
            raise ValueError("RUNS_RESPONSE_NOT_A_MAPPING")
        rows = body.get("workflow_runs")
        if rows is None or not isinstance(rows, list):
            raise ValueError("RUNS_RESPONSE_HAS_NO_RUN_LIST")
        if total is None:
            total = body.get("total_count")
        pages.append(rows)
        if len(rows) < PER_PAGE:
            break
    return rc.workflow_walk(workflow, pages, PER_PAGE, total_count=total)


def domain_state(fetch=None, repo=None, root=None, self_run_id=None):
    """The complete domain census and its isolation verdict.

    Returns {"STATE": CLEAR|ACTIVE|UNKNOWN, ...}. Every failure path is
    UNKNOWN; none of them is CLEAR.
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if fetch is None:
        if not repo or not token:
            return {"STATE": UNKNOWN, "REASON": "NO_REPO_OR_TOKEN",
                    "note": AN_UNESTABLISHED_DOMAIN_IS_NOT_IDLE}

        def fetch(url):                                   # pragma: no cover
            import httpx
            r = httpx.get(url, timeout=30.0, headers={
                "Authorization": "Bearer %s" % token,
                "Accept": "application/vnd.github+json"})
            r.raise_for_status()
            return r.json()

    try:
        rc, vd = machinery(root)
    except MachineryUnavailable as exc:
        return {"STATE": UNKNOWN, "REASON": "%s: %s" % (B_NO_MACHINERY, exc),
                "note": AN_UNESTABLISHED_DOMAIN_IS_NOT_IDLE}

    try:
        members = inventory(root)
    except Exception as exc:                                   # noqa: BLE001
        return {"STATE": UNKNOWN,
                "REASON": "INVENTORY_UNREADABLE: %s" % type(exc).__name__,
                "note": AN_UNESTABLISHED_DOMAIN_IS_NOT_IDLE}
    if not members:
        # An empty inventory would read as an idle domain. run_census
        # names that failure; it is never a pass.
        return {"STATE": UNKNOWN, "REASON": "EMPTY_INVENTORY",
                "note": AN_UNESTABLISHED_DOMAIN_IS_NOT_IDLE}

    walks, errors = {}, []
    for wf in members:
        try:
            walks[wf] = _walk_workflow(fetch, repo, wf, rc)
        except Exception as exc:                               # noqa: BLE001
            errors.append("%s: %s" % (wf, type(exc).__name__))

    cen = rc.census(walks, members, errors=errors)
    if not cen.get("CENSUS_COMPLETE"):
        return {"STATE": UNKNOWN, "REASON": "CENSUS_INCOMPLETE",
                "census": cen, "note": AN_UNESTABLISHED_DOMAIN_IS_NOT_IDLE}

    # GATE_INPUT is run_census's own name for the occupying rows it
    # certified. Reading its key rather than re-filtering the rows keeps
    # the classification in one place.
    occupying = cen.get("GATE_INPUT") or []
    verdict = vd.isolation(occupying, members, self_run_id=self_run_id)
    # venue_domain names its own verdict; this module does not re-derive it.
    clear = verdict.get("DIRECT_RESEARCH_COLLECTOR_ISOLATION") == "ESTABLISHED"
    active = [r for r in occupying
              if (r.get("status") or "").lower() in rc.RUNNING_STATES]
    waiting = [r for r in occupying
               if (r.get("status") or "").lower() in rc.WAITING_STATES]
    return {
        "STATE": CLEAR if clear else ACTIVE,
        "REASON": None if clear else "DOMAIN_OCCUPIED",
        "isolation": verdict,
        "inventory": members,
        "running": [{"workflow": r.get("name"), "id": r.get("id")}
                    for r in active],
        "waiting": [{"workflow": r.get("name"), "id": r.get("id")}
                    for r in waiting],
        "censusComplete": True,
        "snapshotIsNotALock": A_SNAPSHOT_IS_NOT_A_LOCK,
    }


def reservation_state(env=None):
    """Whether this process holds the group's own serialisation, or a snapshot.

    The GitHub concurrency group is the mechanism the collectors obey. A
    job running inside it holds a real reservation; anything else holds a
    snapshot. The workflow sets PMUS_DOMAIN_RESERVATION to the group name
    it is running under, and this reads it rather than assuming.
    """
    env = env if env is not None else os.environ
    held = env.get(RESERVATION_ENV)
    try:
        _rc, vd = machinery()
        group = vd.GLOBAL_DOMAIN
    except MachineryUnavailable:
        group = "pmus-public-read-global"
    if held and str(held).strip() == group:
        return {"RESERVATION_HELD": True, "MECHANISM": "GITHUB_CONCURRENCY_GROUP",
                "GROUP": group}
    return {"RESERVATION_HELD": False, "MECHANISM": "SNAPSHOT_ONLY",
            "GROUP": group, "why": A_SNAPSHOT_IS_NOT_A_LOCK}


def coordination(fetch=None, repo=None, root=None, self_run_id=None,
                 env=None, require_reservation=True):
    """The whole coordination answer: census verdict AND reservation.

    COORDINATION IS NOT OPTIONAL and omitting a flag cannot bypass it --
    this is what the evidence command calls unconditionally. Its blockers
    are returned; there is no argument that turns the check off, only one
    that records an operator's explicit acceptance of a snapshot-only
    read for a NON-PRODUCTION gather.
    """
    state = domain_state(fetch=fetch, repo=repo, root=root,
                         self_run_id=self_run_id)
    res = reservation_state(env)
    blockers = []
    if state["STATE"] == ACTIVE:
        blockers.append(B_DOMAIN)
    elif state["STATE"] != CLEAR:
        blockers.append(B_DOMAIN_UNREADABLE)
    if require_reservation and not res["RESERVATION_HELD"]:
        blockers.append(B_NO_RESERVATION)
    return {"domain": state, "reservation": res,
            "blockers": sorted(set(blockers)),
            "MAY_READ_THE_VENUE": not blockers}
