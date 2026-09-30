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


def value_actions(*, held_cents: dict, p_win, p_partial=None,
                  void_rate=None, qty, basis_usd, candidates) -> dict:
    """Every candidate valued on the one measure. `candidates` are dicts:

      {"action": "HOLD"}
      {"action": "DIRECT_EXIT"|"REDUCE", "qty": r, "net_proceeds_usd": N}
      {"action": "ACQUIRE_INDIRECT_HEDGE", "candidate_id",
       "regions": [{"region", "per_leg_cents": [held, hedge]}],
       "region_probabilities": {region: p}, "hedge_qty": h,
       "hedge_cost_usd", "fees_usd", "distribution_void_rate"}

    Returns each with `value_usd` (at the rate used), `value_at_zero_void`,
    `void_slope_usd` (d value / d v), the settlement exposure it leaves, and
    a named refusal where it cannot be valued on this measure."""
    out: dict[str, Any] = {"version": VERSION, "ok": False}
    m = measure(held_cents=held_cents, p_win=p_win, p_partial=p_partial,
                void_rate=void_rate)
    if not m.get("ok"):
        return dict(out, refusal=m["refusal"], measure=m)
    q = _f(qty)
    basis = _f(basis_usd)
    if q is None or q < 0 or basis is None:
        return dict(out, refusal=R_BAD_QTY, qty=qty, basis_usd=basis_usd)
    e_used = m["expected_held_payout_usd"]
    e0 = m["expected_at_zero_void"]
    e1 = m["expected_at_all_void"]
    slope_per_unit = e1 - e0            # d E[held] / d v
    marginal = held_marginal(m)
    valued = []
    for cand in candidates or ():
        c = dict(cand)
        a = str(c.get("action") or "")
        row = {"action": a, "candidate_id": c.get("candidate_id"),
               "rankable": True, "refusal": None}
        if a == "HOLD":
            row.update(value_usd=q * e_used - basis,
                       value_at_zero_void=q * e0 - basis,
                       void_slope_usd=q * slope_per_unit,
                       qty=q, residual_qty=q,
                       settlement_exposure_after=True,
                       components={"held_expected_payout_usd": q * e_used,
                                   "basis_usd": -basis})
        elif a in ("DIRECT_EXIT", "REDUCE"):
            r = _f(c.get("qty"))
            n = _f(c.get("net_proceeds_usd"))
            if r is None or r < 0 or r > q + _TOL:
                row.update(rankable=False, refusal=R_BAD_QTY)
            elif n is None:
                row.update(rankable=False, refusal=R_NO_PROCEEDS)
            else:
                rem = max(0.0, q - r)
                row.update(value_usd=n + rem * e_used - basis,
                           value_at_zero_void=n + rem * e0 - basis,
                           void_slope_usd=rem * slope_per_unit,
                           qty=r, residual_qty=rem,
                           settlement_exposure_after=rem > _TOL,
                           components={"net_proceeds_usd": n,
                                       "residual_expected_payout_usd":
                                           rem * e_used,
                                       "basis_usd": -basis})
        elif a in ("ACQUIRE_INDIRECT_HEDGE", "ACQUIRE_HEDGE"):
            regs = list(c.get("regions") or ())
            probs = dict(c.get("region_probabilities") or {})
            h = _f(c.get("hedge_qty"))
            cost = _f(c.get("hedge_cost_usd"))
            fee = _f(c.get("fees_usd"))
            if not regs or h is None or cost is None or fee is None or \
                    any(probs.get(r.get("region")) is None for r in regs):
                row.update(rankable=False, refusal=R_NO_JOINT_TABLE)
                valued.append(row)
                continue
            dv = _f(c.get("distribution_void_rate"))
            joint_marg: dict[float, float] = {}
            e_hedge = 0.0
            for r in regs:
                pr = float(probs[r["region"]])
                hc_, gc_ = (float(x) for x in r["per_leg_cents"][:2])
                joint_marg[hc_] = joint_marg.get(hc_, 0.0) + pr
                e_hedge += pr * gc_ / 100.0
            gap = {k: joint_marg.get(k, 0.0) - marginal.get(k, 0.0)
                   for k in set(joint_marg) | set(marginal)}
            if (max(abs(x) for x in gap.values()) > 1e-9
                    or (m["can_void"] and dv is not None
                        and abs(dv - m["void_rate"]) > 1e-12)):
                row.update(rankable=False,
                           refusal=R_MEASURES_DO_NOT_RECONCILE,
                           held_marginal_gap=gap,
                           distribution_void_rate=dv,
                           measure_void_rate=m["void_rate"])
                valued.append(row)
                continue
            hold_same = q * e_used - basis
            inc = h * e_hedge - cost - fee
            row.update(value_usd=hold_same + inc,
                       whole_position_expected_net_usd=hold_same + inc,
                       hold_value_same_measure_usd=hold_same,
                       increment_vs_hold_usd=inc,
                       qty=h, residual_qty=q,
                       settlement_exposure_after=True,
                       components={"held_expected_payout_usd": q * e_used,
                                   "hedge_expected_payout_usd": h * e_hedge,
                                   "hedge_cost_usd": -cost,
                                   "hedge_fees_usd": -fee,
                                   "basis_usd": -basis},
                       # the hedge's own void sensitivity is inside its
                       # distribution; only the rate used is claimed here
                       value_at_zero_void=None, void_slope_usd=None)
        else:
            row.update(rankable=False, refusal="NOT_A_VALUED_ACTION")
        valued.append(row)
    ranked = sorted((r for r in valued if r["rankable"]),
                    key=lambda r: -r["value_usd"])
    winner = ranked[0] if ranked else None
    breakeven = []
    if winner is not None and m["can_void"] and \
            winner.get("void_slope_usd") is not None:
        for r in ranked[1:]:
            if r.get("void_slope_usd") is None:
                continue
            ds = r["void_slope_usd"] - winner["void_slope_usd"]
            d0 = winner["value_at_zero_void"] - r["value_at_zero_void"]
            if abs(ds) > 1e-12:
                vstar = d0 / ds
                if 0.0 <= vstar <= 1.0:
                    breakeven.append({"rival": r["action"],
                                      "rival_candidate_id":
                                          r.get("candidate_id"),
                                      "void_rate": vstar})
    return dict(out, ok=True, refusal=None, measure=m, valued=valued,
                winner=None if winner is None else
                {"action": winner["action"],
                 "candidate_id": winner.get("candidate_id"),
                 "value_usd": winner["value_usd"]},
                void_rate_status=m["void_rate_status"],
                void_breakeven=sorted(breakeven,
                                      key=lambda b: b["void_rate"]),
                the_winner_holds_for_void_rates_below=(
                    min((b["void_rate"] for b in breakeven), default=None)
                    if winner is not None else None),
                basis_is=("the same remaining basis is subtracted from every "
                          "action, so it never changes the order; it is kept "
                          "so each value is the position's net result"))
