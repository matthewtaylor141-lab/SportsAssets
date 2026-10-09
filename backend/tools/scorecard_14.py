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

Usage:  python backend/tools/scorecard_14.py ACC_DIR [--frontend-preview preview.json]
        writes ACC_DIR/scorecard_14.json and prints the table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

VERSION = "SCORECARD_14_V1"
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


class Unavailable(Exception):
    pass


def _load(acc: str, name: str):
    p = os.path.join(acc, name)
    if not os.path.isfile(p):
        raise Unavailable("READ_UNAVAILABLE:%s:MISSING" % name)
    try:
        with open(p) as f:
            return json.load(f)
    except ValueError:
        raise Unavailable("READ_UNAVAILABLE:%s:NOT_JSON" % name)


def _at(doc, path: str, name: str):
    """The value at a declared dotted path; absent is Unavailable."""
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise Unavailable("READ_UNAVAILABLE:%s:%s" % (name, path))
    return cur


def _env(acc, name, path, *, envelope=True):
    """Read through the Command readback envelope (status OK required)."""
    doc = _load(acc, name)
    if envelope and isinstance(doc, dict) and "status" in doc and "data" in doc:
        if doc.get("status") != "OK":
            raise Unavailable("READ_UNAVAILABLE:%s:ENVELOPE_%s" % (
                name, doc.get("status")))
        doc = doc["data"]
    return _at(doc, path, name)


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
                               "detail": str(exc), "class": "READ_UNAVAILABLE"})

    def counted(self, uid, num, den, *, detail=None):
        """A counted member set: each member is a unit. den 0 = UNMEASURED."""
        if den is None or num is None:
            self.units.append({"unit": uid, "passed": False, "members": None,
                               "detail": detail or "READ_UNAVAILABLE",
                               "class": "READ_UNAVAILABLE"})
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
          frontend_preview: str | None = None) -> dict:
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
    c.unit("frontend_device_gate", lambda: _frontend_gate(frontend_preview))
    cards.append(c)

    # 3 RED-TEAM SAFEGUARDS: every red-team control GREEN (forward REDs annotated)
    c = Card(CATEGORIES[2])
    try:
        controls = _env(acc, "red_team.json", "readiness.controls")
    except Unavailable as exc:
        controls = None
        c.units.append({"unit": "controls", "passed": False, "detail": str(exc),
                        "class": "READ_UNAVAILABLE"})
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
    for svc in SERVICES:
        c.unit("%s_on_release" % svc, lambda svc=svc: (
            release_sha is not None and
            _at(_load(acc, "render.json"), "%s.live_commit" % svc, "render.json") == release_sha,
            _at(_load(acc, "render.json"), "%s.live_commit" % svc, "render.json")))
    c.unit("workers_boot_on_release", lambda: (
        _at(_load(acc, "canary.json"), "boots.workers_boot.commit_sha", "canary.json") == release_sha,
        _at(_load(acc, "canary.json"), "boots.workers_boot.commit_sha", "canary.json")))
    c.unit("market_plane_heartbeat_on_release", lambda: _plane_beat(acc, release_sha))
    # applied == build AND an empty database builds to the same fingerprint
    # (the capital-critical fresh build of this SHA; RC6)
    c.unit("migration_integrity", lambda: migration_integrity(acc, release_sha))
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
        c.counted("held_positions_fresh", None, None, detail=str(exc))
    try:
        pf = _env(acc, "completion.json", "market_data.priority_freshness")
        c.counted("priority_members_fresh", pf.get("numerator"), pf.get("denominator"),
                  detail="priority freshness (dedicated plane snapshot)")
    except Unavailable as exc:
        c.counted("priority_members_fresh", None, None, detail=str(exc))
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
        c.counted("active_contracts_priceable", None, None, detail=str(exc))
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
        c.counted("held_positions_with_complete_current_packet", None, None, detail=str(exc))
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
        c.counted("scanned_markets_with_fresh_books", None, None, detail=str(exc))
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
        c.counted("tracked_books_fresh", None, None, detail=str(exc))
    c.unit("catalogue_complete", lambda: (kh("catalogue.complete") is True, kh("catalogue.stopped")))
    c.unit("health_state_ok", lambda: (kh("state") == "OK", kh("state")))
    c.unit("credential_class_control", lambda: (
        _env(acc, "red_team.json", "readiness.controls.CREDENTIAL_CLASSES.status") == "GREEN",
        _env(acc, "red_team.json", "readiness.controls.CREDENTIAL_CLASSES.blockers")))
    cards.append(c)

    # 14 COMMAND CENTER DESKTOP AND MOBILE: device-view checks
    c = Card(CATEGORIES[13])
    _device_units(c, frontend_preview)
    cards.append(c)

    out = [x.result() for x in cards]
    return {"version": VERSION, "target": TARGET, "release_sha": release_sha,
            "categories": out,
            "all_categories_pass": all(x["passes"] for x in out),
            "passing": sum(1 for x in out if x["passes"]),
            "not_averaged": True,
            "profitability": "classified separately (PROVEN / UNPROVEN); not a category here"}


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
    pl = _env(acc, "venues.json", "health.KALSHI_HEALTH.mechanism.plane")
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


def _frontend_gate(path):
    if not path or not os.path.isfile(path):
        raise Unavailable("READ_UNAVAILABLE:frontend_preview:NOT_SUPPLIED")
    recs = json.load(open(path)).get("records") or []
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


def _device_units(card, path):
    if not path or not os.path.isfile(path):
        card.units.append({"unit": "device_views", "passed": False, "members": None,
                           "detail": "READ_UNAVAILABLE:frontend_preview:NOT_SUPPLIED",
                           "class": "READ_UNAVAILABLE"})
        return
    recs = json.load(open(path)).get("records") or []
    for r in recs:
        for k, v in _view_checks(r).items():
            card.units.append({"unit": "%s:%s" % (r.get("view"), k), "passed": bool(v),
                               "detail": None, "class": "PASS" if v else "FAIL"})
    _trader_units(card, os.path.join(os.path.dirname(path), "trader_accept.json"))


#: every device the Trader acceptance must cover (frontend-preview trader_accept.js)
TRADER_DEVICES = ("desktop", "iphone", "iphone_landscape", "ipad_portrait", "ipad_landscape")


def _trader_units(card, path):
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
    got = {r.get("device"): r for r in (json.load(open(path)).get("results") or [])}
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
    a = ap.parse_args(argv)
    out = score(a.acc, release_sha=a.release_sha, frontend_preview=a.frontend_preview)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
