"""THE FINAL CAPITAL-READINESS INTERLOCK (red_team.readiness) OVER BETTOR'S
OWN EVIDENCE.

PAPER_SHADOW_ONLY unless EVERY check is green: release lineage / exact SHA,
runtime, market data, held freshness, priority freshness, settlement,
certified Twin, truth-source reconciliation, positive independent edge,
positive capacity, canonical exposure, mechanism profit breakers, live
authority still shadow, historical PAPER immutable. Only then
CAPITAL_CANDIDATE -- which activates nothing.

Everything is read inside the caller's READ ONLY transaction (the
completion readback, the controls); the runner writes the receipts.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal

from ..red_team import readiness as RTR
from ..red_team import ui_truth as UT
from ..red_team.models import ReadinessInput
from . import controls as C
from . import venue_health as VH

VERSION = "RED_TEAM_READINESS_V1"
ACCOUNT_ID = "paper_acct_main"
CAPACITY_WINDOW_DAYS = 14
QTY_GRID = ((1, 10), (11, 50), (51, 100), (101, 500), (501, 10 ** 9))
METRIC_MAX_AGE_S = 900.0


def implementation_sha() -> str:
    return (os.environ.get("RENDER_GIT_COMMIT") or "").strip().lower() \
        or "UNKNOWN"


# ── readers ──────────────────────────────────────────────────────────────

async def capacity_points(conn) -> list:
    """The positive-capacity frontier by size bucket from the bind's own
    per-entry evaluations (one value per fixture first, then the bucket's
    event-clustered lower bound)."""
    if not await C._has(conn, "paper_profitability_evaluations"):
        return []
    rows = await conn.fetch(
        "SELECT fixture, qty_in, ev_per_contract_usd ev, fill_probability fp,"
        "       all_in_ev_usd net, market_price px, expected_hold_hours h "
        "  FROM paper_profitability_evaluations WHERE evaluated_at > now() "
        "   - make_interval(days => $1) AND qty_in IS NOT NULL "
        "   AND ev_per_contract_usd IS NOT NULL", CAPACITY_WINDOW_DAYS)
    out = []
    for lo, hi in QTY_GRID:
        rs = [r for r in rows if lo <= float(r["qty_in"] or 0) <= hi]
        if not rs:
            continue
        by_fx: dict = {}
        for r in rs:
            by_fx.setdefault(r["fixture"] or "?", []).append(float(r["ev"]))
        per = [sum(v) / len(v) for v in by_fx.values()]
        n = len(per)
        mean = sum(per) / n
        sd = (sum((x - mean) ** 2 for x in per) / (n - 1)) ** 0.5 \
            if n > 1 else None
        lb = (mean - 1.645 * sd / n ** 0.5) if sd is not None else -1.0
        fps = [float(r["fp"]) for r in rs if r["fp"] is not None]
        cap_h = sum(float(r["qty_in"]) * float(r["px"] or 0) * float(
            r["h"] or 0) for r in rs)
        out.append({"qty": hi if hi < 10 ** 9 else lo,
                    "bucket": [lo, hi], "events": n,
                    "lb_ev_per_contract": round(lb, 6),
                    "fill_probability": round(sum(fps) / len(fps), 4)
                    if fps else 0,
                    "capital_hours": round(cap_h, 2),
                    "expected_net": round(sum(float(r["net"] or 0)
                                              for r in rs), 4),
                    "capital_usd": round(sum(float(r["qty_in"]) * float(
                        r["px"] or 0) for r in rs), 2)})
    return out


async def karen_counterfactuals(conn) -> tuple:
    if not await C._has(conn, "twin_agent_scorecards"):
        return None, None, "twin_agent_scorecards absent"
    rows = {r["metric"]: r["value"] for r in await conn.fetch(
        "SELECT DISTINCT ON (metric) metric, value FROM "
        "twin_agent_scorecards WHERE agent = 'KAREN' AND metric = ANY($1) "
        " AND book = 'PAPER' ORDER BY metric, computed_at DESC",
        ["loss_avoided_paper_basis", "profit_sacrificed_paper_basis"])}
    return (rows.get("loss_avoided_paper_basis"),
            rows.get("profit_sacrificed_paper_basis"),
            "twin_agent_scorecards (RESEARCH counterfactual; the twin is "
            "not certified, so this is never capital evidence)")


async def workers_credential_classes(conn) -> dict | None:
    r = await conn.fetchrow(
        "SELECT value FROM ingestion_state WHERE key = 'workers_boot'")
    if r is None:
        return None
    v = C._j(r["value"]) or {}
    return v.get("credential_classes")


async def workers_pmus_census(conn) -> dict | None:
    """The workers' PMUS credential census from their boot heartbeat (names
    and shape enums only, pmus_credential_census)."""
    r = await conn.fetchrow(
        "SELECT value FROM ingestion_state WHERE key = 'workers_boot'")
    if r is None:
        return None
    v = C._j(r["value"]) or {}
    return v.get("pmus_credential_census")


async def release_receipt(conn, sha: str) -> dict | None:
    if not sha or sha == "UNKNOWN" or not await C._has(
            conn, "red_team_release_receipts"):
        return None
    r = await conn.fetchrow(
        "SELECT * FROM red_team_release_receipts WHERE deployed_sha = $1 "
        " ORDER BY observed_at DESC LIMIT 1", sha)
    return dict(r) if r else None


async def certificate_census(conn) -> dict:
    if not await C._has(conn, "red_team_settlement_certificates"):
        return {"status": "TABLE_ABSENT"}
    rows = await conn.fetch(
        "SELECT status, count(*) n FROM (SELECT DISTINCT ON (alias_key) "
        " alias_key, status FROM red_team_settlement_certificates ORDER BY "
        " alias_key, at DESC) x GROUP BY 1")
    return {r["status"]: int(r["n"]) for r in rows}


# ── assembly ─────────────────────────────────────────────────────────────

def _metric(value, *, as_of, source, status, num=None, den=None) -> dict:
    m = {"value": value, "as_of": as_of, "source": source, "status": status}
    if num is not None or den is not None:
        m.update(numerator=num, denominator=den)
    return m


def ui_truth(metrics: dict, *, now: float) -> dict:
    out, bad = {}, []
    for k, m in metrics.items():
        g = UT.metric_truth_gate(m, now=now, max_age_s=METRIC_MAX_AGE_S)
        shown = m["status"] if g["green"] else "STALE_OR_UNKNOWN"
        out[k] = dict(m, displayed_status=shown, blockers=list(g["blockers"]))
        if not g["green"]:
            bad.append(k)
    return C.result("UI_TRUTH", C.GREEN if not bad else C.RED,
                    ["METRIC_NOT_DISPLAYABLE_AS_GREEN:%s" % k for k in bad],
                    {"metrics": out, "max_age_s": METRIC_MAX_AGE_S})


#: each sectioned read and the controls it feeds
SECTION_CONTROLS = {
    "venue_health": ("VENUE_HEALTH",),
    "truth_quorum": ("TRUTH_QUORUM",),
    "attribution": ("PROFIT_BREAKERS", "ATTRIBUTION"),
    "holdout_registry": ("SAMPLE_INTEGRITY", "MULTIPLE_TESTING"),
    "capacity": ("CAPACITY",),
    "karen": ("KAREN_VALUE",),
    "credential_classes": ("CREDENTIAL_CLASSES",),
    "pmus_census": ("CREDENTIAL_CLASSES",),
    "release_receipt": ("RELEASE", "MIGRATION_INTEGRITY"),
    "migrations": ("MIGRATION_INTEGRITY",),
    "fee_evidence": ("FEE_EVIDENCE",),
    "certificates": ("SETTLEMENT_CERTIFICATES",),
    "canonical_exposure": ("CANONICAL_EXPOSURE",),
}


async def evaluate(conn, *, now: float | None = None,
                   completion: dict | None = None) -> dict:
    from ..completion import read as CR
    from . import exposure as X
    from . import fees as F
    now = float(now if now is not None else time.time())
    sha = implementation_sha()
    comp = completion or await CR.read(conn, now=now)
    ready = comp.get("readiness") or {}
    md = comp.get("market_data") or {}
    mgmt = comp.get("management_freshness") or {}
    runtime = comp.get("runtime") or {}
    venue = comp.get("venue_positions") or {}
    held = {"markable": mgmt.get("markable"),
            "freshly_manageable": (None if mgmt.get("fresh") is None else
                                   int(mgmt.get("fresh") or 0)
                                   + int(mgmt.get("quiet_valid") or 0)),
            "as_of": now}
    pri = md.get("priority_freshness") or {}
    sec = CR._Sections(conn)
    unk = {"rate": None, "green": False, "status": "UNAVAILABLE",
           "source": "UNAVAILABLE", "numerator": None, "denominator": None}
    vh = await sec.run("venue_health", lambda: VH.read(
        conn, held=held, priority=pri, total=None, now=now), {
            "venues": {}, "isolated": None, "cross_venue_pair": None,
            "freshness": {"held": dict(unk), "priority": dict(unk)}})
    venues = vh["venues"]
    pm_green = bool((venues.get(VH.POLYMARKET_US) or {}).get("green"))
    controls = {}
    controls["VENUE_HEALTH"] = C.result(
        "VENUE_HEALTH",
        C.GREEN if venues and all(v.get("green") for v in venues.values())
        else C.RED,
        ["%s:%s" % (k, b) for k, v in venues.items()
         for b in v.get("blockers") or ()],
        {"venues": venues, "isolated": vh["isolated"],
         "cross_venue_pair": vh["cross_venue_pair"],
         "freshness": vh["freshness"]})
    rows, open_disc = await sec.run("truth_quorum", lambda: C.quorum_rows(
        conn, now=now, venue_confirmed=bool(venue.get("venue_confirmed")),
        market_data_green=pm_green), ([], None))
    controls["TRUTH_QUORUM"] = C.quorum(rows, now=now,
                                        audrey_open_discrepancies=open_disc)
    attributed, fixtures = await sec.run(
        "attribution", lambda: C.attributed_positions(conn, now=now),
        ([], {}))
    mech = C.mechanism_rows(attributed, fixtures)
    controls["PROFIT_BREAKERS"] = C.profit_breakers(mech)
    controls["ATTRIBUTION"] = C.attribution(attributed)
    controls["DIGITAL_TWIN"] = C.twin(comp.get("digital_twin") or {})
    reg = await sec.run("holdout_registry",
                        lambda: C.holdout_registry(conn), {})
    controls["SAMPLE_INTEGRITY"] = C.samples(comp.get("probability") or {},
                                             reg)
    controls["MULTIPLE_TESTING"] = C.multiple_testing(reg)
    from .. import allie_capital as ALLIE
    controls["CAPACITY"] = C.capacity(
        await sec.run("capacity", lambda: capacity_points(conn), []),
        requested_usd=ALLIE.BOOK_CAP_USD)
    saved, false_cost, ksrc = await sec.run(
        "karen", lambda: karen_counterfactuals(conn), (None, None, None))
    controls["KAREN_VALUE"] = C.karen(saved, false_cost, source=ksrc)
    controls["CREDENTIAL_CLASSES"] = C.credentials({
        "api": C.credential_classes(),
        "workers": await sec.run("credential_classes",
                                 lambda: workers_credential_classes(conn),
                                 None)})
    from .. import pmus_credential_census as PCC
    controls["CREDENTIAL_CLASSES"]["evidence"]["pmus_census"] = {
        "sportsassets-api": PCC.census(service="sportsassets-api"),
        "sportsassets-workers": await sec.run(
            "pmus_census", lambda: workers_pmus_census(conn), None)}
    rel = await sec.run("release_receipt", lambda: release_receipt(conn, sha),
                        None)
    controls["MIGRATION_INTEGRITY"] = C.migrations(
        await sec.run("migrations", lambda: C.applied_migrations(conn), {}),
        C.repo_migrations(),
        fresh_db_passed=(bool(rel["capital_critical_green"]) if rel
                         else None))
    controls["FEE_EVIDENCE"] = C.fee_control(
        await sec.run("fee_evidence", lambda: F.census(conn, now=now), {}),
        now=now)
    certs = await sec.run("certificates", lambda: certificate_census(conn),
                          {})
    controls["SETTLEMENT_CERTIFICATES"] = C.result(
        "SETTLEMENT_CERTIFICATES",
        C.GREEN if certs.get("CERTIFIED") and not certs.get("INVALIDATED")
        else C.RED if certs.get("INVALIDATED") else C.UNKNOWN,
        (["INVALIDATED_ALIASES:%d" % certs["INVALIDATED"]]
         if certs.get("INVALIDATED") else [])
        + ([] if certs.get("CERTIFIED") else ["NO_CERTIFIED_ALIAS"]),
        {"latest_by_status": certs})
    ex = await sec.run("canonical_exposure", lambda: X.census(
        conn, ACCOUNT_ID, sha=sha, at=now), {
            "eligible": False, "blockers": ["READ_UNAVAILABLE"],
            "receipts": []})
    controls["CANONICAL_EXPOSURE"] = C.result(
        "CANONICAL_EXPOSURE", C.GREEN if ex["eligible"] else C.RED,
        ex["blockers"], {k: v for k, v in ex.items() if k != "receipts"})
    release_ok = bool(rel) and not (C._j(rel.get("blockers")) or []) and \
        rel.get("release_sha") == sha
    controls["RELEASE"] = C.result(
        "RELEASE", C.GREEN if release_ok else C.UNKNOWN if not rel else C.RED,
        (["NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA"] if not rel else
         list(C._j(rel.get("blockers")) or [])),
        {"running_sha": sha, "receipt": {k: (str(v) if not isinstance(
            v, (bool, int, float, type(None), str)) else v)
            for k, v in (rel or {}).items()}})
    # a read that failed is never a vacuous pass: every control it feeds is
    # RED and names it
    for name, t in sec.timings.items():
        if t["ok"]:
            continue
        for ctl in SECTION_CONTROLS.get(name, ()):
            c = controls.get(ctl)
            if c is not None:
                controls[ctl] = C.result(
                    ctl, C.RED, list(c.get("blockers") or []) + [
                        "READ_UNAVAILABLE:%s" % name],
                    dict(c.get("evidence") or {}, read_failure=t))
    fr = vh["freshness"]
    # each metric's as_of is its SOURCE read's, never this render's clock
    src_at = comp.get("as_of") if isinstance(comp.get("as_of"),
                                              (int, float)) else None
    metrics = {
        "held_freshness": _metric(fr["held"]["rate"], as_of=src_at,
                                  source=fr["held"]["source"],
                                  status=fr["held"]["status"],
                                  num=fr["held"]["numerator"],
                                  den=fr["held"]["denominator"]),
        "priority_freshness": _metric(fr["priority"]["rate"], as_of=src_at,
                                      source=fr["priority"]["source"],
                                      status=fr["priority"]["status"],
                                      num=fr["priority"]["numerator"],
                                      den=fr["priority"]["denominator"]),
        "readiness_status": _metric(ready.get("status"), as_of=src_at,
                                    source="completion readiness",
                                    status=ready.get("status")),
    }
    controls["UI_TRUTH"] = ui_truth(metrics, now=now)
    sw = runtime.get("shared_workers") or {}
    gov = ready.get("governors") or {}
    prob = comp.get("probability") or {}
    edge_ok = (prob.get("authority") == "BETTOR_RESIDUAL_ALLOWED"
               and (prob.get("improvement_lower_bound") or 0) > 0
               and "CASH" not in gov.values())
    inp = ReadinessInput(
        release_ok=release_ok,
        runtime_ok=("SHARED_WORKER_RSS_OR_UPTIME" not in str(
            ready.get("blockers")) and float(sw.get(
                "minutes_since_process_start") or 0) >= 60
            and float(sw.get("rss_highwater_fraction") or 1.0) < 0.85
            and not sw.get("universal_market_plane_started_here")),
        market_data_ok=controls["VENUE_HEALTH"]["status"] == C.GREEN,
        held_freshness_ok=fr["held"]["green"],
        priority_freshness_ok=fr["priority"]["green"],
        settlement_ok=bool((comp.get("settlement") or {}).get(
            "settlement_proven")) and not certs.get("INVALIDATED"),
        twin_ok=controls["DIGITAL_TWIN"]["status"] == C.GREEN,
        reconciliation_ok=controls["TRUTH_QUORUM"]["status"] == C.GREEN,
        positive_edge_ok=edge_ok,
        capacity_ok=controls["CAPACITY"]["status"] == C.GREEN,
        claim_exposure_ok=controls["CANONICAL_EXPOSURE"]["status"]
        == C.GREEN,
        profit_breakers_ok=controls["PROFIT_BREAKERS"]["status"] == C.GREEN,
        live_authority_still_shadow=comp.get("small_live") == "SHADOW",
        historical_paper_immutable=True)
    g = RTR.readiness_gate(inp)
    return {"version": VERSION, "as_of": now, "implementation_sha": sha,
            "status": g["status"], "checks": g["checks"],
            "blockers": list(g["blockers"]),
            "completion_readiness": {"status": ready.get("status"),
                                     "blockers": ready.get("blockers"),
                                     "governors": gov},
            "controls": controls,
            "mechanism_rows": len(mech),
            "exposure_receipts": ex["receipts"],
            "auto_activation": False,
            "authority": {"small_live": "SHADOW",
                          "kalshi_live_money": "NOT_ACTIVATED",
                          "adriana": "SHADOW_ONLY",
                          "capital_authority_granted": False},
            "historical_paper_immutable_basis": (
                "this read writes nothing; PAPER history is append-only by "
                "the ledger's own guards"),
            "read_timings": sec.timings,
            "completion": comp}


def capital_status(result: dict) -> str:
    return result.get("status") or "PAPER_SHADOW_ONLY"


def as_decimal(x):
    return Decimal(str(x))
