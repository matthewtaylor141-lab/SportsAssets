"""THE PRODUCTION EVIDENCE PACKET (closeout, 2026-10-08; V2 after the PM
review of the RC4 packet, pm-acceptance run 37738089957).

Built by the pm-acceptance workflow from the files it collected in acc/
(GitHub gate conclusions, Render deploys / memory metrics / log counts /
the runtime window, the PAPER history fingerprints, and the API's own
read-only readbacks). Every value carries the FILE and the DECLARED JSON
PATH it was read from:

  * a value is read only at its declared path (SCHEMA below), never found
    by name. V1 took the first key of that name in a breadth-first walk,
    and in the RC4 packet reported policyVersion = "RN1_SHADOW_V1" from
    shadow_health.environment (the benchmark lane's policy) next to the
    BETTOR_POLICY_INTEGRITY component's own status; a reordered body or an
    old record nested in a history list could have been reported the same
    way.
  * a declared path that the readback does not carry is NOT_IN_READBACK;
    a readback that was not read (file absent, HTTP status not 200, body
    not JSON, envelope not OK, or the producing section of the API's own
    read failed) is READ_UNAVAILABLE with the named reason; a readback
    about another account / policy / venue / service than the one
    declared is IDENTITY_MISMATCH. None of these is ever a default pass.
  * memory and log counts cover all three services of a release, the
    market plane included (V1 read only the API and the workers, while
    sportsassets-market-plane was being oomKilled at 2Gi).

The packet and every input file are hash-manifested (SHA256SUMS), so the
packet is checkable against the bytes the job actually received.

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
from datetime import datetime

VERSION = "PRODUCTION_EVIDENCE_PACKET_V2"
MISSING = "NOT_IN_READBACK"
UNAVAILABLE = "READ_UNAVAILABLE"
MISMATCH = "IDENTITY_MISMATCH"

#: the three Render services one release runs on (pm_bind.acceptance)
SERVICES = ("sportsassets-api", "sportsassets-workers",
            "sportsassets-market-plane")
#: the PAPER account every account-scoped readback must be about
ACCOUNT_ID = "paper_acct_main"
#: the policy whose integrity the packet reports: the BETTOR decision
#: policy's own component, keyed by the primary lane it belongs to
POLICY_COMPONENT = "BETTOR_POLICY_INTEGRITY"
#: the log lines counted per service (name, exact filter text); the
#: workflow requests exactly these
LOG_FILTERS = (("churn", "exited cleanly; restarting"),
               ("crash", "crashed; restarting"),
               ("migration_changed", "WAS ALREADY APPLIED"),
               ("policy_drift", "POLICY_CODE_DRIFT"),
               ("memory_error", "MemoryError"))
#: readbacks whose body is the command envelope {status, why, data}: read
#: only when status == "OK"
ENVELOPED = ("red_team", "venues", "completion", "pm_before", "pm_after",
             "capital_readiness", "profitability_scoreboard",
             "revenue_readiness")
MEMORY_RESOLUTION_S = 60


def load(acc: pathlib.Path, name: str):
    p = acc / ("%s.json" % name)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def http_codes(acc: pathlib.Path) -> dict:
    """{readback: HTTP status} as the workflow recorded each GET."""
    out = {}
    try:
        lines = (acc / "readback_http.tsv").read_text().splitlines()
    except OSError:
        return out
    for ln in lines:
        parts = ln.split("\t")
        if len(parts) >= 2 and parts[0]:
            out[parts[0]] = parts[1].strip()
    return out


class Readbacks:
    """Each readback once: (body, None) when it was read, (None, reason)
    when it was not."""

    def __init__(self, acc: pathlib.Path):
        self.acc, self.codes, self._cache = acc, http_codes(acc), {}

    def get(self, name: str):
        if name in self._cache:
            return self._cache[name]
        p = self.acc / ("%s.json" % name)
        code = self.codes.get(name)
        if not p.exists():
            out = (None, "%s:%s:FILE_ABSENT" % (UNAVAILABLE, name))
        elif code is not None and code != "200":
            out = (None, "%s:%s:HTTP_%s" % (UNAVAILABLE, name, code))
        else:
            d = load(self.acc, name)
            if d is None:
                out = (None, "%s:%s:NOT_JSON" % (UNAVAILABLE, name))
            elif name in ENVELOPED and (not isinstance(d, dict) or
                                        d.get("status") != "OK"):
                out = (None, "%s:%s:%s" % (
                    UNAVAILABLE, name, (d.get("status") if isinstance(
                        d, dict) else type(d).__name__)))
            else:
                out = (d, None)
        self._cache[name] = out
        return out


def at(data, *path):
    for p in path:
        if not isinstance(data, dict) or p not in data:
            return MISSING
        data = data[p]
    return data


def _failed_sections(rb: Readbacks, name: str, sections) -> list:
    """The producing sections of the API's own sectioned read that failed
    (completion: sections_unavailable / section_timings; red team:
    read_timings): a value they feed is not evidence."""
    d, why = rb.get(name)
    if why or not sections:
        return []
    if name == "completion":
        down = set(at(d, "data", "sections_unavailable") or []) if \
            isinstance(at(d, "data", "sections_unavailable"), list) else set()
        tim = at(d, "data", "section_timings")
    elif name == "red_team":
        down, tim = set(), at(d, "data", "readiness", "read_timings")
    else:
        return []
    tim = tim if isinstance(tim, dict) else {}
    return [s for s in sections if s in down or (
        isinstance(tim.get(s), dict) and tim[s].get("ok") is False)]


def declared(rb: Readbacks, name: str, path: tuple, *, sections=(),
             identity=None) -> dict:
    """The value at its DECLARED path, never searched for by name.
    identity = ((path...), expected): the readback must be about that."""
    src, p = "%s.json" % name, ".".join(path)
    d, why = rb.get(name)
    if why:
        return {"value": UNAVAILABLE, "source": src, "path": p,
                "reason": why}
    down = _failed_sections(rb, name, sections)
    if down:
        return {"value": UNAVAILABLE, "source": src, "path": p,
                "reason": "%s:%s.%s" % (UNAVAILABLE, name,
                                        ",".join(sorted(down)))}
    if identity is not None:
        ipath, expected = identity
        got = at(d, *ipath)
        if got != expected:
            return {"value": MISMATCH, "source": src, "path": p,
                    "reason": "%s:%s:%s=%s!=%s" % (
                        MISMATCH, name, ".".join(ipath), got, expected)}
    v = at(d, *path)
    if v is MISSING:
        return {"value": MISSING, "source": src, "path": p,
                "reason": "%s:%s:%s" % (MISSING, name, p)}
    return {"value": v, "source": src, "path": p}


def whole(rb: Readbacks, name: str):
    """A readback reported whole (its body), or its named unavailability."""
    d, why = rb.get(name)
    if why:
        return {"value": UNAVAILABLE, "reason": why}
    return d


# ── memory / logs, per declared service ────────────────────────────────────

def _epoch(v):
    if not isinstance(v, str):
        return None
    s = v.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _slope_per_h(pts) -> float | None:
    """Least-squares MB per hour over (epoch, MB) samples."""
    if len(pts) < 2:
        return None
    mt = statistics.fmean(t for t, _ in pts)
    mv = statistics.fmean(v for _, v in pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    if den <= 0:
        return None
    return round(sum((t - mt) * (v - mv) for t, v in pts) / den * 3600.0, 1)


def _gaps(times, resolution_s: float) -> dict:
    """Missing samples between the first and last: a step longer than 1.5
    resolutions is a gap (the metric was not reported)."""
    out = {"count": 0, "missing_samples": 0, "longest_s": 0.0,
           "intervals": []}
    for a, b in zip(times, times[1:]):
        dt = b - a
        if dt > 1.5 * resolution_s:
            out["count"] += 1
            out["missing_samples"] += int(round(dt / resolution_s)) - 1
            out["longest_s"] = max(out["longest_s"], round(dt, 1))
            if len(out["intervals"]) < 10:
                out["intervals"].append([a, b])
    return out


def mem_stats(raw) -> dict:
    """Render /metrics/memory -> {instance: min/p50/p95/max MB, samples,
    first/last, trend (MB/h), gaps}."""
    out = {}
    for series in raw if isinstance(raw, list) else []:
        if not isinstance(series, dict):
            continue
        labels = {x.get("field"): x.get("value")
                  for x in series.get("labels") or [] if isinstance(x, dict)}
        inst = labels.get("instance") or ",".join(
            str(v) for v in labels.values()) or "service"
        samples = [v for v in series.get("values") or []
                   if isinstance(v, dict) and v.get("value") is not None]
        vals = sorted(float(v.get("value")) / 1048576.0 for v in samples)
        if not vals:
            continue
        ts = [v.get("timestamp") for v in samples]
        pts = sorted((_epoch(v.get("timestamp")),
                      float(v.get("value")) / 1048576.0)
                     for v in samples if _epoch(v.get("timestamp")))

        def q(f):
            return round(vals[min(len(vals) - 1, int(f * (len(vals) - 1)))],
                         1)
        out[inst] = {"samples": len(vals), "min_mb": round(vals[0], 1),
                     "p50_mb": round(statistics.median(vals), 1),
                     "p95_mb": q(0.95), "max_mb": round(vals[-1], 1),
                     "first": min(t for t in ts if t) if any(ts) else None,
                     "last": max(t for t in ts if t) if any(ts) else None,
                     "trend_mb_per_h": _slope_per_h(pts),
                     "gaps": _gaps([t for t, _ in pts],
                                   MEMORY_RESOLUTION_S),
                     "service_label": labels.get("service")}
    return out


def service_ids(acc: pathlib.Path) -> dict:
    """{service name: its Render id} from the job's own exact-name lookup
    (one hit only); otherwise the reason it is not bound."""
    out = {}
    for svc in SERVICES:
        d = load(acc, "render_lookup_%s" % svc)
        if not isinstance(d, list):
            out[svc] = {"id": None, "reason": "%s:render_lookup_%s" % (
                UNAVAILABLE, svc)}
            continue
        hits = [x["service"].get("id") for x in d if isinstance(x, dict)
                and isinstance(x.get("service"), dict)
                and x["service"].get("name") == svc]
        out[svc] = ({"id": hits[0], "reason": None} if len(hits) == 1 else
                    {"id": None, "reason": "SERVICE_NOT_BOUND:%d_HITS" %
                     len(hits)})
    return out


def memory(acc: pathlib.Path, ids: dict) -> tuple:
    """({service: {instance: stats}} for every declared service, or its
    named unavailability; {service: identity of what was read})."""
    col = load(acc, "render_collection") or {}
    codes = col.get("memory_http") if isinstance(col, dict) else None
    codes = codes if isinstance(codes, dict) else {}
    mem, ident = {}, {}
    for svc in SERVICES:
        sid = (ids.get(svc) or {}).get("id")
        raw = load(acc, "mem_%s" % svc)
        code = codes.get(svc)
        why = None
        if not (acc / ("mem_%s.json" % svc)).exists():
            why = "%s:mem_%s:FILE_ABSENT" % (UNAVAILABLE, svc)
        elif code is not None and str(code) != "200":
            why = "%s:mem_%s:HTTP_%s" % (UNAVAILABLE, svc, code)
        elif not isinstance(raw, list):
            why = "%s:mem_%s:NOT_A_SERIES_LIST" % (UNAVAILABLE, svc)
        if why:
            mem[svc] = {"value": UNAVAILABLE, "reason": why}
            ident[svc] = {"service_id": sid, "read": why}
            continue
        stats = mem_stats(raw)
        mem[svc] = stats
        wrong_svc = sorted({s["service_label"] for s in stats.values()
                            if s["service_label"] is not None
                            and s["service_label"] != sid})
        foreign = sorted(i for i in stats
                         if sid and not i.startswith(sid + "-"))
        ident[svc] = {"service_id": sid, "read": "OK",
                      "instances": sorted(stats),
                      "wrong_service_labels": wrong_svc if sid else [],
                      "instances_not_of_service": foreign,
                      # an instance is attributed to a service only by the
                      # job's own exact-name lookup of that service's id
                      "verdict": ("SERVICE_NOT_BOUND" if not sid else
                                  "NO_SAMPLES" if not stats else MISMATCH
                                  if wrong_svc or foreign else "BOUND")}
    return mem, ident


def log_counts(acc: pathlib.Path, ids: dict | None = None) -> dict:
    """Per declared service and filter: the line count Render returned
    (lower_bound when hasMore), or its named unavailability."""
    ids = ids or {}
    out = {}
    for svc in SERVICES:
        sid = (ids.get(svc) or {}).get("id")
        for name, text in LOG_FILTERS:
            key = "%s_%s" % (svc, name)
            p = acc / ("logs_%s.json" % key)
            if not p.exists():
                out[key] = {"value": UNAVAILABLE, "reason": "%s:logs_%s:"
                            "FILE_ABSENT" % (UNAVAILABLE, key)}
                continue
            d = load(acc, "logs_%s" % key)
            if not isinstance(d, dict):
                out[key] = {"value": UNAVAILABLE, "reason": "%s:logs_%s:"
                            "NOT_JSON" % (UNAVAILABLE, key)}
                continue
            code = d.get("_http")
            if code is not None and str(code) != "200":
                out[key] = {"value": UNAVAILABLE, "reason": "%s:logs_%s:"
                            "HTTP_%s" % (UNAVAILABLE, key, code)}
                continue
            if not isinstance(d.get("logs"), list):
                out[key] = {"value": UNAVAILABLE, "reason": "%s:logs_%s:"
                            "NO_LOG_LIST" % (UNAVAILABLE, key)}
                continue
            if d.get("_filter") != text:
                out[key] = {"value": MISMATCH, "reason": "%s:logs_%s:"
                            "_filter" % (MISMATCH, key)}
                continue
            wrong = 0
            for ln in d["logs"]:
                lab = {x.get("name"): x.get("value") for x in (
                    ln.get("labels") or []) if isinstance(x, dict)} if \
                    isinstance(ln, dict) else {}
                if sid and lab.get("resource") not in (None, sid):
                    wrong += 1
            out[key] = {"lines": len(d["logs"]),
                        "has_more": bool(d.get("hasMore")),
                        "lower_bound": bool(d.get("hasMore")),
                        "filter": d.get("_filter"), "window": d.get("_window"),
                        "wrong_resource": wrong}
    return out


READBACKS = ("release", "red_team", "venues", "completion", "pm_before",
             "pm_after", "shadow_health", "capital_readiness", "canary",
             "small_live", "xavier_management", "paper_freshness",
             "market_plane", "profitability_scoreboard", "revenue_readiness",
             "paper_reconciliation", "loop_health", "frontend_viewports")


def readback_status(acc: pathlib.Path, rb: Readbacks | None = None) -> dict:
    """Every readback the job fetched: present, bytes, HTTP, its own status
    and, when it cannot be used, why."""
    rb = rb or Readbacks(acc)
    out = {}
    for name in READBACKS:
        p = acc / ("%s.json" % name)
        d = load(acc, name)
        out[name] = {"present": p.exists(),
                     "bytes": p.stat().st_size if p.exists() else 0,
                     "parsed": d is not None,
                     "http": rb.codes.get(name),
                     "status": (d.get("status", d.get("state", MISSING))
                                if isinstance(d, dict) else MISSING),
                     "unavailable": rb.get(name)[1]}
    return out


def evidence_input(rb: Readbacks):
    """The PM harness's machine evidence fields, each with its path."""
    d, why = rb.get("pm_after")
    if why:
        return {"value": UNAVAILABLE, "source": "pm_after.json",
                "path": "data.pm_acceptance.evidence_input", "reason": why}
    ev = at(d, "data", "pm_acceptance", "evidence_input")
    if not isinstance(ev, dict):
        return {"value": MISSING, "source": "pm_after.json",
                "path": "data.pm_acceptance.evidence_input"}
    return {k: {"value": v, "source": "pm_after.json",
                "path": "data.pm_acceptance.evidence_input.%s" % k}
            for k, v in ev.items()}


