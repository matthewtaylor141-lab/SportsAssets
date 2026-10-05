"""CHAMPION / CHALLENGER EVALUATION (RESEARCH). Pure; no I/O.

TWO LEVELS, NEVER MIXED:

  STRATEGY (built here). Over the CLOSED PAPER positions of the window, the
  per-position return on capital r = net profit / capital committed, per
  strategy, with a bootstrap CI of the mean. The CHAMPION is the INVESTMENT
  strategy with the highest lower bound among those with at least
  MIN_POSITIONS closed positions; every other strategy (any sleeve) is a
  CHALLENGER, compared by a bootstrap CI of mean(challenger) -
  mean(champion):
      CHALLENGER_OUTPERFORMS   lower bound > 0, both arms >= MIN_POSITIONS
      CHAMPION_HOLDS           upper bound < 0
      NOT_PROVEN               the interval contains 0
      INSUFFICIENT_SAMPLE      an arm below MIN_POSITIONS (no verdict)
  A verdict PROMOTES NOTHING: promotion is governed (predeclared criteria,
  Karen, Audrey, a human) and this is an observation on a page.
  Counterfactual rows are excluded by construction (book PAPER only).

  MODEL (exists: sportsassets/poslearn/scoring.py `paired`, persisted as the
  MODEL_TOURNAMENT snapshot). Summarised from its latest snapshot.
"""
from __future__ import annotations

from . import common as C

VERSION = "POS_OS_CHAMPION_V1"
MIN_POSITIONS = 10


def returns_by_strategy(positions):
    out: dict = {}
    for p in positions or []:
        if p.get("book") != "PAPER" or p.get("state") == "OPEN":
            continue
        net, cap = C.num(p.get("net_profit_usd")), C.num(
            p.get("capital_committed_usd"))
        if net is None or not cap or cap <= 0:
            continue
        s = str(p.get("strategy") or "UNKNOWN")
        e = out.setdefault(s, {"strategy": s, "sleeve": C.sleeve_of(p),
                               "returns": [], "net_usd": 0.0})
        e["returns"].append(net / cap)
        e["net_usd"] += net
    return out


def strategy_tournament(positions, *, min_n=MIN_POSITIONS):
    by = returns_by_strategy(positions)
    rows = []
    for s, e in sorted(by.items()):
        lo, hi = C.bootstrap_mean_ci(e["returns"], seed=C.seed_of(
            ["champion", s, len(e["returns"])]))
        rows.append({"strategy": s, "sleeve": e["sleeve"],
                     "closed_positions": len(e["returns"]),
                     "mean_return_on_capital": C.rnd(C.mean(e["returns"])),
                     "ci90": [C.rnd(lo), C.rnd(hi)],
                     "realized_net_usd": C.rnd(e["net_usd"]),
                     "status": C.MEASURED if len(e["returns"]) >= min_n
                     else C.INSUFFICIENT})
    eligible = [r for r in rows if r["sleeve"] == C.INVESTMENT
                and r["status"] == C.MEASURED and r["ci90"][0] is not None]
    champ = max(eligible, key=lambda r: r["ci90"][0]) if eligible else None
    out = {"version": VERSION, "metric": "net profit / capital committed, "
           "per closed PAPER position", "min_positions": min_n,
           "strategies": rows,
           "champion": champ["strategy"] if champ else None,
           "champion_why": None if champ else (
               "NO_INVESTMENT_STRATEGY_WITH_%d_CLOSED_POSITIONS" % min_n),
           "challengers": [], "promotes": False,
           "promotion_rule": "a verdict here promotes nothing; promotion is "
                             "predeclared, challenged, evaluated and "
                             "human-approved (see governance)"}
    if champ is None:
        return out
    base = by[champ["strategy"]]["returns"]
    for r in rows:
        if r["strategy"] == champ["strategy"]:
            continue
        mine = by[r["strategy"]]["returns"]
        if len(mine) < min_n:
            out["challengers"].append(dict(
                strategy=r["strategy"], sleeve=r["sleeve"],
                verdict=C.INSUFFICIENT, n=len(mine),
                diff_ci90=[None, None]))
            continue
        lo, hi = C.bootstrap_diff_ci(mine, base, seed=C.seed_of(
            ["chal", r["strategy"], champ["strategy"], len(mine),
             len(base)]))
        if lo is None:
            v = C.INSUFFICIENT
        elif lo > 0:
            v = "CHALLENGER_OUTPERFORMS"
        elif hi < 0:
            v = "CHAMPION_HOLDS"
        else:
            v = "NOT_PROVEN"
        out["challengers"].append({
            "strategy": r["strategy"], "sleeve": r["sleeve"], "verdict": v,
            "n": len(mine), "diff_mean": C.rnd(C.mean(mine) - C.mean(base)),
            "diff_ci90": [C.rnd(lo), C.rnd(hi)]})
    return out


def model_summary(snapshot_rows, *, now):
    if not snapshot_rows:
        return None
    s = snapshot_rows[0]
    pay = s.get("payload") or {}
    models = []
    for m in (pay.get("models") or [])[:30]:
        vc = m.get("versus_champion") or {}
        models.append({k: m.get(k) for k in (
            "registration_id", "subject_id", "role", "status")
            if k in m} | {"versus_champion_verdict": vc.get("verdict")})
    return {"champion": pay.get("champion"), "computed_at": s["computed_at"],
            "age_s": C.rnd(now - s["computed_at"], 1)
            if s.get("computed_at") else None,
            "opportunities": pay.get("opportunities"),
            "resolved": pay.get("resolved"), "models": models,
            "champion_rule": pay.get("champion_rule")}


def build(inputs, *, now):
    if inputs.get("positions") is None:
        return C.unread(["positions"], inputs, audit=C.BUILT,
                        sources=["pos_economics_latest"])
    strat = strategy_tournament(inputs["positions"])
    model = None
    model_why = None
    if inputs.get("model_tournament") is None:
        model_why = "%s:model_tournament" % C.R_INPUT_NOT_READ
    else:
        model = model_summary(inputs["model_tournament"], now=now)
        model_why = None if model else "NO_MODEL_TOURNAMENT_SNAPSHOT"
    has = bool(strat["strategies"]) or model is not None
    return C.section(
        C.OK if has else C.EMPTY,
        None if has else "%s: no closed PAPER position and no model "
        "tournament snapshot" % C.R_NO_ROWS, audit=C.BUILT,
        sources=["pos_economics_latest", "poslearn_snapshots"],
        data={"strategy": strat, "model_tournament": model,
              "model_tournament_why": model_why},
        existing={"model_tournament": "sportsassets/poslearn/scoring.py"})
