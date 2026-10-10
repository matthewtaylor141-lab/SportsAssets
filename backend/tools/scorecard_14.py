"""THE OWNER'S 14-CATEGORY ENGINEERING SCORECARD, FROM MACHINE EVIDENCE ONLY.

RC5 directive (2026-10-08): every category must reach >= 95% verified
readiness under a clearly defined denominator supported by independently
reproducible evidence; categories are not averaged; no subjective scores;
failures are not excluded by changing the denominator. Consistent
profitability is classified separately (PROVEN / UNPROVEN), never here.

HOW A CATEGORY IS SCORED. Each category is a FROZEN list of UNITS (checks or
counted members), each read at a DECLARED path in a pm-acceptance readback
(the acc/ directory a run produces). readiness = passed units / all units.
A unit whose input cannot be read is a FAILED unit with a named
READ_UNAVAILABLE reason (never skipped, never a pass). A counted denominator
of zero is UNMEASURED (zero samples are not 100%). A category passes only at
readiness >= 0.95 with no unreadable unit.

Economic-evidence REDs (a control that is RED because forward evidence has
not accrued) are still FAILED units here: they are annotated FORWARD so the
reader can tell them from software defects, but they are not dropped.

TWO RED-TEAM CONTROLS ARE BOUND WITH THE HALF THE API CANNOT HOLD (RC6).
MIGRATION_INTEGRITY and RELEASE each read UNKNOWN in production for one
named absence, not for a finding: FRESH_DB_RESULT_NOT_IN_THIS_PROCESS (the
serving API cannot build an empty database) and
NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA (receipts are POSTed only on
post_receipt == "on"). The judge holds both halves as attested files of
this packet -- fresh_db.json (capital-critical's own fresh PostgreSQL build
of the release SHA) and release_verdict.json (the receipt the sender would
POST, judged by the API's own release gate) -- and _bound() combines them
with the API's control: any RED from either side is RED, an API control
GREEN stays GREEN, and an UNKNOWN is lifted ONLY when that one named
absence is its sole blocker AND the judge's half is PROVEN / GREEN for the
release SHA. Absent or unverified is UNPROVEN (failed); every detail keeps
the API's own status and blockers beside the binding.

V2 (RC6 lane E, owner directive 2E): THE EVALUATOR AND ITS INPUTS ARE PINNED.

  * scorecard_14.json records the evaluator (the judge commit, this file's
    own sha256 and VERSION), the implementation, release and production
    frontend SHAs, and the sha256 of EVERY input file it read (the bytes it
    parsed, read once). A unit whose input NAMES ANOTHER RELEASE than the
    one being graded (the serving API's own RENDER_GIT_COMMIT, a readback's
    own serving build, the Trader acceptance's API, the previewed frontend
    against production's build.json) is UNMEASURED with the input named:
    a scorecard of release X is never computed from release Y's readbacks.
    An input that names no release is graded and listed UNATTRIBUTED.
  * GRADING_CHANGES declares every unit whose grading differs from the
    pinned prior evaluator (2fadc8dc, the RC5 judge) and why;
    tools/grading_diff.py runs both evaluators on the same packet and lists
    every unit whose verdict differs -- declared or not, never hidden.
  * Kalshi integration's credential unit reads KALSHI's own verdict and
    provisioning in CREDENTIAL_CLASSES (production 37836393458: aggregate
    RED from PMUS alone, evidence.verdicts.KALSHI = MATCHES); the Red-team
    row keeps the aggregate control unchanged.
  * Deployment infrastructure adds release_lineage (implementation tree ==
    release tree, single-parent release commit, gates green on both SHAs),
    upgrade_path (the release's migrations onto the previous release's
    schema with representative rows, nothing applied changed) and
    rollback_ready (the previous release per service, its gates, and its
    compatibility with the new schema): three requirements added, none
    removed; absent evidence is a failed unit, never a pass.

Usage:  python backend/tools/scorecard_14.py ACC_DIR [--release-sha SHA]
            [--frontend-preview preview.json] [--evaluator-sha SHA]
            [--implementation-sha SHA]
        writes ACC_DIR/scorecard_14.json and prints the table.

STANDARD LIBRARY ONLY, ONE FILE: a later judge runs this exact file (by
commit) as its pinned prior evaluator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

VERSION = "SCORECARD_14_V2"
TARGET = 0.95
CATEGORIES = (
    "Core trading engine", "GitHub CI and regression tests",
    "Red-team safeguards", "Deployment infrastructure",
    "Market-plane stability", "Data freshness and latency",
    "Sports and market coverage", "EV and pricing methodology",
    "Xavier and agent coordination", "Archer and reconciliation",
    "Adriana cross-venue arbitrage", "Risk management and capital controls",
    "Kalshi integration", "Command Center desktop and mobile")
SERVICES = ("sportsassets-api", "sportsassets-workers",
            "sportsassets-market-plane")
#: blockers that mean "forward evidence has not accrued", for annotation only
FORWARD_MARKERS = ("FEWER_THAN_", "INSUFFICIENT_", "NO_POSITIVE_", "NO_PREREGISTERED",
                   "DSR_NOT_ACCEPTABLE", "PBO_NOT_ACCEPTABLE",
                   "NO_EVENT_CLUSTERED_PARTITION", "MECHANISM_DISABLED:",
                   "KAREN_COUNTERFACTUALS_UNMEASURED")

# ── the judge's halves of MIGRATION_INTEGRITY and RELEASE (RC6) ───────────
GREEN, RED, UNKNOWN = "GREEN", "RED", "UNKNOWN"
PROVEN, UNPROVEN = "PROVEN", "UNPROVEN"
#: the ONE blocker each API control carries when only the judge's half is
#: missing (redteam.controls.migrations / redteam.readiness RELEASE)
API_FRESH_DB_ABSENT = "FRESH_DB_RESULT_NOT_IN_THIS_PROCESS"
API_RELEASE_ABSENT = "NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA"
#: tools/fresh_db_receipt.py and tools/release_verdict.py
CAPITAL_CRITICAL_WORKFLOW = ".github/workflows/capital-critical.yml"
FRESH_DB_RECEIPT_VERSION = "FRESH_DB_RECEIPT_V1"
RELEASE_VERDICT_VERSION = "RELEASE_VERDICT_V1"
R_FRESH_DB_READBACK_ABSENT = "FRESH_DB_READBACK_ABSENT"
R_FRESH_DB_RECEIPT_ABSENT = "FRESH_DB_RECEIPT_ABSENT"
R_FRESH_DB_NOT_ATTESTED = "FRESH_DB_RECEIPT_ATTESTATION_NOT_VERIFIED"
R_FRESH_DB_NOT_CAPITAL_CRITICAL = "FRESH_DB_RECEIPT_NOT_FROM_CAPITAL_CRITICAL"
R_FRESH_DB_NOT_THE_RELEASE = "FRESH_DB_RECEIPT_NOT_FOR_THE_RELEASE_SHA"
R_FRESH_DB_NOT_THE_GATE_RUN = "FRESH_DB_RECEIPT_NOT_FROM_THE_GATE_RUN"
R_FRESH_DB_MALFORMED = "FRESH_DB_RECEIPT_MALFORMED"
R_FRESH_DB_BUILD_FAILED = "FRESH_DB_BUILD_FAILED"
R_FRESH_DB_FINGERPRINT_DIFFERS = "FRESH_DB_FINGERPRINT_DIFFERS_FROM_RUNNING"
R_FRESH_DB_COUNT_DIFFERS = "FRESH_DB_COUNT_DIFFERS_FROM_RUNNING"
R_RUNNING_FINGERPRINT_UNREADABLE = "RUNNING_MIGRATION_FINGERPRINT_UNREADABLE"
R_RUNNING_NOT_THE_RELEASE = "RUNNING_API_NOT_ON_THE_RELEASE_SHA"
R_RELEASE_VERDICT_ABSENT = "RELEASE_VERDICT_ABSENT"
R_RELEASE_VERDICT_NOT_THE_RELEASE = "RELEASE_VERDICT_NOT_FOR_THE_RELEASE_SHA"
R_RELEASE_VERDICT_REFUSED = "RELEASE_VERDICT_REFUSED"
R_RELEASE_VERDICT_INCONSISTENT = "RELEASE_VERDICT_INCONSISTENT"

# ── V2: inputs pinned to the release they name ───────────────────────────
R_INPUT_NAMES_ANOTHER_RELEASE = "INPUT_NAMES_ANOTHER_RELEASE"
R_API_IDENTITY_CONFLICT = "API_SERVING_IDENTITY_CONFLICT"
R_FRONTEND_NOT_DEPLOYED = "FRONTEND_PREVIEW_IS_NOT_THE_DEPLOYED_FRONTEND"
MATCHES, FOREIGN, UNATTRIBUTED = ("MATCHES", "NAMES_ANOTHER_RELEASE",
                                  "UNATTRIBUTED")
NO_REFERENCE = "NO_RELEASE_SHA_GIVEN"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
#: readbacks served by sportsassets-api in the run (their release is the
#: serving API's unless they name their own serving build)
API_SERVED = ("red_team.json", "release.json", "completion.json",
              "venues.json", "shadow_health.json", "loop_health.json",
              "market_plane.json", "paper_freshness.json",
              "paper_reconciliation.json", "xavier_management.json",
              "small_live.json", "canary.json", "capital_readiness.json",
              "pm_before.json", "pm_after.json",
              "profitability_scoreboard.json", "revenue_readiness.json")
#: an input's OWN statement of the build that produced it, at a declared
#: path (pm-acceptance 37836393458 carries each of these)
OWN_IDENTITY = {
    "red_team.json": "data.readiness.implementation_sha",
    "release.json": "api.sha",
    "small_live.json": "launch.serving_build",
    "canary.json": "boots.api.commit_sha",
    "capital_readiness.json": "data.source_sha",
    "pm_after.json": "data.pm_acceptance.evidence_input.deployed_sha",
    "pm_before.json": "data.pm_acceptance.evidence_input.deployed_sha",
    # the judge's own receipts, written for one SHA
    "acceptance.json": "sha",
    "runtime_window.json": "expected_commit",
}
#: where the serving API names itself (the RENDER_GIT_COMMIT it reports);
#: two that disagree mean the API changed during the run
API_IDENTITY = (("red_team.json", "data.readiness.implementation_sha"),
                ("release.json", "api.sha"))
#: the production frontend's own build record (frontend/scripts/
#: write-build-info.mjs, https://command.bettortoken.com/build.json) and the
#: frontend-preview run's target (its out/target_sha.txt)
FRONTEND_BUILD = "frontend_build.json"
FRONTEND_PREVIEW_IDENTITY = "frontend_preview_identity.json"

# ── V2: Kalshi's own credential verdict (CREDENTIAL_CLASSES, scoped) ──────
R_KALSHI_CREDENTIAL_CONTROL_NOT_COMPUTED = "KALSHI_CREDENTIAL_CONTROL_NOT_COMPUTED"
R_KALSHI_CREDENTIAL_EVIDENCE_ABSENT = "KALSHI_CREDENTIAL_EVIDENCE_ABSENT"
R_KALSHI_CREDENTIAL_VERDICT = "KALSHI_CREDENTIAL_VERDICT_NOT_MATCHES"
R_KALSHI_CREDENTIAL_NOT_PROVISIONED = "KALSHI_CREDENTIAL_NOT_PROVISIONED"
R_KALSHI_CREDENTIAL_CLASS = "KALSHI_CREDENTIAL_CLASS_NOT_APPROVED"
R_KALSHI_CREDENTIAL_BLOCKER = "KALSHI_CREDENTIAL_BLOCKER"
R_CREDENTIAL_BLOCKER_UNATTRIBUTED = "CREDENTIAL_BLOCKER_NOT_ATTRIBUTABLE_TO_A_SLOT"
_CRED_BLOCKER = re.compile(r"^(CREDENTIAL_CLASS_MISMATCH|MISSING_CREDENTIAL_CLASS)"
                           r":([A-Z0-9_]+)(:|$)")

# ── V2: release lineage, upgrade path, rollback readiness (Deployment) ────
R_LINEAGE_IMPLEMENTATION_ABSENT = "LINEAGE_IMPLEMENTATION_SHA_ABSENT"
R_LINEAGE_TREES_DIFFER = "LINEAGE_IMPLEMENTATION_TREE_DIFFERS_FROM_RELEASE_TREE"
R_LINEAGE_NOT_SINGLE_PARENT = "LINEAGE_RELEASE_COMMIT_NOT_SINGLE_PARENT"
R_LINEAGE_NOT_THE_RELEASE = "LINEAGE_NOT_FOR_THE_RELEASE_SHA"
R_LINEAGE_BRANCH_ELSEWHERE = "LINEAGE_RELEASE_BRANCH_NOT_AT_THE_RELEASE_SHA"
R_LINEAGE_NOT_DESCENDANT = "LINEAGE_NOT_DESCENDANT_OF_ACCEPTED_BASE"
R_LINEAGE_RELEASE_GATE = "LINEAGE_RELEASE_GATE_NOT_GREEN"
R_LINEAGE_IMPLEMENTATION_GATE = "LINEAGE_IMPLEMENTATION_GATE_NOT_GREEN"
R_LINEAGE_IMPLEMENTATION_GATES_UNREAD = "LINEAGE_IMPLEMENTATION_GATES_UNREAD"
GATES = ("backend_tests", "capital_critical", "commit_guard",
         "engine_diagnostic")
UPGRADE_PATH_RECEIPT_VERSION = "UPGRADE_PATH_RECEIPT_V1"
ROLLBACK_VERSION = "ROLLBACK_READINESS_V1"
R_UPGRADE_READBACK_ABSENT = "UPGRADE_PATH_READBACK_ABSENT"
R_UPGRADE_RECEIPT_ABSENT = "UPGRADE_PATH_RECEIPT_ABSENT"
R_UPGRADE_NOT_ATTESTED = "UPGRADE_PATH_RECEIPT_ATTESTATION_NOT_VERIFIED"
R_UPGRADE_NOT_CAPITAL_CRITICAL = "UPGRADE_PATH_RECEIPT_NOT_FROM_CAPITAL_CRITICAL"
R_UPGRADE_NOT_THE_RELEASE = "UPGRADE_PATH_RECEIPT_NOT_FOR_THE_RELEASE_SHA"
R_UPGRADE_NOT_THE_GATE_RUN = "UPGRADE_PATH_RECEIPT_NOT_FROM_THE_GATE_RUN"
R_UPGRADE_MALFORMED = "UPGRADE_PATH_RECEIPT_MALFORMED"
R_UPGRADE_FAILED = "UPGRADE_PATH_FAILED"
R_UPGRADE_BASE_NOT_TARGET = "UPGRADE_PATH_BASE_IS_NOT_THE_ROLLBACK_TARGET"
R_UPGRADE_NOT_REPRESENTATIVE = "UPGRADE_PATH_ROWS_NOT_REPRESENTATIVE"
R_UPGRADE_BASE_UNBUILT = "UPGRADE_PATH_BASE_NOT_BUILT"
#: tools/upgrade_path_receipt reasons about the BASE alone (no previous
#: release found / built): never a finding about the release's migrations
UPGRADE_BASE_SIDE = ("UPGRADE_NOT_A_FULL_SHA", "UPGRADE_BASE_TREE_UNREADABLE",
                     "UPGRADE_BASE_BUILD_FAILED", "UPGRADE_RUN_CRASHED")
R_ROLLBACK_NOT_THE_RELEASE = "ROLLBACK_READINESS_NOT_FOR_THE_RELEASE_SHA"
R_ROLLBACK_MALFORMED = "ROLLBACK_READINESS_MALFORMED"
R_ROLLBACK_TARGET_UNKNOWN = "ROLLBACK_TARGET_UNKNOWN"
R_ROLLBACK_SERVICE_NOT_ON_RELEASE = "ROLLBACK_SERVICE_NOT_ON_THE_RELEASE"
R_ROLLBACK_TARGET_NOT_ON_RELEASE_LINE = "ROLLBACK_TARGET_NOT_AN_ANCESTOR_OF_THE_RELEASE"
R_ROLLBACK_TARGET_GATE = "ROLLBACK_TARGET_GATE_NOT_GREEN"
R_ROLLBACK_COMMANDS_INCOMPLETE = "ROLLBACK_COMMANDS_INCOMPLETE"
R_ROLLBACK_SCHEMA_BLOCKED = "ROLLBACK_SCHEMA_BLOCKED"
R_ROLLBACK_SCHEMA_UNPROVEN = "ROLLBACK_SCHEMA_COMPATIBILITY_UNPROVEN"
# (rc6.3 rollback-fix) the judge re-derives each service's rollback from
# that service's OWN deploy list in the packet and passes on only commands
# it re-derived (approved-judge 38002788631 wrote a market-plane command for
# 3d5af039, the release that hung the plane, onto a plane not on the release)
R_ROLLBACK_HISTORY_UNREADABLE = "ROLLBACK_DEPLOY_HISTORY_UNREADABLE"
R_ROLLBACK_LIVE_NOT_RENDERS = "ROLLBACK_LIVE_DEPLOY_DIFFERS_FROM_RENDER_SUMMARY"
R_ROLLBACK_COMMAND_OFF_RELEASE = \
    "ROLLBACK_COMMAND_FOR_A_SERVICE_NOT_ON_THE_RELEASE"
R_ROLLBACK_NOT_OWN_PREVIOUS = \
    "ROLLBACK_COMMAND_NOT_THE_SERVICES_OWN_PREVIOUS_LIVE_COMMIT"
R_ROLLBACK_NOT_ITS_SERVICE = \
    "ROLLBACK_COMMAND_NOT_THE_SERVICES_OWN_DEPLOY_ACTION"
#: a deploy status Render does not document, newer than the live deploy or
#: between it and the previous live commit: whether it served is unknown
R_ROLLBACK_DEPLOY_STATUS_UNKNOWN = "ROLLBACK_DEPLOY_STATUS_UNKNOWN"
#: a served (`deactivated`) deploy newer in the list than the live one: the
#: list is not newest-first, so what ran before cannot be read from it
R_ROLLBACK_SERVED_NEWER_THAN_LIVE = \
    "ROLLBACK_SERVED_DEPLOY_NEWER_THAN_THE_LIVE_DEPLOY"
#: Render's deploy statuses that never served (the same set as
#: tools/rollback_readiness.NEVER_SERVED, pinned equal by test): an
#: allowlist -- any status that is not one of these, `live` or
#: `deactivated` is unknown, never taken to mean "never served"
RENDER_NEVER_SERVED = frozenset((
    "build_failed", "update_failed", "canceled", "pre_deploy_failed",
    "created", "queued", "build_in_progress", "update_in_progress",
    "pre_deploy_in_progress"))
#: each service's documented deploy-by-commit action (the same text as
#: tools/rollback_readiness.COMMANDS, pinned equal by test; this file stays
#: one standard-library file): a command is passed on only when it is
#: exactly its own service's form with its own previous live commit, so a
#: right commit can never ride to the wrong service
ROLLBACK_COMMAND_FORMS = {
    "sportsassets-api":
        "gh workflow run render-ops.yml --ref claude/p0-closeout "
        "-f action=deploy-api-commit -f service=sportsassets-api "
        "-f arg=%s -f confirm=DO",
    "sportsassets-workers":
        "gh workflow run render-ops.yml --ref claude/p0-closeout "
        "-f action=workers-commit-deploy -f service=sportsassets-workers "
        "-f arg=%s -f confirm=DO",
    "sportsassets-market-plane":
        "gh workflow run market-plane.yml --ref claude/release-api "
        "-f action=deploy-commit -f arg=%s -f confirm=DO",
}
IDENTICAL = "IDENTICAL_MIGRATION_SET"
COMPATIBLE = "COMPATIBLE"


class Unavailable(Exception):
    pass


class Foreign(Unavailable):
    """The input names another release than the one graded: UNMEASURED."""


class Acc(str):
    """The packet directory, carrying what this scoring read: every input
    file parsed once ({name: (doc, sha256)}) and each input's identity."""

    def __new__(cls, path):
        o = str.__new__(cls, path)
        o.cache, o.read, o.absent, o.identity = {}, {}, {}, {}
        return o


