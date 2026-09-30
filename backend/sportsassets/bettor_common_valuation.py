"""ONE MEASURE, ONE BASIS, EVERY ACTION.

HOLD, DIRECT_EXIT, REDUCE and ACQUIRE are compared on a single distribution
over the held contract's settlement states and a single accounting basis:

    P(held pays c) = (1 - v) x P(c | the fixture is settled normally)
                     + v x [c is the held leg's VOID payout]

where `v` is the void rate the acquisition's distribution also uses, and the
held leg's payout in every state -- WIN, LOSE, a TIE / PUSH cell, VOID -- is the
contract's OWN payout from its settlement rules (a basis refund pays the
basis; a 50-50 resolution pays 50 cents). Every value is net of the same
remaining basis of the held contracts:

    HOLD         q x E[held payout] - basis
    DIRECT_EXIT  net proceeds of the full quantity - basis
                 (nothing is held afterwards, so no settlement term at all)
    REDUCE r     net proceeds of r + (q - r) x E[held payout] - basis
                 (a depth-limited DIRECT_EXIT is valued the same way)
    ACQUIRE h    E[joint payout of both legs at their real quantities]
                 - hedge cost - hedge fees - basis

ACQUIRE's joint expectation comes from the payout-state distribution. It is
admitted only when that distribution's marginal for the held leg IS this
measure (same probability, same void rate, same payouts): the check is made
here, not assumed, so "HOLD plus the hedge's increment" is an identity rather
than an anchoring convention.

Values are linear in `v`; each action carries its value at the rate used and
its slope, and the verdict reports the void rate at which the winner would
change. With no measured rate, every action is valued at v = 0 -- one
distribution for all of them, labelled -- and that break-even rate is what the
decision says about its dependence on the unmeasured quantity.

Pure; never raises.
"""
from __future__ import annotations

import math
from typing import Any

VERSION = "COMMON_VALUATION_V1"

WIN, LOSE, VOID, PARTIAL = "WIN", "LOSE", "VOID", "PARTIAL"

R_NO_HELD_PAYOUTS = "THE_HELD_CONTRACTS_SETTLEMENT_PAYOUTS_ARE_NOT_ESTABLISHED"
R_PROBABILITY = "THE_PRIMARY_PROBABILITY_IS_NOT_A_PROBABILITY"
R_PARTIAL_UNPRICED = "A_PARTIAL_SETTLEMENT_STATE_HAS_NO_PROBABILITY"
R_VOID_UNPRICED = "THE_HELD_CONTRACTS_VOID_PAYOUT_IS_NOT_ESTABLISHED"
R_NO_PROCEEDS = "THE_EXIT_STATES_NO_NET_PROCEEDS"
R_BAD_QTY = "THE_ACTION_STATES_NO_VALID_QUANTITY"
R_MEASURES_DO_NOT_RECONCILE = (
    "THE_ACQUISITIONS_DISTRIBUTION_IS_NOT_THIS_MEASURE_FOR_THE_HELD_LEG")
R_NO_JOINT_TABLE = "THE_ACQUISITION_HAS_NO_PRICED_JOINT_TABLE"

VOID_RATE_MEASURED = "MEASURED"
VOID_RATE_UNMEASURED = "UNMEASURED_VALUED_AT_ZERO"
VOID_NOT_POSSIBLE = "THE_TABLE_ADMITS_NO_VOID"

_TOL = 1e-9


def _f(v):
    try:
        f = None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None
    return f if f is not None and math.isfinite(f) else None


