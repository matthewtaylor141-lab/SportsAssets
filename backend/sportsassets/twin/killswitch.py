"""ECONOMIC KILL SWITCHES (RESEARCH POLICY). Pure.

Frozen criteria (KILL_SWITCH_CRITERIA v1), evaluated per book and strategy
on the recorded positions of the window (PAPER and ACTUAL separately):

  REALIZED_EDGE_BELOW_PREDICTED  n >= 30 settled positions carrying a
                                 prediction and the 95% CI of the paired
                                 (realized - predicted) edge per contract
                                 lies entirely below 0
  CALIBRATION_FAILURE            n >= 50 ordinary settlements and the 95%
                                 CI of mean(o - p) excludes 0 with
                                 |mean| >= 0.05
  EXECUTION_LOSS_CONSUMES_EDGE   n >= 30, mean predicted edge > 0 and the
                                 95% CI of (predicted edge - slippage -
                                 fee) per contract lies at or below 0
  STRATEGY_DRAWDOWN              the cumulative realized P&L's drawdown is
                                 at or beyond the frozen threshold
                                 (PAPER $25,000 = 5% of the $500,000 paper
                                 account; ACTUAL $100 = 10% of the $1,000
                                 sleeve)
  CHALLENGER_BEATS_CHAMPION      a tournament verdict (pos_iface_tournament_
                                 verdicts, 218) CHALLENGER_BEATS_CHAMPION on
                                 >= 100 observations with p <= 0.05
  REGIME_SHIFT                   the latest intel regime state is NO_TRADE,
                                 or the last 3 are all not NORMAL

A TRIGGERED criterion produces ONE thing: a RECOMMEND_PAUSE record in
twin_kill_switch_recommendations (applied = false, stops_capital = false,
activates_capital = false, authority RESEARCH_NO_AUTHORITY -- CHECKed).
Nothing here reads or writes a control, limit, switch or order table.
"""
from __future__ import annotations

from . import common as C

VERSION = "TWIN_KILLSWITCH_V1"
CRITERIA = ("REALIZED_EDGE_BELOW_PREDICTED", "CALIBRATION_FAILURE",
            "EXECUTION_LOSS_CONSUMES_EDGE", "STRATEGY_DRAWDOWN",
            "CHALLENGER_BEATS_CHAMPION", "REGIME_SHIFT")
SPEC = {
    "version": 1,
    "REALIZED_EDGE_BELOW_PREDICTED": {"min_n": 30, "ci": 0.95},
    "CALIBRATION_FAILURE": {"min_n": 50, "ci": 0.95, "min_abs_gap": 0.05},
    "EXECUTION_LOSS_CONSUMES_EDGE": {"min_n": 30, "ci": 0.95},
    "STRATEGY_DRAWDOWN": {"threshold_usd": {"PAPER": 25000.0,
                                            "ACTUAL": 100.0}},
    "CHALLENGER_BEATS_CHAMPION": {"min_evidence_n": 100, "max_p": 0.05,
                                  "verdict": "CHALLENGER_BEATS_CHAMPION"},
    "REGIME_SHIFT": {"consecutive_not_normal": 3, "immediate": "NO_TRADE"},
    "effect": "RECOMMEND_PAUSE record only; never stops or activates capital",
    "label": C.LABEL}


def _ev(criterion, book, strategy, status, evidence, why=None) -> dict:
    return {"criterion": criterion, "book": book, "strategy": strategy,
            "status": status, "evidence": evidence, "why": why,
            "recommendation": ("RECOMMEND_PAUSE" if status == "TRIGGERED"
                               else None)}


def _strategy_groups(positions: list) -> dict:
    out: dict = {}
    for p in positions:
        out.setdefault(p.get("strategy") or "UNKNOWN", []).append(p)
    return out


