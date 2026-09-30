"""XAVIER'S SPREAD LADDER AND FULL ALTERNATIVE COMPARISON FOR A HELD POSITION.

WHAT THIS ADDS, AND WHAT IT DOES NOT. The scheduled servicing pass already
supplies every settlement-compatible sibling (`candidate_legs_for`), admits
them (`discover`), ranks EVERY admitted one (`rank_admitted`), binds each to
its own plan (`decision_options`), prices each (`decide_and_record`) and
compares them all with HOLD / EXIT / REDUCE in ONE expected-value ranking
(`bettor_funded_decision.decide`). Xavier's record (`bettor_xavier.
alternatives_of`) carries every alternative with the standard whole-position
economics. This module adds, to EACH alternative on that same persisted record,
the fields a manager needs to read the comparison -- and nothing here ranks,
selects, sizes or sends anything. There is no second decision path.

THE FIELDS (separate, never blended into a score -- `LADDER_FIELDS`):
  alternative_class ............ HOLD / FULL_EXIT / PARTIAL_REDUCTION /
                                 SAME_INSTRUMENT_NETTING / INDIRECT_PAIR_FOR_
                                 PROFIT / INDIRECT_PAIR_ACCEPTING_A_
                                 CONTROLLED_LOSS / ... (see ALT_CLASSES)
  expected_net_pnl_same_measure_usd  the expectation over the payout table
                                 under the ONE measure below (the ranked
                                 `expected_net_usd` is unchanged beside it)
  worst_case_established_usd ... the minimum of the COMBINED per-state net
                                 over ESTABLISHED settlement states
  worst_case_state ............. which state that is
  p_net_profit ................. P(combined net > 0) under the measure
  p_both_legs_win .............. P(every leg pays in full), pairs only
  sensitivity .................. across the void-rate range and a stated
                                 probability stress
  qty_executable_at_limit ...... what the displayed depth supports at the
                                 exact limit (FOK: displayed, not queued)
  exposure_after ............... primary / hedge / matched / unpaired qty
  capital_duration ............. the catalogue's lower bound, never invented
  fees_and_execution ........... fee, its basis, the execution assumption
  settlement_compatibility ..... the grading key, taxonomy, line, rules
  evidence_age_and_expiry ...... how old, when it expires, expired or not
  eligibility .................. RANKABLE or the exact refusal
  payout_table ................. every settlement state: margin buckets,
                                 void / push / tie with established or
                                 UNESTABLISHED payouts
  double_win_break_even ........ for a fully matched pair: the P(both) above
                                 which it has positive expectation
  search_preference ............ the policy's search preferences (not
                                 permissions)

ONE MEASURE, ONE SETTLEMENT INTERPRETATION. The held leg's payout classes
(WIN / LOSE / PARTIAL / VOID) are read from its own settlement rules through
the same payoff-table code the structures use; probabilities are
P(win | normal) = HOLD's `value_per_contract` and the measured void rate --
exactly the payout-state distribution's convention (its implied primary
marginal is (1 - v) x p). An acquisition's classes are the distribution's own
(`cv_acquisition`), which rest on the same p and v. A void payout that is not
established is shown UNESTABLISHED with its bounded range; it is never
assumed to refund or to pay 50 cents.

WHOLE POSITION. Every table is built at the REAL quantities -- 10 primary and
6 hedge keep the 4 unpaired primary in every row, every expectation, every
worst case and every probability. The position minimum is a minimum over the
combined per-state payouts, never a sum of per-leg minima from different
states. The historical basis is subtracted once, the same way on every
alternative: a loss already taken is in the basis, and it never makes spending
more look better.
"""
from __future__ import annotations

import math
import time
from typing import Any

VERSION = "XAVIER_LADDER_V1"

ALT_HOLD = "HOLD"
ALT_FULL_EXIT = "FULL_EXIT"
ALT_PARTIAL = "PARTIAL_REDUCTION"
ALT_NETTING = "SAME_INSTRUMENT_NETTING"
ALT_PAIR_PROFIT = "INDIRECT_PAIR_FOR_PROFIT"
ALT_PAIR_LOSS = "INDIRECT_PAIR_ACCEPTING_A_CONTROLLED_LOSS"
ALT_PAIR_UNKNOWN = "INDIRECT_PAIR_WORST_CASE_NOT_ESTABLISHED"
ALT_PROTECTION = "MANDATORY_OR_EMERGENCY_PROTECTION"
ALT_NO_ACQUISITION = "NO_ACQUISITION_EVIDENCE_OR_AUTHORITY_MISSING"
ALT_OTHER = "OTHER_NOT_RANKED_ACTION"
ALT_CLASSES = (ALT_HOLD, ALT_FULL_EXIT, ALT_PARTIAL, ALT_NETTING,
               ALT_PAIR_PROFIT, ALT_PAIR_LOSS, ALT_PAIR_UNKNOWN,
               ALT_PROTECTION, ALT_NO_ACQUISITION, ALT_OTHER)

LADDER_FIELDS = (
    "alternative_class", "expected_net_pnl_same_measure_usd",
    "worst_case_established_usd", "worst_case_state", "p_net_profit",
    "p_both_legs_win", "sensitivity", "qty_executable_at_limit",
    "exposure_after", "capital_duration", "fees_and_execution",
    "settlement_compatibility", "evidence_age_and_expiry", "eligibility",
    "payout_table", "double_win_break_even", "search_preference",
    "ladder_field_reasons")

#: The probability stress every alternative states: this much probability is
#: moved from the best-paying class to the worst-paying class (downside) and
#: back (upside). A stated stress, not a measure.
PROBABILITY_STRESS = 0.05

MINIMUM_RULE = ("the minimum over the COMBINED per-state net payouts of the "
                "whole position; never a sum of per-leg minima taken from "
                "different states")
NOT_ESTABLISHED = "NOT_ESTABLISHED"
UNESTABLISHED = "UNESTABLISHED"
R_NO_MEASURE = "NO_PROBABILITY_MEASURE_OVER_THESE_STATES_WAS_ESTABLISHED"
R_NO_JOINT_MEASURE = (
    "NO_JOINT_DISTRIBUTION_PRICED_THE_HELD_PAIR_ON_THIS_REVIEW")
R_NO_TABLE = "THE_PAYOUT_TABLE_COULD_NOT_BE_BUILT"
R_TIE_UNKNOWN = "WHETHER_THE_FIXTURE_CAN_END_LEVEL_IS_NOT_ESTABLISHED"
R_VOID_UNMEASURED = "THE_VOID_RATE_IS_NOT_MEASURED_PROBABILITIES_ARE_CONDITIONAL"
R_NOT_A_PAIR = "NOT_A_TWO_LEG_STRUCTURE"
R_NOT_FULLY_MATCHED = (
    "THE_PAIR_LEAVES_UNPAIRED_INVENTORY_SO_A_PER_PAIR_BREAK_EVEN_IS_NOT_THE_"
    "POSITIONS")

_TOL = 1e-9


