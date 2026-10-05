"""ALLIE · THE CAPITAL-EFFICIENCY ALLOCATION OF ONE CANDIDATE (R30). Pure.

The Chief Allocator's allocation that every canonical decision intent carries.
Capital efficiency is what DETERMINES the amount -- not a label:

  1  expected executable net profit      Eddie's executable EV (after fees,
                                         slippage, fill probability); the
                                         decision's modelled net only as a
                                         labelled fallback
  2  expected capital required           acquisition cost + fees
  3  expected time to capital release    event start - decision + the median
                                         recorded settlement lag
  4  expected capital-hours              (2) x (3)
  5  expected profit per capital-hour    (1) / (4)
  6  profit per $1,000 per hour          (5) x 1,000
  7  capacity ceiling                    executable depth at a positive edge
                                         (Eddie's max executable quantity x
                                         limit), else the displayed depth
                                         within the limit
  8  correlation / concentration effect  open exposure already on the same
                                         fixture haircuts (5) by
                                         CORRELATION_HAIRCUT per open group
                                         and bounds the amount by the fixture
                                         cap; total open exposure by the book
                                         cap
  9  opportunity-cost comparison         idle capital covers the requirement
                                         -> the alternative use of the capital
                                         is idle cash (0 per capital-hour);
                                         otherwise the hurdle is the
                                         HURDLE_QUANTILE of recent measured
                                         INVESTMENT candidates' adjusted (5).
                                         Funded only when adjusted (5) beats it
  10 Allie proposed allocation           0 when (1),(2),(3) are unmeasured, (1)
                                         <= 0, or adjusted (5) <= (9); else
                                         min(capital required, capacity
                                         ceiling, fixture headroom, book
                                         headroom, idle capital) -- the
                                         binding term is named
  11 hard-risk rail cap                  the paper account's per-order cap
                                         (the live lane's own cap is applied
                                         by the SMALL LIVE adapter at capital
                                         scale and reported here)
  12 final allocatable amount            min(10, 11)
  13 confidence / evidence               which inputs are measured, their
                                         sample sizes, and a grade

AUTHORITY: Allie's identity is SHADOW_WEIGHTS_ONLY / PENDING_OWNER_APPROVAL.
The allocation is recorded on the canonical intent and compared with the
order (`order_vs_allocation`); it does not resize the order until the owner
approves that authority.
"""
from __future__ import annotations

VERSION = "ALLIE_CAPITAL_EFFICIENCY_V1"
CORRELATION_HAIRCUT = 0.25          # per open group already on the fixture
FIXTURE_CAP_USD = 125_000.0         # 25% of the $500,000 paper account
BOOK_CAP_USD = 400_000.0            # 80% of the paper account deployed at once
HURDLE_QUANTILE = 0.75
MIN_HURDLE_SAMPLE = 10
MIN_LAG_SAMPLE = 5
HOUR = 3600.0

UNMEASURED = "UNMEASURED"
HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _r(v, n=6):
    return None if v is None else round(float(v), n)


