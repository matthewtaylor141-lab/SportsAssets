"""Section 4. The immutable capture manifest, written at dispatch and hashed.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
THIS MODULE CONTACTS NOTHING. It records what the experiment WAS.

WHAT PROBLEM THIS SOLVES
------------------------
A harvest reads the capture's rows with today's code. If a constant moves
between dispatch and harvest -- a horizon, a support floor, an interval, a
tier boundary -- the harvest silently reports a DIFFERENT experiment from the
one that ran, and nothing in the output says so.

So the experiment definition is frozen at start, hashed, and the harvest must
prove it is analysing the run it thinks it is:

    dispatch:  m = build_manifest(...)  ->  write_manifest(path, m)
    harvest:   verify_against(m, live_definitions())  ->  MATCH or DRIFTED

A drift is NAMED, field by field. It is not repaired, not tolerated, and not
averaged over. A harvest against a drifted manifest is a different experiment
and must say so in its own report.

WHY THE MANIFEST IS WRITE-ONCE
------------------------------
A manifest that can be rewritten is not a manifest. write_manifest REFUSES to
overwrite an existing file, because the failure it guards against -- a later
run quietly restating the definition -- looks exactly like a legitimate write.
"""

import datetime
import hashlib
import json
import os

NOT_IDENTIFIED = "NOT_IDENTIFIED"

MANIFEST_VERSION = "1"

THIS_MODULE_CONTACTS_NOTHING = True

WRITE_ONCE = True
WHY_WRITE_ONCE = (
    "a manifest that can be rewritten is not a manifest. The failure it "
    "guards against -- a later run quietly restating the experiment "
    "definition -- is indistinguishable from a legitimate second write, so "
    "the second write is refused")

# Every field below is part of the experiment's identity. Adding a field is a
# manifest-version change; silently dropping one is the defect this prevents.
REQUIRED_FIELDS = (
    "MANIFEST_VERSION",
    "CODE_SHA",
    "CAPTURE_START_UTC",
    "PLANNED_END_UTC",
    "CAPTURE_SECONDS",
    "EVENT_IDS",
    "MARKET_IDS",
    "MARKET_FAMILIES",
    "POLL_INTERVAL_S",
    "RATE_RPS",
    "REQUEST_POLICY",
    "SELECTION_SHA",
    "CAPTURE_SPEC_SHA",
    "RUN_ID",
    "HOST",
    "RATE_LIMIT_PARAMETERS",
    "SCIENTIFIC_DEFINITIONS",
    "FROZEN_QUALITY_THRESHOLDS",
    "FROZEN_QUALITY_THRESHOLDS_SHA",
)

# The frozen scientific definitions. These are what a harvest is forbidden to
# reinterpret. They are stored BY VALUE, not by reference to a module, so a
# later edit to the module cannot retroactively change what the run meant.
DEFINITION_KEYS = (
    "TRANSITION_CLASSES",
    "OPPORTUNITY_TIERS",
    "TARGET_HORIZONS_SECONDS",
    "BASELINES",
    "SUPPORT_FLOORS",
    "EXECUTION_EVIDENCE_HIERARCHY",
    "INDEPENDENCE_UNIT",
)


def _iso(t):
    if isinstance(t, datetime.datetime):
        t = t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)
        return t.astimezone(datetime.timezone.utc).isoformat()
    return str(t)


def _canonical(obj):
    """Sorted, separator-fixed JSON. The hash must not depend on dict order."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def sha256_of(obj):
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def file_sha256(path):
    """Hash a source file, so CAPTURE_SPEC_SHA / SELECTION_SHA are real."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return NOT_IDENTIFIED


