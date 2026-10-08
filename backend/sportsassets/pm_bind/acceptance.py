"""THE INDEPENDENT FINAL PM ACCEPTANCE HARNESS (PM Evidence Pack, component
3) FED FROM MACHINE EVIDENCE ONLY.

The harness (pm_evidence.pm_acceptance.harness, byte for byte) decides RED
/ YELLOW / GREEN. This module only COLLECTS its input, field by field, from
machine sources, each with its as_of and source:

  release (CI)       the release receipt the pm-acceptance workflow posts
                     after reading GitHub (exact-SHA gate conclusions,
                     ancestry) -- accepted by the API only when its deployed
                     SHA is the API's own RENDER_GIT_COMMIT
  runtime            workers_boot / workers_memory (completion readback)
  market data        held / priority freshness, PMX source, Kalshi health
  controls           the red-team interlock (truth quorum, credential
                     classes, migration integrity, twin, capacity, breakers)
  economics          the forward scoreboard, the profitability governor

A field with no machine evidence is LEFT OUT (the harness then fails that
gate): missing evidence is never success, and nothing here is typed from a
status narrative.
"""
from __future__ import annotations

import json
import pathlib

from ..pm_evidence.pm_acceptance import harness as H

VERSION = "PM_ACCEPTANCE_BIND_V1"
DATA = pathlib.Path(__file__).resolve().parents[1] / "pm_evidence" / "data"


def spec() -> dict:
    return json.loads((DATA / "acceptance_spec.json").read_text())


