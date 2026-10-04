"""THE PROFITABILITY EVIDENCE LADDER AND PROFITABILITY CONFIDENCE. Pure.

LEVELS (predeclared, frozen as LADDER_CRITERIA v1):
  0  ARCHITECTURE_ONLY                 the layer is deployed and computed
  1  HISTORICAL_BACKTEST               the RECORDED replay scored >= 100
                                       settled PAPER decisions carrying a
                                       probability, and their calibration
                                       is measured
  2  FORWARD_PAPER                     >= 30 independent PAPER positions
                                       closed, opened on or after the
                                       quality scorecard's forward_start,
                                       over >= 7 calendar days
  3  SMALL_LIVE_EXECUTION              >= 10 ACTUAL (real venue) positions
                                       settled with realized P&L and the
                                       execution tracking error measured on
                                       >= 10
  4  CREDIBLE_SMALL_LIVE_NET_EDGE      the predeclared forward sample rule
                                       (BETTOR_FORWARD_SAMPLE_V1: >= 300
                                       independent ACTUAL positions, >= 30
                                       days, one-sided 95% lower bound of
                                       mean net P&L > 0) is SUPPORTED
  5  SCALED_LIVE                       >= 300 independent forward ACTUAL
                                       positions with median entry cost
                                       >= $100 and a positive one-sided
                                       lower bound
  6  PERSISTENT_MULTI_REGIME_MULTI_SPORT  a positive one-sided lower bound
                                       in each of >= 3 sports and >= 2
                                       regime states (>= 100 positions each)

THE LEVEL is the highest L such that EVERY level 0..L passes -- a level is
never counted when a lower one fails (and the database CHECKs it).

PROFITABILITY CONFIDENCE is computed from measurable evidence only --
sample size, realized edge, its uncertainty (CI, one-sided lower bound,
one-sided p-value), calibration, drawdown, regime and sport coverage,
execution tracking error -- on the ACTUAL forward sample. Its status is
UNPROVEN unless the ladder is at level 4 or above (LOW at 4, MODERATE at 5,
HIGH at 6, by rule). It is never a subjective percentage.
"""
from __future__ import annotations

import datetime as _dt
import math
import statistics

from . import common as C

VERSION = "TWIN_LADDER_V1"
LEVELS = (
    {"level": 0, "name": "ARCHITECTURE_ONLY",
     "criterion": "the twin layer is deployed and this ladder was computed"},
    {"level": 1, "name": "HISTORICAL_BACKTEST", "min_scored": 100,
     "criterion": ("the RECORDED replay (PAPER basis) scored >= 100 settled "
                   "decisions carrying a probability, and their Brier is "
                   "measured")},
    {"level": 2, "name": "FORWARD_PAPER", "min_positions": 30, "min_days": 7,
     "criterion": ("BETTOR_FORWARD_SAMPLE_V1's population and independence "
                   "on PAPER: >= 30 independent closed positions opened on "
                   "or after forward_start, over >= 7 calendar days")},
    {"level": 3, "name": "SMALL_LIVE_EXECUTION", "min_positions": 10,
     "criterion": (">= 10 ACTUAL positions settled with realized P&L and "
                   "execution tracking error measured on >= 10")},
    {"level": 4, "name": "CREDIBLE_SMALL_LIVE_NET_EDGE",
     "criterion": ("BETTOR_FORWARD_SAMPLE_V1 on ACTUAL closed positions is "
                   "SUPPORTED_BY_FORWARD_SAMPLE")},
    {"level": 5, "name": "SCALED_LIVE", "min_positions": 300,
     "min_median_cost_usd": 100.0,
     "criterion": (">= 300 independent forward ACTUAL positions with median "
                   "entry cost >= $100 and one-sided 95% lower bound of mean "
                   "net P&L > 0")},
    {"level": 6, "name": "PERSISTENT_MULTI_REGIME_MULTI_SPORT",
     "min_sports": 3, "min_regimes": 2, "min_per_group": 100,
     "criterion": ("one-sided 95% lower bound of mean net ACTUAL P&L > 0 in "
                   "each of >= 3 sports and >= 2 regime states, >= 100 "
                   "independent positions each")},
)
LADDER_SPEC = {"version": 1, "levels": list(LEVELS),
               "rule": ("level = the highest L such that every level 0..L "
                        "passes; a level never counts when a lower one "
                        "fails"),
               "forward_rule": C.FORWARD_SAMPLE_RULE["id"],
               "label": C.LABEL}
CONFIDENCE_SPEC = {
    "version": 1,
    "default": "UNPROVEN",
    "status_rule": ("UNPROVEN unless evidence level >= 4; LOW at level 4, "
                    "MODERATE at level 5, HIGH at level 6"),
    "components": ["sample_size", "realized_edge", "uncertainty",
                   "calibration", "drawdown", "regime_coverage",
                   "sport_coverage", "execution_tracking_error"],
    "min_group_n": 30,
    "never": "a subjective or stated percentage confidence",
    "label": C.LABEL}
