"""THE OPPORTUNITY SCORE. Pure; no I/O.

    score = E[net executable $ | fill]  x  P(fill)  x  capacity factor
            ---------------------------------------------------------
                         capital-hours (USD.h)

  E[net executable $ | fill]  pos_capacity.executable_opportunity_dollars:
                     the sum over book levels with marginal net edge > 0 of
                     q x (p - price - fee), conditional on fill (pos-econ
                     capacity model, from the recorded book at decision)
  P(fill)            the CAPACITY snapshot's book-level fill probability
                     (PAPER-simulated entry fill share; pos-econ rates)
  capacity factor    min(1, idle PAPER capital / executable capacity): the
                     share of the opportunity's capital the book could fund
                     from the latest CAPITAL snapshot (1 when it fits)
  capital-hours      executable capacity (USD) x expected hold hours, where
                     expected hold = (event start - decision) + the median
                     settlement lag recorded BEFORE the decision (>= 5
                     samples, no look-ahead -- the economics module's rule)

THE DECOMPOSITION (`components`, each {value, unit, status, why, basis,
in_score}): NET_EV, EXECUTION_CONFIDENCE and LIQUIDITY_CAPACITY enter the
score's formula; EDGE_CONFIDENCE (pos-learn meta-model, when deployed),
CALIBRATION_CONFIDENCE (pos-econ EDGE_CALIBRATION, PAPER, as of the
decision), SETTLEMENT_CONFIDENCE (the valuation's settlement comparison),
REGIME_CONFIDENCE (the intel regime state in force) and CORRELATION_RISK_COST
are shown beside it and do NOT multiply into it: a component that is not
measured is null with its reason, never a silent 1.0.

Unit: expected net USD per USD-hour of capital. A candidate whose
executable opportunity is measured 0 scores 0 (a measured zero). Any missing
input -> status UNAVAILABLE, score None, `why` and `unmeasured` naming it.
"""
from __future__ import annotations

from ..profitability import common as C
from ..profitability import economics as EC

VERSION = "LOL_OPPORTUNITY_SCORE_V1"
UNIT = "USD_EXPECTED_NET_PER_USD_CAPITAL_HOUR"
HOUR = 3600.0


def _comp(value, *, unit=None, status=None, why=None, basis=None,
          in_score=False, state=None) -> dict:
    st = status or (C.MEASURED if (value is not None or state is not None)
                    else C.UNAVAILABLE)
    return {"value": value, "state": state, "unit": unit, "status": st,
            "why": None if st == C.MEASURED else why, "basis": basis,
            "in_score": in_score}


def components(out: dict, ctx: dict) -> dict:
    """The decomposition of one score (see the module docstring)."""
    ctx = ctx or {}
    um = out.get("unmeasured") or {}
    cal = ctx.get("calibration") or {}
    ec = ctx.get("edge_confidence") or {}
    reg = ctx.get("regime") or {}
    sett = ctx.get("settlement_compatibility")
    return {
        "NET_EV": _comp(out.get("expected_net_executable_ev_usd"),
                        unit="USD", in_score=True,
                        why=um.get("expected_net_executable_ev_usd"),
                        basis="pos_capacity EXECUTABLE_OPPORTUNITY_DOLLARS "
                              "(conditional on fill)"),
        "EDGE_CONFIDENCE": _comp(
            C.num(ec.get("value")), unit="probability",
            why=ec.get("why") or "POS_LEARN_EDGE_CONFIDENCE_NOT_DEPLOYED",
            basis=ec.get("basis") or "pos-learn edge-confidence meta-model "
                                     "(SHADOW), by the decision's valuation"),
        "CALIBRATION_CONFIDENCE": _comp(
            C.num(cal.get("value")), unit=cal.get("unit") or "ratio",
            status=(cal.get("status") if cal.get("status") else None),
            why=cal.get("why") or "NO_EDGE_CALIBRATION_OBSERVATION_AT_OR_"
                                  "BEFORE_THE_DECISION",
            basis="pos-econ EDGE_CALIBRATION (PAPER) as of the decision, "
                  "n=%s" % cal.get("sample_n")),
        "EXECUTION_CONFIDENCE": _comp(
            out.get("fill_probability"), unit="probability", in_score=True,
            why=um.get("fill_probability"),
            basis=out.get("fill_probability_basis")),
        "LIQUIDITY_CAPACITY": _comp(
            out.get("capacity_factor"), unit="fraction", in_score=True,
            why=um.get("capacity_factor"),
            basis="%s; executable capacity %s; capacity ceiling %s" % (
                out.get("capacity_factor_basis"),
                out.get("executable_capacity_usd"),
                ctx.get("capacity_ceiling_usd"))),
        "SETTLEMENT_CONFIDENCE": _comp(
            None, state=sett, unit="state",
            why="SETTLEMENT_COMPATIBILITY_NOT_RECORDED_ON_THE_VALUATION",
            basis="external valuation settlement_comparison.compatibility"),
        "REGIME_CONFIDENCE": _comp(
            None, state=reg.get("recommendation"), unit="state",
            why=reg.get("why") or "NO_INTEL_REGIME_STATE_AT_OR_BEFORE_THE_"
                                  "DECISION",
            basis="intel_regime_states in force at the decision (SHADOW)"),
        "CORRELATION_RISK_COST": _comp(
            None, unit="USD",
            why=("NO_ESTABLISHED_EVENT_SETTLEMENT_SIDE_CORRELATION_MODEL_FOR_"
                 "CANDIDATES: correlation needs established event, "
                 "settlement and side identity; titles are never grouped"),
            basis="not measured"),
    }