def build_manifest(code_sha, capture_start_utc, capture_seconds,
                   event_ids, market_ids, market_families,
                   poll_interval_s, rate_rps, request_policy,
                   selection_sha, capture_spec_sha, run_id, host,
                   rate_limit_parameters, scientific_definitions,
                   frozen_quality_thresholds=None,
                   frozen_quality_thresholds_sha=None):
    """Assemble the manifest. Every field is required; none defaults silently.

    A missing value must arrive as NOT_IDENTIFIED from the caller, so that the
    manifest records the absence rather than hiding it behind a default.
    """
    start = _iso(capture_start_utc)
    try:
        end = _iso(_parse(start) + datetime.timedelta(
            seconds=float(capture_seconds)))
    except Exception:
        end = NOT_IDENTIFIED
    m = {
        "MANIFEST_VERSION": MANIFEST_VERSION,
        "CODE_SHA": str(code_sha),
        "CAPTURE_START_UTC": start,
        "PLANNED_END_UTC": end,
        "CAPTURE_SECONDS": int(capture_seconds),
        "EVENT_IDS": sorted(str(e) for e in (event_ids or ())),
        "MARKET_IDS": sorted(str(m_) for m_ in (market_ids or ())),
        "MARKET_FAMILIES": sorted(str(f) for f in (market_families or ())),
        "POLL_INTERVAL_S": float(poll_interval_s),
        "RATE_RPS": str(rate_rps),
        "REQUEST_POLICY": dict(request_policy or {}),
        "SELECTION_SHA": str(selection_sha),
        "CAPTURE_SPEC_SHA": str(capture_spec_sha),
        "RUN_ID": str(run_id),
        "HOST": str(host),
        "RATE_LIMIT_PARAMETERS": dict(rate_limit_parameters or {}),
        "SCIENTIFIC_DEFINITIONS": dict(scientific_definitions or {}),
        # BY VALUE. The gate's floors are part of the experiment's identity:
        # a capture scored under different thresholds is a different
        # experiment, whatever its rows say.
        "FROZEN_QUALITY_THRESHOLDS": dict(frozen_quality_thresholds or {}),
        "FROZEN_QUALITY_THRESHOLDS_SHA": str(
            frozen_quality_thresholds_sha or NOT_IDENTIFIED),
    }
    m["INDEPENDENT_EVENT_N"] = len(m["EVENT_IDS"])
    m["MARKET_N"] = len(m["MARKET_IDS"])
    missing = [f for f in REQUIRED_FIELDS if f not in m]
    m["MISSING_REQUIRED_FIELDS"] = missing
    m["MANIFEST_COMPLETE"] = not missing
    # The hash covers everything EXCEPT itself.
    m["MANIFEST_SHA"] = sha256_of({k: v for k, v in m.items()
                                   if k != "MANIFEST_SHA"})
    return m


def _parse(ts):
    s = str(ts).replace("Z", "+00:00")
    d = datetime.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def recompute_sha(manifest):
    return sha256_of({k: v for k, v in (manifest or {}).items()
                      if k != "MANIFEST_SHA"})


def manifest_is_intact(manifest):
    """Has the manifest been edited since it was hashed?"""
    if not manifest or "MANIFEST_SHA" not in manifest:
        return {"INTACT": False, "REASON": "NO_MANIFEST_SHA"}
    got = recompute_sha(manifest)
    ok = got == manifest["MANIFEST_SHA"]
    return {"INTACT": ok,
            "RECORDED_SHA": manifest["MANIFEST_SHA"],
            "RECOMPUTED_SHA": got,
            "REASON": None if ok else "MANIFEST_EDITED_AFTER_HASHING"}


def write_manifest(path, manifest):
    """Write once. An existing file is a refusal, never an overwrite."""
    if os.path.exists(path):
        return {"STATUS": "REFUSED_MANIFEST_ALREADY_EXISTS", "PATH": path,
                "WHY_WRITE_ONCE": WHY_WRITE_ONCE, "WRITTEN": False}
    with open(path, "w") as fh:
        fh.write(json.dumps(manifest, indent=2, sort_keys=True, default=str))
        fh.write("\n")
    return {"STATUS": "WRITTEN", "PATH": path, "WRITTEN": True,
            "MANIFEST_SHA": manifest.get("MANIFEST_SHA", NOT_IDENTIFIED)}


