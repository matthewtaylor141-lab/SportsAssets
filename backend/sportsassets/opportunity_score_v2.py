"""OPPORTUNITY SCORE V2: AN UNCERTAINTY-ADJUSTED LOWER-CONFIDENCE-BOUND SCORE.
Pure; no I/O. SHADOW, NO AUTHORITY.

WHY. V1 (score.py, LOL_OPPORTUNITY_SCORE_V1) multiplies POINT estimates:
E[net | fill] x P(fill) x capacity / capital-hours. A point estimate fitted
on 20 paper orders and one fitted on 2,000 score alike, a paper fill rate is
read as if it were the venue's, and nothing in V1 prices correlation with
the open book or the tail of a binary contract. The owner audit
(2026-10-04): "test a shadow lower-confidence-bound score using executable
EV, fill probability, capital-hours, correlation and tail risk."

V2, per candidate, at the decision instant, from the decision's own recorded
inputs (no outcome is ever an input):

  q, c, fee     Eddie's executable size and its walked cost per contract
                (the VWAP of the decision's recorded book for the economic
                size), and the fee per contract (Eddie's, by style)
  f_lcb         the LOWER 95% bound of the fill probability: Wilson at the
                effective sample n_eff / K, where n_eff is the
                cluster-effective count Eddie's fill rate was fitted on
                (independent fixtures; the raw order count, labelled
                unclustered, when no event key was recorded) and K the
                execution-evidence transfer penalty of the class it was
                fitted on (execution_evidence: 9 for PAPER_SIMULATION, 1
                for ACTUAL)
  a_ucb         the UPPER 95% bound of adverse selection per contract:
                max(0, mean markout + max(h x sqrt(K), floor)), h the
                class half-width (cluster-robust t x se over independent
                events, else t x sd / sqrt(n)) and floor the declared
                transfer floor (one $0.01 tick; none for ACTUAL), so a
                zero-spread markout history is never read as certain
  e_lcb         p - c - fee - a_ucb      (net executable edge per contract,
                after spread, depth, fees, slippage and adverse selection)
  EV_lcb        f_lcb x e_lcb x q
  CORRELATION   x (1 - h), h = Allie's fixture haircut (0.25 per group
                already open on the fixture, allie_capital), on a positive
                EV only; a decision with no fixture has no countable open
                groups -> correlation UNMEASURED (never a silent 0)
  TAIL (a)      x (1 - v_ucb), v_ucb = the Wilson UPPER bound of the share
                of markets that settled EXCEPTIONALLY -- VOID_REFUND or
                SETTLED_AT_VENUE_PRICE (the venue's own published price,
                e.g. $0.50 on a tie; either forfeits or replaces the
                edge) -- by sport over the canonical INVESTMENT path, else
                pooled over every paper settlement (any sleeve, labelled);
                no settlement history at all -> UNAVAILABLE (unknown is not
                zero, and it is not a guess either)
  TAIL (b)      - RISK_AVERSION x f_lcb x q^2 x p(1-p) / (2 W): the
                log-utility (Kelly) certainty-equivalent cost of the binary
                contract's variance against W = Allie's idle capital
  capacity      min(1, W / (q x (c + fee)))  (V1's rule)
  capital-hours q x (c + fee) x Allie's expected hours to capital release

  V2 = (EV_lcb x (1-h) x (1-v_ucb) - tail cost) x capacity / capital-hours

Unit: USD per USD-hour (V1's unit, so the two are comparable). V2 may be
NEGATIVE: a negative lower bound is information and ranks below a zero.
Every missing input makes V2 UNAVAILABLE with the reason named -- never a
silent zero.

WHAT IS DELIBERATELY NOT IN IT. A book-wide edge-realization ratio (pos-econ
EDGE_CALIBRATION) multiplies every candidate alike and cannot change a rank,
so it is left out; the probability itself is Derek's (no per-candidate
probability-error model is measured, and none is invented).

SPEC is frozen and hashed (SPEC_SHA is recorded with every score): a change
to any constant is a new VERSION, so a tournament can never compare a V2
re-tuned after its outcomes were seen.
"""
from __future__ import annotations

import hashlib
import json
import math

from . import execution_evidence as EE

