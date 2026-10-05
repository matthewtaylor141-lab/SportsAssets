"""THE OPPORTUNITY SCORE. Pure; no I/O.

    score = E[net executable $ | fill]  x  P(fill)  x  capacity factor
            ---------------------------------------------------------
                         capital-hours (USD.h)

  E[net executable $ | fill]  pos_capacity.executable_opportunity_dollars:
                     the sum over book levels with marginal net edge > 0 of
                     q x (p - price - fee), conditional on fill (pos-econ
                     capacity model, from the recorded book at decision)
  P(fill)            Archer's expected fill probability for THIS decision
                     (eddie_execution_estimates, migration 217, SHADOW_ONLY,
                     from the decision's recorded book) when he measured
                     one; else the CAPACITY snapshot's book-level fill
                     probability (PAPER-simulated entry fill share; pos-econ
                     rates) as of the decision. EXECUTION_CONFIDENCE names
                     which (`source`) and always shows Archer's estimate (or
                     why there is none) beside it.
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

V2 (R30A, owner audit 2026-10-04) -- THE SCORE IS A PRODUCTION-CONFIDENCE
NUMBER, so nothing in it may be moved by a TRAINING or BENCHMARK outcome or
priced on a book no entry could have used:
  * NET_EV counts the capacity assessment ONLY when its book meets the
    strategy's executable freshness standard (profitability/capacity.
    executable_fresh, the entry decision's own book-age bound); otherwise
    UNAVAILABLE (BOOK_OLDER_THAN_THE_STRATEGY_EXECUTABLE_FRESHNESS) -- the
    V1 score priced books up to 300 s old;
  * P(fill) uses Archer's estimate only when HIS book meets that standard
    (his estimator accepts books up to 120 s); else the CAPACITY snapshot's
    PRODUCTION rate (INVESTMENT strategies' entry orders only);
  * CALIBRATION_CONFIDENCE is the INVESTMENT sleeve's EDGE_CALIBRATION
    (V1 read the book-wide PAPER one, which pooled TRAINING outcomes).
"""
from __future__ import annotations

from ..profitability import capacity as CP
from ..profitability import common as C
from ..profitability import economics as EC

VERSION = "LOL_OPPORTUNITY_SCORE_V2"
UNIT = "USD_EXPECTED_NET_PER_USD_CAPITAL_HOUR"
HOUR = 3600.0


def _comp(value, *, unit=None, status=None, why=None, basis=None,
          in_score=False, state=None) -> dict:
    st = status or (C.MEASURED if (value is not None or state is not None)
                    else C.UNAVAILABLE)
    return {"value": value, "state": state, "unit": unit, "status": st,
            "why": None if st == C.MEASURED else why, "basis": basis,
            "in_score": in_score}


ARCHER = "ARCHER_EXECUTION_ESTIMATE"
CAPACITY_SNAPSHOT = "POS_CAPACITY_SNAPSHOT_FILL_SHARE"

#: R30C · WHAT P(fill) IS FITTED ON. Both sources -- Archer's estimate and the
#: CAPACITY snapshot's fill share -- are rates of the PAPER SIMULATOR's own
#: orders: never live execution evidence. Stated as the literal so this
#: research package imports nothing outside itself and the profitability
#: layer; pinned equal to execution_evidence.PAPER_SIMULATION (and the
#: live-use label to execution_evidence.LIVE_USE) by
#: tests/test_execution_calibration.py.
EXECUTION_EVIDENCE_CLASS = "PAPER_SIMULATION"
EXECUTION_EVIDENCE_LIVE_USE = (
    "DERIVED_FROM_PAPER_SIMULATION_NOT_PROOF_OF_LIVE_EXECUTION")


def archer_view(est, why=None) -> dict:
    """Archer's (SHADOW_ONLY) estimate of this decision as shown beside the
    execution component; UNAVAILABLE with its reason when there is none."""
    if not est:
        return {"status": C.UNAVAILABLE,
                "why": why or "NO_ARCHER_ESTIMATE_FOR_THIS_DECISION"}
    keys = ("estimate_id", "estimator_version", "estimated_at",
            "expected_fill_probability", "expected_net_executable_edge_pp",
            "expected_execution_loss_pp", "expected_executable_ev_usd",
            "expected_time_to_fill_s", "max_executable_qty",
            "execution_style", "recommendation", "recommendation_reason",
            "book_obs_id", "book_age_s")
    v = {k: est.get(k) for k in keys}
    v.update(status=C.MEASURED, why=None, authority="SHADOW_ONLY")
    if v["expected_fill_probability"] is None:
        v["fill_probability_why"] = (est.get("fill_why")
                                     or "ARCHER_FILL_PROBABILITY_UNMEASURED")
    return v