def _parse_file(acc, key: str, path: str):
    """(doc) of one file, read and hashed ONCE per scoring; Unavailable
    when absent or not JSON (named)."""
    cache = getattr(acc, "cache", None)
    if cache is not None and key in cache:
        doc, why = cache[key]
        if why:
            raise Unavailable(why)
        return doc
    doc, why = None, None
    if not os.path.isfile(path):
        why = "READ_UNAVAILABLE:%s:MISSING" % key
    else:
        with open(path, "rb") as f:
            raw = f.read()
        if cache is not None:
            acc.read[key] = hashlib.sha256(raw).hexdigest()
        try:
            doc = json.loads(raw)
        except ValueError:
            why = "READ_UNAVAILABLE:%s:NOT_JSON" % key
    if cache is not None:
        cache[key] = (doc, why)
        if why and key not in acc.read:
            acc.absent[key] = why
    if why:
        raise Unavailable(why)
    return doc


def _load(acc: str, name: str, *, pinned: bool = True):
    """An input of the packet. pinned: refused (UNMEASURED) when the input
    names another release than the one graded; the identity checks
    themselves read unpinned."""
    doc = _parse_file(acc, name, os.path.join(acc, name))
    if pinned:
        ident = (getattr(acc, "identity", None) or {}).get("files", {}).get(
            name)
        if ident and ident.get("verdict") == FOREIGN:
            raise Foreign("UNMEASURED:%s:%s:%s" % (
                R_INPUT_NAMES_ANOTHER_RELEASE, name, ident.get("reason")))
    return doc


def _at(doc, path: str, name: str):
    """The value at a declared dotted path; absent is Unavailable."""
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise Unavailable("READ_UNAVAILABLE:%s:%s" % (name, path))
    return cur


def _env(acc, name, path, *, envelope=True, pinned=True):
    """Read through the Command readback envelope (status OK required)."""
    doc = _load(acc, name, pinned=pinned)
    if envelope and isinstance(doc, dict) and "status" in doc and "data" in doc:
        if doc.get("status") != "OK":
            raise Unavailable("READ_UNAVAILABLE:%s:ENVELOPE_%s" % (
                name, doc.get("status")))
        doc = doc["data"]
    return _at(doc, path, name)


def _failed_read(exc) -> str:
    """The class of a unit that could not be graded: an input naming
    another release is UNMEASURED; anything else unread is
    READ_UNAVAILABLE. Both are failures."""
    return "UNMEASURED" if isinstance(exc, Foreign) else "READ_UNAVAILABLE"


class Card:
    def __init__(self, name):
        self.name, self.units = name, []

    def unit(self, uid, fn, *, forward=False):
        try:
            ok, detail = fn()
            self.units.append({"unit": uid, "passed": bool(ok),
                               "detail": detail,
                               "class": ("PASS" if ok else
                                         ("FORWARD" if forward else "FAIL"))})
        except Unavailable as exc:
            self.units.append({"unit": uid, "passed": False,
                               "detail": str(exc), "class": _failed_read(exc)})

    def counted(self, uid, num, den, *, detail=None, exc=None):
        """A counted member set: each member is a unit. den 0 = UNMEASURED.
        A member set whose input names another release is UNMEASURED."""
        if den is None or num is None:
            self.units.append({"unit": uid, "passed": False, "members": None,
                               "detail": detail or "READ_UNAVAILABLE",
                               "class": _failed_read(exc)})
            return
        self.units.append({"unit": uid, "members": [int(num), int(den)],
                           "passed": den > 0 and num / den >= TARGET,
                           "detail": detail,
                           "class": ("UNMEASURED" if den == 0 else
                                     ("PASS" if num / den >= TARGET
                                      else "FAIL"))})

    def result(self):
        """readiness = the MINIMUM over the category's components: each
        counted member set is a component, and all check units together are
        one component. A large healthy member set can never carry failed
        checks (or a small failing set) over the target."""
        comps = []
        checks = [u for u in self.units if "members" not in u]
        if checks:
            comps.append({"component": "checks",
                          "numerator": sum(1 for u in checks if u["passed"]),
                          "denominator": len(checks)})
        for u in self.units:
            if "members" in u:
                m = u["members"]
                comps.append({"component": u["unit"],
                              "numerator": None if m is None else m[0],
                              "denominator": None if m is None else m[1]})
        for c in comps:
            c["rate"] = (round(c["numerator"] / c["denominator"], 4)
                         if c["denominator"] else None)
        rates = [c["rate"] for c in comps]
        readiness = None if (not rates or any(r is None for r in rates)) \
            else min(rates)
        binding = None
        if comps:
            binding = min(comps, key=lambda c: -1 if c["rate"] is None
                          else c["rate"])["component"]
        unreadable = [u["unit"] for u in self.units
                      if u["class"] in ("READ_UNAVAILABLE", "UNMEASURED")]
        return {"category": self.name, "readiness": readiness,
                "binding_component": binding, "components": comps,
                "passes": bool(readiness is not None and readiness >= TARGET
                               and not unreadable),
                "unreadable_units": unreadable, "units": self.units}