def score(cand: dict, *, fill_probability=None, fill_basis=None,
          idle_capital_usd=None, idle_capital_why=None, lag_samples=(),
          ctx=None) -> dict:
    out = _score(cand, fill_probability=fill_probability,
                 fill_basis=fill_basis, idle_capital_usd=idle_capital_usd,
                 idle_capital_why=idle_capital_why, lag_samples=lag_samples)
    out["components"] = components(out, dict(
        ctx or {}, capacity_ceiling_usd=C.num(
            cand.get("capacity_ceiling_usd"))))
    return out


def _score(cand: dict, *, fill_probability=None, fill_basis=None,
           idle_capital_usd=None, idle_capital_why=None, lag_samples=(),
           ) -> dict:
    """One candidate's score. `cand` = the latest pos_capacity row (plus
    decided_at, event_start_at)."""
    out = C.Out(candidate_id=cand.get("candidate_id"),
                capacity_id=cand.get("capacity_id"),
                decided_at=cand.get("decided_at"),
                strategy=cand.get("strategy"), verdict=cand.get("verdict"),
                league=cand.get("league"),
                us_market_slug=cand.get("us_market_slug"),
                holding_side=cand.get("holding_side"), score_unit=UNIT)
    whys = []
    if cand.get("status") != C.MEASURED:
        why = "CAPACITY_UNAVAILABLE: %s" % (cand.get("why") or
                                            "no capacity assessment")
        out.put("expected_net_executable_ev_usd", None, why)
        whys.append(why)
    else:
        out.put("expected_net_executable_ev_usd",
                C.num(cand.get("executable_opportunity_dollars")),
                "NO_EXECUTABLE_OPPORTUNITY_FIGURE")
    ev = out["expected_net_executable_ev_usd"]
    cap = C.num(cand.get("executable_capacity_usd"))
    out.put("executable_capacity_usd", cap, "NO_EXECUTABLE_CAPACITY_FIGURE")

    fp = C.num(fill_probability)
    out.put("fill_probability", fp, "FILL_PROBABILITY_NOT_MEASURED")
    out["fill_probability_basis"] = fill_basis

    # measured zero: nothing executable -> score 0, no other input needed
    if ev is not None and ev <= 0:
        out.put("capacity_factor", None, "NOT_NEEDED_NO_EXECUTABLE_EDGE")
        out.put("expected_hold_h", None, "NOT_NEEDED_NO_EXECUTABLE_EDGE")
        out.put("capital_hours", None, "NOT_NEEDED_NO_EXECUTABLE_EDGE")
        out.put("opportunity_score", 0.0)
        out.update(status=C.MEASURED, why=None,
                   score_basis="MEASURED_ZERO_NO_POSITIVE_NET_LEVEL")
        return out

    idle = C.num(idle_capital_usd)
    if idle is None:
        out.put("capacity_factor", None, "IDLE_CAPITAL_UNMEASURED: %s" % (
            idle_capital_why or "no PAPER CAPITAL snapshot"))
    elif cap is None or cap <= 0:
        out.put("capacity_factor", None, "NO_EXECUTABLE_CAPACITY_FIGURE")
    else:
        out.put("capacity_factor", round(max(0.0, min(1.0, idle / cap)), 9))
    out["capacity_factor_basis"] = (
        "min(1, idle PAPER capital %s / executable capacity %s)" % (
            "n/a" if idle is None else "$%.2f" % idle,
            "n/a" if cap is None else "$%.2f" % cap))

    dec, ev_start = C.num(cand.get("decided_at")), C.num(
        cand.get("event_start_at"))
    lag, lag_n = EC.settlement_lag(lag_samples, as_of=dec)
    if dec is None:
        out.put("expected_hold_h", None, "NO_DECISION_TIME")
    elif ev_start is None:
        out.put("expected_hold_h", None, "NO_EVENT_START")
    elif lag is None:
        out.put("expected_hold_h", None, EC.R_NO_LAG)
    else:
        out.put("expected_hold_h",
                round(max(0.0, ev_start - dec + lag) / HOUR, 6))
    out["expected_hold_basis"] = (
        "event start - decision + median settlement lag over %d settlements "
        "recorded before the decision" % lag_n)
    hold = out["expected_hold_h"]
    if cap is not None and hold is not None and cap > 0 and hold > 0:
        out.put("capital_hours", round(cap * hold, 6))
    else:
        out.put("capital_hours", None,
                out["unmeasured"].get("expected_hold_h")
                or out["unmeasured"].get("executable_capacity_usd")
                or "ZERO_CAPITAL_HOURS")

    for k in ("expected_net_executable_ev_usd", "fill_probability",
              "capacity_factor", "capital_hours"):
        if out[k] is None:
            whys.append("%s: %s" % (k, out["unmeasured"].get(k)))
    if whys:
        out.put("opportunity_score", None, "; ".join(dict.fromkeys(whys)))
        out.update(status=C.UNAVAILABLE,
                   why=out["unmeasured"]["opportunity_score"])
        return out
    out.put("opportunity_score", round(
        ev * fp * out["capacity_factor"] / out["capital_hours"], 9))
    out.update(status=C.MEASURED, why=None,
               score_basis="EV x P(fill) x capacity factor / capital-hours")
    return out