def archer_fresh(archer_est, strategy) -> tuple:
    """(usable, why): Archer's estimate counts only when the book HE priced
    meets the strategy's executable freshness standard (his own estimator
    accepts books up to 120 s; the entry rule does not)."""
    if not archer_est:
        return False, None
    age = C.num(archer_est.get("book_age_s"))
    bound = CP.executable_bound(strategy)
    if age is None:
        return False, "ARCHER_BOOK_AGE_UNKNOWN"
    if abs(age) > bound:
        return False, ("ARCHER_BOOK_%.1fs_OLDER_THAN_THE_STRATEGY_EXECUTABLE_"
                       "FRESHNESS_%.0fs" % (age, bound))
    return True, None


def execution_input(archer_est, snapshot_fp, snapshot_basis,
                    strategy=None) -> tuple:
    """(fill probability, basis, source) for the score's P(fill): Archer's
    expected fill probability for this decision when he measured one on a
    book meeting the strategy's executable freshness, else the CAPACITY
    snapshot's PRODUCTION fill share as of the decision, else None."""
    ok, why = archer_fresh(archer_est, strategy)
    fp = C.num((archer_est or {}).get("expected_fill_probability")) \
        if ok else None
    if fp is None and why:
        snapshot_basis = "%s (Archer's estimate not used: %s)" % (
            snapshot_basis, why)
    if fp is not None:
        return fp, ("eddie_execution_estimates %s (%s, SHADOW_ONLY) from the "
                    "decision's recorded book %s, estimated at %s" % (
                        archer_est.get("estimate_id"),
                        archer_est.get("estimator_version"),
                        archer_est.get("book_obs_id"),
                        archer_est.get("estimated_at"))), ARCHER
    if C.num(snapshot_fp) is not None:
        return C.num(snapshot_fp), snapshot_basis, CAPACITY_SNAPSHOT
    return None, snapshot_basis, None


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
            why=cal.get("why") or "NO_INVESTMENT_EDGE_CALIBRATION_"
                                  "OBSERVATION_AT_OR_BEFORE_THE_DECISION",
            basis="pos-econ EDGE_CALIBRATION of the PAPER INVESTMENT sleeve "
                  "(never TRAINING / BENCHMARK) as of the decision, n=%s"
                  % cal.get("sample_n")),
        "EXECUTION_CONFIDENCE": dict(_comp(
            out.get("fill_probability"), unit="probability", in_score=True,
            why=um.get("fill_probability"),
            basis=out.get("fill_probability_basis")),
            source=out.get("fill_probability_source"),
            # R30C: the class the P(fill) was fitted on, beside the number
            evidence_class=(EXECUTION_EVIDENCE_CLASS
                            if out.get("fill_probability_source") else None),
            live_use=(EXECUTION_EVIDENCE_LIVE_USE
                      if out.get("fill_probability_source") else None),
            archer=archer_view(ctx.get("archer"), ctx.get("archer_why"))),
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
          ctx=None, fill_source=None) -> dict:
    out = _score(cand, fill_probability=fill_probability,
                 fill_basis=fill_basis, idle_capital_usd=idle_capital_usd,
                 idle_capital_why=idle_capital_why, lag_samples=lag_samples)
    out["fill_probability_source"] = (
        fill_source if out.get("fill_probability") is not None else None)
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
    # THE EXECUTABLE FRESHNESS OF THE CAPACITY BOOK (V2). The runner stamps
    # `executable_freshness` (profitability/capacity.executable_fresh) from
    # the capacity row and its decision: a book no entry could have used is
    # not executable EV, and its capacity is not executable capacity. (The
    # canonical decision's own call scores the ENTER decision's own book,
    # which the entry rule already held to the bound, and stamps nothing.)
    xf = cand.get("executable_freshness")
    stale = isinstance(xf, dict) and not xf.get("fresh")
    if cand.get("status") != C.MEASURED:
        why = "CAPACITY_UNAVAILABLE: %s" % (cand.get("why") or
                                            "no capacity assessment")
        out.put("expected_net_executable_ev_usd", None, why)
        whys.append(why)
    elif stale:
        why = "CAPACITY_NOT_EXECUTABLE: %s (age %s s, bound %s s)" % (
            xf.get("why"), xf.get("age_s"), xf.get("bound_s"))
        out.put("expected_net_executable_ev_usd", None, why)
        whys.append(why)
    else:
        out.put("expected_net_executable_ev_usd",
                C.num(cand.get("executable_opportunity_dollars")),
                "NO_EXECUTABLE_OPPORTUNITY_FIGURE")
    ev = out["expected_net_executable_ev_usd"]
    cap = None if stale else C.num(cand.get("executable_capacity_usd"))
    out.put("executable_capacity_usd", cap,
            "CAPACITY_NOT_EXECUTABLE" if stale
            else "NO_EXECUTABLE_CAPACITY_FIGURE")

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
