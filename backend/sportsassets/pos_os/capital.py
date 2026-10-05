"""THE CAPITAL-HOUR OPTIMIZER (RECOMMENDATION ONLY) AND THE EXPECTED-PROFIT
CLOCK (RESEARCH). Pure; no I/O.

THE OPTIMIZER. Over the window's CLOSED PAPER positions, per strategy:
realized profit per capital-hour = sum(net) / sum(capital-hours) with a
bootstrap CI over positions. The recommendation reallocates CAPITAL-HOURS
(not dollars, not caps) in proportion to each INVESTMENT strategy's LOWER
bound when that bound is > 0 and the strategy has MIN_POSITIONS closed
positions; a strategy whose UPPER bound is < 0 is recommended to receive
fewer capital-hours; everything else HOLD_INSUFFICIENT_EVIDENCE. TRAINING /
BENCHMARK / UNCLASSIFIED strategies are shown, never allocated to
(research, not production confidence). The output is a number on a page:
`applied: false`, `authority: RECOMMENDATION_ONLY`. Nothing reads it to set
a cap, a size, an allowlist or a gate.

THE EXPECTED-PROFIT CLOCK. From the OPEN PAPER book now, per position with
a decision probability p, bought quantity q and expected net profit E
(pos economics: q x p - acquisition cost) and an expected release:
  accrual rate      sum(E_i / hours to expected release_i): $ expected per
                    hour if each position's expected profit accrued evenly
                    to its release
  released within H expected $ released by now + H (positions whose
                    expected release falls inside), 90% band from the
                    binary payoff variance q^2 p (1 - p), positions assumed
                    independent (stated -- correlation is in the
                    correlation section)
  trailing realized realized net $ / window hours over the last 24 h and
                    7 d (closed positions released inside)
  honesty           sum(realized net) / sum(expected net) over closed
                    positions with both: how far the expectations held
A position without p, E or an expected release is counted as unmeasured
with its cost basis -- never a zero contribution. EXPECTED figures use the
decision's probability and are UNPROVEN until the honesty ratio says so.
"""
from __future__ import annotations

import math

from . import common as C

VERSION = "POS_OS_CAPITAL_V1"
MIN_POSITIONS = 10
Z90 = 1.645
HORIZONS_H = (1.0, 24.0, 168.0)
PROD = C.INVESTMENT


def _closed(positions):
    return [p for p in positions or [] if p.get("book") == "PAPER"
            and p.get("state") != "OPEN"
            and C.num(p.get("net_profit_usd")) is not None
            and (C.num(p.get("capital_hours")) or 0.0) > 0]


def optimizer(positions, *, min_n=MIN_POSITIONS):
    by: dict = {}
    for p in _closed(positions):
        s = str(p.get("strategy") or "UNKNOWN")
        e = by.setdefault(s, {"sleeve": C.sleeve_of(p), "pairs": []})
        e["pairs"].append((float(p["net_profit_usd"]),
                           float(p["capital_hours"])))
    tot_ch = sum(ch for e in by.values() for _, ch in e["pairs"])
    rows = []
    for s, e in sorted(by.items()):
        net = sum(a for a, _ in e["pairs"])
        ch = sum(b for _, b in e["pairs"])
        lo, hi = C.bootstrap_ratio_ci(e["pairs"], seed=C.seed_of(
            ["caph", s, len(e["pairs"])]))
        rows.append({"strategy": s, "sleeve": e["sleeve"],
                     "closed_positions": len(e["pairs"]),
                     "capital_hours": C.rnd(ch),
                     "realized_net_usd": C.rnd(net),
                     "realized_profit_per_capital_hour": C.rnd(net / ch, 9),
                     "ci90": [C.rnd(lo, 9), C.rnd(hi, 9)],
                     "current_capital_hour_share": C.share(ch, tot_ch)})
    weights = {}
    for r in rows:
        lo, hi = r["ci90"]
        if r["sleeve"] != PROD:
            r["recommendation"] = "NOT_ALLOCATED_RESEARCH_SLEEVE"
        elif r["closed_positions"] < min_n or lo is None:
            r["recommendation"] = "HOLD_INSUFFICIENT_EVIDENCE"
        elif lo > 0:
            r["recommendation"] = "FAVOUR"
            weights[r["strategy"]] = lo
        elif hi is not None and hi < 0:
            r["recommendation"] = "REDUCE_CAPITAL_HOURS"
        else:
            r["recommendation"] = "HOLD_NOT_PROVEN"
    wsum = sum(weights.values())
    prod_share = sum(r["current_capital_hour_share"] or 0.0 for r in rows
                     if r["sleeve"] == PROD)
    for r in rows:
        if r["strategy"] in weights:
            r["recommended_capital_hour_share"] = C.rnd(
                prod_share * weights[r["strategy"]] / wsum)
        else:
            r["recommended_capital_hour_share"] = None
    return {"version": VERSION, "strategies": rows, "min_positions": min_n,
            "applied": False, "authority": "RECOMMENDATION_ONLY",
            "changes": "NOTHING: no cap, size, allowlist or gate reads this",
            "allocates": "capital-hours share among INVESTMENT strategies, "
                         "in proportion to the 90% lower bound"}