def measure(*, held_cents: dict, p_win, p_partial=None, void_rate=None
            ) -> dict:
    """The distribution over the held contract's payouts (cents per unit).

    `held_cents` maps WIN / LOSE / PARTIAL / VOID to the contract's own
    payout in that state; PARTIAL and VOID only where the fixture admits
    them. `p_win` / `p_partial` are conditional on normal settlement."""
    out: dict[str, Any] = {"version": VERSION, "ok": False}
    hc = {k: _f(v) for k, v in dict(held_cents or {}).items()}
    if hc.get(WIN) is None or hc.get(LOSE) is None:
        return dict(out, refusal=R_NO_HELD_PAYOUTS, held_cents=held_cents)
    p = _f(p_win)
    if p is None or not 0.0 <= p <= 1.0:
        return dict(out, refusal=R_PROBABILITY, p_win=p_win)
    pp = 0.0
    if PARTIAL in hc:
        pp = _f(p_partial)
        if hc[PARTIAL] is None or pp is None or not 0.0 <= pp <= 1.0 - p:
            return dict(out, refusal=R_PARTIAL_UNPRICED)
    elif _f(p_partial) not in (None, 0.0):
        return dict(out, refusal=R_PARTIAL_UNPRICED,
                    why="a partial probability was given and the contract "
                        "has no partial state")
    can_void = VOID in dict(held_cents or {})
    if can_void and hc.get(VOID) is None:
        return dict(out, refusal=R_VOID_UNPRICED)
    v = _f(void_rate)
    if not can_void:
        status, v_used = VOID_NOT_POSSIBLE, 0.0
    elif v is None:
        status, v_used = VOID_RATE_UNMEASURED, 0.0
    else:
        if not 0.0 <= v <= 1.0:
            return dict(out, refusal=R_PROBABILITY, void_rate=void_rate)
        status, v_used = VOID_RATE_MEASURED, v
    normal = {WIN: p, LOSE: max(0.0, 1.0 - p - pp)}
    if PARTIAL in hc:
        normal[PARTIAL] = pp

    def expected(vv):
        e = sum(normal[s] * hc[s] for s in normal) * (1.0 - vv)
        if can_void:
            e += vv * hc[VOID]
        return e / 100.0
    return dict(out, ok=True, refusal=None, held_cents=hc,
                normal_probabilities=normal, can_void=can_void,
                void_rate=v_used, void_rate_status=status,
                expected_held_payout_usd=expected(v_used),
                expected_at_zero_void=expected(0.0),
                expected_at_all_void=expected(1.0) if can_void else
                expected(0.0),
                probability_kinds={
                    "normal_probabilities": "CONDITIONAL_ON_NORMAL_SETTLEMENT",
                    "void_rate": "UNCONDITIONAL",
                    "expected_held_payout_usd": "UNCONDITIONAL"})


def held_marginal(m: dict) -> dict:
    """P(held pays c) under the measure, by cents -- what a joint
    distribution's marginal for the held leg must equal."""
    v = m["void_rate"]
    out: dict[float, float] = {}
    for s, pr in m["normal_probabilities"].items():
        c = m["held_cents"][s]
        out[c] = out.get(c, 0.0) + (1.0 - v) * pr
    if m["can_void"]:
        c = m["held_cents"][VOID]
        out[c] = out.get(c, 0.0) + v
    return out


def held_outcome_cents(held_leg, *, sport_permits_tie: bool,
                       fixture_can_void: bool) -> dict:
    """The held contract's own payout (cents per unit) in each settlement
    state, read from the same payoff-table code the structures use, so a
    basis refund pays the basis and a 50-50 resolution pays 50. Refuses when
    any reachable state's payout is undetermined."""
    from . import bettor_indirect_structures as IS
    rows = IS.payoff_table((held_leg,), sport_permits_tie=sport_permits_tie,
                           fixture_can_void=fixture_can_void,
                           fixture_can_postpone=True)
    out: dict = {}
    for r in rows:
        if r["state"] == IS.STATE_POSTPONED:
            continue
        c = r["per_leg_cents"][0]
        if c is None:
            return {"ok": False, "refusal": R_NO_HELD_PAYOUTS,
                    "region": r["region"]}
        if r["state"] == IS.STATE_VOID:
            key = VOID
        elif c == IS.CENTS:
            key = WIN
        elif c == 0:
            key = LOSE
        else:
            key = PARTIAL
        if key in out and out[key] != c:
            return {"ok": False, "refusal": R_NO_HELD_PAYOUTS,
                    "why": "two %s states pay differently" % key}
        out[key] = c
    return {"ok": True, "refusal": None, "held_cents": out}


R_ACQ_NEEDS_MEASURED_VOID = (
    "AN_ACQUISITION_NEEDS_A_MEASURED_VOID_RATE_WHEN_THE_FIXTURE_CAN_VOID")
R_SELECTION_DEPENDS_ON_VOID = (
    "THE_SELECTED_ACTION_CHANGES_WITHIN_THE_VOID_RATES_UNCERTAINTY")
R_ACQ_BOUND_NOT_PRICED = (
    "THE_ACQUISITION_IS_NOT_PRICED_AT_THE_ENDS_OF_THE_VOID_RATE_RANGE")
BASIS_EXACT = "EXACT_THE_FIXTURE_CANNOT_VOID"
BASIS_ROBUST = "ROBUST_ACROSS_THE_VOID_RATE_RANGE"
BASIS_RESEARCH = "CONDITIONAL_RESEARCH_VALUATION_NOT_FOR_FUNDED_DISPATCH"