def read_manifest(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# --- The harvest-side check. ----------------------------------------------

COMPARED_AT_HARVEST = (
    "CAPTURE_SECONDS", "EVENT_IDS", "MARKET_IDS", "MARKET_FAMILIES",
    "POLL_INTERVAL_S", "RATE_RPS", "SELECTION_SHA", "CAPTURE_SPEC_SHA",
    "SCIENTIFIC_DEFINITIONS", "FROZEN_QUALITY_THRESHOLDS",
    "FROZEN_QUALITY_THRESHOLDS_SHA",
)

THRESHOLD_DRIFT_IS_THE_WORST_KIND = (
    "a drifted SCIENTIFIC_DEFINITION changes what was measured; a drifted "
    "FROZEN_QUALITY_THRESHOLD changes whether the measurement was allowed to "
    "count. The second is the one that turns a failed capture into a passed "
    "one without touching a single row, so it is compared here by value")

DRIFT_IS_NOT_REPAIRED = (
    "a drifted field means the harvest is analysing a different experiment "
    "from the one that ran. The drift is named and the harvest is marked; it "
    "is never reconciled by adopting whichever value looks more convenient")


def verify_against(manifest, live):
    """Does today's code still define the experiment the run was dispatched on?

    `live` is a dict of the same keys, read from the modules the harvest is
    about to use. Any difference is reported field by field.
    """
    intact = manifest_is_intact(manifest)
    drifted = {}
    for k in COMPARED_AT_HARVEST:
        if k not in (manifest or {}) or k not in (live or {}):
            continue
        a, b = manifest[k], live[k]
        if isinstance(a, list):
            a = sorted(str(x) for x in a)
        if isinstance(b, list):
            b = sorted(str(x) for x in b)
        if _canonical(a) != _canonical(b):
            drifted[k] = {"MANIFEST": a, "LIVE": b}
    ok = intact["INTACT"] and not drifted
    return {
        "MANIFEST_INTACT": intact["INTACT"],
        "MANIFEST_INTEGRITY": intact,
        "DRIFTED_FIELDS": drifted,
        "DRIFT_COUNT": len(drifted),
        "DEFINITION_MATCHES_MANIFEST": ok,
        "STATUS": "MATCH" if ok else "DRIFTED",
        "DRIFT_IS_NOT_REPAIRED": DRIFT_IS_NOT_REPAIRED,
        "HARVEST_MAY_PROCEED": ok,
        "WHY_NOT": (None if ok else
                    "the harvest would report a different experiment from the "
                    "one that ran, without saying so"),
    }


def capture_spec_record(capture_params, frozen_quality_thresholds,
                        frozen_quality_thresholds_sha):
    """The CAPTURE_SPEC: capture parameters PLUS the frozen gate, hashed.

    The frozen capture code is pinned by SHA and may not be edited to carry
    these, so the spec record is where the two are bound together and the
    binding is hashed.
    """
    rec = {
        "CAPTURE_PARAMETERS": dict(capture_params or {}),
        "FROZEN_QUALITY_THRESHOLDS": dict(frozen_quality_thresholds or {}),
        "FROZEN_QUALITY_THRESHOLDS_SHA": str(frozen_quality_thresholds_sha),
        "THRESHOLDS_FROZEN_BEFORE_ANY_CAPTURE_DATA": True,
        "WHY_NOT_IN_THE_CAPTURE_SOURCE": (
            "the substantive capture is dispatched at a pinned code SHA and "
            "may not be altered before it runs. The gate is harvest-side, so "
            "the thresholds live with the gate and are bound to the run HERE, "
            "by value and by hash, rather than by editing pinned source"),
    }
    rec["CAPTURE_SPEC_RECORD_SHA"] = sha256_of(
        {k: v for k, v in rec.items() if k != "CAPTURE_SPEC_RECORD_SHA"})
    return rec


def describe():
    return {
        "MANIFEST_VERSION": MANIFEST_VERSION,
        "REQUIRED_FIELDS": REQUIRED_FIELDS,
        "DEFINITION_KEYS": DEFINITION_KEYS,
        "COMPARED_AT_HARVEST": COMPARED_AT_HARVEST,
        "WRITE_ONCE": WRITE_ONCE,
        "WHY_WRITE_ONCE": WHY_WRITE_ONCE,
        "DRIFT_IS_NOT_REPAIRED": DRIFT_IS_NOT_REPAIRED,
        "THRESHOLD_DRIFT_IS_THE_WORST_KIND": THRESHOLD_DRIFT_IS_THE_WORST_KIND,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
    }


# --- The in-job manifest step (ORCHESTRATION v2). --------------------------
#
# RUN 35333848994 IS WHY THIS EXISTS AS A CLI.
#
# Under v1 the manifest was written by an EXTERNAL check-in fired after the
# job had been dispatched, which meant the ordering the evidence claimed --
# approval, frozen selection, roster-bearing manifest, first sampled GET --
# was not the ordering anything enforced. The check-in could only arrive once
# sampling had already begun, and a manifest written then and called
# "pre-capture" would be a backdated record.
#
# This entry point runs INSIDE the job, between the frozen selection and the
# first sampled GET. It reads the roster the selection step just froze, so it
# cannot invent one; it refuses if the selection did not freeze; and because
# the workflow runs it with `set -e` and no `if: always()`, the capture step
# below it cannot execute unless it succeeded.
ORCHESTRATION_VERSION = "2"
ORDERING_IS_ENFORCED_BY_THE_JOB_NOT_A_CHECK_IN = (
    "approval -> frozen selection -> roster-bearing manifest -> first sampled "
    "GET. v1 left the third step to an external check-in that could only run "
    "after sampling may have started, so the ordering was asserted rather "
    "than enforced. v2 executes it in the job, before the sampling step")
A_MANIFEST_WRITTEN_AFTER_SAMPLING_IS_NOT_A_PRE_CAPTURE_MANIFEST = (
    "stamping a manifest with a start time that has already passed does not "
    "make it a pre-capture record; it makes it a backdated one. The manifest "
    "step stamps CAPTURE_START_UTC itself, before any sampled GET exists")


MEMBERSHIP_IS_A_SET_THE_SCHEDULE_IS_A_SEQUENCE = (
    "build_manifest canonicalises EVENT_IDS and MARKET_IDS by sorting them, "
    "which is right for MEMBERSHIP -- it makes the same roster hash the same "
    "way whatever order it arrived in. It is wrong for the SAMPLING "
    "SCHEDULE, whose order is the rotation the capture executes. The old "
    "binding check compared the sorted membership against the selection's "
    "original order and failed every roster that was not already sorted. "
    "Membership is compared as a set; the schedule is kept and compared in "
    "its exact frozen order")
A_SLUG_IS_NOT_A_FAMILY = (
    "MARKET_FAMILIES names the market TYPE family (moneyline, spread, "
    "total). Falling back to MARKET_SLUGS filled that field with market "
    "identifiers, so a reader grouping by family would see every market in "
    "its own family. Absent families are NOT_IDENTIFIED")
MANIFEST_CREATED_AT_IS_NOT_SAMPLING_START = (
    "the manifest is written BEFORE the first sampled GET, so it cannot know "
    "when sampling began. MANIFEST_CREATED_AT is when this record was made; "
    "PLANNED_CAPTURE_WINDOW is arithmetic from it; SAMPLING_START_UTC is "
    "NOT_OBSERVED here and is recorded later, by the sampler, in a separate "
    "record that links back to this manifest's hash. Stamping an observed "
    "start into a pre-capture manifest would backdate it")


def manifest_from_selection(selection, code_sha, run_id, capture_seconds,
                            host, selection_sha, capture_spec_sha,
                            capture_start_utc, request_policy,
                            rate_limit_parameters, scientific_definitions,
                            frozen_quality_thresholds=None,
                            frozen_quality_thresholds_sha=None,
                            poll_interval_s=4.0, rate_rps="0.25",
                            indirect_isolation_regime="DISCLOSED_RESIDUAL",
                            selection_file_sha256=None):
    """Build the manifest from the roster the selection step actually froze.

    Refuses a selection that did not freeze: a manifest with no roster is the
    thing the write-once rule most needs to keep off disk.
    """
    if selection.get("EVENT_SELECTION_FROZEN") != "YES":
        raise ValueError(
            "SELECTION_NOT_FROZEN: EVENT_SELECTION_FROZEN=%r "
            "SELECTION_STATUS=%r -- no roster, so no manifest"
            % (selection.get("EVENT_SELECTION_FROZEN"),
               selection.get("SELECTION_STATUS")))
    event_ids = [str(e) for e in (selection.get("EVENT_IDS") or ())]
    market_ids = [str(m) for m in (selection.get("MARKET_IDS") or ())]
    if not event_ids or not market_ids:
        raise ValueError("SELECTION_FROZEN_BUT_ROSTER_EMPTY: events=%d "
                         "markets=%d" % (len(event_ids), len(market_ids)))
    families = selection.get("MARKET_FAMILIES")
    m = build_manifest(
        code_sha=code_sha,
        capture_start_utc=capture_start_utc,
        capture_seconds=capture_seconds,
        event_ids=event_ids,
        market_ids=market_ids,
        market_families=list(families or ()),
        poll_interval_s=poll_interval_s,
        rate_rps=rate_rps,
        request_policy=request_policy,
        selection_sha=selection_sha,
        capture_spec_sha=capture_spec_sha,
        run_id=run_id,
        host=host,
        rate_limit_parameters=rate_limit_parameters,
        scientific_definitions=scientific_definitions,
        frozen_quality_thresholds=frozen_quality_thresholds,
        frozen_quality_thresholds_sha=frozen_quality_thresholds_sha)

    # MEMBERSHIP (sorted, from build_manifest) AND SCHEDULE (frozen order).
    m["EVENT_ID_SCHEDULE"] = list(event_ids)
    m["MARKET_ID_SCHEDULE"] = list(market_ids)
    m["MEMBERSHIP_IS_A_SET_THE_SCHEDULE_IS_A_SEQUENCE"] = (
        MEMBERSHIP_IS_A_SET_THE_SCHEDULE_IS_A_SEQUENCE)

    # A SLUG IS NOT A FAMILY.
    if not families:
        m["MARKET_FAMILIES"] = NOT_IDENTIFIED
        m["MARKET_FAMILIES_WHY"] = A_SLUG_IS_NOT_A_FAMILY

    # THE SELECTION FILE ITSELF, BY HASH -- not just the selector's code.
    m["SELECTION_FILE_SHA256"] = selection_file_sha256 or NOT_IDENTIFIED
    m["SELECTOR_CODE_SHA256"] = selection_sha

    # METADATA THAT DOES NOT PRETEND TO BE AN OBSERVATION.
    m["MANIFEST_CREATED_AT"] = capture_start_utc
    m["PLANNED_CAPTURE_WINDOW"] = {
        "PLANNED_START_UTC": m.get("CAPTURE_START_UTC"),
        "PLANNED_END_UTC": m.get("PLANNED_END_UTC"),
        "BASIS": "ARITHMETIC_FROM_MANIFEST_CREATED_AT",
    }
    m["SAMPLING_START_UTC"] = "NOT_OBSERVED_AT_MANIFEST_TIME"
    m["MANIFEST_CREATED_AT_IS_NOT_SAMPLING_START"] = (
        MANIFEST_CREATED_AT_IS_NOT_SAMPLING_START)

    # The approval scope travels with the manifest, unaltered and unclaimed.
    m["ORCHESTRATION_VERSION"] = ORCHESTRATION_VERSION
    m["INDIRECT_ISOLATION_REGIME"] = indirect_isolation_regime
    m["INDIRECT_BETTOR_PMUS_LOAD_ISOLATION"] = "NOT_ESTABLISHED"
    m["INDIRECT_CONFOUND_MAGNITUDE"] = NOT_IDENTIFIED
    m["ORDERING_IS_ENFORCED_BY_THE_JOB_NOT_A_CHECK_IN"] = (
        ORDERING_IS_ENFORCED_BY_THE_JOB_NOT_A_CHECK_IN)
    m["MANIFEST_PRECEDES_FIRST_SAMPLED_GET"] = True
    m["MANIFEST_SHA"] = recompute_sha(m)
    return m


def sampling_start_record(manifest, observed_sampling_start_utc,
                          first_sampled_get_utc=None):
    """The OBSERVED start, in its own record, linked to the immutable manifest.

    The manifest is not edited -- that is what write-once means. This is a
    second artifact that names the manifest it belongs to by hash.
    """
    return {
        "RECORD": "SAMPLING_START",
        "MANIFEST_SHA": manifest.get("MANIFEST_SHA", NOT_IDENTIFIED),
        "RUN_ID": manifest.get("RUN_ID", NOT_IDENTIFIED),
        "SAMPLING_START_UTC": observed_sampling_start_utc,
        "FIRST_SAMPLED_GET_UTC": first_sampled_get_utc or NOT_IDENTIFIED,
        "MANIFEST_CREATED_AT": manifest.get("MANIFEST_CREATED_AT",
                                            NOT_IDENTIFIED),
        "MANIFEST_IS_NOT_EDITED_BY_THIS_RECORD": True,
        "MANIFEST_CREATED_AT_IS_NOT_SAMPLING_START":
            MANIFEST_CREATED_AT_IS_NOT_SAMPLING_START,
    }


def manifest_binds_selection(manifest, selection, selection_file_sha256=None):
    """The manifest must name the roster this selection froze -- no other."""
    sel_events = [str(e) for e in (selection.get("EVENT_IDS") or ())]
    sel_markets = [str(m) for m in (selection.get("MARKET_IDS") or ())]
    out = {
        "MANIFEST_INTACT": manifest_is_intact(manifest).get("INTACT"),
        "ORCHESTRATION_VERSION": manifest.get("ORCHESTRATION_VERSION"),
        # MEMBERSHIP: order-insensitive on both sides, compared consistently.
        "EVENT_MEMBERSHIP_MATCHES":
            sorted(manifest.get("EVENT_IDS") or ()) == sorted(sel_events),
        "MARKET_MEMBERSHIP_MATCHES":
            sorted(manifest.get("MARKET_IDS") or ()) == sorted(sel_markets),
        # SCHEDULE: exact frozen order, preserved end to end.
        "EVENT_SCHEDULE_MATCHES":
            list(manifest.get("EVENT_ID_SCHEDULE") or ()) == sel_events,
        "MARKET_SCHEDULE_MATCHES":
            list(manifest.get("MARKET_ID_SCHEDULE") or ()) == sel_markets,
        "ROSTER_NON_EMPTY": bool(manifest.get("EVENT_IDS")),
        "MANIFEST_PRECEDES_FIRST_SAMPLED_GET":
            manifest.get("MANIFEST_PRECEDES_FIRST_SAMPLED_GET") is True,
        "SAMPLING_START_NOT_CLAIMED":
            manifest.get("SAMPLING_START_UTC") == "NOT_OBSERVED_AT_MANIFEST_TIME",
    }
    if selection_file_sha256 is not None:
        out["SELECTION_FILE_SHA256_MATCHES"] = (
            manifest.get("SELECTION_FILE_SHA256") == selection_file_sha256)
    out["VERIFIED"] = all(v is True for k, v in out.items()
                          if k != "ORCHESTRATION_VERSION")
    return out


# --- The approved configuration, read from the modules that own it. -------
#
# THE CLI USED TO ASK substantive_capture FOR REQUEST_POLICY,
# RATE_LIMIT_PARAMETERS AND SCIENTIFIC_DEFINITIONS, WHICH IT HAS NEVER
# DEFINED. The getattr fallback handed the STRING "NOT_IDENTIFIED" to
# build_manifest, where dict("NOT_IDENTIFIED") raises
#   ValueError: dictionary update sequence element #0 has length 1; 2 is required
# so the manifest step could never have run. The values are real and already
# approved -- they simply live under their own names in the modules that own
# them. They are assembled here explicitly. Substituting {} to get past the
# error would have written a manifest that records no policy at all.
CONFIG_SOURCES = {
    "REQUEST_POLICY": "substantive_capture",
    "RATE_LIMIT_PARAMETERS": "rate_pilot",
    "SCIENTIFIC_DEFINITIONS": "substantive_capture+substantive_harvest",
}
MISSING_DEFINITION_IS_NAMED_NOT_DEFAULTED = (
    "a configuration mapping the manifest cannot build from its approved "
    "source is named and refused. An empty dict would record 'no policy' as "
    "though it were the policy")


def _need(mod, name):
    """Read an approved constant, or name exactly what is missing."""
    if not hasattr(mod, name):
        raise ValueError("MISSING_APPROVED_DEFINITION: %s.%s"
                         % (getattr(mod, "__name__", mod), name))
    return getattr(mod, name)


def request_policy_from_modules(SC):
    """The pacing/method policy actually executed by the capture."""
    return {
        "METHOD": _need(SC, "METHOD"),
        "CREDENTIAL": _need(SC, "CREDENTIAL"),
        "ORDER_PATH_EXISTS": _need(SC, "ORDER_PATH_EXISTS"),
        "MIRROR_LIVE": _need(SC, "MIRROR_LIVE"),
        "RATE_RPS": _need(SC, "RATE_RPS"),
        # GLOBAL REQUEST INTERVAL AND PER-MARKET REVISIT INTERVAL ARE NOT THE
        # SAME NUMBER. 4.0 s is the gap between consecutive requests across
        # the whole rotation; a single market is revisited every 24.0 s.
        # Reporting one as the other overstates each market's sampling rate
        # by the number of markets.
        "GLOBAL_REQUEST_INTERVAL_S": float(_need(SC, "REQUEST_INTERVAL_S")),
        "PER_MARKET_REVISIT_INTERVAL_S": float(_need(SC, "NOMINAL_REVISIT_S")),
        "MARKETS_IN_ROTATION": int(_need(SC, "MARKETS")),
        "RATE_IS_THE_CONFIRMED_ONE": _need(SC, "RATE_IS_THE_CONFIRMED_ONE"),
        "RATE_MAY_NOT_BE_RAISED": _need(SC, "RATE_MAY_NOT_BE_RAISED"),
    }


def rate_limit_parameters_from_modules(RP):
    """What the pacer does when the venue pushes back."""
    return {
        "DEFAULT_BACKOFF_S": float(_need(RP, "DEFAULT_BACKOFF_S")),
        "MAX_BACKOFF_S": float(_need(RP, "MAX_BACKOFF_S")),
        "COOLDOWN_BASIS_RETRY_AFTER": _need(RP, "COOLDOWN_BASIS_RETRY_AFTER"),
        "RATE_LIMIT_HEADER_CANDIDATES":
            list(_need(RP, "RATE_LIMIT_HEADER_CANDIDATES")),
        "HEADERS_ARE_REPORTED_NOT_INTERPRETED":
            _need(RP, "HEADERS_ARE_REPORTED_NOT_INTERPRETED"),
        "RATE_LIMIT_SEMANTICS": _need(RP, "RATE_LIMIT_SEMANTICS"),
        "VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED":
            _need(RP, "VENUE_RATE_LIMIT_MECHANISM_IDENTIFIED"),
    }


def scientific_definitions_from_modules(SC, SH):
    """What this capture is, is not, and refuses to claim."""
    return {
        "THIS_IS": _need(SC, "THIS_IS"),
        "THIS_IS_NOT": list(_need(SC, "THIS_IS_NOT")),
        "EXECUTION_EVIDENCE_HIERARCHY":
            list(_need(SH, "EXECUTION_EVIDENCE_HIERARCHY")),
        "HIERARCHY_IS_NOT_COLLAPSED": _need(SH, "HIERARCHY_IS_NOT_COLLAPSED"),
        "TOUCH_IS_NOT_A_FILL": _need(SH, "TOUCH_IS_NOT_A_FILL"),
        "UNKNOWN_IS_NOT_NOT_FILLED": _need(SH, "UNKNOWN_IS_NOT_NOT_FILLED"),
        "PUBLIC_TICK_FILL_IDENTIFICATION":
            _need(SH, "PUBLIC_TICK_FILL_IDENTIFICATION"),
        "ACTUAL_BETTOR_FILL_RATE": _need(SH, "ACTUAL_BETTOR_FILL_RATE"),
        "REALIZED_MAKER_ECONOMICS": _need(SH, "REALIZED_MAKER_ECONOMICS"),
    }


def approved_configuration():
    """Assemble all three from the real modules, or name what is missing."""
    import rate_pilot as RP
    import substantive_capture as SC
    import substantive_harvest as SH
    return {
        "REQUEST_POLICY": request_policy_from_modules(SC),
        "RATE_LIMIT_PARAMETERS": rate_limit_parameters_from_modules(RP),
        "SCIENTIFIC_DEFINITIONS": scientific_definitions_from_modules(SC, SH),
    }


def _cli():                                                   # pragma: no cover
    import argparse
    import datetime as _dt
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--manifest")
    ap.add_argument("--selection")
    ap.add_argument("--out")
    ap.add_argument("--code-sha")
    ap.add_argument("--run-id")
    ap.add_argument("--capture-seconds", default="5400")
    ap.add_argument("--orchestration-version", default=ORCHESTRATION_VERSION)
    ap.add_argument("--indirect-isolation-regime", default="DISCLOSED_RESIDUAL")
    a = ap.parse_args()

    with open(a.selection) as fh:
        selection = json.load(fh)
    selection_file_sha = file_sha256(a.selection)

    if a.verify:
        with open(a.manifest) as fh:
            manifest = json.load(fh)
        v = manifest_binds_selection(manifest, selection, selection_file_sha)
        for k in sorted(v):
            print("%-42s %s" % (k, v[k]))
        if not v["VERIFIED"]:
            raise SystemExit("MANIFEST_DOES_NOT_BIND_THIS_SELECTION")
        return

    if a.orchestration_version != ORCHESTRATION_VERSION:
        raise SystemExit("ORCHESTRATION_VERSION mismatch: workflow says %r, "
                         "module is %r" % (a.orchestration_version,
                                           ORCHESTRATION_VERSION))
    import capture_quality as CQ
    import collect as C
    intact = CQ.thresholds_intact()
    if intact.get("INTACT") is not True:
        raise SystemExit("FROZEN_QUALITY_THRESHOLDS_NOT_INTACT")
    try:
        cfg = approved_configuration()
    except ValueError as e:
        raise SystemExit(str(e))            # names the missing definition
    created = _dt.datetime.now(_dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        m = manifest_from_selection(
            selection,
            code_sha=a.code_sha,
            run_id=a.run_id,
            capture_seconds=int(a.capture_seconds),
            host=C.HOST,
            selection_sha=file_sha256("substantive_select.py"),
            capture_spec_sha=file_sha256("substantive_capture.py"),
            capture_start_utc=created,
            request_policy=cfg["REQUEST_POLICY"],
            rate_limit_parameters=cfg["RATE_LIMIT_PARAMETERS"],
            scientific_definitions=cfg["SCIENTIFIC_DEFINITIONS"],
            frozen_quality_thresholds=CQ.FROZEN_QUALITY_THRESHOLDS,
            frozen_quality_thresholds_sha=CQ.FROZEN_QUALITY_THRESHOLDS_SHA,
            indirect_isolation_regime=a.indirect_isolation_regime,
            selection_file_sha256=selection_file_sha)
    except ValueError as e:
        raise SystemExit(str(e))            # not frozen, or roster empty

    res = write_manifest(a.out, m)          # write-once
    # A REFUSAL IS A NONZERO EXIT, AND PRINTS NO SHA. Printing a freshly
    # computed MANIFEST_SHA after a refused write would announce a manifest
    # that was never written and is not the one on disk.
    if not res.get("WRITTEN"):
        print("WRITE_STATUS          = %s" % res.get("STATUS"))
        print("PATH                  = %s" % res.get("PATH"))
        raise SystemExit("MANIFEST_NOT_WRITTEN: %s" % res.get("STATUS"))
    print("WRITE_STATUS          = %s" % res["STATUS"])
    print("CAPTURE_MANIFEST_SHA  = %s" % m["MANIFEST_SHA"])
    print("MANIFEST_CREATED_AT   = %s" % m["MANIFEST_CREATED_AT"])
    print("SAMPLING_START_UTC    = %s" % m["SAMPLING_START_UTC"])
    print("INDEPENDENT_EVENTS    = %d" % len(m["EVENT_IDS"]))
    print("MARKETS               = %d" % len(m["MARKET_IDS"]))
    print("ORCHESTRATION_VERSION = %s" % m["ORCHESTRATION_VERSION"])


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