def _gate(acc, key):
    run = _at(_load(acc, "gates.json"), "runs.%s" % key, "gates.json")
    return run.get("conclusion") == "success", {
        "run": run.get("id"), "sha": run.get("head_sha"),
        "conclusion": run.get("conclusion")}


def score(acc: str, *, release_sha: str | None = None,
          frontend_preview: str | None = None,
          evaluator_sha: str | None = None,
          implementation_sha: str | None = None) -> dict:
    acc = Acc(acc)
    acc.identity = identities(acc, release_sha, frontend_preview)
    cards = []

    # 1 CORE TRADING ENGINE: the engine's required production controls
    c = Card(CATEGORIES[0])
    gates = lambda k: _env(acc, "completion.json", "gates.%s.value" % k)  # noqa: E731
    c.unit("software_reds_zero", lambda: (gates("software_reds_zero") is True,
            _env(acc, "completion.json", "readiness.evidence.software_red_count")))
    c.unit("production_canary_clean", lambda: (gates("production_canary_clean") is True, None))
    c.unit("profitability_bind_active", lambda: (gates("profitability_bind_active") is True, None))
    c.unit("executable_ev_priced_decisions", lambda: (
        (_env(acc, "completion.json", "executable_ev.decisions_priced") or 0) > 0,
        _env(acc, "completion.json", "executable_ev.decisions_priced")))
    c.unit("cash_when_no_candidate_qualifies", lambda: (
        _env(acc, "completion.json", "strategy_tournament.selected") == "CASH"
        or _env(acc, "completion.json", "strategy_tournament.status") != "NO_PROMOTION",
        _env(acc, "completion.json", "strategy_tournament.reason")))
    c.unit("decision_pipeline_live", lambda: (
        _at(_load(acc, "shadow_health.json"),
            "components.BETTOR_DECISION_PIPELINE.state", "shadow_health.json") in ("LIVE", "HEALTHY"),
        _at(_load(acc, "shadow_health.json"),
            "components.BETTOR_DECISION_PIPELINE.state", "shadow_health.json")))
    c.unit("capital_critical_loops_healthy", lambda: (
        _at(_load(acc, "loop_health.json"), "capital_critical_not_healthy", "loop_health.json") == [],
        _at(_load(acc, "loop_health.json"), "summary", "loop_health.json")))
    c.unit("historical_paper_immutable_by_receipt", lambda: (
        _at(_load(acc, "acceptance.json"), "paper_history.status", "acceptance.json") == "PROVEN"
        and _at(_load(acc, "acceptance.json"), "paper_history.immutable", "acceptance.json") is True,
        _at(_load(acc, "acceptance.json"), "paper_history", "acceptance.json")))
    cards.append(c)

    # 2 GITHUB CI AND REGRESSION TESTS: required gates on the exact release SHA
    c = Card(CATEGORIES[1])
    for k in ("backend_tests", "capital_critical", "commit_guard", "engine_diagnostic"):
        c.unit(k, lambda k=k: _gate(acc, k))
    c.unit("gates_on_release_sha", lambda: (
        release_sha is not None and all(
            _at(_load(acc, "gates.json"), "runs.%s.head_sha" % k, "gates.json") == release_sha
            for k in ("backend_tests", "capital_critical", "commit_guard", "engine_diagnostic")),
        release_sha))
    c.unit("frontend_device_gate", lambda: _frontend_gate(frontend_preview, acc))
    cards.append(c)

    # 3 RED-TEAM SAFEGUARDS: every red-team control GREEN (forward REDs annotated)
    c = Card(CATEGORIES[2])
    try:
        controls = _env(acc, "red_team.json", "readiness.controls")
    except Unavailable as exc:
        controls = None
        c.units.append({"unit": "controls", "passed": False, "detail": str(exc),
                        "class": _failed_read(exc)})
    for name, ctl in sorted((controls or {}).items()):
        if name in BOUND:
            # the API's own status AND the judge's attested half (RC6)
            c.unit(name, lambda name=name: BOUND[name](acc, release_sha))
            continue
        blockers = ctl.get("blockers") or []
        fwd = bool(blockers) and all(any(m in str(b) for m in FORWARD_MARKERS)
                                     for b in blockers)
        c.unit(name, lambda ctl=ctl, blockers=blockers: (
            ctl.get("status") == "GREEN", {"status": ctl.get("status"),
                                           "blockers": blockers[:6]}), forward=fwd)
    cards.append(c)

    # 4 DEPLOYMENT INFRASTRUCTURE: every service on the release, migrations intact
    c = Card(CATEGORIES[3])
    # (the identity checks themselves: read unpinned, a different commit is
    # the FAIL they exist to report)
    for svc in SERVICES:
        c.unit("%s_on_release" % svc, lambda svc=svc: (
            release_sha is not None and
            _at(_load(acc, "render.json", pinned=False), "%s.live_commit" % svc,
                "render.json") == release_sha,
            _at(_load(acc, "render.json", pinned=False), "%s.live_commit" % svc,
                "render.json")))
    c.unit("workers_boot_on_release", lambda: (
        _at(_load(acc, "canary.json", pinned=False), "boots.workers_boot.commit_sha",
            "canary.json") == release_sha,
        _at(_load(acc, "canary.json", pinned=False), "boots.workers_boot.commit_sha",
            "canary.json")))
    c.unit("market_plane_heartbeat_on_release", lambda: _plane_beat(acc, release_sha))
    # applied == build AND an empty database builds to the same fingerprint
    # (the capital-critical fresh build of this SHA; RC6)
    c.unit("migration_integrity", lambda: migration_integrity(acc, release_sha))
    # V2 (owner directive 2E): the release IS the tested implementation, the
    # migrations upgrade the previous release's database, and the previous
    # release can be put back on the new schema
    c.unit("release_lineage", lambda: release_lineage(acc, release_sha))
    c.unit("upgrade_path", lambda: upgrade_path(acc, release_sha))
    c.unit("rollback_ready", lambda: rollback_ready(acc, release_sha))
    cards.append(c)

    # 5 MARKET-PLANE STABILITY: OOM-free complete window with headroom
    c = Card(CATEGORIES[4])
    p = "sportsassets-market-plane"
    c.unit("zero_oom_since_live", lambda: (
        _at(_load(acc, "render.json"), "%s.oom_events_since_live" % p, "render.json") == 0,
        _at(_load(acc, "render.json"), "%s.oom_events_since_live" % p, "render.json")))
    c.unit("zero_server_failed_since_live", lambda: (
        _at(_load(acc, "render.json"), "%s.server_failed_since_live" % p, "render.json") == 0,
        _at(_load(acc, "render.json"), "%s.server_failed_since_live" % p, "render.json")))
    c.unit("runtime_window_ge_60_min", lambda: _runtime_window(acc))
    c.unit("memory_highwater_le_85pct", lambda: (
        (_at(_load(acc, "market_plane.json"), "snapshot.runtime.resources.highwater_fraction",
             "market_plane.json") or 1.0) <= 0.85,
        _at(_load(acc, "market_plane.json"), "snapshot.runtime.resources", "market_plane.json")))
    c.unit("snapshot_current", lambda: (
        _env(acc, "completion.json", "market_data.snapshot") == "CURRENT"
        or str(_env(acc, "completion.json", "market_data.snapshot")).startswith("CURRENT"),
        _env(acc, "completion.json", "market_data.snapshot")))
    cards.append(c)

    # 6 DATA FRESHNESS AND LATENCY: held + priority members fresh, latency SLO
    c = Card(CATEGORIES[5])
    try:
        fr = _at(_load(acc, "paper_freshness.json"), "freshness", "paper_freshness.json")
        counts = fr.get("counts") or {}
        fresh = (counts.get("FRESH") or {}).get("count", 0) + (counts.get("QUIET_VALID") or {}).get("count", 0)
        c.counted("held_positions_fresh", fresh, fr.get("markable"),
                  detail="(FRESH + QUIET_VALID) / markable, SLA %ss" % fr.get("sla_s"))
    except Unavailable as exc:
        c.counted("held_positions_fresh", None, None, detail=str(exc), exc=exc)
    try:
        pf = _env(acc, "completion.json", "market_data.priority_freshness")
        c.counted("priority_members_fresh", pf.get("numerator"), pf.get("denominator"),
                  detail="priority freshness (dedicated plane snapshot)")
    except Unavailable as exc:
        c.counted("priority_members_fresh", None, None, detail=str(exc), exc=exc)
    c.unit("latency_slo_green", lambda: (
        _at(_load(acc, "market_plane.json"), "snapshot.latency.green", "market_plane.json") is True,
        _at(_load(acc, "market_plane.json"), "snapshot.latency.transport_ms", "market_plane.json")))
    cards.append(c)

    # 7 SPORTS AND MARKET COVERAGE: priceable share of the active universe
    c = Card(CATEGORIES[6])
    try:
        bs = _at(_load(acc, "market_plane.json"), "snapshot.coverage.by_state", "market_plane.json")
        total = _at(_load(acc, "market_plane.json"), "snapshot.coverage.active", "market_plane.json")
        ext = int(bs.get("EXTERNAL_DATA_UNAVAILABLE") or 0)
        c.counted("active_contracts_priceable", int(bs.get("PRICEABLE") or 0), int(total) - ext,
                  detail={"by_state": bs, "external_excluded_and_reported": ext})
    except Unavailable as exc:
        c.counted("active_contracts_priceable", None, None, detail=str(exc), exc=exc)
    cards.append(c)

    # 8 EV AND PRICING METHODOLOGY: methodology controls evidenced in production
    c = Card(CATEGORIES[7])
    c.unit("calibration_measured", lambda: (
        _env(acc, "completion.json", "probability.bettor_calibration_error_ece10") is not None
        and (_env(acc, "completion.json", "probability.independent_events") or 0)
        >= (_env(acc, "completion.json", "probability.minimum_events") or 10**9),
        {"ece10": _env(acc, "completion.json", "probability.bettor_calibration_error_ece10"),
         "independent_events": _env(acc, "completion.json", "probability.independent_events")}))
    c.unit("executable_ev_computed", lambda: (
        (_env(acc, "completion.json", "executable_ev.decisions_priced") or 0) > 0,
        _env(acc, "completion.json", "executable_ev.verdict")))
    for ctl in ("FEE_EVIDENCE", "DIGITAL_TWIN", "SAMPLE_INTEGRITY", "MULTIPLE_TESTING",
                "CAPACITY", "ATTRIBUTION"):
        c.unit("control_%s" % ctl, lambda ctl=ctl: (
            _env(acc, "red_team.json", "readiness.controls.%s.status" % ctl) == "GREEN",
            _env(acc, "red_team.json", "readiness.controls.%s.blockers" % ctl)),
            forward=ctl in ("DIGITAL_TWIN", "SAMPLE_INTEGRITY", "MULTIPLE_TESTING", "CAPACITY"))
    cards.append(c)

    # 9 XAVIER AND AGENT COORDINATION: complete current packets over held positions
    c = Card(CATEGORIES[8])
    try:
        rate = _env(acc, "completion.json", "readiness.evidence.xavier_packet_complete_rate")
        held = _at(_load(acc, "paper_freshness.json"), "freshness.open_positions", "paper_freshness.json")
        c.counted("held_positions_with_complete_current_packet",
                  None if rate is None else round(rate * held), held,
                  detail="xavier_packet_complete_rate x open positions")
    except Unavailable as exc:
        c.counted("held_positions_with_complete_current_packet", None, None, detail=str(exc), exc=exc)
    c.unit("no_open_position_without_review", lambda: (
        _at(_load(acc, "xavier_management.json"), "summary.open_without_review", "xavier_management.json") == 0
        and _at(_load(acc, "xavier_management.json"), "summary.reviews_overdue", "xavier_management.json") == 0,
        _at(_load(acc, "xavier_management.json"), "summary", "xavier_management.json")))
    cards.append(c)

    # 10 ARCHER AND RECONCILIATION
    c = Card(CATEGORIES[9])
    rc = lambda k: _at(_load(acc, "paper_reconciliation.json"), "reconciliation.counts.%s" % k,  # noqa: E731
                       "paper_reconciliation.json")
    c.unit("no_phantom_opens", lambda: (rc("phantom_opens_in_canonical_readers") == 0, None))
    c.unit("no_live_protection_on_closed", lambda: (rc("live_protection_on_closed_positions") == 0, None))
    c.unit("no_economic_duplicate_suspects", lambda: (rc("economic_duplicate_suspect_groups") == 0,
                                                      {"groups": rc("economic_duplicate_suspect_groups"),
                                                       "extra_qty": rc("economic_duplicate_suspect_extra_qty")}))
    c.unit("management_epoch_reconciled", lambda: (gates("management_epoch_reconciled") is True, None))
    c.unit("truth_quorum", lambda: (
        _env(acc, "red_team.json", "readiness.controls.TRUTH_QUORUM.status") == "GREEN",
        _env(acc, "red_team.json", "readiness.controls.TRUTH_QUORUM.blockers")))
    c.unit("retail_venue_confirmed", lambda: (
        _env(acc, "completion.json", "venue_positions.venue_confirmed") is True,
        _env(acc, "completion.json", "venue_positions.primary_refusal")))
    cards.append(c)

    # 11 ADRIANA CROSS-VENUE ARBITRAGE (SHADOW): scans run on fresh, terms-checked inputs
    c = Card(CATEGORIES[10])
    c.unit("claim_scan_ran", lambda: (
        _env(acc, "venues.json", "arbitrage.scan.status") == "OK",
        _env(acc, "venues.json", "arbitrage.scan.scan_id")))
    try:
        arb = _env(acc, "completion.json", "arbitrage.latest_scan")
        c.counted("scanned_markets_with_fresh_books", arb.get("books_fresh"), arb.get("markets_read"),
                  detail="books_fresh / markets_read")
    except Unavailable as exc:
        c.counted("scanned_markets_with_fresh_books", None, None, detail=str(exc), exc=exc)
    c.unit("void_terms_established", lambda: (
        "void terms not established" not in str(_env(acc, "completion.json", "arbitrage.fail_closed")),
        _env(acc, "completion.json", "arbitrage.fail_closed")))
    c.unit("every_refusal_named", lambda: (
        all(r.get("primary_code") for r in _env(acc, "venues.json", "arbitrage.refusals")),
        len(_env(acc, "venues.json", "arbitrage.refusals"))))
    c.unit("shadow_only", lambda: (
        _env(acc, "completion.json", "arbitrage.authority") == "SHADOW_ONLY", None))
    cards.append(c)

    # 12 RISK MANAGEMENT AND CAPITAL CONTROLS
    c = Card(CATEGORIES[11])
    auth = lambda k: _env(acc, "red_team.json", "readiness.authority.%s" % k)  # noqa: E731
    c.unit("small_live_shadow", lambda: (auth("small_live") == "SHADOW", None))
    c.unit("kalshi_live_money_not_activated", lambda: (auth("kalshi_live_money") == "NOT_ACTIVATED", None))
    c.unit("adriana_shadow_only", lambda: (auth("adriana") == "SHADOW_ONLY", None))
    c.unit("no_capital_authority_granted", lambda: (auth("capital_authority_granted") is False, None))
    c.unit("canonical_exposure_lock", lambda: (
        _env(acc, "red_team.json", "readiness.controls.CANONICAL_EXPOSURE.status") == "GREEN", None))
    c.unit("workers_venue_writes_locked", lambda: (
        _at(_load(acc, "canary.json"), "boots.workers_boot.venue_writes", "canary.json") == "LOCKED", None))
    c.unit("market_plane_process_locked", lambda: (
        _env(acc, "venues.json", "health.KALSHI_HEALTH.mechanism.plane.process_locked") is True, None))
    c.unit("actual_orders_impossible_now", lambda: (
        _at(_load(acc, "small_live.json"), "launch.actual_orders_possible_now", "small_live.json") is False,
        _at(_load(acc, "small_live.json"), "launch.why_not", "small_live.json")))
    cards.append(c)

    # 13 KALSHI INTEGRATION
    c = Card(CATEGORIES[12])
    kh = lambda k: _env(acc, "venues.json", "health.KALSHI_HEALTH.%s" % k)  # noqa: E731
    c.unit("websocket_is_the_mechanism", lambda: (kh("mechanism.mechanism") == "KALSHI_WS", kh("mechanism.why")))
    try:
        f = kh("freshness")
        c.counted("tracked_books_fresh", f.get("numerator"), f.get("denominator"),
                  detail={"by_source": f.get("current_by_source"), "sla_s": f.get("sla_s")})
    except Unavailable as exc:
        c.counted("tracked_books_fresh", None, None, detail=str(exc), exc=exc)
    c.unit("catalogue_complete", lambda: (kh("catalogue.complete") is True, kh("catalogue.stopped")))
    c.unit("health_state_ok", lambda: (kh("state") == "OK", kh("state")))
    # Kalshi's OWN credential verdict and provisioning (V2): the aggregate
    # CREDENTIAL_CLASSES status stays the Red-team row's unit
    c.unit("credential_class_control", lambda: kalshi_credentials(acc))
    cards.append(c)

    # 14 COMMAND CENTER DESKTOP AND MOBILE: device-view checks
    c = Card(CATEGORIES[13])
    _device_units(c, frontend_preview, acc)
    cards.append(c)

    out = [x.result() for x in cards]
    return {"version": VERSION, "target": TARGET, "release_sha": release_sha,
            "categories": out,
            "all_categories_pass": all(x["passes"] for x in out),
            "passing": sum(1 for x in out if x["passes"]),
            "not_averaged": True,
            "profitability": "classified separately (PROVEN / UNPROVEN); not a category here",
            "pinning": pinning(acc, release_sha=release_sha,
                               evaluator_sha=evaluator_sha,
                               implementation_sha=implementation_sha),
            "grading_changes": [dict(g) for g in GRADING_CHANGES]}


