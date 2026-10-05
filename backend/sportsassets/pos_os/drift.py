"""MODEL AND ECONOMIC DRIFT (RESEARCH). Pure; no I/O.

RECENT (the last RECENT_DAYS) against BASELINE (the rest of the window):

  probability_psi     population stability index of the probabilities the
                      decisions recorded (every decision with one): has the
                      model's output distribution moved? < 0.10 STABLE,
                      < 0.25 MODERATE, else MAJOR
  calibration_bias    mean(outcome - p) over settled PAPER positions (p the
                      entry decision's probability, outcome the recorded
                      WON / LOST payout): recent minus baseline with a
                      bootstrap CI; Brier scores beside it
  economic_gap        mean(realized - predicted edge per dollar) over closed
                      PAPER positions: is the money the model said was there
                      arriving at a different rate than before?
A comparison is DRIFT_DETECTED only when its CI excludes 0 (or the PSI is
MAJOR) with at least MIN_N observations on each side; otherwise NO_DRIFT_
DETECTED or INSUFFICIENT_SAMPLE. Each is computed for every sleeve pooled
(research) and for INVESTMENT alone (production confidence). It changes no
model and no threshold.
"""
from __future__ import annotations

from . import common as C

VERSION = "POS_OS_DRIFT_V1"
RECENT_DAYS = 7.0
MIN_N = 10
MIN_PSI_N = 30


def _split(rows, key, *, now):
    cut = now - RECENT_DAYS * C.DAY
    rec = [r for r in rows if (C.num(r.get(key)) or 0) >= cut]
    base = [r for r in rows if C.num(r.get(key)) is not None
            and r[key] < cut]
    return base, rec


def _compare(name, base, rec, *, min_n=MIN_N):
    out = {"metric": name, "baseline_n": len(base), "recent_n": len(rec),
           "baseline_mean": C.rnd(C.mean(base)) if base else None,
           "recent_mean": C.rnd(C.mean(rec)) if rec else None}
    if len(base) < min_n or len(rec) < min_n:
        out.update(verdict=C.INSUFFICIENT, diff_ci90=[None, None])
        return out
    lo, hi = C.bootstrap_diff_ci(rec, base, seed=C.seed_of(
        ["drift", name, len(base), len(rec)]))
    out["diff_ci90"] = [C.rnd(lo), C.rnd(hi)]
    out["verdict"] = ("DRIFT_DETECTED" if lo is not None and (lo > 0 or hi < 0)
                      else "NO_DRIFT_DETECTED")
    return out


def outcomes(settlements):
    """position_key -> 1.0 / 0.0 for live ordinary (WON / LOST)
    settlements."""
    superseded = {s.get("supersedes") for s in settlements or []
                  if s.get("supersedes")}
    out = {}
    for s in settlements or []:
        if s.get("settlement_id") in superseded:
            continue
        if s.get("outcome") == "WON":
            out[s["position_key"]] = 1.0
        elif s.get("outcome") == "LOST":
            out[s["position_key"]] = 0.0
    return out


def drift(positions, settlements, decisions, *, now):
    res = {"version": VERSION, "recent_days": RECENT_DAYS, "scopes": {}}
    ps = [{"p": C.probability_of(d), "at": d.get("decided_at"),
           "sleeve": None} for d in decisions or []]
    ps = [x for x in ps if x["p"] is not None]
    base, rec = _split(ps, "at", now=now)
    if len(base) >= MIN_PSI_N and len(rec) >= MIN_PSI_N:
        v = C.psi([x["p"] for x in base], [x["p"] for x in rec])
        res["probability_psi"] = {
            "psi": C.rnd(v), "baseline_n": len(base), "recent_n": len(rec),
            "verdict": "STABLE" if v < 0.10 else "MODERATE" if v < 0.25
            else "MAJOR"}
    else:
        res["probability_psi"] = {"psi": None, "baseline_n": len(base),
                                  "recent_n": len(rec),
                                  "verdict": C.INSUFFICIENT}
    ys = outcomes(settlements)
    closed = [p for p in positions or [] if p.get("book") == "PAPER"
              and p.get("state") != "OPEN"]
    for scope in ("ALL", C.INVESTMENT):
        mine = [p for p in closed
                if scope == "ALL" or C.sleeve_of(p) == scope]
        cal = [{"e": ys[p["position_key"]] - float(p["probability"]),
                "b": (ys[p["position_key"]] - float(p["probability"])) ** 2,
                "at": p.get("released_at")} for p in mine
               if p.get("position_key") in ys
               and C.num(p.get("probability")) is not None]
        cb, cr = _split(cal, "at", now=now)
        c = _compare("calibration_bias", [x["e"] for x in cb],
                     [x["e"] for x in cr])
        c["baseline_brier"] = C.rnd(C.mean([x["b"] for x in cb])) \
            if cb else None
        c["recent_brier"] = C.rnd(C.mean([x["b"] for x in cr])) \
            if cr else None
        gap = [{"g": float(p["realized_edge_per_dollar"])
                - float(p["predicted_edge_per_dollar"]),
                "at": p.get("released_at")} for p in mine
               if C.num(p.get("realized_edge_per_dollar")) is not None
               and C.num(p.get("predicted_edge_per_dollar")) is not None]
        gb, gr = _split(gap, "at", now=now)
        res["scopes"][scope] = {
            "calibration_bias": c,
            "economic_gap": _compare("economic_gap", [x["g"] for x in gb],
                                     [x["g"] for x in gr]),
            "confidence_scope": "PRODUCTION_CONFIDENCE"
            if scope == C.INVESTMENT else "RESEARCH_NOT_PRODUCTION_"
                                          "CONFIDENCE"}
    verdicts = [res["probability_psi"]["verdict"]] + [
        s[k]["verdict"] for s in res["scopes"].values()
        for k in ("calibration_bias", "economic_gap")]
    res["overall"] = ("DRIFT_DETECTED" if "DRIFT_DETECTED" in verdicts
                      or "MAJOR" in verdicts else
                      C.INSUFFICIENT if all(v == C.INSUFFICIENT
                                            for v in verdicts)
                      else "NO_DRIFT_DETECTED")
    res["changes"] = "NOTHING"
    return res


def build(inputs, *, now):
    miss = C.missing(inputs, ["positions", "settlements", "decisions"])
    if miss:
        return C.unread(miss, inputs, audit=C.BUILT)
    if not inputs["positions"] and not inputs["decisions"]:
        return C.section(C.EMPTY, "%s: no position or decision in the "
                         "window" % C.R_NO_ROWS, audit=C.BUILT)
    return C.section(C.OK, None, audit=C.BUILT, data=drift(
        inputs["positions"], inputs["settlements"], inputs["decisions"],
        now=now), sources=["paper_decisions", "paper_settlements",
                           "pos_economics_latest"])