def collect(*, red: dict, scoreboard: dict, release: dict | None,
            now: float) -> tuple:
    """(harness input, provenance {field: {source, as_of}})."""
    e, prov = {}, {}

    def put(k, v, source):
        if v is None:
            return
        e[k] = v
        prov[k] = {"source": source, "as_of": now}

    comp = red.get("completion") or {}
    ctr = red.get("controls") or {}
    sha = red.get("implementation_sha")
    rel = release or {}
    if rel:
        src = "red_team_release_receipts (pm-acceptance workflow: GitHub " \
              "exact-SHA gates + ancestry; deployed SHA verified by the API)"
        put("accepted_base_sha", rel.get("accepted_base_sha"), src)
        put("tested_sha", rel.get("tested_sha"), src)
        put("release_sha", rel.get("release_sha"), src)
        put("is_descendant_of_accepted_base", rel.get("descendant_of_base"),
            src)
        for k, col in (("backend_tests", "backend_tests_green"),
                       ("capital_critical", "capital_critical_green"),
                       ("commit_guard", "commit_guard_green"),
                       ("engine_diagnostic", "engine_diagnostic_green")):
            put(k, rel.get(col), src)
    blk = rel.get("blockers") or []
    if isinstance(blk, str):
        blk = json.loads(blk or "[]")
    wk_bad = [b for b in blk if str(b).startswith(
        "WORKERS_NOT_ON_RELEASE_SHA")]
    # deployed = BOTH services: a workers live commit that differs from the
    # API's (recorded by the release receipt from Render) is not deployed
    put("deployed_sha", (wk_bad[0] if wk_bad else sha)
        if sha and sha != "UNKNOWN" else None,
        "RENDER_GIT_COMMIT of the serving API" + (
            "; the workers' live commit differs (release receipt, Render)"
            if wk_bad else ""))
    mig = ctr.get("MIGRATION_INTEGRITY") or {}
    put("migration_integrity", mig.get("status") == "GREEN" if mig else None,
        "red team MIGRATION_INTEGRITY (schema_migrations vs this build)")
    sw = ((comp.get("runtime") or {}).get("shared_workers") or {})
    put("no_oom_minutes", sw.get("minutes_since_process_start"),
        "workers_boot (minutes since the shared workers last started)")
    put("worker_rss_fraction", sw.get("rss_highwater_fraction"),
        "workers_memory peak / container limit")
    put("shared_worker_starts_ump",
        sw.get("universal_market_plane_started_here")
        if sw.get("commit_sha") else None, "workers_boot.started")
    mp = ((comp.get("runtime") or {}).get("market_plane") or {})
    put("dedicated_market_plane_present",
        mp.get("state") == "DEDICATED_RUNNING" if mp else None,
        "universal_market_plane heartbeat runtime label")
    fr = ((ctr.get("VENUE_HEALTH") or {}).get("evidence") or {}).get(
        "freshness") or {}
    held, pri = fr.get("held") or {}, fr.get("priority") or {}
    if held.get("denominator"):
        put("held_fresh", held.get("numerator"), held.get("source"))
        put("held_required", held.get("denominator"), held.get("source"))
    if pri.get("denominator"):
        put("priority_fresh", pri.get("numerator"), pri.get("source"))
        put("priority_required", pri.get("denominator"), pri.get("source"))
    q = ctr.get("TRUTH_QUORUM") or {}
    put("truth_quorum", q.get("status") == "GREEN" if q else None,
        "red team TRUTH_QUORUM")
    gates = comp.get("gate_evidence") or {}
    swg = gates.get("software_reds_zero") or {}
    ev = swg.get("evidence")
    reds = (ev.get("software") if isinstance(ev, dict)
            else swg.get("software"))
    put("software_red_count", reds if isinstance(reds, (int, float))
        else None, "capital readiness gate software_reds_zero")
    can = gates.get("production_canary_clean") or {}
    put("canary", can.get("value") if "value" in can else None,
        "capital readiness gate production_canary_clean")
    cr = ctr.get("CREDENTIAL_CLASSES") or {}
    put("credential_classes", cr.get("status") == "GREEN" if cr else None,
        "red team CREDENTIAL_CLASSES (shape only)")
    put("historical_paper_immutable", True,
        "PAPER ledger append-only guards (no write path in this read)")
    put("live_authority_shadow", comp.get("small_live") == "SHADOW",
        "live_authorization.SMALL_LIVE_MODE / small_live_control")
    md = comp.get("market_data") or {}
    fresh_pmx = md.get("fresh")
    pp = md.get("pmx_primary") or {}
    if md.get("snapshot") != "CURRENT" and pp:
        # NO CURRENT DEDICATED-PLANE SNAPSHOT: the PMX gRPC primary is read
        # where it runs -- the deciding process's stream and the held marks
        # it produced (completion pmx_primary_block). PMX_GRPC only with a
        # connected stream, a current resident book and >= 1 held mark from
        # it; the count is those held marks, never a subscription size.
        src = ("deciding-process PMX gRPC stream (paper_mark_refresh_runs."
               "market_data) + bettor_paper_freshness feeds")
        put("pmx_primary_source", pp.get("source"), src)
        put("pmx_grpc_fresh_count", pp.get("held_fresh_from_stream"), src)
    else:
        put("pmx_primary_source", "PMX_GRPC" if (fresh_pmx or 0) > 0 and
            md.get("subscription_mode") else ("REST" if md else None),
            "market plane snapshot subscription")
        put("pmx_grpc_fresh_count", fresh_pmx, "market plane snapshot")
    kv = (((ctr.get("VENUE_HEALTH") or {}).get("evidence") or {}).get(
        "venues") or {}).get("KALSHI") or {}
    put("kalshi_market_data_current", kv.get("green") if kv else None,
        "KALSHI_HEALTH (kalshi market data heartbeat)")
    put("kalshi_mapped_markets", kv.get("denominator"),
        "KALSHI_HEALTH tracked books")
    vh = (ctr.get("VENUE_HEALTH") or {}).get("evidence") or {}
    put("venue_health_isolated", vh.get("isolated"),
        "red team VENUE_HEALTH (one entry per venue, never blended)")
    st = comp.get("settlement") or {}
    put("settlement_proven", st.get("settlement_proven") if st else None,
        "market plane settlement state of the open-position markets")
    tw = (ctr.get("DIGITAL_TWIN") or {}).get("evidence") or {}
    if tw.get("compared"):
        put("twin_compared", tw.get("compared"), "repaired fill replay")
        put("twin_matched", tw.get("matched"), "repaired fill replay")
        put("twin_optimistic_false_fills", tw.get("optimistic_false_fills"),
            "repaired fill replay")
        put("twin_lookahead_violations", tw.get("lookahead_violations"),
            "repaired fill replay")
    xav = gates.get("xavier_complete") or {}
    if "value" in xav:
        put("xavier_complete_rate", 1.0 if xav.get("value") is True else 0.0,
            "capital readiness gate xavier_complete")
    dmech = ((scoreboard or {}).get("mechanisms") or {}).get(
        "DIRECTIONAL") or {}
    lcb = dmech.get("forward_pnl_lcb_per_event")
    put("forward_edge_lower_bound", float(lcb) if lcb is not None else None,
        "forward scoreboard DIRECTIONAL event-clustered lower bound")
    cap = (ctr.get("CAPACITY") or {}).get("evidence") or {}
    if cap:
        put("proven_positive_capacity", float(
            cap.get("proven_positive_capacity_usd") or 0),
            "red team CAPACITY (bind evaluations by size)")
    gov = ((red.get("completion_readiness") or {}).get("governors") or {})
    if gov:
        put("profitability_governor", "CASH" if "CASH" in gov.values()
            else "ELIGIBLE", "completion readiness governors")
    pb = ctr.get("PROFIT_BREAKERS") or {}
    put("mechanism_breakers_green", pb.get("status") == "GREEN" if pb
        else None, "red team PROFIT_BREAKERS")
    tot = (scoreboard or {}).get("paper_ledger_realized_total_usd")
    put("paper_pnl", float(tot) if tot is not None else None,
        "PAPER ledger realized P&L (all positions)")
    return e, prov