def clock(positions, *, now):
    open_ = [p for p in positions or [] if p.get("book") == "PAPER"
             and p.get("state") == "OPEN"]
    meas, unmeas = [], []
    for p in open_:
        e, q = C.num(p.get("expected_net_profit_usd")), C.num(
            p.get("bought_qty"))
        pr, rel = C.num(p.get("probability")), C.num(
            p.get("expected_release_at"))
        why = (None if e is not None else "NO_EXPECTED_NET_PROFIT") or (
            None if pr is not None and 0 < pr < 1 else "NO_PROBABILITY") or (
            None if q else "NO_BOUGHT_QTY") or (
            None if rel is not None else "NO_EXPECTED_RELEASE")
        if why:
            unmeas.append({"position_key": p.get("position_key"), "why": why,
                           "open_cost_basis_usd": C.num(
                               p.get("open_cost_basis_usd"))})
            continue
        meas.append({"e": e, "var": q * q * pr * (1 - pr), "rel": rel,
                     "sleeve": C.sleeve_of(p)})
    out = {"version": VERSION, "open_positions": len(open_),
           "measured_positions": len(meas),
           "unmeasured_positions": len(unmeas),
           "unmeasured_cost_basis_usd": C.rnd(sum(
               u["open_cost_basis_usd"] or 0.0 for u in unmeas)),
           "unmeasured": unmeas[:25], "by_sleeve": {},
           "label": "EXPECTED_FROM_DECISION_PROBABILITY_UNPROVEN",
           "independence_assumed": True}
    for sl in C.SLEEVES:
        mine = [m for m in meas if m["sleeve"] == sl]
        rate = sum(m["e"] / max((m["rel"] - now) / C.HOUR, 1.0)
                   for m in mine if m["rel"] > now)
        overdue = [m for m in mine if m["rel"] <= now]
        hz = {}
        for h in HORIZONS_H:
            inside = [m for m in mine if m["rel"] <= now + h * C.HOUR]
            ev = sum(m["e"] for m in inside)
            sd = math.sqrt(sum(m["var"] for m in inside))
            hz["%dh" % h] = {"positions": len(inside),
                             "expected_usd": C.rnd(ev),
                             "band90_usd": [C.rnd(ev - Z90 * sd),
                                            C.rnd(ev + Z90 * sd)],
                             "expected_usd_per_hour": C.rnd(ev / h)}
        out["by_sleeve"][sl] = {
            "positions": len(mine),
            "accrual_usd_per_hour": C.rnd(rate) if mine else None,
            "overdue_positions": len(overdue),
            "expected_usd_overdue": C.rnd(sum(m["e"] for m in overdue)),
            "released_within": hz,
            "confidence_scope": "PRODUCTION_CONFIDENCE" if sl == PROD
            else "RESEARCH_NOT_PRODUCTION_CONFIDENCE"}
    closed = _closed(positions)
    trail = {}
    for h in (24.0, 168.0):
        xs = [float(p["net_profit_usd"]) for p in closed
              if (C.num(p.get("released_at")) or 0) >= now - h * C.HOUR]
        trail["%dh" % h] = {"positions": len(xs),
                            "realized_usd": C.rnd(sum(xs)),
                            "realized_usd_per_hour": C.rnd(sum(xs) / h)}
    out["trailing_realized"] = trail
    both = [p for p in closed
            if C.num(p.get("expected_net_profit_usd")) is not None]
    se = sum(float(p["expected_net_profit_usd"]) for p in both)
    out["honesty"] = {
        "positions": len(both),
        "realized_over_expected": C.rnd(sum(float(p["net_profit_usd"])
                                            for p in both) / se)
        if both and abs(se) > 1e-9 else None,
        "why": None if both and abs(se) > 1e-9
        else "NO_CLOSED_POSITION_WITH_AN_EXPECTATION"}
    return out


def build_optimizer(inputs, *, now):
    if inputs.get("positions") is None:
        return C.unread(["positions"], inputs, audit=C.BUILT,
                        sources=["pos_economics_latest"])
    d = optimizer(inputs["positions"])
    return C.section(C.OK if d["strategies"] else C.EMPTY,
                     None if d["strategies"] else
                     "%s: no closed PAPER position with capital-hours"
                     % C.R_NO_ROWS, audit=C.BUILT, data=d,
                     sources=["pos_economics_latest"])


def build_clock(inputs, *, now):
    if inputs.get("positions") is None:
        return C.unread(["positions"], inputs, audit=C.BUILT,
                        sources=["pos_economics_latest"])
    d = clock(inputs["positions"], now=now)
    has = d["open_positions"] or d["honesty"]["positions"] or any(
        t["positions"] for t in d["trailing_realized"].values())
    return C.section(C.OK if has else C.EMPTY,
                     None if has else "%s: no open or recently closed PAPER "
                     "position" % C.R_NO_ROWS, audit=C.BUILT, data=d,
                     sources=["pos_economics_latest"])