def _runtime_window(acc):
    """The RC5 harness's own Render receipt (acc/acceptance.json
    runtime_window: CLEAN / FAILED / UNKNOWN over api, workers and plane)."""
    rw = _at(_load(acc, "acceptance.json"), "runtime_window", "acceptance.json")
    m = rw.get("no_oom_minutes")
    if rw.get("status") == "UNKNOWN" or m is None:
        raise Unavailable("READ_UNAVAILABLE:acceptance.json:runtime_window:%s" % (
            rw.get("status"),))
    return rw.get("status") == "CLEAN" and m >= 60, {
        "status": rw.get("status"), "no_oom_minutes": m,
        "observation_minutes": rw.get("observation_minutes")}


def _plane_beat(acc, release_sha):
    # the plane's OWN heartbeat commit: an identity check, read unpinned
    pl = _env(acc, "venues.json", "health.KALSHI_HEALTH.mechanism.plane",
              pinned=False)
    return (pl.get("present") is True and pl.get("commit") == release_sha
            and (pl.get("age_s") or 1e9) <= 120), pl


# ── MIGRATION_INTEGRITY and RELEASE: the API's control + the judge's half ──

def _opt(acc, name):
    """A judge-written file of this packet, or None when absent / not JSON
    (the caller names why: UNPROVEN, never a pass and never a crash)."""
    try:
        return _load(acc, name)
    except Unavailable:
        return None


def _fingerprint(applied: dict) -> str:
    """redteam.controls.migrations' applied fingerprint byte for byte (and
    tools/fresh_db_receipt.fingerprint): sha256 of the sorted
    {version: content_sha} items as JSON. Pinned against both by test."""
    return hashlib.sha256(json.dumps(sorted(applied.items()), sort_keys=True,
                                     default=str).encode()).hexdigest()


def _gate_run_id(acc):
    """The capital-critical run the CI row reads for the release SHA."""
    try:
        return _at(_load(acc, "gates.json"), "runs.capital_critical.id",
                   "gates.json")
    except Unavailable:
        return None


def _bound(ctl: dict, absence: str, judge_status: str, judge_reasons: list):
    """(status, blockers) of an API control bound with the judge's half.

    RED from either side is RED. The API's GREEN stays GREEN. An UNKNOWN is
    lifted to GREEN only when `absence` is its SOLE blocker and the judge's
    half is PROVEN / GREEN; with that half missing it is UNPROVEN. Any other
    blocker keeps the API's own status and blockers: the judge can supply a
    missing fact, never overrule a finding."""
    api = ctl.get("status")
    blk = sorted(str(b) for b in (ctl.get("blockers") or []))
    if judge_status == RED:
        return RED, [b for b in blk if b != absence] + list(judge_reasons)
    if api == RED:
        return RED, blk
    if api == GREEN:
        return GREEN, []
    if blk != [absence]:
        return api or UNKNOWN, blk
    if judge_status in (PROVEN, GREEN):
        return GREEN, []
    return UNPROVEN, list(judge_reasons)


def fresh_db(acc, release_sha, running: dict, running_sha=None) -> dict:
    """THE FRESH-DATABASE HALF (acc/fresh_db.json, tools/fresh_db_receipt.py
    readback): PROVEN, UNPROVEN or RED with named reasons.

    UNPROVEN: no readback, no receipt (a release whose capital-critical
    predates the receipt -- RC5 69a8a07e is one), an attestation that did
    not verify, a run that is not capital-critical, not the release SHA or
    not the run the CI row reads, a receipt whose fingerprint is not its own
    map's, or a running API on another SHA. RED: the fresh build FAILED, or
    its fingerprint / count differs from the running API's applied history
    (`running`: the API control's evidence)."""
    doc = _opt(acc, "fresh_db.json")
    if not isinstance(doc, dict):
        return {"status": UNPROVEN, "reasons": [R_FRESH_DB_READBACK_ABSENT]}
    prov = doc.get("provenance") if isinstance(doc.get("provenance"),
                                               dict) else {}
    rec = doc.get("receipt")
    out = {"run_id": prov.get("run_id"),
           "workflow_path": prov.get("workflow_path"),
           "head_sha": prov.get("head_sha"),
           "conclusion": prov.get("conclusion"),
           "attestation_verified": prov.get("attestation_verified") is True}
    if not isinstance(rec, dict):
        return dict(out, status=UNPROVEN, reasons=[
            R_FRESH_DB_RECEIPT_ABSENT + (":%s" % doc["reason"]
                                         if doc.get("reason") else "")])
    out.update({k: rec.get(k) for k in (
        "sha", "result", "migrations_applied", "migrations_in_tree",
        "fingerprint", "server_version", "build_outcome")})
    out["receipt_reasons"] = list(rec.get("reasons") or [])[:10]
    why = []
    if prov.get("attestation_verified") is not True:
        why.append(R_FRESH_DB_NOT_ATTESTED)
    if prov.get("workflow_path") != CAPITAL_CRITICAL_WORKFLOW:
        why.append(R_FRESH_DB_NOT_CAPITAL_CRITICAL)
    if release_sha is None or prov.get("head_sha") != release_sha \
            or rec.get("sha") != release_sha:
        why.append(R_FRESH_DB_NOT_THE_RELEASE)
    gate = _gate_run_id(acc)
    if gate is None or str(prov.get("run_id")) != str(gate) \
            or str(rec.get("run_id")) != str(gate):
        why.append(R_FRESH_DB_NOT_THE_GATE_RUN)
    applied = rec.get("applied")
    if rec.get("version") != FRESH_DB_RECEIPT_VERSION \
            or not isinstance(applied, dict) \
            or _fingerprint(applied) != rec.get("fingerprint") \
            or len(applied) != rec.get("migrations_applied"):
        why.append(R_FRESH_DB_MALFORMED)
    if running_sha is not None and running_sha != release_sha:
        why.append(R_RUNNING_NOT_THE_RELEASE)
    if why:
        return dict(out, status=UNPROVEN, reasons=why)
    red = [] if rec.get("result") == "PASSED" else [R_FRESH_DB_BUILD_FAILED]
    af, rf = running.get("applied_fingerprint"), running.get(
        "repo_fingerprint")
    if not af or not rf:
        return dict(out, status=RED if red else UNPROVEN,
                    reasons=red + [R_RUNNING_FINGERPRINT_UNREADABLE])
    if rec.get("fingerprint") != af or rec.get("fingerprint") != rf:
        red.append(R_FRESH_DB_FINGERPRINT_DIFFERS)
    if rec.get("migrations_applied") != running.get("applied"):
        red.append(R_FRESH_DB_COUNT_DIFFERS)
    return dict(out, status=RED if red else PROVEN, reasons=red)