def evaluate(*, red: dict, scoreboard: dict, release: dict | None,
             now: float) -> dict:
    e, prov = collect(red=red, scoreboard=scoreboard, release=release,
                      now=now)
    r = H.evaluate(e, spec())
    missing = sorted(set(H.CRITICAL + H.ECONOMIC) - {
        k for k, g in r["gates"].items() if g["pass"]})
    return {"version": VERSION, "as_of": now, "pm_state": r["pm_state"],
            "capital_status": r["capital_status"],
            "critical_failures": r["critical_failures"],
            "economic_or_evidence_gaps": r["economic_or_evidence_gaps"],
            "gates": r["gates"], "summary": r["summary"],
            "evidence_input": e, "provenance": prov,
            "fields_without_machine_evidence": sorted(
                set(_INPUT_FIELDS) - set(e)),
            "failing_gates": missing,
            "activates_money": False}


_INPUT_FIELDS = (
    "accepted_base_sha", "tested_sha", "release_sha", "deployed_sha",
    "is_descendant_of_accepted_base", "backend_tests", "capital_critical",
    "commit_guard", "engine_diagnostic", "migration_integrity", "truth_quorum",
    "canary", "credential_classes", "historical_paper_immutable",
    "live_authority_shadow", "no_oom_minutes", "worker_rss_fraction",
    "shared_worker_starts_ump", "dedicated_market_plane_present",
    "held_fresh", "held_required", "priority_fresh", "priority_required",
    "software_red_count", "pmx_primary_source", "pmx_grpc_fresh_count",
    "kalshi_market_data_current", "kalshi_mapped_markets",
    "venue_health_isolated", "settlement_proven", "twin_compared",
    "twin_matched", "twin_optimistic_false_fills",
    "twin_lookahead_violations", "xavier_complete_rate",
    "forward_edge_lower_bound", "proven_positive_capacity",
    "profitability_governor", "mechanism_breakers_green", "paper_pnl")