def _value_one(c, *, q, basis, e_held, marginal, v, probs_at_v, m):
    """One candidate's value at void rate `v` on the measure `m`."""
    a = str(c.get("action") or "")
    if a == "HOLD":
        return {"value_usd": q * e_held - basis, "qty": q, "residual_qty": q,
                "settlement_exposure_after": True,
                "components": {"held_expected_payout_usd": q * e_held,
                               "basis_usd": -basis}}
    if a in ("DIRECT_EXIT", "REDUCE"):
        r = _f(c.get("qty"))
        n = _f(c.get("net_proceeds_usd"))
        if r is None or r < 0 or r > q + _TOL:
            return {"refusal": R_BAD_QTY}
        if n is None:
            return {"refusal": R_NO_PROCEEDS}
        rem = max(0.0, q - r)
        return {"value_usd": n + rem * e_held - basis, "qty": r,
                "residual_qty": rem, "settlement_exposure_after": rem > _TOL,
                "components": {"net_proceeds_usd": n,
                               "residual_expected_payout_usd": rem * e_held,
                               "basis_usd": -basis}}
    if a in ("ACQUIRE_INDIRECT_HEDGE", "ACQUIRE_HEDGE"):
        regs = list(c.get("regions") or ())
        h = _f(c.get("hedge_qty"))
        cost = _f(c.get("hedge_cost_usd"))
        fee = _f(c.get("fees_usd"))
        if probs_at_v is None:
            return {"refusal": R_ACQ_BOUND_NOT_PRICED}
        if not regs or h is None or cost is None or fee is None or \
                any(probs_at_v.get(r.get("region")) is None for r in regs):
            return {"refusal": R_NO_JOINT_TABLE}
        joint_marg: dict = {}
        e_hedge = 0.0
        for r in regs:
            pr = float(probs_at_v[r["region"]])
            hc_, gc_ = (float(x) for x in r["per_leg_cents"][:2])
            joint_marg[hc_] = joint_marg.get(hc_, 0.0) + pr
            e_hedge += pr * gc_ / 100.0
        gap = {k: joint_marg.get(k, 0.0) - marginal.get(k, 0.0)
               for k in set(joint_marg) | set(marginal)}
        if max(abs(x) for x in gap.values()) > 1e-9:
            return {"refusal": R_MEASURES_DO_NOT_RECONCILE,
                    "held_marginal_gap": gap, "at_void_rate": v}
        hold_same = q * e_held - basis
        inc = h * e_hedge - cost - fee
        return {"value_usd": hold_same + inc,
                "whole_position_expected_net_usd": hold_same + inc,
                "hold_value_same_measure_usd": hold_same,
                "increment_vs_hold_usd": inc, "qty": h, "residual_qty": q,
                "settlement_exposure_after": True,
                "components": {"held_expected_payout_usd": q * e_held,
                               "hedge_expected_payout_usd": h * e_hedge,
                               "hedge_cost_usd": -cost,
                               "hedge_fees_usd": -fee, "basis_usd": -basis}}
    return {"refusal": "NOT_A_VALUED_ACTION"}


def _probs_for(c, v, *, point):
    by = dict(c.get("region_probabilities_by_void") or {})
    for k, pr in by.items():
        try:
            if abs(float(k) - v) <= 1e-12:
                return pr
        except (TypeError, ValueError):
            continue
    dv = _f(c.get("distribution_void_rate"))
    if point is not None and dv is not None and abs(dv - v) <= 1e-12:
        return c.get("region_probabilities")
    return None


def _rank(rows):
    ok = [r for r in rows if r.get("value_usd") is not None]
    return sorted(ok, key=lambda r: -r["value_usd"])