def migration_integrity(acc, release_sha):
    """MIGRATION_INTEGRITY, bound: the API's control (its applied history
    == this build's files, nothing edited in place) AND capital-critical's
    fresh PostgreSQL build of the release SHA reaching the SAME fingerprint.
    The Deployment unit and the Red-team unit read this one function."""
    ctl = _env(acc, "red_team.json", "readiness.controls.MIGRATION_INTEGRITY")
    ev = ctl.get("evidence") if isinstance(ctl.get("evidence"), dict) else {}
    try:
        running_sha = _env(acc, "red_team.json", "readiness.implementation_sha")
    except Unavailable:
        running_sha = None
    fd = fresh_db(acc, release_sha, ev, running_sha)
    status, blockers = _bound(ctl, API_FRESH_DB_ABSENT, fd["status"],
                              fd["reasons"])
    return status == GREEN, {
        "status": status, "blockers": blockers[:6],
        "api_status": ctl.get("status"),
        "api_blockers": list(ctl.get("blockers") or []),
        "api_evidence": ev, "fresh_db": fd}


_VERDICT_TRUE = ("descendant_of_base", "backend_tests_green",
                 "capital_critical_green", "commit_guard_green",
                 "engine_diagnostic_green", "migration_fingerprint_match")


def release_verdict(acc, release_sha) -> dict:
    """THE JUDGE'S RELEASE RECEIPT (acc/release_verdict.json,
    tools/release_verdict.py): GREEN, RED or UNPROVEN.

    GREEN only when the file is for the release SHA, recorded GREEN by the
    API's own release gate, AND says so consistently: every gate field
    true, tested == release == deployed == the serving API == the workers.
    A REFUSED body (the API would have recorded nothing) is UNPROVEN; a RED
    verdict is RED with its blockers."""
    doc = _opt(acc, "release_verdict.json")
    if not isinstance(doc, dict):
        return {"status": UNPROVEN, "reasons": [R_RELEASE_VERDICT_ABSENT]}
    body = doc.get("body") if isinstance(doc.get("body"), dict) else {}
    gate = doc.get("release_gate") if isinstance(doc.get("release_gate"),
                                                 dict) else {}
    out = {"recorded": doc.get("status"), "sha": doc.get("sha"),
           "running_api_sha": doc.get("running_api_sha"),
           "release_gate": gate, "refused": doc.get("refused"),
           "posted": doc.get("posted")}
    if doc.get("version") != RELEASE_VERDICT_VERSION:
        return dict(out, status=UNPROVEN,
                    reasons=[R_RELEASE_VERDICT_INCONSISTENT])
    if release_sha is None or doc.get("sha") != release_sha:
        return dict(out, status=UNPROVEN,
                    reasons=[R_RELEASE_VERDICT_NOT_THE_RELEASE])
    if doc.get("status") == "REFUSED":
        return dict(out, status=UNPROVEN, reasons=[
            "%s:%s" % (R_RELEASE_VERDICT_REFUSED, r)
            for r in (doc.get("refused") or ["?"])])
    blockers = [str(b) for b in (gate.get("blockers") or [])]
    if doc.get("status") == RED:
        return dict(out, status=RED,
                    reasons=blockers or [R_RELEASE_VERDICT_INCONSISTENT])
    consistent = (
        doc.get("status") == GREEN and gate.get("green") is True
        and not blockers
        and all(body.get(k) is True for k in _VERDICT_TRUE)
        and body.get("tested_sha") == body.get("release_sha")
        == body.get("deployed_sha") == doc.get("running_api_sha")
        == body.get("workers_deployed_sha") == release_sha)
    if not consistent:
        return dict(out, status=UNPROVEN,
                    reasons=[R_RELEASE_VERDICT_INCONSISTENT])
    return dict(out, status=GREEN, reasons=[])


def release_control(acc, release_sha):
    """RELEASE, bound: the API's control (a receipt for its running SHA) AND
    the judge's attested receipt for the release SHA. A receipt's presence
    is not the release passing: only a GREEN verdict lifts the API's
    NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA, and a RED one -- here or in the
    API -- is RED."""
    ctl = _env(acc, "red_team.json", "readiness.controls.RELEASE")
    rv = release_verdict(acc, release_sha)
    status, blockers = _bound(ctl, API_RELEASE_ABSENT, rv["status"],
                              rv["reasons"])
    ev = ctl.get("evidence") if isinstance(ctl.get("evidence"), dict) else {}
    running = ev.get("running_sha")
    if status == GREEN and running is not None and running != release_sha:
        # a receipt about another build is not this release's
        status, blockers = UNPROVEN, [R_RUNNING_NOT_THE_RELEASE]
    return status == GREEN, {
        "status": status, "blockers": blockers[:6],
        "api_status": ctl.get("status"),
        "api_blockers": list(ctl.get("blockers") or []),
        "api_running_sha": running, "release_verdict": rv}


#: the red-team controls bound with the judge's half (Red-team row)
BOUND = {"MIGRATION_INTEGRITY": migration_integrity,
         "RELEASE": release_control}


# ── V2: Kalshi's own credential verdict ───────────────────────────────────

def kalshi_credentials(acc):
    """CREDENTIAL_CLASSES SCOPED TO KALSHI (Kalshi integration row).

    Production (pm-acceptance 37836393458, 69a8a07e): the control was RED
    with ONE blocker, CREDENTIAL_CLASS_MISMATCH:PMUS:POLYMARKET_EXCHANGE_
    RSA_M2M (the PMUS funded slot holds the PMX RSA key -- an owner action),
    while evidence.verdicts.KALSHI = MATCHES and api and workers both hold
    KALSHI_ED25519_API_KEY, a documented Kalshi type. The Kalshi row read
    the aggregate status and so failed on PMUS. Passes only when the
    control was computed (GREEN or RED), Kalshi's own verdict is MATCHES,
    Kalshi is provisioned, EVERY process the control classified holds an
    approved Kalshi class, no blocker names Kalshi, and no blocker is
    unattributable to a slot. The aggregate status and the other slots'
    blockers stay in the detail; the Red-team row still reads the
    aggregate and still fails on PMUS."""
    ctl = _env(acc, "red_team.json", "readiness.controls.CREDENTIAL_CLASSES")
    ctl = ctl if isinstance(ctl, dict) else {}
    ev = ctl.get("evidence") if isinstance(ctl.get("evidence"), dict) else {}
    blockers = [str(b) for b in (ctl.get("blockers") or [])]
    slots = set((ev.get("expected") or {}).keys()) if isinstance(
        ev.get("expected"), dict) else set()
    approved = list(((ev.get("approved") or {}).get("KALSHI") or [])
                    if isinstance(ev.get("approved"), dict) else [])
    verdict = (ev.get("verdicts") or {}).get("KALSHI") if isinstance(
        ev.get("verdicts"), dict) else None
    by_proc = ev.get("by_process") if isinstance(ev.get("by_process"),
                                                 dict) else {}
    per = {p: (s or {}).get("KALSHI") if isinstance(s, dict) else None
           for p, s in sorted(by_proc.items())}
    reasons, kal_blk, other_blk = [], [], []
    for b in blockers:
        m = _CRED_BLOCKER.match(b)
        if m and m.group(2) == "KALSHI":
            kal_blk.append(b)
        elif m and m.group(2) in slots:
            other_blk.append(b)
        else:
            reasons.append("%s:%s" % (R_CREDENTIAL_BLOCKER_UNATTRIBUTED, b))
    if ctl.get("status") not in (GREEN, RED):
        reasons.append("%s:%s" % (R_KALSHI_CREDENTIAL_CONTROL_NOT_COMPUTED,
                                  ctl.get("status")))
    if not ev or not per or verdict is None or not approved:
        reasons.append(R_KALSHI_CREDENTIAL_EVIDENCE_ABSENT)
    if verdict is not None and verdict != "MATCHES":
        reasons.append("%s:%s" % (R_KALSHI_CREDENTIAL_VERDICT, verdict))
    if "KALSHI" in (ev.get("not_provisioned") or []):
        reasons.append(R_KALSHI_CREDENTIAL_NOT_PROVISIONED)
    for p, cls in per.items():
        if cls is None:
            reasons.append("%s:%s" % (R_KALSHI_CREDENTIAL_NOT_PROVISIONED, p))
        elif cls not in approved:
            reasons.append("%s:%s:%s" % (R_KALSHI_CREDENTIAL_CLASS, p, cls))
    reasons += ["%s:%s" % (R_KALSHI_CREDENTIAL_BLOCKER, b) for b in kal_blk]
    return not reasons, {
        "scope": "KALSHI", "kalshi_verdict": verdict,
        "kalshi_by_process": per, "approved": approved,
        "kalshi_blockers": kal_blk, "reasons": reasons[:10],
        # the aggregate, unchanged and visible (the Red-team row's unit)
        "aggregate_status": ctl.get("status"),
        "aggregate_blockers_outside_kalshi": other_blk[:6]}


# ── V2: release lineage, upgrade path and rollback readiness ─────────────

def _is_sha(v) -> bool:
    return isinstance(v, str) and bool(_SHA40.match(v))


def _gates_green(doc, sha) -> list:
    """The gates NOT green on `sha` in a gates.json-shaped file: the newest
    run of each must be completed / success on exactly that SHA."""
    runs = (doc or {}).get("runs") if isinstance(doc, dict) else None
    runs = runs if isinstance(runs, dict) else {}
    bad = []
    for k in GATES:
        r = runs.get(k) if isinstance(runs.get(k), dict) else {}
        if not (r.get("conclusion") == "success" and
                r.get("status") == "completed" and
                _is_sha(sha) and r.get("head_sha") == sha):
            bad.append("%s=%s" % (k, r.get("conclusion")))
    return bad


def release_lineage(acc, release_sha):
    """THE RELEASE IS THE TESTED IMPLEMENTATION (acc/lineage.json, read by
    the judge from git and GitHub): the implementation SHA given, its tree
    == the release commit's tree, the release commit has exactly one parent
    (a release commit on claude/release-api, never a merge), the branch
    both Render services track is AT the release, it descends from the
    accepted base, and the four gates are green on BOTH SHAs."""
    lin = _load(acc, "lineage.json", pinned=False)
    lin = lin if isinstance(lin, dict) else {}
    reasons = []
    impl = lin.get("implementation_sha")
    if not _is_sha(impl):
        reasons.append(R_LINEAGE_IMPLEMENTATION_ABSENT)
    elif not (lin.get("trees_equal") is True
              and _is_sha(lin.get("implementation_tree"))
              and lin.get("implementation_tree") == lin.get("release_tree")):
        reasons.append(R_LINEAGE_TREES_DIFFER)
    parents = lin.get("release_parents")
    if not (isinstance(parents, list) and len(parents) == 1):
        reasons.append("%s:%s" % (R_LINEAGE_NOT_SINGLE_PARENT,
                                  len(parents) if isinstance(parents, list)
                                  else "UNREAD"))
    if not _is_sha(release_sha) or lin.get("sha") != release_sha:
        reasons.append(R_LINEAGE_NOT_THE_RELEASE)
    if lin.get("release_sha") != release_sha:
        reasons.append(R_LINEAGE_BRANCH_ELSEWHERE)
    if lin.get("descendant_of_base") is not True:
        reasons.append(R_LINEAGE_NOT_DESCENDANT)
    rel_bad = _gates_green(_opt(acc, "gates.json"), release_sha)
    reasons += ["%s:%s" % (R_LINEAGE_RELEASE_GATE, b) for b in rel_bad]
    if _is_sha(impl):
        ig = _opt(acc, "gates_implementation.json")
        if not isinstance(ig, dict):
            reasons.append(R_LINEAGE_IMPLEMENTATION_GATES_UNREAD)
        else:
            reasons += ["%s:%s" % (R_LINEAGE_IMPLEMENTATION_GATE, b)
                        for b in _gates_green(ig, impl)]
    return not reasons, {
        "implementation_sha": impl, "release_sha": lin.get("release_sha"),
        "tested_sha": lin.get("sha"),
        "implementation_tree": lin.get("implementation_tree"),
        "release_tree": lin.get("release_tree"),
        "release_parents": parents, "reasons": reasons[:10]}


