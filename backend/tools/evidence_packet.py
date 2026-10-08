"""THE PRODUCTION EVIDENCE PACKET (closeout, 2026-10-08).

Built by the pm-acceptance workflow from the files it collected in acc/
(GitHub gate conclusions, Render deploys / memory metrics / log counts,
and the API's own read-only readbacks). Every value carries the FILE and
the JSON PATH it was read from; a value no readback carries is reported
as NOT_IN_READBACK -- never inferred, never defaulted to a pass. The packet
and every input file are hash-manifested (SHA256SUMS), so the packet is
checkable against the bytes the job actually received.

    python3 -I backend/tools/evidence_packet.py <acc dir>

Stdlib only; reads files, writes evidence_packet.json and SHA256SUMS.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import statistics
import sys
import time

VERSION = "PRODUCTION_EVIDENCE_PACKET_V1"
MISSING = "NOT_IN_READBACK"


def load(acc: pathlib.Path, name: str):
    p = acc / ("%s.json" % name)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def find(obj, key, path=""):
    """(path, value) of the first `key` in a breadth-first walk, or None."""
    queue = [(path, obj)]
    while queue:
        p, o = queue.pop(0)
        if isinstance(o, dict):
            if key in o:
                return ("%s.%s" % (p, key)).lstrip("."), o[key]
            queue += [("%s.%s" % (p, k), v) for k, v in o.items()]
        elif isinstance(o, list):
            queue += [("%s[%d]" % (p, i), v) for i, v in enumerate(o[:200])]
    return None


def field(acc, name, key, *, data):
    hit = find(data, key) if data is not None else None
    if hit is None:
        return {"value": MISSING, "source": "%s.json" % name, "path": key}
    return {"value": hit[1], "source": "%s.json" % name, "path": hit[0]}


def at(data, *path):
    for p in path:
        if not isinstance(data, dict) or p not in data:
            return MISSING
        data = data[p]
    return data


def mem_stats(raw) -> dict:
    """Render /metrics/memory -> {instance: min/p50/p95/max MB, samples}."""
    out = {}
    for series in raw if isinstance(raw, list) else []:
        labels = {x.get("field"): x.get("value")
                  for x in series.get("labels") or [] if isinstance(x, dict)}
        inst = labels.get("instance") or ",".join(
            str(v) for v in labels.values()) or "service"
        vals = sorted(float(v.get("value")) / 1048576.0
                      for v in series.get("values") or []
                      if isinstance(v, dict) and v.get("value") is not None)
        if not vals:
            continue
        ts = [v.get("timestamp") for v in series.get("values") or []
              if isinstance(v, dict)]

        def q(f):
            return round(vals[min(len(vals) - 1, int(f * (len(vals) - 1)))],
                         1)
        out[inst] = {"samples": len(vals), "min_mb": round(vals[0], 1),
                     "p50_mb": round(statistics.median(vals), 1),
                     "p95_mb": q(0.95), "max_mb": round(vals[-1], 1),
                     "first": min(t for t in ts if t) if any(ts) else None,
                     "last": max(t for t in ts if t) if any(ts) else None}
    return out


def log_counts(acc: pathlib.Path) -> dict:
    out = {}
    for p in sorted(acc.glob("logs_*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        out[p.stem[len("logs_"):]] = {
            "lines": len(d.get("logs") or []),
            "has_more": bool(d.get("hasMore")),
            "filter": d.get("_filter"), "window": d.get("_window")}
    return out


READBACKS = ("release", "red_team", "venues", "completion", "pm_before",
             "pm_after", "shadow_health", "capital_readiness", "canary",
             "small_live", "xavier_management", "paper_freshness",
             "market_plane", "profitability_scoreboard", "revenue_readiness",
             "paper_reconciliation", "loop_health", "frontend_viewports")


def readback_status(acc: pathlib.Path) -> dict:
    """Every readback the job fetched: present, bytes, its own status."""
    out = {}
    for name in READBACKS:
        p = acc / ("%s.json" % name)
        d = load(acc, name)
        out[name] = {"present": p.exists(),
                     "bytes": p.stat().st_size if p.exists() else 0,
                     "parsed": d is not None,
                     "status": (d.get("status", d.get("state", MISSING))
                                if isinstance(d, dict) else MISSING)}
    return out


def evidence_input(pm) -> dict:
    """The PM harness's machine evidence fields, each with its path."""
    ev = at(pm, "data", "pm_acceptance", "evidence_input")
    if not isinstance(ev, dict):
        return {"value": MISSING, "source": "pm_after.json",
                "path": "data.pm_acceptance.evidence_input"}
    return {k: {"value": v, "source": "pm_after.json",
                "path": "data.pm_acceptance.evidence_input.%s" % k}
            for k, v in ev.items()}


