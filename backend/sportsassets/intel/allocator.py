"""A · THE CHIEF ALLOCATOR (SHADOW). Which of the currently qualified
candidates the $1,000 notional sleeve would fund, how much, and why.

CANDIDATES: recent ENTER-qualified paper decisions (within QUALIFIED_S) and
open paper positions. A position competes for the same dollar as a new
entry: keeping it is a choice with an opportunity cost, not a sunk default.

THE SCORE (risk-adjusted net executable EV per dollar):
  new decision   (p_cons - cost_cons) / cost_cons, from intel/sizing: the
                 probability net of calibration uncertainty against the
                 planned acquisition VWAP + fees + execution uncertainty
  open position  (p_now_cons - exit_value) / exit_value: Xavier's latest
                 probability for the held contract, net of calibration
                 uncertainty, against what selling now returns
  minus CORRELATION_PENALTY x (open positions already on the same game +
  open positions already on the same team); the SPORT_CAP bounds the rest

THE ALLOCATION (greedy, highest score first, all in sleeve dollars):
  budget     SLEEVE x drawdown factor x regime factor
  capacity   new: the evidence-quality shadow size (intel/sizing, which
             already carries liquidity/depth and the per-position cap);
             open: the position's paper exposure at sleeve scale
             (SLEEVE / PAPER_ACCOUNT_USD), capped per position
  game cap   GAME_CAP x SLEEVE, net of ACTUAL exposure already on that game
  sport cap  SPORT_CAP x SLEEVE
A candidate with a non-positive score is ranked and gets 0. Each funded
candidate's `opportunity_cost_per_dollar` is the score of the best
candidate left unfunded -- what the dollar gave up.

SHADOW: these are weights on paper. No order, size, limit or threshold
anywhere reads them.
"""
from __future__ import annotations

from . import calibration as CAL
from . import common as C

VERSION = "INTEL_ALLOCATOR_V1"
QUALIFIED_S = 2 * 3600.0
GAME_CAP = 0.25
SPORT_CAP = 0.60
PER_POSITION_CAP = 0.10
CORRELATION_PENALTY = 0.01
PAPER_ACCOUNT_USD = 500000.0
SLEEVE_SCALE = C.SLEEVE_NOTIONAL_USD / PAPER_ACCOUNT_USD


def _team_of(label) -> str:
    if not isinstance(label, dict):
        return "UNKNOWN"
    pays = str(label.get("pays_on") or "").upper()
    team = (label.get("home_team") if pays == "HOME" else
            label.get("away_team") if pays == "AWAY" else None)
    return str(team or "UNKNOWN")


def candidates_from(sizing_rows: list, decisions: dict, open_positions: list,
                    measures: dict, *, cal_report, now) -> list:
    """Build candidate dicts (pure)."""
    out = []
    for s in sizing_rows:
        d = decisions.get(s["decision_id"]) or {}
        if (C.epoch(d.get("decided_at")) or 0) < float(now) - QUALIFIED_S:
            continue
        f = s.get("factors") or {}
        label = C.jload(d.get("label")) or {}
        out.append({
            "candidate_id": "decision:%s" % s["decision_id"],
            "candidate_kind": "NEW_DECISION",
            "decision_id": s["decision_id"], "group_id": None,
            "us_market_slug": s.get("us_market_slug"),
            "game": str((label.get("event_key") if isinstance(label, dict)
                         else None) or d.get("fixture")
                        or d.get("us_market_slug")),
            "sport": (label.get("sport") if isinstance(label, dict)
                      else None) or "UNKNOWN",
            "team": _team_of(label),
            "ev": f.get("net_ev_per_dollar_conservative"),
            "ev_why": (None if f.get("net_ev_per_dollar_conservative")
                       is not None else
                       s["unmeasured"].get("shadow_usd")
                       or "NO_CONSERVATIVE_EV"),
            "capacity_usd": s.get("shadow_usd"),
            "calibration_uncertainty": f.get("calibration_uncertainty"),
            "liquidity_cap_usd": (f.get("caps_usd") or {}).get("LIQUIDITY"),
            "unmeasured": dict(s.get("unmeasured") or {})})
    for p in open_positions:
        m = C.jload(measures.get(p["group_id"])) or {}
        pn = C.num(m.get("p"))
        exit_v = C.num(m.get("best_exit_at_review"))
        unc = CAL.uncertainty_for(cal_report, p=pn)
        u = unc.get("half_width")
        um = {}
        if u is None:
            u = 0.10
            um["calibration_uncertainty"] = (
                (unc.get("unmeasured") or {}).get("half_width")
                or "NO_CALIBRATION_EVIDENCE") + "_DEFAULT_0.10"
        if pn is None or exit_v is None or exit_v <= 0:
            ev, why = None, ("NO_CURRENT_PROBABILITY_FOR_THE_HELD_CONTRACT"
                             if pn is None else
                             "NO_EXIT_PRICE_AT_THE_LATEST_REVIEW")
        else:
            ev, why = (pn - u - exit_v) / exit_v, None
        cap = None
        if p.get("exposure_usd") is not None:
            cap = min(p["exposure_usd"] * SLEEVE_SCALE,
                      PER_POSITION_CAP * C.SLEEVE_NOTIONAL_USD)
        else:
            um["capacity_usd"] = "POSITION_HAS_NO_COST_BASIS"
        out.append({
            "candidate_id": "position:%s" % p["group_id"],
            "candidate_kind": "OPEN_POSITION", "decision_id": None,
            "group_id": p["group_id"],
            "us_market_slug": p.get("us_market_slug"),
            "game": p.get("game") or "UNKNOWN",
            "sport": p.get("sport") or "UNKNOWN",
            "team": p.get("team") or "UNKNOWN",
            "ev": C.rnd(ev), "ev_why": why, "capacity_usd": C.rnd(cap),
            "calibration_uncertainty": C.rnd(u),
            "liquidity_cap_usd": None, "unmeasured": um})
    return out


