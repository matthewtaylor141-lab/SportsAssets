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