VERSION = "LOL_OPPORTUNITY_SCORE_V2_LCB"
UNIT = "USD_LCB_NET_PER_USD_CAPITAL_HOUR"
AUTHORITY = "SHADOW_NO_AUTHORITY"
Z = EE.Z95
RISK_AVERSION = 1.0           # log utility
TAKER, MAKER = "TAKER_MARKETABLE", "MAKER_RESTING"
MEASURED, UNAVAILABLE = "MEASURED", "UNAVAILABLE"

SPEC = {
    "version": VERSION, "unit": UNIT, "z": Z,
    "risk_aversion": RISK_AVERSION,
    "transfer_penalty": dict(EE.TRANSFER_PENALTY),
    "mean_transfer_floor": dict(EE.MEAN_TRANSFER_FLOOR),
    "evidence_rule": EE.VERSION,
    "fill_sample": "cluster-effective n (independent fixtures) when "
                   "recorded, else the raw terminal-order count",
    "exceptional_outcomes": ["VOID_REFUND", "SETTLED_AT_VENUE_PRICE"],
    "formula": ("(f_lcb x (p - c - fee - a_ucb) x q x (1-h) x (1-v_ucb) "
                "[the two haircuts on a positive EV only] - RISK_AVERSION x "
                "f_lcb x q^2 x p(1-p) / (2W)) x min(1, W/(q(c+fee))) / "
                "(q(c+fee) x hours_to_release)"),
    "inputs": ["eddie estimate (walk, fee, fitted fill rate and markouts)",
               "allie (hours to release, fixture haircut, idle capital)",
               "exceptional settlement counts (VOID_REFUND + "
               "SETTLED_AT_VENUE_PRICE share by sport on the INVESTMENT "
               "path, else pooled)"],
    "authority": AUTHORITY,
}
SPEC_SHA = hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()
                          ).hexdigest()


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v, n=9):
    return None if v is None else round(float(v), n)


def inputs_from(eddie_est: dict | None, allie: dict | None,
                exceptional: dict | None) -> dict:
    """The V2 inputs, read from Eddie's raw estimate (agents/eddie.estimate),
    Allie's allocation (allie_capital.allocate) and the exceptional
    settlement counts. Pure; a missing source is carried as its reason."""
    e = eddie_est if isinstance(eddie_est, dict) else {}
    ins = e.get("inputs") if isinstance(e.get("inputs"), dict) else {}
    style = TAKER if e.get("execution_style") != MAKER else MAKER
    hist = ins.get("history") if isinstance(ins.get("history"), dict) else {}
    a = allie if isinstance(allie, dict) else {}
    cc = a.get("correlation_concentration") or {}
    oc = a.get("opportunity_cost") or {}
    # a haircut labelled UNMEASURED (no fixture on the decision) is not a
    # measured 0 (canonical_components.allie_at_decision)
    h_unmeasured = str(cc.get("haircut_status") or "").startswith(
        "UNMEASURED")
    return {
        "eddie_status": ("MEASURED" if e and e.get("status") != UNAVAILABLE
                         and e.get("estimate_id") else
                         (e.get("why") or "NO_EDDIE_ESTIMATE")),
        "p": _num(e.get("probability")),
        "style": style,
        "vwap": _num(ins.get("planned_vwap")),
        "qty": _num(ins.get("planned_qty")),
        "fee_pp": _num(ins.get("fee_pp_maker") if style == MAKER
                       else ins.get("fee_pp_taker")),
        "fill": ((hist.get("fill_rate") or {}).get(style) or {}),
        "adverse": ((hist.get("adverse_selection") or {}).get(style) or {}),
        "evidence_class": hist.get("evidence_class") or EE.PAPER_SIMULATION,
        "hours": _num(a.get("expected_hours_to_capital_release")),
        "hours_why": (a.get("unmeasured") or {}).get(
            "expected_hours_to_capital_release"),
        "haircut": None if h_unmeasured else _num(cc.get("haircut")),
        "haircut_why": cc.get("haircut_why") if h_unmeasured else None,
        "idle_usd": _num(oc.get("idle_capital_usd")),
        "exceptional": exceptional if isinstance(exceptional, dict) else None,
    }