def position_criteria(book: str, positions: list) -> list:
    out = []
    for strat, ps in sorted(_strategy_groups(positions).items()):
        done = [p for p in ps if p["realized_pnl_usd"] is not None
                and p["q"] > 0]
        gap = [p["realized_pnl_usd"] / p["q"] - p["model_edge_pc"]
               for p in done if p["model_edge_pc"] is not None]
        c = SPEC["REALIZED_EDGE_BELOW_PREDICTED"]
        ci = C.mean_ci(gap)
        ev = {"n": len(gap), "mean_gap": C.rnd(C.mean(gap)), "ci": ci}
        if len(gap) < c["min_n"] or ci is None:
            out.append(_ev("REALIZED_EDGE_BELOW_PREDICTED", book, strat,
                           "INSUFFICIENT_SAMPLE", ev,
                           "%d of %d settled positions" % (len(gap),
                                                           c["min_n"])))
        else:
            out.append(_ev("REALIZED_EDGE_BELOW_PREDICTED", book, strat,
                           "TRIGGERED" if ci["high"] < 0 else
                           "NOT_TRIGGERED", ev))
        cal = [p["payoff"] - p["p"] for p in done if p["p"] is not None
               and p["payoff"] in (0.0, 1.0)]
        c = SPEC["CALIBRATION_FAILURE"]
        ci = C.mean_ci(cal)
        m = C.mean(cal)
        ev = {"n": len(cal), "mean_o_minus_p": C.rnd(m), "ci": ci}
        if len(cal) < c["min_n"] or ci is None:
            out.append(_ev("CALIBRATION_FAILURE", book, strat,
                           "INSUFFICIENT_SAMPLE", ev,
                           "%d of %d ordinary settlements" % (len(cal),
                                                              c["min_n"])))
        else:
            hit = (ci["low"] > 0 or ci["high"] < 0) and abs(m) >= c[
                "min_abs_gap"]
            out.append(_ev("CALIBRATION_FAILURE", book, strat,
                           "TRIGGERED" if hit else "NOT_TRIGGERED", ev))
        ex = [(p["model_edge_pc"], p["model_edge_pc"] - p["slippage_pc"]
               - (p["fee_pc"] or 0.0)) for p in ps
              if p["model_edge_pc"] is not None
              and p["slippage_pc"] is not None]
        c = SPEC["EXECUTION_LOSS_CONSUMES_EDGE"]
        net = [b for _, b in ex]
        ci = C.mean_ci(net)
        pred = C.mean([a for a, _ in ex])
        ev = {"n": len(ex), "mean_predicted_edge_pc": C.rnd(pred),
              "mean_edge_after_execution_pc": C.rnd(C.mean(net)), "ci": ci}
        if len(ex) < c["min_n"] or ci is None:
            out.append(_ev("EXECUTION_LOSS_CONSUMES_EDGE", book, strat,
                           "INSUFFICIENT_SAMPLE", ev,
                           "%d of %d filled positions" % (len(ex),
                                                          c["min_n"])))
        else:
            out.append(_ev("EXECUTION_LOSS_CONSUMES_EDGE", book, strat,
                           "TRIGGERED" if (pred > 0 and ci["high"] <= 0)
                           else "NOT_TRIGGERED", ev))
        thr = SPEC["STRATEGY_DRAWDOWN"]["threshold_usd"][book]
        ordered = [p["realized_pnl_usd"] for p in sorted(
            done, key=lambda p: (p["close_at"] or 0, p["group_id"]))]
        dd = C.max_drawdown(ordered)
        ev = {"n": len(ordered), "max_drawdown_usd": dd,
              "threshold_usd": thr}
        if dd is None:
            out.append(_ev("STRATEGY_DRAWDOWN", book, strat, "UNAVAILABLE",
                           ev, "NO_SETTLED_POSITION"))
        else:
            out.append(_ev("STRATEGY_DRAWDOWN", book, strat,
                           "TRIGGERED" if dd <= -thr else "NOT_TRIGGERED",
                           ev))
    return out


def tournament_criteria(rows, why) -> list:
    c = SPEC["CHALLENGER_BEATS_CHAMPION"]
    if rows is None:
        return [_ev("CHALLENGER_BEATS_CHAMPION", "NONE", "*", "UNAVAILABLE",
                    {}, why)]
    out = []
    for r in rows:
        n, pv = C.num(r.get("evidence_n")), C.num(r.get("p_value"))
        ev = {"tournament_id": r.get("tournament_id"), "kind": r.get("kind"),
              "champion": r.get("champion"),
              "challenger": r.get("challenger"), "verdict": r.get("verdict"),
              "evidence_n": n, "p_value": pv, "decided_at": r.get("at")}
        hit = (r.get("verdict") == c["verdict"] and n is not None
               and n >= c["min_evidence_n"] and pv is not None
               and pv <= c["max_p"])
        out.append(_ev("CHALLENGER_BEATS_CHAMPION", "NONE",
                       str(r.get("champion") or "*"),
                       "TRIGGERED" if hit else "NOT_TRIGGERED", ev))
    if not out:
        out.append(_ev("CHALLENGER_BEATS_CHAMPION", "NONE", "*",
                       "NOT_TRIGGERED", {"verdicts": 0}))
    return out


def regime_criteria(regimes: list) -> list:
    c = SPEC["REGIME_SHIFT"]
    if not regimes:
        return [_ev("REGIME_SHIFT", "NONE", "*", "UNAVAILABLE", {},
                    "NO_INTEL_REGIME_STATE_IN_WINDOW")]
    last = [r["recommendation"] for r in regimes[-c[
        "consecutive_not_normal"]:]]
    hit = (last[-1] == c["immediate"]
           or (len(last) == c["consecutive_not_normal"]
               and all(x != "NORMAL" for x in last)))
    return [_ev("REGIME_SHIFT", "NONE", "*",
                "TRIGGERED" if hit else "NOT_TRIGGERED",
                {"last_states": last, "last_run_id": regimes[-1]["run_id"],
                 "last_at": regimes[-1]["at"]})]


def evaluate(*, streams: dict, regimes: list, tournament_rows,
             tournament_why, spec_id: str, spec_sha: str) -> dict:
    evs = []
    for book, st in sorted(streams.items()):
        evs += position_criteria(book, st.positions)
    evs += tournament_criteria(tournament_rows, tournament_why)
    evs += regime_criteria(regimes)
    recs = []
    for e in evs:
        if e["status"] != "TRIGGERED":
            continue
        recs.append({"criterion": e["criterion"], "book": e["book"],
                     "strategy": e["strategy"], "evidence": e["evidence"],
                     "evidence_sha256": C.sha(e["evidence"]),
                     "criteria_spec_id": spec_id, "criteria_sha256": spec_sha,
                     "recommendation": "RECOMMEND_PAUSE"})
    return {"evaluations": evs, "recommendations": recs}