def _f(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _r(v, n=6):
    return None if v is None else round(float(v), n)


# ═════════════════════════════════════════════════════════════════════
# THE MEASURE AND THE HELD LEG'S STATES
# ═════════════════════════════════════════════════════════════════════

def measure_of(inputs: dict) -> dict:
    """The one probability convention every held-leg table uses. Pure."""
    li = dict(inputs or {})
    p = _f(li.get("p_win"))
    vr = dict(li.get("void_read") or {})
    v = _f(vr.get("rate")) if vr.get("ok") else None
    vu = _f(vr.get("upper_95")) if vr.get("ok") else None
    return {"p_win_given_normal": p, "void_rate": v, "void_upper_95": vu,
            "void_status": ("MEASURED" if v is not None
                            else R_VOID_UNMEASURED),
            "convention": ("P(WIN)=(1-v)p, P(LOSE)=(1-v)(1-p), P(VOID)=v, "
                           "p = HOLD's value_per_contract (P(win | normal "
                           "settlement)), v = the measured void rate -- the "
                           "payout-state distribution's own convention"),
            "source": {"p": "hold_ranking.HOLD.value_per_contract",
                       "v": "bettor_pair_observations.void_rate "
                            "(via the decision's common valuation read)"}}


def _held_class(state, cents):
    from .. import bettor_indirect_structures as IS
    if state == IS.STATE_VOID:
        return "VOID"
    if cents is None:
        return UNESTABLISHED
    if int(cents) == IS.CENTS:
        return "WIN"
    if int(cents) == 0:
        return "LOSE"
    return "PARTIAL"


def _class_probability(cls, m: dict, *, at_void=None):
    """P(class) for the held leg's class under the measure (or None)."""
    p = m.get("p_win_given_normal")
    v = m.get("void_rate") if at_void is None else at_void
    if p is None:
        return None
    vv = 0.0 if v is None else float(v)
    if cls == "WIN":
        return (1.0 - vv) * p
    if cls == "LOSE":
        return (1.0 - vv) * (1.0 - p)
    if cls == "VOID":
        return None if v is None and at_void is None else vv
    return None                       # PARTIAL / TIE: no probability stated


def held_states(inputs: dict) -> dict:
    """The held leg's settlement states, per unit, from its own rules. Pure.

    With no built held leg: a binary contract (WIN 100 / LOSE 0) and, where
    the fixture can void, a VOID whose payout is UNESTABLISHED."""
    from .. import bettor_indirect_structures as IS
    li = dict(inputs or {})
    leg = li.get("held_leg")
    can_void = bool(li.get("fixture_can_void", True))
    rows: list = []
    if leg is not None and li.get("sport_permits_tie") is not None:
        try:
            one = IS._with_quantity(leg, 1)
            for r in IS.payoff_table(
                    (one,), sport_permits_tie=bool(li["sport_permits_tie"]),
                    fixture_can_void=can_void, fixture_can_postpone=True):
                c = (r.get("per_leg_cents") or [None])[0]
                rows.append({"region": r["region"], "state": r["state"],
                             "cents": c, "determined": c is not None
                             or r["state"] == IS.STATE_POSTPONED,
                             "class": ("POSTPONED"
                                       if r["state"] == IS.STATE_POSTPONED
                                       else _held_class(r["state"], c))})
            return {"ok": True, "rows": rows,
                    "source": "THE_HELD_LEGS_OWN_SETTLEMENT_RULES"}
        except Exception as exc:                                # noqa: BLE001
            rows = []
            err = type(exc).__name__
        else:
            err = None
    else:
        err = (R_TIE_UNKNOWN if leg is not None else
               "NO_BUILT_HELD_LEG_THE_CONTRACT_IS_READ_AS_BINARY")
    rows = [{"region": "held side wins", "state": IS.STATE_REGULAR,
             "cents": 100, "determined": True, "class": "WIN"},
            {"region": "held side loses", "state": IS.STATE_REGULAR,
             "cents": 0, "determined": True, "class": "LOSE"}]
    if can_void:
        rows.append({"region": "fixture cancelled or abandoned",
                     "state": IS.STATE_VOID, "cents": None,
                     "determined": False, "class": "VOID"})
    return {"ok": True, "rows": rows, "fallback_because": err,
            "source": "BINARY_PAYOUTS_VOID_PAYOUT_NOT_ESTABLISHED"}


# ═════════════════════════════════════════════════════════════════════
# TABLES
# ═════════════════════════════════════════════════════════════════════

def _summarise_table(rows: list, classes: list, *, fixed_note=None) -> dict:
    est = [r for r in rows if r.get("established") and
           r.get("net_pnl_usd") is not None]
    worst = min(est, key=lambda r: r["net_pnl_usd"]) if est else None
    unest = [{"region": r["region"], "state": r["state"],
              "net_pnl_range_usd": r.get("net_pnl_range_usd"),
              "why": r.get("why")}
             for r in rows if not r.get("established")
             and r.get("state") != "POSTPONED"]
    return {"rows": rows, "probability_classes": classes,
            "position_minimum_usd": None if worst is None
            else worst["net_pnl_usd"],
            "position_minimum_state": None if worst is None
            else worst["region"],
            "position_minimum_over_unestablished_low_end_usd": (
                None if not rows else min(
                    [r["net_pnl_usd"] for r in est]
                    + [r["net_pnl_range_usd"][0] for r in rows
                       if r.get("net_pnl_range_usd")] or [None])),
            "unestablished_states": unest, "minimum_rule": MINIMUM_RULE,
            "note": fixed_note}


def held_table(inputs: dict, *, kept, cash, total_basis) -> dict:
    """ONE LEG HELD: kept contracts pay per state; `cash` is realised now
    (net of fees); the whole remaining basis is subtracted once. Pure."""
    hs = held_states(inputs)
    m = measure_of(inputs)
    k = float(kept or 0.0)
    c0 = float(cash or 0.0)
    b = float(total_basis or 0.0)
    rows, by_class = [], {}
    for r in hs["rows"]:
        if r["class"] == "POSTPONED":
            rows.append({"region": r["region"], "state": "POSTPONED",
                         "established": False, "payout_class": "POSTPONED",
                         "why": "a postponement is not a payout: the "
                                "market stays open"})
            continue
        row = {"region": r["region"], "state": r["state"],
               "payout_class": r["class"],
               "per_leg_cents_per_unit": [r["cents"]],
               "cash_realised_usd": _r(c0)}
        if r["cents"] is None:
            row.update(established=False, combined_payout_usd=None,
                       net_pnl_usd=None,
                       net_pnl_range_usd=[_r(c0 - b), _r(k + c0 - b)],
                       why=("the held contract's payout in this state is "
                            "not established by its settlement rules; "
                            "bounded by [0, 100] cents, never assumed"))
        else:
            pay = k * float(r["cents"]) / 100.0
            row.update(established=True, combined_payout_usd=_r(pay),
                       net_pnl_usd=_r(pay + c0 - b))
        rows.append(row)
        by_class.setdefault(r["class"], row)
    classes = []
    for cls, row in by_class.items():
        classes.append({"payout_class": cls,
                        "probability": _r(_class_probability(cls, m)),
                        "net_pnl_usd": row.get("net_pnl_usd"),
                        "net_pnl_range_usd": row.get("net_pnl_range_usd")})
    out = _summarise_table(rows, classes)
    out.update(kind="HELD_LEG", measure=m, states_source=hs.get("source"),
               states_fallback_because=hs.get("fallback_because"),
               quantities={"kept": _r(k), "cash_realised_usd": _r(c0),
                           "total_basis_usd": _r(b)})
    return out


def group_table(inputs: dict, *, kept_p, kept_h, cash, total_basis) -> dict:
    """BOTH LEGS HELD: one joint table at the kept quantities. Pure.
    Probabilities are NOT established (no joint distribution priced the
    held pair on this review) -- the table and its minimum still are."""
    from .. import bettor_indirect_structures as IS
    import dataclasses
    li = dict(inputs or {})
    pl, hl = li.get("held_leg"), li.get("hedge_held_leg")
    tie = li.get("sport_permits_tie")
    kp, kh = int(round(float(kept_p or 0))), int(round(float(kept_h or 0)))
    c0, b = float(cash or 0.0), float(total_basis or 0.0)
    if pl is None or hl is None or tie is None:
        return {"kind": "GROUP", "ok": False, "refusal": R_NO_TABLE,
                "why": ("both held legs and the tie partition are needed "
                        "for one joint table"), "rows": [],
                "probability_classes": [], "minimum_rule": MINIMUM_RULE}
    legs = []
    if kp > 0:
        legs.append(dataclasses.replace(pl, quantity=kp))
    if kh > 0:
        legs.append(IS._with_quantity(hl, kh))
    rows = []
    if not legs:
        rows.append({"region": "every state", "state": "ANY",
                     "established": True, "payout_class": "FLAT",
                     "combined_payout_usd": 0.0, "cash_realised_usd": _r(c0),
                     "net_pnl_usd": _r(c0 - b)})
    else:
        try:
            table = IS.payoff_table(
                tuple(legs), sport_permits_tie=bool(tie),
                fixture_can_void=bool(li.get("fixture_can_void", True)),
                fixture_can_postpone=True)
        except Exception as exc:                                # noqa: BLE001
            return {"kind": "GROUP", "ok": False, "refusal": R_NO_TABLE,
                    "error": type(exc).__name__, "rows": [],
                    "probability_classes": [], "minimum_rule": MINIMUM_RULE}
        for r in table:
            if r["state"] == IS.STATE_POSTPONED:
                rows.append({"region": r["region"], "state": "POSTPONED",
                             "established": False,
                             "payout_class": "POSTPONED",
                             "why": "a postponement is not a payout"})
                continue
            row = {"region": r["region"], "state": r["state"],
                   "per_leg_cents_per_unit": r["per_leg_cents"],
                   "cash_realised_usd": _r(c0)}
            if r["joint_cents"] is None:
                hi = sum(100 * lg.quantity for lg in legs) / 100.0
                lo = sum((c or 0) * lg.quantity for c, lg in
                         zip(r["per_leg_cents"], legs)) / 100.0
                row.update(established=False, net_pnl_usd=None,
                           combined_payout_usd=None,
                           net_pnl_range_usd=[_r(lo + c0 - b),
                                              _r(hi + c0 - b)],
                           why=("a leg's payout in this state is not "
                                "established; bounded, never assumed"))
            else:
                pay = r["joint_cents"] / 100.0
                row.update(established=True, combined_payout_usd=_r(pay),
                           net_pnl_usd=_r(pay + c0 - b))
            rows.append(row)
    out = _summarise_table(rows, [])
    out.update(kind="GROUP", ok=True,
               probabilities=NOT_ESTABLISHED,
               probabilities_why=R_NO_JOINT_MEASURE,
               quantities={"kept_primary": kp, "kept_hedge": kh,
                           "cash_realised_usd": _r(c0),
                           "total_basis_usd": _r(b)})
    return out


def acquisition_table(inputs: dict, alt: dict, row: dict | None) -> dict:
    """HELD LEG + ONE ACQUIRED LEG, at the real quantities. Pure.

    The margin buckets come from the ranking row's whole-position table
    (`position_worst_case`); the probabilities from the candidate's own
    payout-state distribution classes (`cv_acquisition`). Net = combined
    payout - both legs' cost - the acquisition fee."""
    pv = dict((row or {}).get("position_worst_case") or {})
    fee = _f(alt.get("fees_usd"))
    if fee is None:
        fee = _f(pv.get("fees_usd"))
    cost = _f(pv.get("cost_usd"))
    held_q, hedge_q = _f(pv.get("held_qty")), _f(pv.get("hedge_qty"))
    rows = []
    if not pv.get("regions") or cost is None:
        return {"kind": "ACQUISITION", "ok": False, "refusal": R_NO_TABLE,
                "why": (pv.get("refusal") or
                        "the ranking row carries no whole-position table"),
                "rows": [], "probability_classes": [],
                "minimum_rule": MINIMUM_RULE}
    ff = fee or 0.0
    for r in pv.get("regions") or []:
        pay = _f(r.get("payout_usd"))
        rows.append({"region": r.get("region"), "state": r.get("state"),
                     "per_leg_cents_per_unit": r.get("per_leg_cents"),
                     "established": pay is not None,
                     "combined_payout_usd": _r(pay),
                     "cost_usd": _r(cost), "fees_usd": _r(ff),
                     "net_pnl_usd": (None if pay is None
                                     else _r(pay - cost - ff))})
    for s in pv.get("unresolved_states") or []:
        rows.append({"region": s, "state": "POSTPONED", "established": False,
                     "payout_class": "POSTPONED",
                     "why": "a postponement is not a payout"})
    cva = dict(alt.get("cv_acquisition") or {})
    classes = []
    if cva.get("regions") and cva.get("p_point"):
        for c in cva["regions"]:
            plc = list(c.get("per_leg_cents") or [])
            if held_q is None or hedge_q is None or None in plc \
                    or len(plc) < 2:
                net = None
            else:
                net = _r((held_q * plc[0] + hedge_q * plc[1]) / 100.0
                         - cost - ff)
            classes.append({"payout_class": c.get("region"),
                            "state": c.get("state"),
                            "per_leg_cents_per_unit": plc,
                            "probability": _r((cva.get("p_point") or {}).get(
                                c.get("region"))),
                            "probability_at_void_upper_95": _r(
                                (cva.get("p_upper") or {}).get(
                                    c.get("region"))),
                            "net_pnl_usd": net})
    out = _summarise_table(rows, classes)
    out.update(kind="ACQUISITION", ok=True,
               quantities={"held_qty": held_q, "hedge_qty": hedge_q,
                           "uncovered_qty": _f(pv.get("uncovered_qty")),
                           "cost_usd": cost, "fees_usd": ff},
               probabilities=("PAYOUT_STATE_DISTRIBUTION_CLASSES"
                              if classes else NOT_ESTABLISHED),
               probabilities_why=(None if classes else
                                  (alt.get("blocker")
                                   or "THE_ACQUISITION_WAS_NOT_PRICED_BY_AN_"
                                      "APPROVED_DISTRIBUTION")),
               void_rate=_f(cva.get("void_rate")),
               void_upper_95=_f(cva.get("void_upper_95")))
    return out


# ═════════════════════════════════════════════════════════════════════
# METRICS OVER A TABLE
# ═════════════════════════════════════════════════════════════════════

def _expect(classes: list, key="probability") -> float | None:
    tot, mass = 0.0, 0.0
    for c in classes:
        p, n = _f(c.get(key)), _f(c.get("net_pnl_usd"))
        if p is None:
            return None               # a state with no probability: no mean
        if n is None:
            if p > _TOL:
                return None           # an unestablished payout with mass
            continue
        tot += p * n
        mass += p
    if not classes or abs(mass - 1.0) > 1e-6:
        return None
    return tot


def _p_where(classes: list, pred, key="probability") -> float | None:
    got, mass = 0.0, 0.0
    for c in classes:
        p = _f(c.get(key))
        if p is None:
            return None
        mass += p
        if pred(c):
            got += p
    if not classes or abs(mass - 1.0) > 1e-6:
        return None
    return got


def _stress(classes: list, delta=PROBABILITY_STRESS) -> dict:
    ok = [c for c in classes if _f(c.get("probability")) is not None
          and _f(c.get("net_pnl_usd")) is not None]
    if not ok or len(ok) != len(classes) or len(ok) < 2:
        return {"delta": delta, "downside_usd": None, "upside_usd": None,
                "why": R_NO_MEASURE}
    best = max(ok, key=lambda c: c["net_pnl_usd"])
    worst = min(ok, key=lambda c: c["net_pnl_usd"])
    base = _expect(ok)
    if base is None:
        return {"delta": delta, "downside_usd": None, "upside_usd": None,
                "why": R_NO_MEASURE}
    spread = best["net_pnl_usd"] - worst["net_pnl_usd"]
    d_down = min(delta, float(best["probability"]))
    d_up = min(delta, float(worst["probability"]))
    return {"delta": delta,
            "downside_usd": _r(base - d_down * spread),
            "upside_usd": _r(base + d_up * spread),
            "is": ("A STATED STRESS, NOT A MEASURE: `delta` probability moved "
                   "from the best-paying class (%s) to the worst-paying class "
                   "(%s) and back" % (best.get("payout_class"),
                                      worst.get("payout_class")))}


def _held_void_sensitivity(inputs, kept, cash, basis) -> dict:
    m = measure_of(inputs)
    out = {"void_rate_point": m["void_rate"],
           "void_rate_upper_95": m["void_upper_95"]}
    if m["p_win_given_normal"] is None:
        return dict(out, why=R_NO_MEASURE)
    hs = held_states(inputs)
    by = {}
    for r in hs["rows"]:
        if r["class"] in ("WIN", "LOSE", "VOID"):
            by.setdefault(r["class"], r["cents"])
    k, c0, b = float(kept or 0), float(cash or 0), float(basis or 0)

    def ev(v):
        tot = 0.0
        for cls in ("WIN", "LOSE"):
            if cls not in by or by[cls] is None:
                return None
            tot += _class_probability(cls, m, at_void=v) * (
                k * by[cls] / 100.0 + c0 - b)
        if "VOID" in by and v > 0:
            if by["VOID"] is None:
                return None
            tot += v * (k * by["VOID"] / 100.0 + c0 - b)
        return _r(tot)
    out["expected_at_void_zero_usd"] = ev(0.0)
    out["expected_at_void_point_usd"] = (None if m["void_rate"] is None
                                         else ev(m["void_rate"]))
    out["expected_at_void_upper_95_usd"] = (None if m["void_upper_95"] is None
                                            else ev(m["void_upper_95"]))
    if "VOID" in by and by["VOID"] is None:
        out["why"] = ("the held contract's void payout is not established; an "
                      "expectation with void mass cannot be stated")
    return out


def _acq_void_sensitivity(alt: dict, table: dict) -> dict:
    from .. import bettor_funded_pair_cycle as PC
    cva = dict(alt.get("cv_acquisition") or {})
    cls = table.get("probability_classes") or []
    v_pt, v_up = _f(cva.get("void_rate")), _f(cva.get("void_upper_95"))
    out = {"void_rate_point": v_pt, "void_rate_upper_95": v_up,
           "expected_at_void_point_usd": _r(_expect(cls)),
           "expected_at_void_upper_95_usd": _r(_expect(
               cls, key="probability_at_void_upper_95"))}
    z = PC._mixture_table_at_zero(cva.get("p_point"), v_pt,
                                  cva.get("p_upper"), v_up)
    if z is not None:
        zc = [dict(c, probability=z.get(c.get("payout_class")))
              for c in cls]
        out["expected_at_void_zero_usd"] = _r(_expect(zc))
    else:
        out["expected_at_void_zero_usd"] = None
    out["ranked_value_at_void_upper_95_usd"] = alt.get(
        "value_at_void_upper_95")
    return out


# ═════════════════════════════════════════════════════════════════════
# ONE ALTERNATIVE
# ═════════════════════════════════════════════════════════════════════

def _classify(alt: dict, *, wc) -> str:
    a = str(alt.get("action") or "")
    if a in ("HOLD",):
        return ALT_HOLD
    if a in ("DIRECT_EXIT", "EXIT", "REDUCE"):
        kept = _f(alt.get("remaining_exposure_qty"))
        if a == "REDUCE" or (kept is not None and kept > _TOL):
            return ALT_PARTIAL
        return ALT_FULL_EXIT
    if a in ("TAKE_COMPLEMENT", "POST_COMPLEMENT", "MERGE"):
        return ALT_NETTING
    if a in ("ACQUIRE_INDIRECT_HEDGE",):
        if wc is None:
            return ALT_PAIR_UNKNOWN
        return ALT_PAIR_PROFIT if wc > _TOL else ALT_PAIR_LOSS
    return ALT_OTHER


def _evidence(inputs: dict, alt: dict, row: dict | None) -> dict:
    at = _f((inputs or {}).get("at")) or time.time()
    ev = dict((inputs or {}).get("evidence") or {})
    exp = _f((row or {}).get("inputs_expire_at")) if row else None
    if exp is None:
        exp = _f(alt.get("inputs_expire_at")) or _f(ev.get("inputs_expire_at"))
    age = _f((row or {}).get("evidence_age_s")) if row else None
    return {"evidence_age_s": age,
            "probability_observed_at": ev.get("probability_observed_at"),
            "assessed_at": ev.get("assessed_at"),
            "inputs_expire_at": exp,
            "expired_at_review": (None if exp is None else at >= exp),
            "why_age_unknown": (None if age is not None else
                                "the quote reader states no age for this "
                                "input" if row else
                                "the held position's book and probability "
                                "state an expiry, not an age")}


def _exposure_after(inputs: dict, alt: dict) -> dict:
    li = dict(inputs or {})
    grp = li.get("group") or None
    q = _f(li.get("qty")) or 0.0
    a = str(alt.get("action") or "")
    role = str(alt.get("leg_role") or "PRIMARY")
    if grp:
        p, h = _f(grp.get("primary_residual_qty")), _f(grp.get(
            "hedge_residual_qty"))
        p, h = p or 0.0, h or 0.0
        kept = _f(alt.get("remaining_exposure_qty"))
        if a not in ("HOLD",) and kept is not None:
            if role == "HEDGE":
                h = kept
            else:
                p = kept
    else:
        p, h = q, 0.0
        if a in ("DIRECT_EXIT", "EXIT", "REDUCE"):
            kept = _f(alt.get("remaining_exposure_qty"))
            p = kept if kept is not None else p
        elif a == "ACQUIRE_INDIRECT_HEDGE":
            h = _f(alt.get("covered_qty")) or _f(alt.get("qty")) or 0.0
    return {"primary_qty": _r(p), "hedge_qty": _r(h),
            "matched_qty": _r(min(p, h)), "unpaired_qty": _r(abs(p - h)),
            "unpaired_role": ("PRIMARY" if p > h + _TOL else "HEDGE"
                              if h > p + _TOL else None)}


def enrich_one(alt: dict, inputs: dict) -> dict:
    """THE LADDER FIELDS FOR ONE ALTERNATIVE. Pure; never raises (a failure
    is named in `ladder_field_reasons`)."""
    try:
        return _enrich_one(alt, inputs)
    except Exception as exc:                                    # noqa: BLE001
        return {"ladder_field_reasons": {
            "*": "LADDER_FIELDS_FAILED:%s:%s" % (type(exc).__name__,
                                                  str(exc)[:160])}}


def _enrich_one(alt: dict, inputs: dict) -> dict:
    li = dict(inputs or {})
    a = str(alt.get("action") or "")
    why: dict[str, str] = {}
    rows_by = dict(li.get("rows") or {})
    cid = alt.get("candidate_id")
    row = rows_by.get(str(cid)) if cid else None
    grp = li.get("group") or None
    q = _f(li.get("qty")) or 0.0
    bpc = _f(li.get("basis_per_contract"))
    out: dict[str, Any] = {k: None for k in LADDER_FIELDS}
    out["eligibility"] = ("RANKABLE" if alt.get("rankable") else
                          {"status": "NOT_RANKABLE",
                           "refusal": alt.get("blocker") or "NOT_RANKABLE",
                           "why": alt.get("why")})
    out["exposure_after"] = _exposure_after(li, alt)
    cap = dict(li.get("capital") or {})
    table = None
    # ── THE TABLE ────────────────────────────────────────────────────
    if a in ("HOLD", "DIRECT_EXIT", "EXIT", "REDUCE") and (
            alt.get("rankable") or a == "HOLD"):
        kept = q if a == "HOLD" else _f(alt.get("remaining_exposure_qty"))
        cash = 0.0 if a == "HOLD" else _f(alt.get("cash_now_usd"))
        if grp:
            pb = _f(grp.get("primary_basis_per_contract"))
            hb = _f(grp.get("hedge_basis_per_contract"))
            p_res = _f(grp.get("primary_residual_qty")) or 0.0
            h_res = _f(grp.get("hedge_residual_qty")) or 0.0
            if pb is None or hb is None or cash is None:
                why["payout_table"] = "GROUP_BASIS_OR_PROCEEDS_NOT_STATED"
            else:
                kp, kh = p_res, h_res
                if a != "HOLD":
                    if str(alt.get("leg_role") or "PRIMARY") == "HEDGE":
                        kh = kept if kept is not None else h_res
                    else:
                        kp = kept if kept is not None else p_res
                table = group_table(li, kept_p=kp, kept_h=kh, cash=cash,
                                    total_basis=p_res * pb + h_res * hb)
        elif bpc is None or kept is None or cash is None:
            why["payout_table"] = "BASIS_KEPT_QUANTITY_OR_PROCEEDS_NOT_STATED"
        else:
            table = held_table(li, kept=kept, cash=cash,
                               total_basis=bpc * q)
            out["sensitivity"] = {
                "void": _held_void_sensitivity(li, kept, cash, bpc * q),
                "probability_stress": _stress(
                    table.get("probability_classes") or [])}
    elif a == "ACQUIRE_INDIRECT_HEDGE" and row is not None:
        table = acquisition_table(li, alt, row)
        out["sensitivity"] = {
            "void": _acq_void_sensitivity(alt, table),
            "probability_stress": _stress(
                table.get("probability_classes") or [])}
    elif a == "ACQUIRE_INDIRECT_HEDGE":
        why["payout_table"] = "THE_CANDIDATE_HAS_NO_RANKING_ROW"
    else:
        why["payout_table"] = "NOT_A_VALUED_ACTION:%s" % (
            alt.get("blocker") or a)
    if table is not None:
        out["payout_table"] = table
        out["worst_case_established_usd"] = table.get("position_minimum_usd")
        out["worst_case_state"] = table.get("position_minimum_state")
        cls = table.get("probability_classes") or []
        e = _expect(cls)
        out["expected_net_pnl_same_measure_usd"] = _r(e)
        if e is None:
            why["expected_net_pnl_same_measure_usd"] = (
                table.get("probabilities_why") or R_NO_MEASURE)
        pp = _p_where(cls, lambda c: (_f(c.get("net_pnl_usd")) or 0.0)
                      > _TOL and c.get("net_pnl_usd") is not None)
        out["p_net_profit"] = _r(pp)
        if pp is None:
            why["p_net_profit"] = table.get("probabilities_why") \
                or R_NO_MEASURE
        if table.get("kind") == "ACQUISITION":
            pb2 = _p_where(cls, lambda c: all(
                (x or 0) >= 100 for x in (c.get("per_leg_cents_per_unit")
                                          or [0])))
            out["p_both_legs_win"] = _r(pb2)
            if pb2 is None:
                why["p_both_legs_win"] = (table.get("probabilities_why")
                                          or R_NO_MEASURE)
        else:
            why["p_both_legs_win"] = (R_NO_JOINT_MEASURE if grp
                                      else R_NOT_A_PAIR)
        if out["worst_case_established_usd"] is None:
            why["worst_case_established_usd"] = (
                table.get("refusal") or "NO_ESTABLISHED_STATE")
    # ── CLASS, CAPITAL, EXECUTION, SETTLEMENT, EVIDENCE ──────────────
    wc = out["worst_case_established_usd"]
    if wc is None:
        wc = _f(alt.get("worst_case_net_usd"))
    out["alternative_class"] = _classify(alt, wc=wc)
    kept_all = out["exposure_after"]
    flat = (kept_all["primary_qty"] or 0) <= _TOL and \
        (kept_all["hedge_qty"] or 0) <= _TOL
    out["capital_duration"] = {
        "capital_duration_h": 0.0 if flat else cap.get("capital_duration_h"),
        "capital_committed_at_least_h": (0.0 if flat else
                                         cap.get("capital_committed_at_least_h")),
        "reason": (None if flat else cap.get("reason")),
        "game_start": cap.get("game_start")}
    if a == "HOLD":
        out["qty_executable_at_limit"] = {"qty": 0.0, "why": "NO_ORDER"}
    elif a in ("DIRECT_EXIT", "EXIT", "REDUCE"):
        out["qty_executable_at_limit"] = {
            "qty": _f(alt.get("qty")), "limit_price": _f(alt.get(
                "limit_price")),
            "depth_limited": alt.get("depth_limited"),
            "basis": "the displayed bid depth the sale ladder counted"}
    elif a == "ACQUIRE_INDIRECT_HEDGE":
        out["qty_executable_at_limit"] = {
            "qty": _f((row or {}).get("covered_qty", alt.get("qty"))),
            "wanted_qty": _f(((row or {}).get("depth") or {}).get(
                "wanted_qty")),
            "displayed_depth_qty": _f((row or {}).get("depth_qty")),
            "fully_supported": (row or {}).get("fully_supported"),
            "limit_price": _f(alt.get("limit_price")),
            "basis": ("displayed depth at the exact limit, not a queue "
                      "position: a FOK may still not fill")}
    out["fees_and_execution"] = {
        "fees_usd": _f(alt.get("fees_usd")),
        "fee_basis": (row or {}).get("fee_basis") if row else (
            "rank_with_hold's fee_fn (bettor_funded_book.fee_for)"
            if a in ("DIRECT_EXIT", "EXIT", "REDUCE") else None),
        "execution_assumption": alt.get("execution_uncertainty"),
        "if_not_filled": alt.get("if_not_filled")}
    if a == "ACQUIRE_INDIRECT_HEDGE":
        adm = dict((li.get("admitted") or {}).get(str(cid)) or {})
        out["settlement_compatibility"] = {
            "status": ("COMPATIBLE_SAME_GRADING_KEY" if adm
                       else NOT_ESTABLISHED),
            "admitted_by": "bettor_funded_pair_cycle.discover",
            "taxonomy": alt.get("taxonomy") or adm.get("taxonomy"),
            "line": adm.get("line"), "backs": adm.get("backs"),
            "kind": adm.get("kind"),
            "both_win_regions": adm.get("both_win_regions"),
            "both_lose_regions": adm.get("both_lose_regions"),
            "rules_established": adm.get("rules_established")}
        out["search_preference"] = _pref(li, alt, row)
        out["double_win_break_even"] = _break_even(alt, row, table)
    elif a in ("HOLD", "DIRECT_EXIT", "EXIT", "REDUCE"):
        out["settlement_compatibility"] = {
            "status": "THE_HELD_CONTRACT_ITSELF",
            "states_source": (table or {}).get("states_source")}
    out["evidence_age_and_expiry"] = _evidence(li, alt, row)
    out["ladder_field_reasons"] = why
    return out


def _pref(li, alt, row) -> dict:
    from . import xavier_policy as XP
    pv = dict((row or {}).get("position_worst_case") or {})
    hb, pb = _f(pv.get("hedge_basis_usd_per_unit")), _f(pv.get(
        "held_basis_usd_per_unit"))
    return XP.search_preference_view(
        combined_cost_per_unit=(None if hb is None or pb is None
                                else round(hb + pb, 6)),
        hedge_price=hb, policy=li.get("policy"))


def _break_even(alt, row, table) -> dict:
    """For a FULLY MATCHED pair whose every normal state pays 1 or 2 units:
    E > 0 iff P(both | normal) exceeds (cost per pair + fee per pair - 1)."""
    pv = dict((row or {}).get("position_worst_case") or {})
    if not pv.get("ok"):
        return {"applies": False, "why": "NO_WHOLE_POSITION_TABLE"}
    if (_f(pv.get("uncovered_qty")) or 0.0) > _TOL:
        return {"applies": False, "why": R_NOT_FULLY_MATCHED,
                "uncovered_qty": _f(pv.get("uncovered_qty"))}
    normal = [r for r in pv.get("regions") or []
              if r.get("state") not in ("VOID", "POSTPONED")]
    sums = {sum(int(c or 0) for c in (r.get("per_leg_cents") or []))
            for r in normal}
    if not normal or not sums <= {100, 200}:
        return {"applies": False,
                "why": "SOME_NORMAL_STATE_PAYS_NEITHER_ONE_NOR_TWO_UNITS",
                "per_pair_payouts_cents": sorted(sums)}
    pairs = _f(pv.get("hedge_qty")) or 0.0
    cpp = (_f(pv.get("held_basis_usd_per_unit")) or 0.0) + (
        _f(pv.get("hedge_basis_usd_per_unit")) or 0.0)
    fee = _f(alt.get("fees_usd")) or 0.0
    fpp = fee / pairs if pairs else None
    over = None if fpp is None else round(cpp + fpp - 1.0, 6)
    return {"applies": True,
            "combined_cost_per_pair_usd": round(cpp, 6),
            "fee_per_pair_usd": _r(fpp),
            "amount_over_one_dollar_incl_fees_usd": over,
            "single_winner_net_per_pair_usd": (None if over is None
                                               else round(-over, 6)),
            "double_win_net_per_pair_usd": (None if fpp is None else
                                            round(2.0 - cpp - fpp, 6)),
            "break_even_p_both_given_normal": (
                None if over is None else round(max(0.0, over), 6)),
            "rule": ("each normal state pays one unit (one leg) or two (both "
                     "legs): expected profit per pair is positive only if "
                     "P(both | normal settlement) exceeds the amount by which "
                     "cost plus fee exceeds $1.00"),
            "void": ("excluded from the rule: a void pays what the "
                     "settlement rules establish, shown in the payout table")}


# ═════════════════════════════════════════════════════════════════════
# THE WHOLE COMPARISON
# ═════════════════════════════════════════════════════════════════════

def extra_rows(alts: list, inputs: dict) -> list:
    """The alternatives no ranking produces but the manager must see."""
    li = dict(inputs or {})
    out = [{
        "action": ALT_PROTECTION, "rankable": False,
        "alternative_class": ALT_PROTECTION,
        "blocker": "NOT_ROUTED_THROUGH_THE_RANKED_PATH",
        "why": ("mandatory and emergency controls -- bettor_funded_execution's "
                "risk gates, the loss stop, the account pause and manage's "
                "reconciliation -- act on their own authority, not through "
                "this ranking; they are unaffected by it"),
        "acts_on_its_own_authority": True}]
    acq = [a for a in alts if a.get("action") == "ACQUIRE_INDIRECT_HEDGE"]
    if not any(a.get("rankable") for a in acq):
        missing = sorted({str(x) for x in (
            list(li.get("hedge_unavailable") or [])
            + [a.get("blocker") for a in acq if a.get("blocker")]
            + ([li["acquisition_ineligible"]]
               if li.get("acquisition_ineligible") else [])) if x})
        out.append({
            "action": ALT_NO_ACQUISITION, "rankable": False,
            "alternative_class": ALT_NO_ACQUISITION,
            "blocker": (missing[0] if len(missing) == 1 else
                        "EVIDENCE_OR_AUTHORITY_MISSING" if missing else
                        "NO_ADMITTED_SECOND_CONTRACT"),
            "missing": missing,
            "why": ("no indirect acquisition could be compared: each named "
                    "input or authority is missing, which is not evidence "
                    "that no favorable pair exists")})
    return out


def enrich(alts: list, inputs: dict | None) -> list:
    """EVERY ALTERNATIVE, WITH THE LADDER FIELDS, plus the protection and
    no-acquisition rows. Pure; never raises."""
    if inputs is None:
        return alts
    out = []
    for a in alts or []:
        if a.get("action") == "ACQUIRE_HEDGE" and \
                a.get("blocker") == "HEDGE_SEARCH_REFUSALS":
            out.append(a)             # the search's refusal summary row
            continue
        row = dict(a)
        row.update(enrich_one(a, inputs))
        out.append(row)
    try:
        out.extend(extra_rows(out, inputs))
    except Exception as exc:                                    # noqa: BLE001
        out.append({"action": ALT_PROTECTION, "rankable": False,
                    "blocker": "LADDER_EXTRA_ROWS_FAILED:%s"
                    % type(exc).__name__})
    return out


STOP_COMPLETE = "COMPLETE"
STOP_BUDGET = "READ_BUDGET_EXHAUSTED"
STOP_LIMIT = "LIMIT_REACHED"
STOP_DEADLINE = "DEADLINE"
STOP_NOT_RUN = "SEARCH_NOT_RUN"
STOP_NOT_REPORTED = "NOT_REPORTED_BY_THE_SUPPLIER"


def _quote_stop(r: dict) -> str | None:
    """A candidate whose QUOTE was refused for want of time or read budget
    was not examined -- it is unexamined, not excluded."""
    if str(r.get("stage") or "") != "QUOTE":
        return None
    q = str(r.get("quote_refusal") or "")
    if "DEADLINE" in q:
        return STOP_DEADLINE
    if "BUDGET" in q:
        return STOP_BUDGET
    return None


def search_account(facts: dict | None) -> dict:
    """HOW THE HEDGE SEARCH ENDED, from the supplier's own report carried on
    the pair inputs (`candidate_legs_read`). Pure.

    discovered = the fixture's (slug, side) sibling pairs in the catalogue;
    examined   = the siblings the search attempted within its read budget,
                 less any whose quote was refused for budget or deadline;
    excluded   = attempted and refused, by (stage, refusal) -- with reasons;
    unexamined = discovered - examined, with the reason the search ended:
                 COMPLETE / READ_BUDGET_EXHAUSTED (the per-pass quote budget,
                 `candidate_legs_for`'s cap) / LIMIT_REACHED (the catalogue
                 read's own limit) / DEADLINE / SEARCH_NOT_RUN /
                 NOT_REPORTED_BY_THE_SUPPLIER.
    A limited search is described as best AMONG THE EXAMINED, never as the
    best available across the market."""
    f = dict(facts or {})
    clr = dict(f.get("candidate_legs_read") or {})
    held = dict(f.get("held_leg_read") or {})
    so = dict(clr.get("search_order") or {})
    elig = clr.get("eligibility")
    refused = list(clr.get("refused") or [])
    out: dict[str, Any] = {
        "supplier_limit": clr.get("limit"),
        "supplier_truncated_at_limit": clr.get("truncated_at_limit"),
        "truncation_note": clr.get("truncation_note"),
        "fixture_candidate_pairs": clr.get("fixture_candidate_pairs"),
        "catalogue_rows_read": so.get("catalogue_rows_read"),
        "eligibility": ({k: (elig or {}).get(k) for k in (
            "rows_fetched", "excluded_before_reads", "eligible_total",
            "examined", "deferred", "exhausted_skipped", "cap")}
            if isinstance(elig, dict) else None),
        "eligibility_why": (None if isinstance(elig, dict) else
                            "the funded path applies no pre-read eligibility "
                            "screen; every sibling in the search order is "
                            "quoted up to the cap")}
    if (held and held.get("ok") is False) or clr.get("refusal"):
        held = dict(held, refusal=held.get("refusal") or clr.get("refusal"))
        return dict(out, complete=False, stop_reason=STOP_NOT_RUN,
                    discovered=None, examined=0, excluded={},
                    unexamined=None,
                    why=held.get("refusal") or "THE_HELD_LEG_WAS_NOT_BUILT",
                    comparison_scope=(
                        "NO_HEDGE_SEARCH: %s -- nothing is concluded about "
                        "pairs that were never looked for"
                        % (held.get("refusal") or "held leg not built")))
    if clr.get("truncated_at_limit") is None:
        return dict(out, complete=None, stop_reason=STOP_NOT_REPORTED,
                    discovered=None, examined=clr.get("examined"),
                    excluded={}, unexamined=None,
                    comparison_scope=(
                        "BEST_AMONG_EXAMINED (%s examined; whether the search "
                        "was complete is not reported)" % clr.get("examined")))
    stops = [x for x in (_quote_stop(r) for r in refused) if x]
    excluded: dict[str, int] = {}
    for r in refused:
        if _quote_stop(r):
            continue
        k = "%s:%s" % (r.get("stage") or "?", r.get("refusal") or "UNNAMED")
        excluded[k] = excluded.get(k, 0) + 1
    for k, n in dict((elig or {}).get("excluded_before_reads") or {}).items():
        key = "SCREEN_BEFORE_READ:%s" % k
        if key not in excluded:
            excluded[key] = int(n)
    attempted = int(clr.get("examined") or 0)
    examined = attempted - len(stops)
    rows_read = so.get("catalogue_rows_read")
    discovered = clr.get("fixture_candidate_pairs")
    if discovered is None:
        discovered = rows_read
    discovered = int(discovered or 0)
    pre_screened = sum(int(v) for v in dict(
        (elig or {}).get("excluded_before_reads") or {}).values())
    unexamined = max(0, discovered - attempted - pre_screened) + len(stops)
    if STOP_DEADLINE in stops:
        stop = STOP_DEADLINE
    elif STOP_BUDGET in stops or clr.get("truncated_at_limit"):
        stop = STOP_BUDGET
    elif rows_read is not None and discovered > int(rows_read):
        stop = STOP_LIMIT
    elif unexamined > 0:
        stop = STOP_BUDGET
    else:
        stop = STOP_COMPLETE
    complete = stop == STOP_COMPLETE
    return dict(out, complete=complete, stop_reason=stop,
                discovered=discovered, attempted=attempted,
                examined=examined, excluded=excluded,
                excluded_total=sum(excluded.values()),
                unexamined=unexamined,
                comparison_scope=(
                    "COMPLETE: every sibling examined (%d of %d)"
                    % (examined, discovered) if complete else
                    "BEST_AMONG_EXAMINED (%d of %d; %d unexamined: %s)"
                    % (examined, discovered, unexamined, stop)),
                is_not=("a claim that the chosen pair is the best available "
                        "across the market" if not complete else None))


def search_completeness(*, facts: dict | None, step: dict | None,
                        alts: list, option_refusals=None) -> dict:
    """DID EVERY ADMITTED CONTRACT REACH THE COMPARISON? And was the search
    budget-limited? A budget-limited search says so; it never concludes
    absence. Pure."""
    f, s = dict(facts or {}), dict(step or {})
    clr = dict(f.get("candidate_legs_read") or {})
    disc = dict(s.get("discovery") or {})
    rk = dict(s.get("hedge_candidate_ranking") or {})
    admitted = [str(x) for x in (s.get("search_order") or [])]
    in_alts = {str(a.get("candidate_id")) for a in alts or []
               if a.get("action") == "ACQUIRE_INDIRECT_HEDGE"}
    refused_opt = {str(r.get("candidate_id")) for r in option_refusals or []}
    not_rankable = {str(r.get("condition_id"))
                    for r in rk.get("not_rankable") or []}
    withheld = s.get("acquisition_ineligible")
    missing = [c for c in admitted if c not in in_alts
               and c not in refused_opt and c not in not_rankable]
    trunc = clr.get("truncated_at_limit")
    acct = search_account(f)
    return {
        "account": acct,
        "complete": acct.get("complete"),
        "stop_reason": acct.get("stop_reason"),
        "comparison_scope": acct.get("comparison_scope"),
        "discovered": acct.get("discovered"),
        "examined": acct.get("examined"),
        "excluded": acct.get("excluded"),
        "unexamined": acct.get("unexamined"),
        "supplier_examined": clr.get("examined"),
        "supplier_built": clr.get("built"),
        "supplier_refused": len(clr.get("refused") or []),
        "supplier_truncated_at_limit": trunc,
        "supplier_limit": clr.get("limit"),
        "fixture_candidate_pairs": clr.get("fixture_candidate_pairs"),
        "budget_statement": (
            "THE_SEARCH_WAS_BUDGET_LIMITED: the candidates are a prefix, not "
            "evidence that no better pair exists" if trunc else
            "THE_SUPPLIER_EXAMINED_EVERY_SIBLING_WITHIN_ITS_LIMIT"
            if trunc is False else
            "NOT_REPORTED_BY_THE_SUPPLIER: whether the sibling read was "
            "truncated is not on the pair inputs (candidate_legs_read), so "
            "absence of a pair is not concluded"),
        "discovery_examined": disc.get("examined"),
        "discovery_rejected": len(disc.get("rejected") or []),
        "admitted": len(admitted),
        "ranked": len(rk.get("ranked") or []),
        "ranking_not_rankable": len(not_rankable),
        "option_refused": len(refused_opt),
        "acquisition_withheld_for_the_whole_position": withheld,
        "acquisitions_in_the_comparison": len(in_alts),
        "every_admitted_reached_the_comparison": (not missing
                                                  if not withheld else None),
        "admitted_missing_from_the_comparison": missing if not withheld
        else [],
        "rule": ("every admitted contract is either in the comparison, "
                 "refused by name at ranking or at plan binding, or withheld "
                 "for the whole position by name -- never silently dropped")}


def ladder_view(alts: list) -> list:
    """THE SPREAD LADDER: every acquisition alternative, sorted by line, with
    its separate figures. Pure."""
    rows = []
    for a in alts or []:
        if a.get("action") != "ACQUIRE_INDIRECT_HEDGE":
            continue
        sc = dict(a.get("settlement_compatibility") or {})
        rows.append({
            "candidate_id": a.get("candidate_id"),
            "line": sc.get("line"), "backs": sc.get("backs"),
            "kind": sc.get("kind"), "taxonomy": a.get("taxonomy"),
            "both_win_regions": sc.get("both_win_regions"),
            "rankable": a.get("rankable"), "blocker": a.get("blocker"),
            "alternative_class": a.get("alternative_class"),
            "expected_net_usd": a.get("expected_net_usd"),
            "increment_vs_hold_usd": a.get("increment_vs_hold_usd"),
            "worst_case_established_usd": a.get("worst_case_established_usd"),
            "p_net_profit": a.get("p_net_profit"),
            "p_both_legs_win": a.get("p_both_legs_win"),
            "capital_required_usd": a.get("capital_required_usd"),
            "fees_usd": a.get("fees_usd"),
            "unpaired_residual_qty": a.get("unpaired_residual_qty")})

    def _k(r):
        # BY THE HANDICAP'S SIZE: `Leg.line` is expressed against team A's
        # margin, so the other participant's +4.5 is stored as -4.5.
        ln = _f(r.get("line"))
        return (ln is None, abs(ln) if ln is not None else 0.0,
                str(r.get("backs")), str(r.get("candidate_id")))
    rows.sort(key=_k)
    return rows


def _num_line(v):
    try:
        from fractions import Fraction
        if isinstance(v, Fraction):
            return float(v)
    except Exception:                                           # noqa: BLE001
        pass
    return _f(v)


def admitted_view(admitted_all) -> dict:
    """What the record needs from each admitted leg (JSON-safe). Pure."""
    out = {}
    for a in admitted_all or []:
        leg = a.get("leg")
        st = dict(a.get("structure") or {})
        rules = {}
        try:
            from .. import bettor_xavier as XV
            si = XV.settlement_identity_of_leg(leg) or {}
            rules = si.get("rules_established")
        except Exception:                                       # noqa: BLE001
            rules = None
        out[str(a.get("condition_id"))] = {
            "taxonomy": a.get("taxonomy"),
            "line": _num_line(getattr(leg, "line", None)),
            "backs": getattr(leg, "backs", None),
            "kind": getattr(leg, "kind", None),
            "both_win_regions": list(st.get("both_win_regions") or ()),
            "both_lose_regions": list(st.get("both_lose_regions") or ()),
            "rules_established": rules}
    return out


# ═════════════════════════════════════════════════════════════════════
# THE HOOK CORE CALLS AFTER EACH SERVICING PASS
# ═════════════════════════════════════════════════════════════════════

def _briefs_of(review: dict) -> list:
    r = dict(review or {})
    for path in (("pair_cycle",), ("funded_servicing", "pair_cycle"),
                 ("funded_service", "pair_cycle"),
                 ("funded_servicing", "funded_service", "pair_cycle")):
        cur: Any = r
        for k in path:
            cur = (cur or {}).get(k) if isinstance(cur, dict) else None
        if isinstance(cur, dict) and cur.get("xavier") is not None:
            return list(cur.get("xavier") or [])
    if isinstance(r.get("xavier"), list):
        return list(r["xavier"])
    return []


async def after_review(conn, *, review: dict, now: float) -> dict:
    """LINK EACH WRITTEN XAVIER DECISION INTO `agent_decisions`, and digest.

    A link, never a copy of the economics: the summary names the chosen
    action, the eligibility and the state; the evidence ref points at the
    authoritative row. Idempotent (the registry's `link_decision` is keyed on
    the decision ref). Never raises."""
    out: dict[str, Any] = {"version": VERSION, "at": float(now),
                           "decisions_seen": 0, "linked": 0,
                           "not_linked": [], "registry": None}
    try:
        briefs = [b for b in _briefs_of(review)
                  if isinstance(b, dict) and b.get("xavier_decision_id")
                  and b.get("recorded", True)]
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, error=type(exc).__name__)
    out["decisions_seen"] = len(briefs)
    by_action: dict[str, int] = {}
    for b in briefs:
        k = str(b.get("chosen_action") or "NONE")
        by_action[k] = by_action.get(k, 0) + 1
    out["by_chosen_action"] = by_action
    try:
        from . import registry as REG
    except Exception as exc:                                    # noqa: BLE001
        out["registry"] = "UNAVAILABLE:%s" % type(exc).__name__
        return dict(out, ok=True,
                    why="core's registry is not importable; the Xavier rows "
                        "themselves are the record")
    out["registry"] = "AVAILABLE"
    for b in briefs:
        xid = str(b["xavier_decision_id"])
        try:
            got = await REG.link_decision(
                conn, agent_id="XAVIER", kind="POSITION_REVIEW",
                subject=str(b.get("group_id") or b.get("intent_id") or ""),
                decided_at=float(now),
                verdict=str(b.get("chosen_action") or "NO_ACTION"),
                summary={"eligibility": b.get("eligibility"),
                         "state": b.get("state"),
                         "intent_id": b.get("intent_id"),
                         "top_blockers": b.get("top_blockers")},
                evidence_refs=[{
                    "kind": "bettor_xavier_decisions", "id": xid,
                    "href": "/api/command/agents/xavier/decisions/%s" % xid}],
                decision_ref="xavier:%s" % xid)
            if (got or {}).get("ok", True):
                out["linked"] += 1
            else:
                out["not_linked"].append({"xavier_decision_id": xid,
                                          "refusal": (got or {}).get(
                                              "refusal")})
        except Exception as exc:                                # noqa: BLE001
            out["not_linked"].append({"xavier_decision_id": xid,
                                      "error": type(exc).__name__})
    return dict(out, ok=True)


def describe() -> dict:
    return {"version": VERSION, "fields": list(LADDER_FIELDS),
            "alternative_classes": list(ALT_CLASSES),
            "probability_stress": PROBABILITY_STRESS,
            "minimum_rule": MINIMUM_RULE,
            "is_a_second_decision_path": False}