def score(x: dict) -> dict:
    """ONE V2 SCORE from `inputs_from(...)`. Pure."""
    um: dict = {}
    comp: dict = {}
    if x.get("eddie_status") != "MEASURED":
        um["execution"] = "EDDIE_ESTIMATE_UNAVAILABLE: %s" % x.get(
            "eddie_status")
    p, c, q, fee = x.get("p"), x.get("vwap"), x.get("qty"), x.get("fee_pp")
    if p is None or not 0 < p < 1:
        um["probability"] = "NO_PROBABILITY_ON_THE_ESTIMATE"
    if c is None or q is None or q <= 0:
        um["executable_walk"] = "NO_EXECUTABLE_WALK_OF_THE_DECISION_BOOK"
    if fee is None:
        um["fee"] = "NO_FEE_PER_CONTRACT_FOR_THE_STYLE"
    cls = x.get("evidence_class") or EE.PAPER_SIMULATION
    k_pen = EE.TRANSFER_PENALTY.get(cls, EE.TRANSFER_PENALTY[
        EE.PAPER_SIMULATION])
    # fill probability lower bound (class-widened)
    fr = x.get("fill") or {}
    f_hat, n_raw = _num(fr.get("value")), _num(fr.get("denominator"))
    n_eff = _num(fr.get("n_eff"))
    n_f = n_eff if n_eff is not None else n_raw
    f_lcb = None
    if f_hat is None or not n_f:
        um["fill_probability"] = "FILL_PROBABILITY_UNMEASURED: %s" % (
            fr.get("why") or "NO_FILL_HISTORY")
    else:
        f_lcb, _hi = EE.wilson(f_hat, n_f / k_pen)
        comp["fill_probability"] = {
            "point": _r(f_hat), "lcb": _r(f_lcb), "n": int(n_raw or 0),
            "n_eff": _r(n_f, 6), "clusters": fr.get("clusters"),
            "clustered": n_eff is not None,
            "fitted_on": cls, "transfer_penalty": k_pen,
            "basis": "Wilson lower bound at n_eff/K"}
    # adverse selection upper bound (class-widened)
    ad = x.get("adverse") or {}
    a_mean, a_sd, n_a = (_num(ad.get("raw_mean_pp")), _num(ad.get("sd_pp")),
                         _num(ad.get("n")))
    a_se, a_g = _num(ad.get("se_clustered_pp")), ad.get("clusters")
    a_ucb = None
    if a_mean is None or a_sd is None or not n_a or n_a < 2:
        um["adverse_selection"] = "ADVERSE_SELECTION_UNMEASURED: %s" % (
            ad.get("why") or "NO_MARKOUT_SPREAD")
    else:
        clustered = a_se is not None and a_g is not None and int(a_g) >= 2
        half = (EE.t_crit_95(int(a_g) - 1) * a_se if clustered else
                EE.t_crit_95(int(n_a) - 1) * a_sd / math.sqrt(n_a))
        floor = EE.mean_floor(cls, EE.USD_PER_CONTRACT)
        a_ucb = max(0.0, a_mean + max(half * math.sqrt(k_pen), floor))
        comp["adverse_selection_pp"] = {
            "mean": _r(a_mean), "ucb": _r(a_ucb), "n": int(n_a),
            "clusters": a_g, "clustered": clustered,
            "fitted_on": cls, "transfer_penalty": k_pen,
            "transfer_floor": floor or None}
    hours = x.get("hours")
    if hours is None:
        um["hours_to_capital_release"] = "ALLIE_HOURS_UNMEASURED: %s" % (
            x.get("hours_why") or "NOT_COMPUTED")
    elif hours <= 0:
        um["hours_to_capital_release"] = "NON_POSITIVE_HOLD"
    h = x.get("haircut")
    if h is None:
        um["correlation"] = "ALLIE_FIXTURE_HAIRCUT_UNMEASURED%s" % (
            ": %s" % x["haircut_why"] if x.get("haircut_why") else "")
    idle = x.get("idle_usd")
    if idle is None:
        um["idle_capital"] = "IDLE_CAPITAL_UNMEASURED"
    ex = x.get("exceptional")
    v_ucb = None
    if not ex or ex.get("rate_ucb") is None:
        um["exceptional_settlement"] = (
            (ex or {}).get("why")
            or "NO_SETTLEMENT_HISTORY_TO_BOUND_THE_EXCEPTIONAL_RATE")
    else:
        v_ucb = float(ex["rate_ucb"])
        comp["exceptional_settlement"] = {
            "rate_ucb": _r(v_ucb), "voids": ex.get("k"),
            "markets": ex.get("n"), "scope": ex.get("scope"),
            "basis": ex.get("basis")}
    out = {"version": VERSION, "spec_sha": SPEC_SHA, "unit": UNIT,
           "authority": AUTHORITY, "components": comp, "unmeasured": um,
           "execution_evidence": {
               "fitted_on": cls, "live_use": EE.LIVE_USE.get(cls),
               "transfer_penalty": k_pen,
               "actual": {"status": EE.UNMEASURED, "why": EE.R_NO_ACTUAL}
               if cls != EE.ACTUAL else {"status": MEASURED}}}
    if um:
        out.update(status=UNAVAILABLE, opportunity_score=None,
                   predicted_net_lcb_usd=None,
                   why="; ".join("%s: %s" % kv for kv in sorted(um.items())))
        return out
    e_lcb = p - c - fee - a_ucb
    ev = f_lcb * e_lcb * q
    if ev > 0:
        ev = ev * (1.0 - min(1.0, max(0.0, h))) * (1.0 - min(1.0, v_ucb))
    capital = q * (c + fee)
    if idle <= 0 or capital <= 0:
        out.update(status=MEASURED, opportunity_score=0.0,
                   predicted_net_lcb_usd=0.0, why=None,
                   score_basis="MEASURED_ZERO_NO_IDLE_CAPITAL"
                   if idle <= 0 else "MEASURED_ZERO_NO_CAPITAL_REQUIRED")
        return out
    tail = RISK_AVERSION * f_lcb * q * q * p * (1.0 - p) / (2.0 * idle)
    cap_factor = max(0.0, min(1.0, idle / capital))
    capital_hours = capital * hours
    net_lcb = (ev - tail) * cap_factor
    comp.update({
        "net_executable_edge_lcb_pp": _r(e_lcb),
        "ev_lcb_usd": _r(f_lcb * e_lcb * q, 6),
        "ev_after_correlation_and_exceptional_usd": _r(ev, 6),
        "correlation_haircut": _r(h, 6),
        "tail_variance_cost_usd": _r(tail, 6),
        "capacity_factor": _r(cap_factor),
        "capital_usd": _r(capital, 6), "hours_to_release": _r(hours, 6),
        "capital_hours": _r(capital_hours, 6),
        "executable_qty": _r(q, 6), "walked_cost_per_contract": _r(c),
        "fee_per_contract": _r(fee), "probability": _r(p)})
    out.update(status=MEASURED, why=None,
               opportunity_score=_r(net_lcb / capital_hours),
               predicted_net_lcb_usd=_r(net_lcb, 6),
               score_basis="LCB_NET_x_CAPACITY_PER_CAPITAL_HOUR")
    return out