def allocate(cands: list, *, game_open: dict, actual_game_exposure: dict,
             drawdown_factor, regime_factor, team_open: dict | None = None,
             sleeve=C.SLEEVE_NOTIONAL_USD) -> dict:
    """The pure greedy allocation. Returns the ranked list + budget."""
    budget = sleeve * drawdown_factor * regime_factor
    team_open = team_open or {}
    for c in cands:
        own = 1 if c["candidate_kind"] == "OPEN_POSITION" else 0
        n = max(0, int(game_open.get(c["game"], 0)) - own)
        team = c.get("team") or "UNKNOWN"
        t = (max(0, int(team_open.get(team, 0)) - own)
             if team != "UNKNOWN" else 0)
        c["same_game_open"] = n        # not penalised for itself
        c["same_team_open"] = t
        c["score"] = (None if c["ev"] is None
                      else C.rnd(c["ev"] - CORRELATION_PENALTY * (n + t)))
    ranked = sorted(cands, key=lambda c: (c["score"] is None,
                                          -(c["score"] or 0.0),
                                          c["candidate_id"]))
    left = budget
    game_used: dict = {}
    sport_used: dict = {}
    for c in ranked:
        reasons = []
        c["shadow_usd"] = 0.0
        if c["score"] is not None:
            reasons.append("RISK_ADJUSTED_NET_EV_PER_DOLLAR=%.4f" % c["score"])
        if c.get("calibration_uncertainty") is not None:
            reasons.append("CALIBRATION_UNCERTAINTY=%.4f"
                           % c["calibration_uncertainty"])
        if c["same_game_open"]:
            reasons.append("CORRELATED_WITH_%d_OPEN_ON_THE_SAME_GAME"
                           % c["same_game_open"])
        if c["same_team_open"]:
            reasons.append("CORRELATED_WITH_%d_OPEN_ON_THE_SAME_TEAM"
                           % c["same_team_open"])
        if c["score"] is None:
            c["binding_constraint"] = "UNMEASURED_EV"
            reasons.append("NOT_FUNDED:%s" % c["ev_why"])
        elif c["score"] <= 0:
            c["binding_constraint"] = "NON_POSITIVE_RISK_ADJUSTED_EV"
            reasons.append("NOT_FUNDED:NO_EDGE_AFTER_UNCERTAINTY_AND_"
                           "CORRELATION")
        elif c["capacity_usd"] is None:
            c["binding_constraint"] = "UNMEASURED_CAPACITY"
            reasons.append("NOT_FUNDED:NO_MEASURED_CAPACITY")
        else:
            gh = GAME_CAP * sleeve - actual_game_exposure.get(
                c["game"], 0.0) - game_used.get(c["game"], 0.0)
            sh = SPORT_CAP * sleeve - sport_used.get(c["sport"], 0.0)
            limits = {"CAPACITY": c["capacity_usd"], "GAME_CAP": gh,
                      "SPORT_CAP": sh, "SLEEVE_BUDGET": left}
            bind = min(limits, key=lambda k: limits[k])
            usd = max(0.0, limits[bind])
            c["shadow_usd"] = C.rnd(usd, 4)
            c["binding_constraint"] = bind
            if actual_game_exposure.get(c["game"]):
                reasons.append("EXISTING_ACTUAL_EXPOSURE_ON_GAME_USD=%.2f"
                               % actual_game_exposure[c["game"]])
            reasons.append("BOUND_BY_%s=%.2f" % (bind, limits[bind]))
            left -= usd
            game_used[c["game"]] = game_used.get(c["game"], 0.0) + usd
            sport_used[c["sport"]] = sport_used.get(c["sport"], 0.0) + usd
        c["reasons"] = reasons
        c["shadow_weight"] = C.rnd((c["shadow_usd"] or 0.0) / sleeve, 6)
    unfunded = [c["score"] for c in ranked
                if c["score"] is not None and c["score"] > 0
                and not c["shadow_usd"]]
    best_unfunded = max(unfunded) if unfunded else None
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
        if c["shadow_usd"]:
            if best_unfunded is None:
                c["opportunity_cost_per_dollar"] = 0.0
                c["reasons"].append("OPPORTUNITY_COST:NO_POSITIVE_"
                                    "CANDIDATE_LEFT_UNFUNDED")
            else:
                c["opportunity_cost_per_dollar"] = C.rnd(best_unfunded)
                c["reasons"].append("OPPORTUNITY_COST:BEST_UNFUNDED_"
                                    "ALTERNATIVE=%.4f" % best_unfunded)
        else:
            c["opportunity_cost_per_dollar"] = None
            c.setdefault("unmeasured", {})["opportunity_cost_per_dollar"] = (
                "CANDIDATE_NOT_FUNDED")
    return {"budget_usd": C.rnd(budget), "allocated_usd":
            C.rnd(budget - left), "unallocated_usd": C.rnd(left),
            "ranked": ranked}


