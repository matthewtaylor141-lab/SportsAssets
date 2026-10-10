"""AUTONOMY-HEALTH METRICS (RESEARCH). Pure; no I/O.

Is the machine deciding, and deciding for economic rather than software
reasons?

  loops        the runtime loop verdicts (sportsassets/loop_health.py, its
               own reader): counts by verdict, alive share among the loops
               that should be running (DISABLED and EVENT_DRIVEN excluded),
               the stale (UNHEALTHY) and DEGRADED shares beside it, the
               capital-critical loops not healthy
  decisions    decisions and entries per hour over the last 1 h and 24 h,
               the strategies that decided in 24 h, the age of the newest
               decision and the longest silence between decisions in 24 h
  refusal mix  the 24 h refusal codes classified by the refusal taxonomy:
               share SOFTWARE / ECONOMIC / UNCLASSIFIED, the stale rate
               (share whose family is FRESHNESS_PLUMBING) and the top codes
An input that was not read leaves its block UNAVAILABLE, never zero.
"""
from __future__ import annotations

from . import common as C

VERSION = "POS_OS_AUTONOMY_V1"
NOT_EXPECTED = ("DISABLED", "EVENT_DRIVEN")
TOP = 10


def loops_block(lh):
    if lh is None:
        return {"status": C.UNAVAILABLE, "why": C.R_INPUT_NOT_READ}
    if not lh:
        return {"status": C.EMPTY, "why": "NO_LOOP_HEALTH"}
    got = lh[0] or {}
    loops = got.get("loops") or []
    expected = [lp for lp in loops if lp.get("status") not in NOT_EXPECTED]
    healthy = [lp for lp in expected if lp.get("status") == "HEALTHY"]
    unhealthy = [lp for lp in expected if lp.get("status") == "UNHEALTHY"]
    # (RC6.2 D6g) running, but its newest run's phases errored: neither
    # alive (healthy) nor stale, so it has its own share
    degraded = [lp for lp in expected if lp.get("status") == "DEGRADED"]
    return {"status": C.OK if loops else C.EMPTY,
            "why": None if loops else "NO_LOOP_IN_INVENTORY",
            "summary": got.get("summary"), "loops": len(loops),
            "expected_running": len(expected),
            "alive_share": C.share(len(healthy), len(expected)),
            "stale_share": C.share(len(unhealthy), len(expected)),
            "degraded_share": C.share(len(degraded), len(expected)),
            "capital_critical_not_healthy":
                got.get("capital_critical_not_healthy") or [],
            "sources_missing": got.get("sources_missing") or []}


def decisions_block(decisions, *, now):
    if decisions is None:
        return {"status": C.UNAVAILABLE, "why": C.R_INPUT_NOT_READ}
    ats = sorted(C.num(d.get("decided_at")) for d in decisions
                 if C.num(d.get("decided_at")) is not None)
    out = {"status": C.OK if ats else C.EMPTY,
           "why": None if ats else "%s: no decision in the window"
           % C.R_NO_ROWS}
    for h in (1.0, 24.0):
        mine = [d for d in decisions
                if (C.num(d.get("decided_at")) or 0) >= now - h * C.HOUR]
        out["last_%dh" % h] = {
            "decisions": len(mine),
            "decisions_per_hour": C.rnd(len(mine) / h),
            "enters": sum(1 for d in mine if d.get("verdict") == "ENTER"),
            "enters_per_hour": C.rnd(sum(1 for d in mine
                                         if d.get("verdict") == "ENTER") / h)}
    day = [a for a in ats if a >= now - 24 * C.HOUR]
    out["strategies_deciding_24h"] = sorted({
        str(d.get("strategy")) for d in decisions
        if (C.num(d.get("decided_at")) or 0) >= now - 24 * C.HOUR})
    out["newest_decision_age_s"] = C.rnd(now - ats[-1], 1) if ats else None
    gaps = [b - a for a, b in zip(day, day[1:])]
    if day:
        gaps.append(now - day[-1])
    out["longest_silence_24h_s"] = C.rnd(max(gaps), 1) if gaps else None
    return out


def refusal_block(decisions, *, now):
    if decisions is None:
        return {"status": C.UNAVAILABLE, "why": C.R_INPUT_NOT_READ}
    from .. import refusal_taxonomy as RT
    codes = []
    for d in decisions:
        if (C.num(d.get("decided_at")) or 0) < now - 24 * C.HOUR:
            continue
        if d.get("verdict") == "ENTER":
            continue
        c = d.get("refusal") or (d.get("refusals") or [None])[0]
        if c:
            codes.append(str(c))
    if not codes:
        return {"status": C.EMPTY, "why": "NO_REFUSAL_IN_24H", "refusals": 0}
    by_class: dict = {}
    fresh = 0
    counts: dict = {}
    for c in codes:
        k = RT.classify(c)
        by_class[k["class"]] = by_class.get(k["class"], 0) + 1
        if k.get("family") == "FRESHNESS_PLUMBING":
            fresh += 1
        e = counts.setdefault(c, {"code": c, "n": 0, "class": k["class"],
                                  "family": k.get("family")})
        e["n"] += 1
    return {"status": C.OK, "refusals": len(codes),
            "share_by_class": {k: C.share(v, len(codes))
                               for k, v in sorted(by_class.items())},
            "stale_rate": C.share(fresh, len(codes)),
            "top_codes": sorted(counts.values(),
                                key=lambda e: -e["n"])[:TOP]}


def build(inputs, *, now):
    lb = loops_block(inputs.get("loops"))
    db = decisions_block(inputs.get("decisions"), now=now)
    rb = refusal_block(inputs.get("decisions"), now=now)
    blocks = (lb, db, rb)
    if all(b["status"] == C.UNAVAILABLE for b in blocks):
        return C.unread(["loops", "decisions"], inputs, audit=C.BUILT)
    st = C.OK if any(b["status"] == C.OK for b in blocks) else C.EMPTY
    return C.section(st, None if st == C.OK else "%s: no loop, decision or "
                     "refusal recorded" % C.R_NO_ROWS, audit=C.BUILT,
                     data={"version": VERSION, "loops": lb, "decisions": db,
                           "refusal_mix": rb},
                     sources=["runtime_loop_health", "service_heartbeats",
                              "paper_decisions"],
                     existing={"loop_verdicts": "sportsassets/loop_health.py"})