def exceptional_from_counts(*, scope_counts: dict | None, scope_key,
                            pooled: dict | None) -> dict:
    """The exceptional-settlement bound V2 uses: the scope's own VOID_REFUND
    share when it has any settled market, else the pooled share; the Wilson
    UPPER bound of it. No settled market anywhere -> no bound (V2 says
    UNAVAILABLE). Pure."""
    own = (scope_counts or {}).get(scope_key) if scope_key else None
    for scope, cnt, basis in (
            (scope_key, own, "VOID_REFUND + SETTLED_AT_VENUE_PRICE share of "
                             "settled markets of this sport on the "
                             "canonical INVESTMENT path"),
            ("POOLED_ALL_PAPER_SETTLEMENTS", pooled,
             "VOID_REFUND + SETTLED_AT_VENUE_PRICE share of every settled "
             "paper market, any sleeve (the sport has none on the "
             "INVESTMENT path yet)")):
        n = int((cnt or {}).get("n") or 0)
        if n > 0:
            k = int((cnt or {}).get("k") or 0)
            _lo, hi = EE.wilson(k / n, n)
            return {"rate_ucb": _r(hi), "k": k, "n": n, "scope": scope,
                    "basis": basis + " (Wilson upper 95%)"}
    return {"rate_ucb": None, "k": 0, "n": 0, "scope": None,
            "why": "NO_SETTLED_MARKET_TO_BOUND_THE_EXCEPTIONAL_SETTLEMENT_"
                   "RATE (unknown is not zero)"}