def report(result: dict, *, now, inputs: dict) -> dict:
    out = C.envelope(version=VERSION, computed_at=now,
                     sleeve_usd=C.SLEEVE_NOTIONAL_USD,
                     caps={"game": GAME_CAP, "sport": SPORT_CAP,
                           "per_position": PER_POSITION_CAP},
                     correlation_penalty=CORRELATION_PENALTY,
                     qualified_window_s=QUALIFIED_S, inputs=inputs)
    out.update({k: v for k, v in result.items() if k != "ranked"})
    out["candidates"] = len(result["ranked"])
    out["allocation"] = result["ranked"]
    return out


async def load_and_allocate(conn, *, now, sizing_rows, risk_paper,
                            risk_actual, regime, cal_report,
                            account_id=C.PAPER_ACCOUNT) -> dict:
    ids = [s["decision_id"] for s in sizing_rows]
    decisions = {}
    if ids:
        decisions = {r["decision_id"]: dict(r) for r in await conn.fetch(
            "SELECT decision_id, decided_at, label, fixture, us_market_slug "
            "  FROM paper_decisions WHERE decision_id = ANY($1::text[])",
            ids)}
    open_pos = list((risk_paper or {}).get("positions") or [])
    gids = [p["group_id"] for p in open_pos]
    measures = {}
    if gids:
        measures = {r["group_id"]: r["measure"] for r in await conn.fetch(
            "SELECT DISTINCT ON (group_id) group_id, measure "
            "  FROM paper_xavier_reviews WHERE group_id = ANY($1::text[]) "
            " ORDER BY group_id, reviewed_at DESC", gids)}
    cands = candidates_from(sizing_rows, decisions, open_pos, measures,
                            cal_report=cal_report, now=now)
    game_open = {g["key"]: g["positions"] for g in
                 ((risk_paper or {}).get("exposure") or {}).get("game") or []}
    team_open = {g["key"]: g["positions"] for g in
                 ((risk_paper or {}).get("exposure") or {}).get("team") or []
                 if g["key"] != "UNKNOWN"}
    actual_game = {g["key"]: g["exposure_usd"] or 0.0 for g in
                   ((risk_actual or {}).get("exposure") or {}).get("game")
                   or []}
    dd = ((risk_paper or {}).get("equity") or {}).get("current_drawdown_pct")
    from . import sizing as S

    unm = {}
    if dd is None:
        ddf = S.UNMEASURED_DRAWDOWN_FACTOR
        unm["drawdown"] = "DRAWDOWN_NOT_MEASURED_DEFAULT_%.2f" % ddf
    else:
        ddf = C.clamp(1.0 - dd / S.DRAWDOWN_HALT_PCT)
    rec = (regime or {}).get("recommendation")
    if rec in S.REGIME_FACTOR:
        rf = S.REGIME_FACTOR[rec]
    else:
        rf = S.UNMEASURED_REGIME_FACTOR
        unm["regime"] = "NO_REGIME_RECOMMENDATION_DEFAULT_%.2f" % rf
    res = allocate(cands, game_open=game_open, team_open=team_open,
                   actual_game_exposure=actual_game, drawdown_factor=ddf,
                   regime_factor=rf)
    return report(res, now=now, inputs={
        "drawdown_factor": C.rnd(ddf), "regime": rec,
        "regime_factor": rf, "unmeasured": unm,
        "existing_exposure_basis": ("ACTUAL book exposure by game counts "
                                    "against the game cap; paper open "
                                    "positions compete as candidates")})