def _rollback_doc(acc, release_sha):
    """acc/rollback.json (tools/rollback_readiness.py) when it is a
    well-formed record for the release SHA, else (None, reason)."""
    rb = _opt(acc, "rollback.json")
    if not isinstance(rb, dict):
        return None, "READ_UNAVAILABLE:rollback.json"
    if rb.get("version") != ROLLBACK_VERSION:
        return None, R_ROLLBACK_MALFORMED
    if not _is_sha(release_sha) or rb.get("sha") != release_sha:
        return None, R_ROLLBACK_NOT_THE_RELEASE
    return rb, None


def _migration_delta(rb) -> dict:
    """The rollback target's migration set against the release's, as the
    judge read both trees: identical only when nothing was added, removed
    or changed AND both fingerprints are present and equal."""
    m = rb.get("migrations") if isinstance(rb.get("migrations"), dict) else {}
    t = m.get("target") if isinstance(m.get("target"), dict) else {}
    r = m.get("release") if isinstance(m.get("release"), dict) else {}
    added, removed, changed = (list(m.get(k) or []) for k in (
        "added", "removed", "changed"))
    identical = (bool(t.get("fingerprint")) and t.get("fingerprint") ==
                 r.get("fingerprint") and not added and not removed and
                 not changed)
    return {"identical": identical, "target_fingerprint": t.get("fingerprint"),
            "release_fingerprint": r.get("fingerprint"),
            "target_count": t.get("count"), "release_count": r.get("count"),
            "added": added[:20], "removed": removed[:20],
            "changed": changed[:20]}


def upgrade_receipt(acc, release_sha, target_fingerprint) -> dict:
    """THE UPGRADE-PATH RECEIPT (acc/upgrade_path.json, tools/
    upgrade_path_receipt.py readback of capital-critical's attested
    receipt): PROVEN, UNPROVEN or RED. UNPROVEN: absent, unattested, not
    capital-critical / the gate run / the release SHA, self-inconsistent,
    or its base is not the rollback target's migration set (another
    schema than production ran). RED: the upgrade FAILED."""
    doc = _opt(acc, "upgrade_path.json")
    if not isinstance(doc, dict):
        return {"status": UNPROVEN, "reasons": [R_UPGRADE_READBACK_ABSENT]}
    prov = doc.get("provenance") if isinstance(doc.get("provenance"),
                                               dict) else {}
    rec = doc.get("receipt")
    if not isinstance(rec, dict):
        return {"status": UNPROVEN, "reasons": [
            R_UPGRADE_RECEIPT_ABSENT + (":%s" % doc["reason"]
                                        if doc.get("reason") else "")]}
    base = rec.get("base") if isinstance(rec.get("base"), dict) else {}
    out = {"run_id": prov.get("run_id"), "result": rec.get("result"),
           "base_sha": base.get("sha"),
           "base_fingerprint": base.get("migrations_fingerprint"),
           "new_migrations": list(rec.get("new_migrations") or [])[:20],
           "seeded": rec.get("seeded"),
           "rollback_compatibility": rec.get("rollback_compatibility"),
           "representative": rec.get("representative"),
           "receipt_reasons": list(rec.get("reasons") or [])[:10]}
    why = []
    if prov.get("attestation_verified") is not True:
        why.append(R_UPGRADE_NOT_ATTESTED)
    if prov.get("workflow_path") != CAPITAL_CRITICAL_WORKFLOW:
        why.append(R_UPGRADE_NOT_CAPITAL_CRITICAL)
    if not _is_sha(release_sha) or prov.get("head_sha") != release_sha \
            or rec.get("sha") != release_sha:
        why.append(R_UPGRADE_NOT_THE_RELEASE)
    gate = _gate_run_id(acc)
    if gate is None or str(prov.get("run_id")) != str(gate) \
            or str(rec.get("run_id")) != str(gate):
        why.append(R_UPGRADE_NOT_THE_GATE_RUN)
    if rec.get("version") != UPGRADE_PATH_RECEIPT_VERSION or \
            rec.get("result") not in ("PASSED", "FAILED") or \
            (rec.get("result") == "PASSED" and rec.get("reasons")):
        why.append(R_UPGRADE_MALFORMED)
    if why:
        return dict(out, status=UNPROVEN, reasons=why)
    if rec.get("result") != "PASSED":
        rs = [str(r) for r in rec.get("reasons") or []]
        if rs and all(r.split(":")[0] in UPGRADE_BASE_SIDE for r in rs):
            # the BASE could not be found or built: nothing was learned
            # about this release's migrations -- unproven, not a finding
            return dict(out, status=UNPROVEN, reasons=[R_UPGRADE_BASE_UNBUILT]
                        + rs[:6])
        return dict(out, status=RED, reasons=[R_UPGRADE_FAILED] + rs[:10])
    if not target_fingerprint or base.get("migrations_fingerprint") != \
            target_fingerprint:
        return dict(out, status=UNPROVEN, reasons=[R_UPGRADE_BASE_NOT_TARGET])
    if rec.get("representative") != "COMPLETE":
        # a table a new migration acts on held no row when it ran: the
        # upgrade was not tested where it matters
        return dict(out, status=UNPROVEN, reasons=[
            R_UPGRADE_NOT_REPRESENTATIVE] + ["%s:%s" % (
                R_UPGRADE_NOT_REPRESENTATIVE, t) for t in (
                    rec.get("unseeded_touched_tables") or [])[:8]])
    return dict(out, status=PROVEN, reasons=[])


def upgrade_path(acc, release_sha):
    """THE RELEASE'S MIGRATIONS UPGRADE THE PREVIOUS RELEASE'S DATABASE.

    Either the release adds, removes and changes NO migration against the
    rollback target (the release's previous running build, per Render): the
    upgrade applies nothing and nothing applied can change -- proven from
    the two trees' content hashes, recorded as IDENTICAL_MIGRATION_SET, no
    database run claimed. Or capital-critical's attested upgrade receipt
    for the release SHA is PASSED from a base with exactly the rollback
    target's migration set. A FAILED receipt is RED even when the sets are
    identical (a finding is never overruled)."""
    rb, why = _rollback_doc(acc, release_sha)
    delta = _migration_delta(rb) if rb else None
    rec = upgrade_receipt(acc, release_sha, (delta or {}).get(
        "target_fingerprint"))
    if rec["status"] == RED:
        return False, {"status": RED, "reasons": rec["reasons"],
                       "receipt": rec, "migration_delta": delta}
    if rb is None:
        if why.startswith("READ_UNAVAILABLE"):
            # the rollback target (production's previous schema) was not
            # read: nothing to upgrade FROM is known
            raise Unavailable(why)
        return False, {"status": UNPROVEN, "reasons": [why],
                       "receipt": rec}
    if delta["identical"]:
        return True, {"status": PROVEN, "evidence": IDENTICAL,
                      "reasons": [], "migration_delta": delta,
                      "receipt": rec}
    return rec["status"] == PROVEN, {
        "status": rec["status"], "evidence": "UPGRADE_PATH_RECEIPT",
        "reasons": rec["reasons"], "migration_delta": delta, "receipt": rec}


_SHA_IN_TEXT = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")


def _deploy_row(row):
    """A deploy-list row's `deploy` object, or None when the row cannot be
    read (not {"deploy": {...}}, or a status that is not a string)."""
    d = row.get("deploy") if isinstance(row, dict) else None
    return d if isinstance(d, dict) and isinstance(d.get("status"), str) \
        else None


def _deploy_commit(d):
    """A deploy's commit SHA, or None: a `commit` that is not an object (a
    string, a list, a number) or names no 40-hex id is unreadable."""
    c = d.get("commit")
    cid = c.get("id") if isinstance(c, dict) else None
    return cid if _is_sha(cid) else None


def _deploy_history(acc, svc):
    """(live commit, [every commit previously live on it, newest first],
    None) from THIS service's own Render deploy list in the packet
    (deploys_<svc>.json, newest first, as pm-acceptance stores it), or
    (None, None, why) when it cannot say what is live or what ran before.
    `deactivated` = was live, then replaced; only a status in
    RENDER_NEVER_SERVED never served -- any other status is unknown.

    Never raises on a field of the wrong shape (one bad deploy row must
    not take the whole scorecard out of the packet). Refused by name:
      * unreadable, not exactly one live deploy, a live commit it cannot
        read, or a row it cannot read newer than the live deploy or between
        it and the previous live commit (it may be what ran before) ->
        R_ROLLBACK_HISTORY_UNREADABLE;
      * a status Render does not document newer than the live deploy or
        between it and the previous live commit (it may have served) ->
        R_ROLLBACK_DEPLOY_STATUS_UNKNOWN;
      * a served (`deactivated`) deploy newer than the live deploy ->
        R_ROLLBACK_SERVED_NEWER_THAN_LIVE.
    A row it cannot read, or of an unknown status, older than the previous
    live commit is not counted as previously live."""
    raw = _opt(acc, "deploys_%s.json" % svc)
    if not isinstance(raw, list):
        return None, None, R_ROLLBACK_HISTORY_UNREADABLE
    deps = [_deploy_row(x) for x in raw]    # None = a row it cannot read
    live = [i for i, d in enumerate(deps)
            if d is not None and d["status"] == "live"]
    if len(live) != 1:
        return None, None, R_ROLLBACK_HISTORY_UNREADABLE
    lc = _deploy_commit(deps[live[0]])
    if lc is None:
        return None, None, R_ROLLBACK_HISTORY_UNREADABLE
    for d in deps[:live[0]]:                # newer than the live deploy
        if d is None:
            return None, None, R_ROLLBACK_HISTORY_UNREADABLE
        if d["status"] in RENDER_NEVER_SERVED:
            continue                    # never served: changes nothing
        return None, None, (R_ROLLBACK_SERVED_NEWER_THAN_LIVE
                            if d["status"] == "deactivated"
                            else R_ROLLBACK_DEPLOY_STATUS_UNKNOWN)
    before = []
    for d in deps[live[0] + 1:]:
        st = d["status"] if d is not None else None
        if st in RENDER_NEVER_SERVED:
            continue                    # never served: its commit is moot
        c = _deploy_commit(d) if st == "deactivated" else None
        if c is None:
            if not before:
                # what ran before cannot be read, or may have been a
                # deploy of a status Render does not document
                return None, None, (
                    R_ROLLBACK_DEPLOY_STATUS_UNKNOWN
                    if st not in (None, "deactivated")
                    else R_ROLLBACK_HISTORY_UNREADABLE)
            continue                    # older: not counted as live
        if c != lc and c not in before:
            before.append(c)
    return lc, before, None