Z1 = 1.645


def _independent_forward(rows: list, start: float) -> list:
    """One closed position per fixture (the first to close), opened on or
    after `start` -- the forward rule's population and independence."""
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: (x.get("closed_at") or 0,
                                         str(x.get("key")))):
        if (r.get("opened_at") or 0) < start or r.get("closed_at") is None:
            continue
        fx = r.get("fixture") or r.get("us_market_slug")
        if fx in seen:
            continue
        seen.add(fx)
        out.append(r)
    return out


def _lower(xs: list):
    if len(xs) < 2:
        return None
    return statistics.fmean(xs) - Z1 * statistics.stdev(xs) / math.sqrt(
        len(xs))


def _p_one_sided(xs: list):
    if len(xs) < 2 or statistics.stdev(xs) == 0:
        return None
    zz = statistics.fmean(xs) / (statistics.stdev(xs) / math.sqrt(len(xs)))
    return 0.5 * math.erfc(zz / math.sqrt(2.0))


def closed_rows(positions: list) -> list:
    """Postmortem-shaped rows from twin positions (fallback when
    position_postmortems is absent)."""
    return [{"key": p["group_id"], "opened_at": p["first_fill_at"],
             "closed_at": p["close_at"], "fixture": p["fixture"],
             "us_market_slug": p["slug"],
             "realized_pnl_usd": p["realized_pnl_usd"],
             "cost_usd": p["cost_usd"], "sport": p["sport"],
             "decided_at": p["decided_at"], "p": p["p"],
             "payoff": p["payoff"], "slippage_pc": p["slippage_pc"]}
            for p in positions if p["realized_pnl_usd"] is not None
            and p["close_at"] is not None]


def regime_at(regimes: list, t: float):
    cur = None
    for r in regimes:
        if r["at"] <= t:
            cur = r["recommendation"]
        else:
            break
    return cur


def historical(paper_positions: list) -> dict:
    """Level 1's evidence from the RECORDED replay's PAPER positions."""
    done = [p for p in paper_positions if p["realized_pnl_usd"] is not None
            and p["p"] is not None]
    return {"scored": len(done),
            "brier_n": sum(1 for p in done if p["payoff"] in (0.0, 1.0))}


def compute(*, history: dict, paper_closed: list,
            actual_closed: list, actual_positions: list, regimes: list,
            now: float) -> dict:
    start = C.epoch(_iso(C.FORWARD_SAMPLE_RULE["forward_start"]))
    days = max(0.0, (now - start) / 86400.0)
    levels = []

    def add(lv, passed, evidence, why=None):
        levels.append({"level": lv["level"], "name": lv["name"],
                       "criterion": lv["criterion"], "passed": bool(passed),
                       "evidence": evidence, "why_not": None if passed
                       else why})

    add(LEVELS[0], True, {"computed_at": now})
    scored, brier_n = int(history["scored"]), int(history["brier_n"])
    add(LEVELS[1], scored >= 100 and brier_n > 0,
        {"recorded_scored": scored, "brier_n": brier_n},
        "%d of 100 settled recorded PAPER decisions scored%s" % (
            scored, "" if brier_n else "; calibration not measured"))
    pf = _independent_forward(paper_closed, start)
    add(LEVELS[2], len(pf) >= 30 and days >= 7,
        {"independent_forward_paper": len(pf), "calendar_days": C.rnd(days, 2),
         "forward_start": C.FORWARD_SAMPLE_RULE["forward_start"]},
        "%d of 30 independent forward PAPER positions; %.1f of 7 days"
        % (len(pf), days))
    te = [p["slippage_pc"] for p in actual_positions
          if p.get("slippage_pc") is not None]
    add(LEVELS[3], len(actual_closed) >= 10 and len(te) >= 10,
        {"actual_closed": len(actual_closed), "tracking_error_n": len(te)},
        "%d of 10 settled ACTUAL positions; tracking error on %d of 10"
        % (len(actual_closed), len(te)))
    fv = C.forward_verdict([dict(r, realized_pnl_usd=float(
        r["realized_pnl_usd"])) for r in actual_closed], now=now)
    add(LEVELS[4], fv["verdict"] == "SUPPORTED_BY_FORWARD_SAMPLE",
        fv, "forward verdict %s (%d of %d positions, %.1f of %d days)" % (
            fv["verdict"], fv["positions"], fv["required_positions"],
            fv["calendar_days"], fv["required_days"]))
    af = _independent_forward(actual_closed, start)
    pnl = [float(r["realized_pnl_usd"]) for r in af]
    costs = [r["cost_usd"] for r in af if r.get("cost_usd") is not None]
    med = statistics.median(costs) if costs else None
    low = _lower(pnl)
    add(LEVELS[5], len(af) >= 300 and med is not None and med >= 100
        and low is not None and low > 0,
        {"independent_forward_actual": len(af),
         "median_entry_cost_usd": C.rnd(med), "one_sided_lower_95": C.rnd(
             low)},
        "%d of 300 positions; median cost %s of $100; lower bound %s" % (
            len(af), C.rnd(med), C.rnd(low)))
    groups_s, groups_r = {}, {}
    for r in af:
        groups_s.setdefault(r.get("sport") or "unknown", []).append(
            float(r["realized_pnl_usd"]))
        rg = regime_at(regimes, r.get("decided_at") or 0) or "UNKNOWN"
        groups_r.setdefault(rg, []).append(float(r["realized_pnl_usd"]))
    ok_s = [s for s, v in groups_s.items() if len(v) >= 100
            and (_lower(v) or -1) > 0]
    ok_r = [g for g, v in groups_r.items() if g != "UNKNOWN"
            and len(v) >= 100 and (_lower(v) or -1) > 0]
    add(LEVELS[6], len(ok_s) >= 3 and len(ok_r) >= 2,
        {"sports_passing": sorted(ok_s), "regimes_passing": sorted(ok_r)},
        "%d of 3 sports and %d of 2 regimes with a positive lower bound on "
        ">= 100 positions" % (len(ok_s), len(ok_r)))
    passes = [lv["passed"] for lv in levels]
    level = -1
    for p in passes:
        if not p:
            break
        level += 1
    conf = confidence(level=level, af=af, groups_s=groups_s,
                      groups_r=groups_r, tracking=te)
    return {"level": level, "level_name": LEVELS[level]["name"],
            "passes": passes, "levels": levels, "confidence": conf,
            "skipped_levels": False}