#: red-team controls reported by status; each read only at
#: data.readiness.controls.<NAME>
CONTROLS = ("ATTRIBUTION", "CANONICAL_EXPOSURE", "CAPACITY",
            "CREDENTIAL_CLASSES", "DIGITAL_TWIN", "FEE_EVIDENCE",
            "KAREN_VALUE", "MIGRATION_INTEGRITY", "MULTIPLE_TESTING",
            "PROFIT_BREAKERS", "RELEASE", "SAMPLE_INTEGRITY",
            "SETTLEMENT_CERTIFICATES", "TRUTH_QUORUM", "UI_TRUTH",
            "VENUE_HEALTH")


def build(acc: pathlib.Path, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    rb = Readbacks(acc)
    rt, _ = rb.get("red_team")
    rel = load(acc, "release") or {}
    lin = load(acc, "lineage") or {}
    gates = load(acc, "gates") or {}
    render = load(acc, "render")
    ids = service_ids(acc)
    mem, mem_ident = memory(acc, ids)
    R = ("data", "readiness")
    C = R + ("controls",)
    pol = ("components", POLICY_COMPONENT)
    sh, _ = rb.get("shadow_health")
    lane = at(sh, "environment", "primaryLane") if sh else MISSING
    ver = at(sh, "components", POLICY_COMPONENT, "policyVersion") if sh \
        else MISSING
    acct = (("data", "account_id"), ACCOUNT_ID)
    packet = {
        "version": VERSION, "built_at": now,
        "identity": {
            "implementation_sha": os.environ.get("IMPL_SHA") or MISSING,
            # the commit whose binder / builder / sender judged the release
            # (the workflow's own, checked out to judge/), never the release
            "judge_sha": os.environ.get("JUDGE_SHA") or MISSING,
            "tested_sha": lin.get("sha", MISSING),
            "release_branch": lin.get("release_branch", MISSING),
            "release_sha": lin.get("release_sha", MISSING),
            "accepted_base_sha": lin.get("accepted_base_sha", MISSING),
            "descendant_of_base": lin.get("descendant_of_base", MISSING),
            "running_api_sha": at(rel, "api", "sha"),
            "running_workers_sha": at(rel, "workers", "sha"),
            "alignment": at(rel, "alignment"),
            "account_id": ACCOUNT_ID,
            "services": {s: ids[s] for s in SERVICES}},
        "gates": {k: {"run_id": (v or {}).get("id"),
                      "conclusion": (v or {}).get("conclusion"),
                      "head_sha": (v or {}).get("head_sha")}
                  for k, v in (gates.get("runs") or {}).items()},
        "deploys": render if render is not None else {
            "value": UNAVAILABLE, "reason": "%s:render:FILE_ABSENT" %
            UNAVAILABLE},
        "runtime_window": whole(rb, "runtime_window"),
        "paper_history": whole(rb, "paper_history"),
        "acceptance": {k: declared(rb, "acceptance", (k,)) for k in (
            "api_pm_state", "same_input_pm_state", "independent_pm_state",
            "critical_failures", "economic_or_evidence_gaps", "unproven")},
        "readbacks": readback_status(acc, rb),
        "pm_evidence_input": evidence_input(rb),
        "migrations": {
            "schema": at(rel, "schema"),
            "control": {k: declared(rb, "red_team", C + (
                "MIGRATION_INTEGRITY",) + tuple(k.split(".")),
                sections=("migrations",))
                for k in ("status", "blockers",
                          "evidence.applied_fingerprint",
                          "evidence.repo_fingerprint",
                          "evidence.edited_in_place",
                          "evidence.applied_not_in_build")}},
        # THE BETTOR POLICY'S OWN COMPONENT, keyed by the primary lane it
        # belongs to: never the environment's (benchmark) policyVersion,
        # never a record found by name elsewhere in the body
        "policy_integrity": {k: declared(rb, "shadow_health", pol + (k,))
                             for k in ("policyIntegrityStatus",
                                       "policyVersion", "policyCodeSha",
                                       "frozenPolicyCodeSha",
                                       "codeShaMatches", "codeBoundary",
                                       "decisionWritingAllowed")},
        "policy_identity": {
            "component": POLICY_COMPONENT, "primary_lane": lane,
            "policy_version": ver,
            "verdict": ("BOUND" if isinstance(lane, str) and isinstance(
                ver, str) and ver.startswith(lane + "_") else MISMATCH
                if sh else UNAVAILABLE)},
        "pmx_primary": declared(rb, "completion", ("data", "market_data",
                                                   "pmx_primary"),
                                sections=("market_data", "pmx_primary",
                                          "management_freshness"),
                                identity=acct),
        "market_plane": whole(rb, "market_plane"),
        "venue_health": declared(rb, "red_team", C + ("VENUE_HEALTH",),
                                 sections=("venue_health",)),
        "management_freshness": declared(
            rb, "completion", ("data", "management_freshness"),
            sections=("management_freshness",), identity=acct),
        "paper_freshness": {k: declared(
            rb, "paper_freshness", ("freshness", k),
            identity=(("freshness", "account_id"), ACCOUNT_ID))
            for k in ("status", "fresh_rate", "markable", "open_positions",
                      "counts", "target_fresh_rate", "feeds")},
        "kalshi_health": declared(
            rb, "venues", ("data", "health", "KALSHI_HEALTH"),
            identity=(("data", "health", "KALSHI_HEALTH", "domain"),
                      "KALSHI_HEALTH")),
        "polymarket_health": declared(
            rb, "venues", ("data", "health", "POLYMARKET_HEALTH"),
            identity=(("data", "health", "POLYMARKET_HEALTH", "domain"),
                      "POLYMARKET_HEALTH")),
        "mirror_shadow": declared(rb, "completion",
                                  ("data", "venue_positions"),
                                  sections=("venue_positions",),
                                  identity=acct),
        "xavier_management": whole(rb, "xavier_management"),
        "frontend_viewports": whole(rb, "frontend_viewports"),
        "canary": whole(rb, "canary"),
        "memory": mem,
        "memory_identity": mem_ident,
        "log_counts": log_counts(acc, ids),
        "scoreboard": {k: declared(rb, "pm_after", ("data", "scoreboard", k),
                                   sections=())
                       for k in ("decision_rows", "independent_events",
                                 "mechanism_states", "routing_counts",
                                 "reconciliation", "ledger_reconciliation",
                                 "paper_ledger_realized_total_usd")},
        "profitability_scoreboard": whole(rb, "profitability_scoreboard"),
        "governors": declared(rb, "completion", ("data", "readiness",
                                                 "governors"),
                              identity=acct),
        "owner_blockers": declared(rb, "completion", ("data",
                                                      "owner_blockers"),
                                   sections=("runtime", "venue_positions",
                                             "market_data"),
                                   identity=acct),
        "profit_breakers": declared(rb, "red_team", C + ("PROFIT_BREAKERS",)),
        "capacity": declared(rb, "red_team", C + ("CAPACITY",),
                             sections=("capacity",)),
        "red_team": {"status": declared(rb, "red_team", R + ("status",)),
                     "blockers": declared(rb, "red_team", R + ("blockers",)),
                     "controls": {k: declared(rb, "red_team",
                                              C + (k, "status"))
                                  for k in CONTROLS}},
        "pm_acceptance": {k: declared(rb, "pm_after",
                                      ("data", "pm_acceptance", k))
                          for k in ("pm_state", "capital_status",
                                    "critical_failures",
                                    "economic_or_evidence_gaps",
                                    "fields_without_machine_evidence",
                                    "unproven")},
        "release_receipt": declared(rb, "pm_after",
                                    ("data", "release_receipt")),
        "golden": {k: declared(rb, "pm_after", ("data", "golden", k))
                   for k in ("passed", "total", "appended_passed",
                             "appended_total", "green")},
        "authority": declared(rb, "pm_after", ("data", "authority")),
    }
    if rt is None:
        packet["red_team"]["controls_read"] = rb.get("red_team")[1]
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