def _service_rollback(acc, svc, rec, cmd, release_sha):
    """ONE service's rollback, re-derived by the judge from that service's
    own deploy list: (plan, the command passed on or None, reasons).

      * not live on the release -> NONE: it stays on its current commit,
        named ROLLBACK_SERVICE_NOT_ON_THE_RELEASE, and no command is passed
        on (a record that wrote one is named);
      * on the release -> DEPLOY_PREVIOUS to ITS OWN previous live commit,
        and only that service's own documented command naming exactly that
        commit is passed on;
      * its history unusable (_deploy_history's named reason: unreadable,
        an unknown deploy status, a served deploy newer than the live one),
        or the record's live / previous commit not what its own history
        says -> REFUSED by name, no command."""
    live = rec.get("live_commit")
    named = set(_SHA_IN_TEXT.findall(str(cmd or "")))
    hist_live, before, bad = _deploy_history(acc, svc)
    why = []
    if live != release_sha:
        why.append("%s:%s" % (R_ROLLBACK_SERVICE_NOT_ON_RELEASE, svc))
        if cmd:
            why.append("%s:%s" % (R_ROLLBACK_COMMAND_OFF_RELEASE, svc))
    if bad is not None:
        return ({"action": "REFUSED", "reason": bad},
                None, why + ["%s:%s" % (bad, svc)])
    if live != release_sha:
        if hist_live != live:
            why.append("%s:%s:%s" % (R_ROLLBACK_NOT_OWN_PREVIOUS, svc, live))
        # it stays on what its OWN deploy list says is live
        return ({"action": "NONE", "stay_on": hist_live,
                 "reason": R_ROLLBACK_SERVICE_NOT_ON_RELEASE}, None, why)
    ren = _opt(acc, "render.json")
    ren = ren.get(svc) if isinstance(ren, dict) else None
    if not isinstance(ren, dict) or ren.get("live_commit") != hist_live:
        # its deploy list and Render's service summary disagree on what is
        # live (or the summary is unread): neither says what ran before
        return ({"action": "REFUSED", "reason": R_ROLLBACK_LIVE_NOT_RENDERS},
                None, ["%s:%s" % (R_ROLLBACK_LIVE_NOT_RENDERS, svc)])
    own = before[0] if before else None
    if hist_live != live or own is None or rec.get("previous_commit") != own:
        # the record's facts are not this service's own history: what it
        # was live on before cannot be taken from the record
        return ({"action": "REFUSED", "reason": R_ROLLBACK_NOT_OWN_PREVIOUS},
                None, ["%s:%s:%s" % (R_ROLLBACK_NOT_OWN_PREVIOUS, svc,
                                     rec.get("previous_commit"))])
    p = {"action": "DEPLOY_PREVIOUS", "from": live, "to": own}
    if named - {own}:
        return p, None, ["%s:%s:%s" % (R_ROLLBACK_NOT_OWN_PREVIOUS, svc,
                                       ",".join(sorted(named - {own})))]
    if not cmd:
        return p, None, ["%s:%s" % (R_ROLLBACK_COMMANDS_INCOMPLETE, svc)]
    if cmd != ROLLBACK_COMMAND_FORMS[svc] % own:
        return p, None, ["%s:%s" % (R_ROLLBACK_NOT_ITS_SERVICE, svc)]
    return p, cmd, []


def rollback_ready(acc, release_sha):
    """THE PREVIOUS RELEASE CAN BE PUT BACK (acc/rollback.json, tools/
    rollback_readiness.py; facts re-judged here, its own status is not
    trusted): every service is on the release and names ONE previous
    commit in its Render deploy history (the rollback target), the target
    is an ancestor of the release on the release line, its four gates are
    green on its own SHA, the deploy command for every service is written
    down, and the target runs on the release's schema -- identical
    migration sets, or the attested upgrade receipt's compatibility check
    (no table / column the target uses dropped, retyped or tightened) from
    a base that IS the target. Nothing here deploys anything.

    EACH SERVICE FROM ITS OWN HISTORY (rc6.3 rollback-fix): the commands
    passed on are the judge's own per-service derivation
    (_service_rollback), never the record's list as written -- a service
    off the release stays where it is (NONE, no command, NOT_READY kept),
    and no command is passed on for a commit that service was not live on
    before."""
    rb, why = _rollback_doc(acc, release_sha)
    if rb is None:
        if why.startswith("READ_UNAVAILABLE"):
            raise Unavailable(why)
        return False, {"status": UNPROVEN, "reasons": [why]}
    reasons = []
    target = rb.get("target_sha")
    svcs = rb.get("services") if isinstance(rb.get("services"), dict) else {}
    cmds = rb.get("commands") if isinstance(rb.get("commands"), dict) else {}
    plans, passed = {}, {}
    for svc in SERVICES:
        s = svcs.get(svc) if isinstance(svcs.get(svc), dict) else {}
        on = s.get("live_commit") == release_sha
        if on and (not _is_sha(target) or s.get("previous_commit") != target):
            reasons.append("%s:%s:%s" % (R_ROLLBACK_TARGET_UNKNOWN, svc,
                                         s.get("previous_commit")))
        plans[svc], passed[svc], why = _service_rollback(
            acc, svc, s, cmds.get(svc), release_sha)
        reasons += why
    if not any(p["action"] != "NONE" for p in plans.values()):
        # nobody is on the release: there is no rollback target at all
        reasons.append("%s:NO_SERVICE_ON_THE_RELEASE" %
                       R_ROLLBACK_TARGET_UNKNOWN)
    if _is_sha(target):
        if rb.get("target_is_ancestor_of_release") is not True:
            reasons.append(R_ROLLBACK_TARGET_NOT_ON_RELEASE_LINE)
        reasons += ["%s:%s" % (R_ROLLBACK_TARGET_GATE, b)
                    for b in _gates_green(rb.get("target_gates"), target)]
    if any(p["action"] == "DEPLOY_PREVIOUS" and passed[s] is None
           for s, p in plans.items()):
        reasons.append(R_ROLLBACK_COMMANDS_INCOMPLETE)
    delta = _migration_delta(rb)
    rec = upgrade_receipt(acc, release_sha, delta["target_fingerprint"])
    compat = rec.get("rollback_compatibility") if isinstance(
        rec.get("rollback_compatibility"), dict) else {}
    if delta["identical"] and rec["status"] != RED:
        schema = "COMPATIBLE_IDENTICAL_SCHEMA"
    elif rec["status"] == PROVEN and compat.get("verdict") == COMPATIBLE:
        schema = "COMPATIBLE_BY_UPGRADE_RECEIPT"
    elif rec["status"] == PROVEN:
        schema = "BLOCKED"
        reasons.append(R_ROLLBACK_SCHEMA_BLOCKED)
        reasons += ["%s:%s" % (R_ROLLBACK_SCHEMA_BLOCKED, b)
                    for b in (compat.get("blocking") or [])[:8]]
    else:
        schema = "UNPROVEN"
        reasons.append(R_ROLLBACK_SCHEMA_UNPROVEN)
        reasons += ["%s:%s" % (R_ROLLBACK_SCHEMA_UNPROVEN, r)
                    for r in rec["reasons"][:4]]
    return not reasons, {
        "status": "READY" if not reasons else "NOT_READY",
        "target_sha": target, "schema": schema,
        # what each service would do, from its OWN history (NONE = stays
        # on `stay_on`); the commands are only those re-derived here
        "rollback_by_service": plans,
        "commands": passed, "procedure": rb.get("procedure"),
        "migration_delta": delta, "reasons": reasons[:16]}


# ── V2: the inputs' identities, and the pinning record ───────────────────