def confidence(*, level, af, groups_s, groups_r, tracking) -> dict:
    pnl = [float(r["realized_pnl_usd"]) for r in af]
    out = C.Out(status="UNPROVEN", evidence_level=level,
                rule=CONFIDENCE_SPEC["status_rule"], book="ACTUAL",
                never_a_percentage=True)
    if level >= 4:
        out["status"] = {4: "LOW", 5: "MODERATE", 6: "HIGH"}[level]
    out["sample_size"] = len(pnl)
    if pnl:
        out.put("realized_edge_usd_per_position", C.rnd(statistics.fmean(
            pnl)))
        cost = sum(r["cost_usd"] for r in af if r.get("cost_usd"))
        out.put("realized_return_on_cost",
                C.rnd(sum(pnl) / cost) if cost else None,
                "NO_ENTRY_COST_RECORDED")
    else:
        out.put("realized_edge_usd_per_position", None,
                "NO_INDEPENDENT_FORWARD_ACTUAL_POSITION")
        out.put("realized_return_on_cost", None,
                "NO_INDEPENDENT_FORWARD_ACTUAL_POSITION")
    ci = C.mean_ci(pnl)
    out["uncertainty"] = {
        "ci95": ci, "one_sided_lower_95": C.rnd(_lower(pnl)),
        "p_value_one_sided_mean_le_0": C.rnd(_p_one_sided(pnl)),
        "method": "normal approximation on independent forward positions",
        "why": None if ci else "FEWER_THAN_2_POSITIONS"}
    cal = [(r["p"] - r["payoff"]) ** 2 for r in af
           if r.get("p") is not None and r.get("payoff") in (0.0, 1.0)]
    gap = [r["payoff"] - r["p"] for r in af
           if r.get("p") is not None and r.get("payoff") in (0.0, 1.0)]
    out.put("calibration_brier", C.rnd(statistics.fmean(cal)) if cal
            else None, "NO_ORDINARY_SETTLEMENT_WITH_A_PROBABILITY")
    out.put("calibration_gap_mean_o_minus_p", C.rnd(statistics.fmean(gap))
            if gap else None, "NO_ORDINARY_SETTLEMENT_WITH_A_PROBABILITY")
    ordered = [float(r["realized_pnl_usd"]) for r in sorted(
        af, key=lambda r: (r.get("closed_at") or 0, str(r.get("key"))))]
    out.put("max_drawdown_usd", C.max_drawdown(ordered),
            "NO_INDEPENDENT_FORWARD_ACTUAL_POSITION")
    mn = CONFIDENCE_SPEC["min_group_n"]
    out["regime_coverage"] = {
        "covered": sorted(g for g, v in groups_r.items()
                          if g != "UNKNOWN" and len(v) >= mn),
        "counts": {g: len(v) for g, v in sorted(groups_r.items())},
        "min_n": mn}
    out["sport_coverage"] = {
        "covered": sorted(s for s, v in groups_s.items() if len(v) >= mn),
        "counts": {s: len(v) for s, v in sorted(groups_s.items())},
        "min_n": mn}
    out.put("execution_tracking_error_pc",
            C.rnd(statistics.fmean([abs(x) for x in tracking]))
            if tracking else None, "NO_ACTUAL_FILL_AGAINST_A_PLANNED_PRICE")
    out["statement"] = (
        "%s: evidence level %d of 6 (%s); %d independent forward ACTUAL "
        "positions" % (out["status"], level, LEVELS[level]["name"],
                       len(pnl)))
    return out


def _iso(s: str):
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