def quantile(xs: list, q: float):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def allocate(*, eddie_ev_usd, modelled_net_usd, capital_required_usd,
             event_start_at, decided_at, median_lag_s, lag_n,
             eddie_max_qty, limit_price, displayed_depth_qty,
             fixture_open_groups, fixture_open_usd, book_open_usd,
             idle_capital_usd, recent_adjusted_ppch: list,
             paper_rail_usd, live_rail_usd=None, live_scale=None,
             order_cost_usd=None) -> dict:
    """THE ALLOCATION (pure). Every field is a value or UNMEASURED with its
    reason; the amount follows from the capital-efficiency comparison."""
    why: dict = {}
    evidence: dict = {}
    # 1 · expected executable net profit
    ev = _num(eddie_ev_usd)
    if ev is not None:
        net, net_basis = ev, "EDDIE_EXECUTABLE_EV_AFTER_FEES_SLIPPAGE_FILL"
    elif _num(modelled_net_usd) is not None:
        net = _num(modelled_net_usd)
        net_basis = "DECISION_MODELLED_NET_AFTER_FEES (Eddie unmeasured)"
    else:
        net, net_basis = None, None
        why["expected_executable_net_profit_usd"] = "NO_EXECUTABLE_OR_MODELLED_NET"
    # 2 · capital required
    cap = _num(capital_required_usd)
    if cap is None or cap <= 0:
        why["expected_capital_required_usd"] = "NO_ACQUISITION_COST"
        cap = None
    # 3 · time to capital release
    hours = None
    st, dec, lag = _num(event_start_at), _num(decided_at), _num(median_lag_s)
    if st is None:
        why["expected_hours_to_capital_release"] = "NO_EVENT_START"
    elif dec is None:
        why["expected_hours_to_capital_release"] = "NO_DECISION_TIME"
    elif lag is None or (lag_n or 0) < MIN_LAG_SAMPLE:
        why["expected_hours_to_capital_release"] = (
            "SETTLEMENT_LAG_SAMPLE_%s_BELOW_%d" % (lag_n or 0, MIN_LAG_SAMPLE))
    else:
        hours = max(0.0, st - dec + lag) / HOUR
        if hours <= 0:
            why["expected_hours_to_capital_release"] = "NON_POSITIVE_HOLD"
            hours = None
    # 4-6 · capital-hours, profit per capital-hour, per $1,000 per hour
    ch = cap * hours if cap is not None and hours is not None else None
    if ch is None:
        why["expected_capital_hours"] = (why.get("expected_capital_required_usd")
                                         or why.get(
                                             "expected_hours_to_capital_release"))
    ppch = net / ch if net is not None and ch else None
    if ppch is None:
        why["expected_profit_per_capital_hour"] = (
            why.get("expected_executable_net_profit_usd")
            or why.get("expected_capital_hours"))
    # 7 · capacity ceiling
    lp = _num(limit_price)
    if _num(eddie_max_qty) is not None and lp:
        ceiling = _num(eddie_max_qty) * lp
        ceiling_basis = "EDDIE_MAX_EXECUTABLE_QTY_x_LIMIT"
    elif _num(displayed_depth_qty) is not None and lp:
        ceiling = _num(displayed_depth_qty) * lp
        ceiling_basis = "DISPLAYED_DEPTH_WITHIN_LIMIT_x_LIMIT (Eddie unmeasured)"
    else:
        ceiling, ceiling_basis = None, None
        why["capacity_ceiling_usd"] = "NO_EXECUTABLE_DEPTH"
    # 8 · correlation / concentration
    n_fix = int(fixture_open_groups or 0)
    haircut = min(1.0, CORRELATION_HAIRCUT * n_fix)
    adj = None if ppch is None else ppch * (1.0 - haircut)
    fix_head = max(0.0, FIXTURE_CAP_USD - (_num(fixture_open_usd) or 0.0))
    book_head = max(0.0, BOOK_CAP_USD - (_num(book_open_usd) or 0.0))
    # 9 · opportunity cost
    idle = _num(idle_capital_usd)
    hurdle_sample = [x for x in (recent_adjusted_ppch or []) if x is not None]
    if idle is not None and cap is not None and idle >= cap:
        opp, opp_basis = 0.0, ("IDLE_CAPITAL_COVERS_THE_REQUIREMENT: the "
                               "alternative use is idle cash")
    elif len(hurdle_sample) >= MIN_HURDLE_SAMPLE:
        opp = quantile(hurdle_sample, HURDLE_QUANTILE)
        opp_basis = ("CAPITAL_SCARCE: the %d%% quantile of %d recent measured "
                     "INVESTMENT candidates' adjusted profit per capital-hour"
                     % (int(HURDLE_QUANTILE * 100), len(hurdle_sample)))
    else:
        opp, opp_basis = None, None
        why["opportunity_cost_per_capital_hour"] = (
            "IDLE_CAPITAL_UNMEASURED" if idle is None else
            "CAPITAL_SCARCE_AND_HURDLE_SAMPLE_%d_BELOW_%d"
            % (len(hurdle_sample), MIN_HURDLE_SAMPLE))
    # 10 · Allie's proposed allocation -- DETERMINED by the comparison
    if net is None or cap is None or hours is None:
        proposed, binding = 0.0, "UNMEASURED_CAPITAL_EFFICIENCY:%s" % ",".join(
            k for k in ("expected_executable_net_profit_usd",
                        "expected_capital_required_usd",
                        "expected_hours_to_capital_release") if k in why)
    elif net <= 0:
        proposed, binding = 0.0, "NON_POSITIVE_EXPECTED_EXECUTABLE_NET"
    elif opp is None:
        proposed, binding = 0.0, "OPPORTUNITY_COST_UNMEASURED_WHILE_CAPITAL_IS_SCARCE"
    elif adj <= opp:
        proposed, binding = 0.0, "PROFIT_PER_CAPITAL_HOUR_NOT_ABOVE_OPPORTUNITY_COST"
    else:
        limits = {"CAPITAL_REQUIRED": cap, "FIXTURE_HEADROOM": fix_head,
                  "BOOK_HEADROOM": book_head}
        if ceiling is not None:
            limits["CAPACITY_CEILING"] = ceiling
        if idle is not None:
            limits["IDLE_CAPITAL"] = idle
        binding = min(limits, key=lambda k: limits[k])
        proposed = max(0.0, limits[binding])
    # 11-12 · the hard-risk rail and the final amount
    rail = _num(paper_rail_usd)
    final = proposed if rail is None else min(proposed, rail)
    final_binding = ("HARD_RISK_RAIL" if rail is not None and rail < proposed
                     else binding)
    # 13 · confidence / evidence
    measured = {
        "executable_net_from_eddie": ev is not None,
        "settlement_lag": hours is not None,
        "capacity_from_eddie": ceiling_basis is not None
        and ceiling_basis.startswith("EDDIE"),
        "opportunity_cost": opp is not None}
    evidence = {"settlement_lag_samples": lag_n,
                "hurdle_sample": len(hurdle_sample),
                "fixture_open_groups": n_fix,
                "measured": measured}
    missing = [k for k, ok in measured.items() if not ok]
    grade = (UNMEASURED if proposed == 0.0 and binding.startswith("UNMEASURED")
             else HIGH if not missing else MEDIUM if len(missing) == 1 else LOW)
    out = {
        "status": "MEASURED" if not binding.startswith("UNMEASURED")
        else "UNAVAILABLE",
        "version": VERSION,
        "why": (None if not binding.startswith("UNMEASURED") else binding),
        "expected_executable_net_profit_usd": _r(net, 4),
        "net_basis": net_basis,
        "expected_capital_required_usd": _r(cap, 4),
        "expected_hours_to_capital_release": _r(hours, 4),
        "expected_capital_hours": _r(ch, 4),
        "expected_profit_per_capital_hour": _r(ppch, 9),
        "profit_per_1000_per_hour": _r(None if ppch is None else ppch * 1000, 6),
        "capacity_ceiling_usd": _r(ceiling, 4),
        "capacity_basis": ceiling_basis,
        "correlation_concentration": {
            "fixture_open_groups": n_fix, "haircut": _r(haircut, 4),
            "adjusted_profit_per_capital_hour": _r(adj, 9),
            "fixture_open_usd": _r(fixture_open_usd, 2),
            "fixture_cap_usd": FIXTURE_CAP_USD,
            "fixture_headroom_usd": _r(fix_head, 2),
            "book_open_usd": _r(book_open_usd, 2), "book_cap_usd": BOOK_CAP_USD,
            "book_headroom_usd": _r(book_head, 2)},
        "opportunity_cost": {
            "per_capital_hour": _r(opp, 9), "basis": opp_basis,
            "idle_capital_usd": _r(idle, 2),
            "candidate_beats_it": (None if adj is None or opp is None
                                   else adj > opp)},
        "allie_proposed_allocation_usd": _r(proposed, 4),
        "binding_constraint": binding,
        "hard_risk_rail_cap_usd": _r(rail, 2),
        "live_rail": {"max_order_usd": _r(live_rail_usd, 2),
                      "scale": _r(live_scale, 4),
                      "paper_equivalent_usd": _r(
                          None if live_rail_usd is None or live_scale is None
                          else live_rail_usd * live_scale, 2),
                      "applied_by": "the SMALL LIVE adapter at capital scale"},
        "final_allocatable_usd": _r(final, 4),
        "final_binding": final_binding,
        "confidence": grade,
        "evidence": evidence,
        "unmeasured": why,
        "authority": "SHADOW_PENDING_OWNER_APPROVAL",
    }
    oc = _num(order_cost_usd)
    out["order_vs_allocation"] = (
        None if oc is None else {
            "order_cost_usd": _r(oc, 4),
            "verdict": ("ORDER_WITHIN_ALLOCATION" if oc <= final + 1e-6
                        else "ORDER_EXCEEDS_ALLOCATION"),
            "excess_usd": _r(max(0.0, oc - final), 4)})
    return out