def build(acc: pathlib.Path, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    rt = load(acc, "red_team") or {}
    comp = load(acc, "completion") or {}
    pm = load(acc, "pm_after") or {}
    sh = load(acc, "shadow_health") or {}
    ven = load(acc, "venues") or {}
    rel = load(acc, "release") or {}
    lin = load(acc, "lineage") or {}
    gates = load(acc, "gates") or {}
    render = load(acc, "render") or {}
    can = load(acc, "canary")
    xm = load(acc, "xavier_management")
    pf = load(acc, "paper_freshness")
    mp = load(acc, "market_plane")
    sb = load(acc, "profitability_scoreboard")
    ctr = at(rt, "data", "readiness", "controls")
    ctr = ctr if isinstance(ctr, dict) else {}
    packet = {
        "version": VERSION, "built_at": now,
        "identity": {
            "implementation_sha": os.environ.get("IMPL_SHA") or MISSING,
            "tested_sha": lin.get("sha", MISSING),
            "release_branch": lin.get("release_branch", MISSING),
            "release_sha": lin.get("release_sha", MISSING),
            "accepted_base_sha": lin.get("accepted_base_sha", MISSING),
            "descendant_of_base": lin.get("descendant_of_base", MISSING),
            "running_api_sha": at(rel, "api", "sha"),
            "running_workers_sha": at(rel, "workers", "sha"),
            "alignment": at(rel, "alignment")},
        "gates": {k: {"run_id": (v or {}).get("id"),
                      "conclusion": (v or {}).get("conclusion"),
                      "head_sha": (v or {}).get("head_sha")}
                  for k, v in (gates.get("runs") or {}).items()},
        "deploys": render,
        "readbacks": readback_status(acc),
        "pm_evidence_input": evidence_input(pm),
        "migrations": {
            "schema": at(rel, "schema"),
            "control": {k: at(ctr, "MIGRATION_INTEGRITY", *k.split("."))
                        for k in ("status", "blockers",
                                  "evidence.applied_fingerprint",
                                  "evidence.repo_fingerprint",
                                  "evidence.edited_in_place",
                                  "evidence.applied_not_in_build")}},
        "policy_integrity": {k: field(acc, "shadow_health", k, data=sh)
                             for k in ("policyIntegrityStatus",
                                       "policyVersion", "policyCodeSha",
                                       "frozenPolicyCodeSha",
                                       "codeShaMatches", "codeBoundary",
                                       "decisionWritingAllowed")},
        "pmx_primary": field(acc, "completion", "pmx_primary", data=comp),
        "market_plane": mp if mp is not None else MISSING,
        "venue_health": at(ctr, "VENUE_HEALTH"),
        "management_freshness": field(acc, "completion",
                                      "management_freshness", data=comp),
        "paper_freshness": ({k: field(acc, "paper_freshness", k, data=pf)
                             for k in ("status", "fresh_rate", "markable",
                                       "open_positions", "counts",
                                       "target_fresh_rate", "feeds")}
                            if pf is not None else MISSING),
        "kalshi_health": at(ven, "data", "health", "KALSHI_HEALTH"),
        "polymarket_health": at(ven, "data", "health", "POLYMARKET_HEALTH"),
        "mirror_shadow": field(acc, "completion", "venue_positions",
                               data=comp),
        "xavier_management": xm if xm is not None else MISSING,
        "frontend_viewports": (load(acc, "frontend_viewports")
                               if load(acc, "frontend_viewports") is not None
                               else MISSING),
        "canary": can if can is not None else MISSING,
        "memory": {p.stem[len("mem_"):]: mem_stats(load(acc, p.stem))
                   for p in sorted(acc.glob("mem_*.json"))},
        "log_counts": log_counts(acc),
        "scoreboard": {k: at(pm, "data", "scoreboard", k) for k in (
            "decision_rows", "independent_events", "mechanism_states",
            "routing_counts", "reconciliation", "ledger_reconciliation",
            "paper_ledger_realized_total_usd")},
        "profitability_scoreboard": sb if sb is not None else MISSING,
        "governors": at(comp, "data", "readiness", "governors"),
        "owner_blockers": at(comp, "data", "owner_blockers"),
        "profit_breakers": at(ctr, "PROFIT_BREAKERS"),
        "capacity": at(ctr, "CAPACITY"),
        "red_team": {"status": at(rt, "data", "readiness", "status"),
                     "blockers": at(rt, "data", "readiness", "blockers"),
                     "controls": {k: at(v, "status") for k, v in ctr.items()}},
        "pm_acceptance": {k: at(pm, "data", "pm_acceptance", k) for k in (
            "pm_state", "capital_status", "critical_failures",
            "economic_or_evidence_gaps", "fields_without_machine_evidence")},
        "release_receipt": at(pm, "data", "release_receipt"),
        "golden": {k: at(pm, "data", "golden", k) for k in (
            "passed", "total", "appended_passed", "appended_total",
            "green")},
        "authority": at(pm, "data", "authority"),
    }
    return packet


def manifest(acc: pathlib.Path) -> dict:
    out = {}
    for p in sorted(acc.iterdir()):
        if p.is_file() and p.name != "SHA256SUMS":
            out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def main(argv) -> int:
    acc = pathlib.Path(argv[1] if len(argv) > 1 else "acc")
    packet = build(acc)
    packet["input_files_sha256"] = manifest(acc)
    body = json.dumps(packet, indent=1, sort_keys=True, default=str)
    (acc / "evidence_packet.json").write_text(body)
    sums = manifest(acc)
    (acc / "SHA256SUMS").write_text("".join(
        "%s  %s\n" % (h, n) for n, h in sorted(sums.items())))
    print("evidence_packet.json sha256 %s" % sums["evidence_packet.json"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