def value_actions(*, held_cents: dict, p_win, p_partial=None,
                  void_rate=None, void_lower=None, void_upper=None,
                  qty, basis_usd, candidates) -> dict:
    """Every candidate valued on the one measure, and whether the selection
    may be dispatched with real money.

    AN UNKNOWN VOID RATE IS NOT ZERO. Where the fixture can void:
      * measured rate -- ranked at the rate, and required to keep the same
        winner at both ends of its stated uncertainty range
        [`void_lower` (default 0), `void_upper` (default the rate)];
      * no measured rate -- the only range the evidence justifies is [0, 1].
        Every action is valued at both ends; the selection stands for funded
        dispatch only if the SAME action wins at both (values are linear in
        the rate, so it then wins everywhere between). Otherwise the ranking
        is a CONDITIONAL RESEARCH VALUATION and funded dispatch is refused by
        name. An acquisition is never admitted on an unmeasured rate.

    `candidates` as before; an acquisition may carry
    `region_probabilities_by_void` = {rate: probabilities} for the range
    ends. Pure; never raises."""
    out: dict[str, Any] = {"version": VERSION, "ok": False}
    m0 = measure(held_cents=held_cents, p_win=p_win, p_partial=p_partial,
                 void_rate=0.0)
    if not m0.get("ok"):
        return dict(out, refusal=m0["refusal"], measure=m0)
    q = _f(qty)
    basis = _f(basis_usd)
    if q is None or q < 0 or basis is None:
        return dict(out, refusal=R_BAD_QTY, qty=qty, basis_usd=basis_usd)
    can_void = m0["can_void"]
    point = _f(void_rate)
    if not can_void:
        lo = hi = 0.0
        point_v = 0.0
        status = VOID_NOT_POSSIBLE
    elif point is None:
        lo, hi, point_v = 0.0, 1.0, None
        status = VOID_RATE_UNMEASURED
    else:
        if not 0.0 <= point <= 1.0:
            return dict(out, refusal=R_PROBABILITY, void_rate=void_rate)
        lo = max(0.0, _f(void_lower) if _f(void_lower) is not None else 0.0)
        hi = min(1.0, _f(void_upper) if _f(void_upper) is not None
                 else point)
        lo, hi = min(lo, point), max(hi, point)
        point_v = point
        status = VOID_RATE_MEASURED

    def at(v):
        m = measure(held_cents=held_cents, p_win=p_win, p_partial=p_partial,
                    void_rate=v if can_void else None)
        e = m["expected_held_payout_usd"]
        marg = held_marginal(dict(m, void_rate=(v if can_void else 0.0)))
        rows = []
        for c in candidates or ():
            a = str(c.get("action") or "")
            is_acq = a in ("ACQUIRE_INDIRECT_HEDGE", "ACQUIRE_HEDGE")
            dv = _f(c.get("distribution_void_rate")) if is_acq else None
            if is_acq and can_void and point is None:
                got = {"refusal": R_ACQ_NEEDS_MEASURED_VOID}
            elif (is_acq and can_void and dv is not None
                  and abs(dv - point) > 1e-12):
                got = {"refusal": R_MEASURES_DO_NOT_RECONCILE,
                       "distribution_void_rate": dv,
                       "measure_void_rate": point}
            else:
                got = _value_one(c, q=q, basis=basis, e_held=e,
                                 marginal=marg, v=v,
                                 probs_at_v=(_probs_for(c, v, point=point)
                                             if is_acq else None), m=m)
            rows.append(dict(got, action=a,
                             candidate_id=c.get("candidate_id")))
        return rows

    rows_lo, rows_hi = at(lo), at(hi)
    rows_pt = at(point_v) if point_v is not None else None
    key = lambda r: (r["action"], r.get("candidate_id"))       # noqa: E731
    win_lo = (_rank(rows_lo) or [None])[0]
    win_hi = (_rank(rows_hi) or [None])[0]
    win_pt = None if rows_pt is None else (_rank(rows_pt) or [None])[0]
    robust = (win_lo is not None and win_hi is not None
              and key(win_lo) == key(win_hi)
              and (win_pt is None or key(win_pt) == key(win_lo)))
    if not can_void:
        basis_kind = BASIS_EXACT
    elif robust:
        basis_kind = BASIS_ROBUST
    else:
        basis_kind = BASIS_RESEARCH
    report = rows_pt if rows_pt is not None else rows_lo
    hi_by = {key(r): r for r in rows_hi}
    lo_by = {key(r): r for r in rows_lo}
    valued = []
    for r in report:
        k = key(r)
        v_lo = lo_by.get(k, {}).get("value_usd")
        v_hi = hi_by.get(k, {}).get("value_usd")
        row = dict(r, rankable=r.get("value_usd") is not None,
                   value_at_range_low=v_lo, value_at_range_high=v_hi,
                   worst_value_over_range=(None if v_lo is None or v_hi is None
                                           else min(v_lo, v_hi)))
        valued.append(row)
    winner = win_pt if win_pt is not None else win_lo
    permitted = basis_kind in (BASIS_EXACT, BASIS_ROBUST) and \
        winner is not None
    refusal = None
    if not permitted and winner is not None:
        refusal = R_SELECTION_DEPENDS_ON_VOID
    return dict(out, ok=True, refusal=None, measure=m0,
                void_rate_status=status, void_rate=point_v,
                void_range=[lo, hi], valued=valued,
                selection_basis=basis_kind,
                funded_dispatch_permitted=permitted,
                funded_dispatch_refusal=refusal,
                winner=None if winner is None else
                {"action": winner["action"],
                 "candidate_id": winner.get("candidate_id"),
                 "value_usd": winner["value_usd"]},
                winner_at_range_low=None if win_lo is None else key(win_lo),
                winner_at_range_high=None if win_hi is None else key(win_hi),
                an_unknown_void_rate_is_not_zero=(
                    "with no measured rate every action is valued at both "
                    "ends of [0, 1]; a selection that differs between them is "
                    "a conditional research valuation, never a funded one"),
                basis_is=("the same remaining basis is subtracted from every "
                          "action, so it never changes the order; it is kept "
                          "so each value is the position's net result"))