def _dig(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _raw(acc, name):
    try:
        return _load(acc, name, pinned=False)
    except Unavailable:
        return None


def _sha_or_none(v):
    v = v.strip().lower() if isinstance(v, str) else None
    return v if v and _SHA40.match(v) else None


def identities(acc, release_sha, frontend_preview=None) -> dict:
    """Which release each input names, at declared paths.

    API-served readbacks name their own serving build where they carry one
    (OWN_IDENTITY), otherwise the serving API's (red_team.json
    implementation_sha / release.json api.sha; two that disagree mean the
    API changed during the run: every API-served input without its own
    identity is then refused). The device views and the Trader acceptance
    are the production frontend's only when the previewed SHA is production
    build.json's; the Trader acceptance's API is the release's only when
    every device's api.source_sha is it."""
    ref = _sha_or_none(release_sha)
    api_vals = {}
    for name, path in API_IDENTITY:
        d = _raw(acc, name)
        if name == "red_team.json" and not (isinstance(d, dict) and
                                            d.get("status") == "OK"):
            continue
        v = _sha_or_none(_dig(d, path))
        if v:
            api_vals["%s:%s" % (name, path)] = v
    distinct = sorted(set(api_vals.values()))
    api = {"sources": api_vals, "conflict": len(distinct) > 1,
           "sha": distinct[0] if len(distinct) == 1 else None}

    def verdict(named):
        if ref is None:
            return {"verdict": NO_REFERENCE, "named": named, "reason": None}
        if named is None:
            return {"verdict": UNATTRIBUTED, "named": None, "reason": None}
        if named == ref:
            return {"verdict": MATCHES, "named": named, "reason": None}
        return {"verdict": FOREIGN, "named": named,
                "reason": "names=%s:release=%s" % (named[:12], ref[:12])}

    files = {}
    for name in sorted(set(API_SERVED) | set(OWN_IDENTITY)):
        if not os.path.isfile(os.path.join(acc, name)):
            continue
        own_path = OWN_IDENTITY.get(name)
        own = _sha_or_none(_dig(_raw(acc, name), own_path)) if own_path \
            else None
        if own:
            files[name] = dict(verdict(own), source="own:%s" % own_path)
        elif name in API_SERVED and api["conflict"]:
            files[name] = {"verdict": FOREIGN if ref else NO_REFERENCE,
                           "named": distinct, "source": "api",
                           "reason": "%s:%s" % (R_API_IDENTITY_CONFLICT,
                                                ",".join(d[:12] for d in
                                                         distinct))}
        elif name in API_SERVED:
            files[name] = dict(verdict(api["sha"]), source="api")
        else:
            files[name] = dict(verdict(None), source="own:%s" % own_path)

    # the frontend: production's build.json against the previewed SHA
    fb = _raw(acc, FRONTEND_BUILD)
    prod = _sha_or_none(_dig(fb, "sha")) if isinstance(fb, dict) and \
        fb.get("schema") == "bt.frontend.build.v1" else None
    prev_id, trader = None, None
    if frontend_preview:
        d = os.path.dirname(frontend_preview)
        pid = os.path.join(d, FRONTEND_PREVIEW_IDENTITY)
        if os.path.isfile(pid):
            try:
                prev_id = _sha_or_none(_dig(_parse_file(
                    acc, _key(acc, pid), pid), "target_sha"))
            except Unavailable:
                prev_id = None
        tp = os.path.join(d, "trader_accept.json")
        if os.path.isfile(tp):
            try:
                t = _parse_file(acc, _key(acc, tp), tp)
                trader = sorted({_sha_or_none(((r or {}).get("api") or {})
                                              .get("source_sha")) or "UNNAMED"
                                 for r in (t.get("results") or [])
                                 if isinstance(r, dict)}) \
                    if isinstance(t, dict) else None
            except Unavailable:
                trader = None
    if prod and prev_id:
        fe = {"verdict": MATCHES if prod == prev_id else FOREIGN,
              "named": prev_id, "reason": None if prod == prev_id else
              "%s:previewed=%s:production=%s" % (
                  R_FRONTEND_NOT_DEPLOYED, prev_id[:12], prod[:12])}
    else:
        fe = {"verdict": UNATTRIBUTED, "named": prev_id, "reason": None}
    fe.update(production_sha=prod, preview_sha=prev_id)
    if not trader:
        tr = {"verdict": UNATTRIBUTED, "named": None, "reason": None}
    elif ref is None:
        tr = {"verdict": NO_REFERENCE, "named": trader, "reason": None}
    elif trader == [ref]:
        tr = {"verdict": MATCHES, "named": trader, "reason": None}
    elif any(x not in (ref, "UNNAMED") for x in trader):
        tr = {"verdict": FOREIGN, "named": trader,
              "reason": "trader_api_source_sha=%s:release=%s" % (
                  ",".join(x[:12] for x in trader), ref[:12])}
    else:
        tr = {"verdict": UNATTRIBUTED, "named": trader, "reason": None}
    return {"reference": ref, "api": api, "files": files, "frontend": fe,
            "trader_api": tr}


def _self_sha256() -> str:
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return "UNREADABLE"


def pinning(acc, *, release_sha, evaluator_sha, implementation_sha) -> dict:
    """WHAT JUDGED WHAT: the evaluator (judge commit, this file's sha256,
    VERSION), the implementation / release / production-frontend SHAs, the
    sha256 of every input file this scoring parsed (the bytes it graded)
    and each input's identity verdict."""
    ident = getattr(acc, "identity", None) or {}
    lin = _raw(acc, "lineage.json")
    lin_impl = _sha_or_none(_dig(lin, "implementation_sha"))
    given = _sha_or_none(implementation_sha)
    files = ident.get("files") or {}
    refused = sorted(n for n, v in files.items() if v["verdict"] == FOREIGN)
    unattributed = sorted(n for n, v in files.items()
                          if v["verdict"] == UNATTRIBUTED)
    for k, label in (("frontend", "frontend_preview"),
                     ("trader_api", "trader_accept")):
        v = (ident.get(k) or {}).get("verdict")
        if v == FOREIGN:
            refused.append(label)
        elif v == UNATTRIBUTED:
            unattributed.append(label)
    fe = ident.get("frontend") or {}
    verdict = (NO_REFERENCE if ident.get("reference") is None else
               "INPUTS_REFUSED" if refused else
               "UNATTRIBUTED_INPUTS" if unattributed else "PINNED")
    return {
        "evaluator": {"sha": _sha_or_none(evaluator_sha),
                      "version": VERSION,
                      "source": "backend/tools/scorecard_14.py",
                      "source_sha256": _self_sha256()},
        "release_sha": ident.get("reference"),
        "implementation_sha": given or lin_impl,
        "implementation_sha_sources": {"argument": given,
                                       "lineage.json": lin_impl},
        "frontend_sha": fe.get("production_sha"),
        "frontend_preview_sha": fe.get("preview_sha"),
        "api_serving_sha": (ident.get("api") or {}).get("sha"),
        "inputs_sha256": dict(sorted(getattr(acc, "read", {}).items())),
        "inputs_unreadable": dict(sorted(getattr(acc, "absent", {}).items())),
        "identity": ident, "refused_inputs": refused,
        "unattributed_inputs": unattributed, "verdict": verdict}


# ── V2: every grading difference from the pinned prior evaluator ─────────

#: the RC5 judge (pm-acceptance 37836393458 ran with it)
PRIOR_EVALUATOR_SHA = "2fadc8dc7e17f403bcdfd449581d070eff2981c4"
#: each unit whose grading differs from PRIOR_EVALUATOR_SHA's, and why.
#: tools/grading_diff.py attaches these to the verdict differences it finds
#: on the same packet; a difference matching none is UNDECLARED, and every
#: difference is listed either way.
GRADING_CHANGES = (
    {"id": "RC6_BOUND_MIGRATION_INTEGRITY", "since": "7ea9a61a",
     "units": [["Red-team safeguards", "MIGRATION_INTEGRITY"],
               ["Deployment infrastructure", "migration_integrity"]],
     "reason": "the API's MIGRATION_INTEGRITY read UNKNOWN for the sole "
               "absence FRESH_DB_RESULT_NOT_IN_THIS_PROCESS (applied 224 = "
               "repo 224, fingerprints equal, 37836393458); bound with "
               "capital-critical's attested fresh-database receipt of the "
               "release SHA: lifted only when PROVEN, RED from either side "
               "is RED, absent is UNPROVEN"},
    {"id": "RC6_BOUND_RELEASE", "since": "7ea9a61a",
     "units": [["Red-team safeguards", "RELEASE"]],
     "reason": "the API's RELEASE read UNKNOWN for the sole absence "
               "NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA (post_receipt off); "
               "bound with the judge's release verdict for the release SHA "
               "(the API's own release gate): only GREEN lifts it"},
    {"id": "RC6E_KALSHI_CREDENTIAL_SCOPE", "since": VERSION,
     "units": [["Kalshi integration", "credential_class_control"]],
     "reason": "read the AGGREGATE CREDENTIAL_CLASSES status, RED only from "
               "CREDENTIAL_CLASS_MISMATCH:PMUS:POLYMARKET_EXCHANGE_RSA_M2M "
               "while evidence.verdicts.KALSHI = MATCHES (37836393458); now "
               "Kalshi's own verdict, provisioning in every process and "
               "Kalshi blockers. The Red-team CREDENTIAL_CLASSES unit stays "
               "the aggregate"},
    {"id": "RC6E_RELEASE_LINEAGE", "since": VERSION,
     "units": [["Deployment infrastructure", "release_lineage"]],
     "reason": "ADDED (owner directive 2E): implementation tree == release "
               "tree, single-parent release commit, the release branch at "
               "the release, gates green on both SHAs"},
    {"id": "RC6E_UPGRADE_PATH", "since": VERSION,
     "units": [["Deployment infrastructure", "upgrade_path"]],
     "reason": "ADDED (owner directive 2E): the release's migrations onto "
               "the previous release's schema with representative rows, "
               "no applied migration changed"},
    {"id": "RC6E_ROLLBACK_READY", "since": VERSION,
     "units": [["Deployment infrastructure", "rollback_ready"]],
     "reason": "ADDED (owner directive 2E): previous release per service, "
               "its gates, its deploy commands, and its compatibility with "
               "the new schema; rc6.3: each service's rollback and command "
               "from its OWN deploy history, a service off the release "
               "stays (NONE, no command)"},
    {"id": "RC6E_INPUT_PINNING", "since": VERSION, "units": [["*", "*"]],
     "when_current_detail_contains": R_INPUT_NAMES_ANOTHER_RELEASE,
     "reason": "an input that names another release than the one graded "
               "is UNMEASURED (never graded as this release's evidence)"},
)


def _key(acc, path) -> str:
    """An input's name in the pinning record: its path inside the packet,
    or as given when it lies elsewhere."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(acc))
    return path if rel.startswith("..") else rel


def _frontend_doc(acc, path, kind):
    """The device-view file ('preview') or the Trader acceptance
    ('trader'), read once and hashed; UNMEASURED when it names another
    frontend than production's build.json, or (Trader) another API
    release than the one graded."""
    doc = _parse_file(acc, _key(acc, path), path)
    ident = getattr(acc, "identity", None) or {}
    fe = ident.get("frontend") or {}
    if fe.get("verdict") == FOREIGN:
        raise Foreign("UNMEASURED:%s:%s:%s" % (
            R_INPUT_NAMES_ANOTHER_RELEASE, _key(acc, path), fe.get("reason")))
    tr = ident.get("trader_api") or {}
    if kind == "trader" and tr.get("verdict") == FOREIGN:
        raise Foreign("UNMEASURED:%s:%s:%s" % (
            R_INPUT_NAMES_ANOTHER_RELEASE, _key(acc, path), tr.get("reason")))
    return doc if isinstance(doc, dict) else {}


def _frontend_gate(path, acc=None):
    if not path or not os.path.isfile(path):
        raise Unavailable("READ_UNAVAILABLE:frontend_preview:NOT_SUPPLIED")
    recs = _frontend_doc(acc, path, "preview").get("records") or []
    ok = bool(recs) and all(_view_ok(r) for r in recs)
    return ok, {"views": len(recs), "views_ok": sum(1 for r in recs if _view_ok(r))}


VIEW_CHECKS = ("http_200", "signed_in", "main_thread_responsive", "no_horizontal_overflow",
               "no_fixed_bar_overlap", "touch_targets_ge_44", "paper_or_shadow_label",
               "no_console_errors", "no_example_wording")


def _view_checks(r):
    touch = r.get("device") not in ("desktop",)
    return {"http_200": r.get("status") == 200,
            "signed_in": r.get("signed_in") is True,
            "main_thread_responsive": r.get("main_thread_responsive") is True,
            "no_horizontal_overflow": (r.get("overflow_px") or 0) <= 0,
            "no_fixed_bar_overlap": not (r.get("fixed_overlaps") or []),
            "touch_targets_ge_44": (not touch) or (r.get("touch_targets_under_44") or 0) == 0,
            "paper_or_shadow_label": ((r.get("paper_mentions") or 0) + (r.get("shadow_mentions") or 0)) > 0,
            "no_console_errors": (r.get("console_errors") or 0) == 0,
            "no_example_wording": not (r.get("suspect_words") or [])}


def _view_ok(r):
    return all(_view_checks(r).values())


def _device_units(card, path, acc=None):
    if not path or not os.path.isfile(path):
        card.units.append({"unit": "device_views", "passed": False, "members": None,
                           "detail": "READ_UNAVAILABLE:frontend_preview:NOT_SUPPLIED",
                           "class": "READ_UNAVAILABLE"})
        return
    try:
        recs = _frontend_doc(acc, path, "preview").get("records") or []
    except Unavailable as exc:
        card.units.append({"unit": "device_views", "passed": False, "members": None,
                           "detail": str(exc), "class": _failed_read(exc)})
        recs = None
    for r in recs or []:
        for k, v in _view_checks(r).items():
            card.units.append({"unit": "%s:%s" % (r.get("view"), k), "passed": bool(v),
                               "detail": None, "class": "PASS" if v else "FAIL"})
    _trader_units(card, os.path.join(os.path.dirname(path), "trader_accept.json"), acc)


#: every device the Trader acceptance must cover (frontend-preview trader_accept.js)
TRADER_DEVICES = ("desktop", "iphone", "iphone_landscape", "ipad_portrait", "ipad_landscape")


def _trader_units(card, path, acc=None):
    """TRADER DEVICE ACCEPTANCE (PM 2026-10-08): the rendered Trader page
    against the production API's own snapshot on each device -- every open
    position, standing orders, bid / ask, Xavier's recorded action, game
    state, logos, no motion while the data is frozen. It is its OWN
    component (devices passing / devices required), so the view checks can
    never outvote a failed device: one failing device of five is 0.8. A
    device passes only when it ran against a real snapshot and named no
    failure; the failure classes are the detail. A missing file or device
    is READ_UNAVAILABLE, never a pass."""
    unit = "trader_device_acceptance"
    if not os.path.isfile(path):
        card.units.append({"unit": unit, "passed": False, "members": None,
                           "detail": "READ_UNAVAILABLE:trader_accept.json:NOT_SUPPLIED",
                           "class": "READ_UNAVAILABLE"})
        return
    try:
        doc = _frontend_doc(acc, path, "trader")
    except Unavailable as exc:
        card.units.append({"unit": unit, "passed": False, "members": None,
                           "detail": str(exc), "class": _failed_read(exc)})
        return
    got = {r.get("device"): r for r in (doc.get("results") or [])}
    absent = [d for d in TRADER_DEVICES if d not in got]
    if absent:
        card.units.append({"unit": unit, "passed": False, "members": None,
                           "detail": "READ_UNAVAILABLE:trader_accept.json:" + ",".join(absent),
                           "class": "READ_UNAVAILABLE"})
        return
    per = {}
    for dev in TRADER_DEVICES:
        r = got[dev]
        ok = (r.get("verdict") == "PASS" and not r.get("failures")
              and (r.get("api") or {}).get("returned") is not None)
        per[dev] = "PASS" if ok else (r.get("failures") or r.get("verdict"))
    n = sum(1 for v in per.values() if v == "PASS")
    card.units.append({"unit": unit, "passed": n == len(TRADER_DEVICES),
                       "members": (n, len(TRADER_DEVICES)), "detail": per,
                       "class": "PASS" if n == len(TRADER_DEVICES) else "FAIL"})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("acc")
    ap.add_argument("--release-sha")
    ap.add_argument("--frontend-preview")
    ap.add_argument("--evaluator-sha")
    ap.add_argument("--implementation-sha")
    a = ap.parse_args(argv)
    out = score(a.acc, release_sha=a.release_sha, frontend_preview=a.frontend_preview,
                evaluator_sha=a.evaluator_sha or None,
                implementation_sha=a.implementation_sha or None)
    with open(os.path.join(a.acc, "scorecard_14.json"), "w") as f:
        json.dump(out, f, indent=1, default=str)
    for i, cat in enumerate(out["categories"], 1):
        r = cat["readiness"]
        comps = "; ".join("%s %s/%s" % (c["component"], c["numerator"], c["denominator"])
                          for c in cat["components"])
        print("%2d %-38s %s %6s  [%s]%s" % (
            i, cat["category"], "PASS" if cat["passes"] else "FAIL",
            "-" if r is None else "%.1f%%" % (100 * r), comps,
            ("  unreadable: " + ",".join(cat["unreadable_units"])) if cat["unreadable_units"] else ""))
    print("categories passing: %d/14 (not averaged)" % out["passing"])
    pin = out["pinning"]
    print("pinned: evaluator %s (%s, source %s) release %s implementation %s "
          "frontend %s; %d inputs hashed; refused %s; unattributed %s" % (
              pin["evaluator"]["sha"], VERSION, pin["evaluator"]["source_sha256"][:12],
              pin["release_sha"], pin["implementation_sha"], pin["frontend_sha"],
              len(pin["inputs_sha256"]), ",".join(pin["refused_inputs"]) or "none",
              ",".join(pin["unattributed_inputs"]) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
