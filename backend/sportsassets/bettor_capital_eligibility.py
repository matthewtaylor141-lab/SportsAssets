"""CAPITAL-ELIGIBLE ENTER, ON THE PAPER PATH. ONE GATE, ONLY EVER TIGHTER.

An ENTER that the strategy's own policy admitted is CAPITAL-ELIGIBLE -- it may
be given simulated capital (a paper order) -- only when ALL of these hold:

  EXECUTABLE DEPTH      the observed ladder shows depth at or inside the
                        order's limit. No executable depth => no
                        capital-eligible ENTER (R_CE_NO_EXECUTABLE_DEPTH).
  PARTIAL DEPTH CAPS    the size is capped to that executable depth, whole
                        contracts. The policy's intended size is never
                        exceeded and never "filled in" from depth that is
                        not displayed.
  SETTLEMENT IDENTITY   the contract identity (slug, payout event, fixture,
                        holding side) and the settlement terms are resolved:
                        compatibility COMPATIBLE. Anything else -- UNKNOWN,
                        absent, INCOMPATIBLE -- refuses
                        (R_CE_SETTLEMENT_UNRESOLVED / R_CE_IDENTITY_UNRESOLVED).
  TOTAL EXECUTABLE EV   on the capped quantity, walked level by level:
                            qty x p  -  cost at the walked levels (the
                            slippage past the top of book is INSIDE this
                            cost and reported separately, never twice)
                                     -  fees, charged per level
                                     -  the adverse-selection estimate
                                     +  rebates / rewards ONLY whose terms
                                        are recorded (a terms reference);
                                        an unrecorded incentive is listed
                                        and credited at $0
                        EV <= 0 => $0 allocated and CASH/WAIT wins, recorded
                        as the decision's named refusal
                        (R_CE_CASH_WAIT_EV_NOT_POSITIVE), never silence.

THE ADVERSE-SELECTION ESTIMATE. A marketable paper order is decided on one
book and simulated against a LATER one (the decision-to-execution delay). The
book can move against it in between; it cannot fill worse than its limit.
Unless the caller supplies a MEASURED per-contract estimate, the gate charges
the identified WORST-CASE BOUND: every contract priced at the limit instead
of the walked VWAP, i.e. (limit - VWAP) per contract. That bound is not a
guess -- an IOC limit order cannot fill above it -- and it is never zero by
default when the walk spans more than one level.

VENUE-NEUTRAL EQUIVALENCE (Kalshi vs Polymarket US). Prices on two venues are
compared only when `kalshi_mapping.establish` returns ESTABLISHED (identical
payoff vectors in every terminal state, structured evidence only). Even then
routing is POLYMARKET-ONLY in production: there is no production Kalshi book
read path (`kalshi_read_path_status` names the exact blocker). The router
below never returns KALSHI while that status is BLOCKED.

THE PARENT / CHILD ORDER PLAN is PAPER SIMULATION ONLY: children are bounded
by the visible depth of the level each targets; their sum never exceeds the
executable depth or the parent. Nothing here builds, signs or sends a venue
order; the module imports no venue client.

OBJECTIVE. Net dollars per capital-hour: `ev_per_capital_hour` is reported
beside the dollar EV (never alone -- a fast penny is still a penny).

PURE. No I/O, no clock, no globals written. Never raises: an exception inside
the evaluation is itself a refusal (R_CE_GATE_ERROR).
"""
from __future__ import annotations

import math
import os
from typing import Any, Callable, Iterable

VERSION = "PAPER_CAPITAL_ELIGIBILITY_V1"

ENTER = "ENTER"
CASH_WAIT = "CASH_WAIT"

R_CE_NO_EXECUTABLE_DEPTH = "CAPITAL_INELIGIBLE_NO_EXECUTABLE_DEPTH"
R_CE_SETTLEMENT_UNRESOLVED = "CAPITAL_INELIGIBLE_SETTLEMENT_TERMS_UNRESOLVED"
R_CE_IDENTITY_UNRESOLVED = "CAPITAL_INELIGIBLE_CONTRACT_IDENTITY_UNRESOLVED"
R_CE_NO_PROBABILITY = "CAPITAL_INELIGIBLE_NO_PROBABILITY"
R_CE_NO_LIMIT = "CAPITAL_INELIGIBLE_NO_LIMIT_PRICE"
R_CE_FEES_NOT_IDENTIFIED = "CAPITAL_INELIGIBLE_FEES_NOT_IDENTIFIED"
R_CE_SIZE_BELOW_ONE_CONTRACT = "CAPITAL_INELIGIBLE_SIZE_BELOW_ONE_CONTRACT"
R_CE_CASH_WAIT_EV_NOT_POSITIVE = "CASH_WAIT_TOTAL_EXECUTABLE_EV_NOT_POSITIVE"
R_CE_GATE_ERROR = "CAPITAL_ELIGIBILITY_GATE_ERROR"
R_CE_VENUE_NOT_EQUIVALENT = "VENUE_PAYOFF_EQUIVALENCE_NOT_ESTABLISHED"
R_CE_KALSHI_READ_PATH_BLOCKED = "KALSHI_BOOK_READ_PATH_BLOCKED_IN_PRODUCTION"

AS_BASIS_MEASURED = "MEASURED_PER_CONTRACT_SUPPLIED_BY_THE_CALLER"
AS_BASIS_LIMIT_BOUND = "IOC_LIMIT_WORST_CASE_BOUND_LIMIT_MINUS_VWAP"

COMPATIBLE = "COMPATIBLE"

POLYMARKET_US = "POLYMARKET_US"
KALSHI = "KALSHI"

#: the parent / child plan: at most this many children, each at most the
#: visible quantity of the level it targets (fraction 1.0 = the whole level,
#: never more). PAPER SIMULATION ONLY.
MAX_CHILDREN = 8
CHILD_MAX_FRACTION_OF_LEVEL = 1.0
assert 0.0 < CHILD_MAX_FRACTION_OF_LEVEL <= 1.0


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def executable_ladder(levels: Iterable[dict], limit) -> list:
    """The ladder levels at or inside `limit` (a BUY consumes ascending
    asks), each {price, qty} with qty > 0. Pure."""
    lim = _num(limit)
    out = []
    for lv in levels or []:
        px, q = _num((lv or {}).get("price")), _num((lv or {}).get("qty"))
        if px is None or q is None or q <= 0 or not 0.0 < px < 1.0:
            continue
        if lim is not None and px > lim + 1e-12:
            continue
        out.append({"price": px, "qty": q})
    out.sort(key=lambda x: x["price"])
    return out


def walk(ladder: list, qty: float) -> list:
    """[(price, qty)] consuming the ladder in price order up to `qty`."""
    left, fills = float(qty), []
    for lv in ladder:
        if left <= 1e-12:
            break
        take = min(left, lv["qty"])
        if take > 0:
            fills.append((lv["price"], take))
            left -= take
    return fills


def settlement_resolved(settlement: dict | None) -> bool:
    """COMPATIBLE terms -- or a contract the PRICED settlement-difference
    policy admitted, named by its own marker, id and CURRENT version with a
    priced probability (bettor_settlement_difference_policy): its exceptional
    states are priced into p, never read as compatible. Anything else is
    unresolved."""
    s = settlement or {}
    if str(s.get("compatibility") or "") == COMPATIBLE:
        return True
    from . import bettor_settlement_difference_policy as SDP
    return (str(s.get("compatibility") or "") == SDP.SETTLEMENT_PRICED
            and s.get("policy_id") == SDP.POLICY_ID
            and s.get("version") == SDP.VERSION
            and s.get("p") is not None)


def evaluate(*, p, levels, qty, limit, fee_fn: Callable | None,
             settlement: dict | None, identity: dict | None,
             adverse_selection_per_contract=None,
             incentives: Iterable[dict] = (),
             expected_hold_hours=None, size_factor: float = 1.0) -> dict:
    """Is this admitted ENTER capital-eligible, at what size, for how much?

    `fee_fn(qty, price) -> usd` (required; absent => refusal). `identity`
    needs us_market_slug, payout_event, fixture and holding_side. `size_factor`
    (<= 1, from the strategy lifecycle) only ever shrinks the size.
    """
    try:
        return _evaluate(p=p, levels=levels, qty=qty, limit=limit,
                         fee_fn=fee_fn, settlement=settlement,
                         identity=identity,
                         adverse_selection_per_contract=(
                             adverse_selection_per_contract),
                         incentives=incentives,
                         expected_hold_hours=expected_hold_hours,
                         size_factor=size_factor)
    except Exception as exc:                                    # noqa: BLE001
        return {"version": VERSION, "capital_eligible": False,
                "decision": CASH_WAIT, "allocation_usd": 0.0, "qty": 0,
                "refusals": [R_CE_GATE_ERROR],
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


def _evaluate(*, p, levels, qty, limit, fee_fn, settlement, identity,
              adverse_selection_per_contract, incentives,
              expected_hold_hours, size_factor) -> dict:
    refusals: list = []
    out: dict[str, Any] = {"version": VERSION,
                           "paper_only": True,
                           "objective": "NET_DOLLARS_PER_CAPITAL_HOUR"}
    ident = dict(identity or {})
    missing = [k for k in ("us_market_slug", "payout_event", "fixture",
                           "holding_side") if not ident.get(k)]
    out["identity_missing"] = missing
    if missing:
        refusals.append(R_CE_IDENTITY_UNRESOLVED)
    out["settlement_compatibility"] = (settlement or {}).get("compatibility")
    if not settlement_resolved(settlement):
        refusals.append(R_CE_SETTLEMENT_UNRESOLVED)
    pv, lim = _num(p), _num(limit)
    if pv is None or not 0.0 <= pv <= 1.0:
        refusals.append(R_CE_NO_PROBABILITY)
    if lim is None or not 0.0 < lim < 1.0:
        refusals.append(R_CE_NO_LIMIT)
    ladder = executable_ladder(levels, lim) if lim is not None else []
    depth = sum(lv["qty"] for lv in ladder)
    out["executable_depth"] = round(depth, 6)
    if depth <= 0:
        refusals.append(R_CE_NO_EXECUTABLE_DEPTH)
    intended = _num(qty) or 0.0
    factor = _num(size_factor)
    factor = 1.0 if factor is None else min(1.0, max(0.0, factor))
    capped = math.floor(min(intended * factor, depth) + 1e-9)
    out.update(intended_qty=intended, size_factor=factor,
               depth_capped=bool(depth < intended * factor),
               qty=int(max(capped, 0)))
    if capped < 1 and R_CE_NO_EXECUTABLE_DEPTH not in refusals:
        refusals.append(R_CE_SIZE_BELOW_ONE_CONTRACT)
    if fee_fn is None:
        refusals.append(R_CE_FEES_NOT_IDENTIFIED)
    if refusals:
        return dict(out, capital_eligible=False, decision=CASH_WAIT,
                    allocation_usd=0.0, qty=0, refusals=_dedup(refusals),
                    why=("not capital-eligible: %s" % ", ".join(
                        _dedup(refusals))))
    fills = walk(ladder, capped)
    filled = sum(q for _, q in fills)
    cost = sum(px * q for px, q in fills)
    vwap = cost / filled
    best = ladder[0]["price"]
    slippage = sum((px - best) * q for px, q in fills)
    fees = 0.0
    for px, q in fills:
        got = fee_fn(q, px)
        charge = got[0] if isinstance(got, tuple) else got
        c = _num(charge)
        if c is None:
            return dict(out, capital_eligible=False, decision=CASH_WAIT,
                        allocation_usd=0.0, qty=0,
                        refusals=[R_CE_FEES_NOT_IDENTIFIED],
                        why="the fee function returned no number")
        fees += abs(c)
    measured = _num(adverse_selection_per_contract)
    if measured is not None:
        as_per, as_basis = max(0.0, measured), AS_BASIS_MEASURED
    else:
        as_per, as_basis = max(0.0, lim - vwap), AS_BASIS_LIMIT_BOUND
    adverse = as_per * filled
    credited, not_credited = 0.0, []
    for inc in incentives or ():
        inc = dict(inc or {})
        usd = _num(inc.get("usd_per_contract"))
        if usd is not None and inc.get("terms_recorded") is True \
                and inc.get("terms_ref"):
            credited += max(0.0, usd) * filled
        else:
            not_credited.append({k: inc.get(k) for k in (
                "kind", "usd_per_contract", "terms_ref", "terms_recorded")})
    gross = filled * pv
    ev = gross - cost - fees - adverse + credited
    alloc = cost + fees
    hold = _num(expected_hold_hours)
    per_ch = (ev / (alloc * hold)) if hold and hold > 0 and alloc > 0 else None
    out.update(
        fills=[[px, q] for px, q in fills], filled_qty=filled,
        vwap=round(vwap, 9), best_price=best,
        expected_payout_usd=round(gross, 9), cost_usd=round(cost, 9),
        slippage_vs_top_usd=round(slippage, 9),
        slippage_is="INSIDE cost_usd (the walked levels); reported, not "
                    "subtracted twice",
        fees_usd=round(fees, 9),
        adverse_selection_usd=round(adverse, 9),
        adverse_selection_per_contract=round(as_per, 9),
        adverse_selection_basis=as_basis,
        incentives_credited_usd=round(credited, 9),
        incentives_not_credited=not_credited,
        total_executable_ev_usd=round(ev, 9),
        expected_hold_hours=hold,
        ev_per_capital_hour=(None if per_ch is None else round(per_ch, 9)),
        ev_per_capital_hour_status=("IDENTIFIED" if per_ch is not None
                                    else "UNAVAILABLE_NO_HOLD_ESTIMATE"))
    if ev <= 0:
        return dict(out, capital_eligible=False, decision=CASH_WAIT,
                    allocation_usd=0.0, qty=0,
                    refusals=[R_CE_CASH_WAIT_EV_NOT_POSITIVE],
                    why=("total executable EV %.6f <= 0 after fees, "
                         "adverse selection and recorded incentives: $0 is "
                         "allocated and CASH/WAIT wins" % ev))
    return dict(out, capital_eligible=True, decision=ENTER,
                allocation_usd=round(alloc, 9), refusals=[],
                why="every capital-eligibility condition holds")


def _dedup(xs: list) -> list:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE PARENT / CHILD PLAN (PAPER SIMULATION ONLY)
# ═════════════════════════════════════════════════════════════════════

def child_order_plan(levels, *, parent_qty, limit,
                     max_children: int = MAX_CHILDREN,
                     max_fraction_of_level: float = CHILD_MAX_FRACTION_OF_LEVEL
                     ) -> dict:
    """An adaptive parent -> child plan: one child per executable level, in
    price order, each bounded by that level's VISIBLE quantity (times
    `max_fraction_of_level` <= 1), whole contracts, until the parent is
    covered or the children / depth run out. The remainder is UNPLANNED --
    never assigned to depth that is not displayed. Pure; PAPER only."""
    frac = min(1.0, max(0.0, _num(max_fraction_of_level) or 0.0))
    ladder = executable_ladder(levels, limit)
    left = math.floor(_num(parent_qty) or 0.0)
    children = []
    for lv in ladder:
        if left < 1 or len(children) >= max(0, int(max_children)):
            break
        cap = math.floor(lv["qty"] * frac + 1e-9)
        q = min(left, cap)
        if q >= 1:
            children.append({"seq": len(children) + 1, "limit_price":
                             lv["price"], "qty": int(q),
                             "visible_qty_at_level": lv["qty"]})
            left -= q
    planned = sum(c["qty"] for c in children)
    return {"version": VERSION, "paper_simulation_only": True,
            "venue_submission": "NONE: a plan for the paper simulator only",
            "parent_qty": math.floor(_num(parent_qty) or 0.0),
            "children": children, "planned_qty": planned,
            "unplanned_qty": max(0, math.floor(_num(parent_qty) or 0.0)
                                 - planned),
            "executable_depth": round(sum(lv["qty"] for lv in ladder), 6),
            "bound": "each child <= the visible quantity of its level"}


# ═════════════════════════════════════════════════════════════════════
# VENUE-NEUTRAL EQUIVALENCE AND THE KALSHI READ PATH
# ═════════════════════════════════════════════════════════════════════

#: THE EXACT BLOCKER, as code. `kalshi_venue.KalshiClient.orderbook` is the
#: only Kalshi book read in the code base; it is credential-free but refuses
#: KALSHI_ENV_UNSET without the KALSHI_ENV variable, and NO production
#: module calls it -- no paper, entry or collector step reads a Kalshi book,
#: and no Kalshi market catalogue feeds `kalshi_mapping.establish` its
#: structured Kalshi-side records. Until both exist, routing is
#: Polymarket-only.
KALSHI_READ_PATH_MODULE = "sportsassets.kalshi_venue.KalshiClient.orderbook"
KALSHI_READ_PATH_ENDPOINT = "GET /markets/{ticker}/orderbook"
KALSHI_READ_PATH_ENV = "KALSHI_ENV"
KALSHI_READ_PATH_MISSING = (
    "NO_PRODUCTION_CALLER: no paper / entry / collector step calls "
    "KalshiClient.orderbook, and no Kalshi catalogue supplies the structured "
    "Kalshi records kalshi_mapping.establish needs")


def kalshi_read_path_status(env=None) -> dict:
    """BLOCKED, by name, until a production caller exists. Reads only the
    presence of KALSHI_ENV (never a secret). Pure apart from that read."""
    env = os.environ if env is None else env
    env_set = bool(str(env.get(KALSHI_READ_PATH_ENV, "") or "").strip())
    blockers = [KALSHI_READ_PATH_MISSING]
    if not env_set:
        blockers.append("KALSHI_ENV_UNSET: KalshiClient.orderbook refuses "
                        "before any request without %s"
                        % KALSHI_READ_PATH_ENV)
    return {"status": "BLOCKED", "refusal": R_CE_KALSHI_READ_PATH_BLOCKED,
            "module": KALSHI_READ_PATH_MODULE,
            "endpoint": KALSHI_READ_PATH_ENDPOINT,
            "env_variable": KALSHI_READ_PATH_ENV, "env_set": env_set,
            "blockers": blockers, "routing": "POLYMARKET_US_ONLY"}


def route_venue(*, polymarket: dict | None, kalshi: dict | None = None,
                mapping_verdict: str | None = None,
                read_path: dict | None = None) -> dict:
    """Which venue's quote may this ENTER use? Polymarket US unless a Kalshi
    quote is (a) read through an AVAILABLE production read path and (b) the
    SAME BET by `kalshi_mapping.establish` (ESTABLISHED payoff vectors) and
    (c) strictly cheaper after its own fees. (a) is BLOCKED in production,
    so this returns POLYMARKET_US -- the comparison is recorded, never used."""
    rp = read_path or kalshi_read_path_status()
    reasons = []
    if rp.get("status") != "AVAILABLE":
        reasons.append(R_CE_KALSHI_READ_PATH_BLOCKED)
    if mapping_verdict != "ESTABLISHED":
        reasons.append(R_CE_VENUE_NOT_EQUIVALENT)
    pm_cost = _num((polymarket or {}).get("all_in_cost_per_contract"))
    k_cost = _num((kalshi or {}).get("all_in_cost_per_contract"))
    use_kalshi = (not reasons and k_cost is not None and pm_cost is not None
                  and k_cost < pm_cost)
    return {"venue": KALSHI if use_kalshi else POLYMARKET_US,
            "kalshi_considered": kalshi is not None,
            "kalshi_refusals": reasons,
            "equivalence_rule": ("only when the payoff functions are "
                                 "genuinely equivalent "
                                 "(kalshi_mapping.establish == ESTABLISHED)"),
            "read_path": rp}


def describe() -> dict:
    return {"version": VERSION, "decisions": [ENTER, CASH_WAIT],
            "adverse_selection_bases": [AS_BASIS_MEASURED,
                                        AS_BASIS_LIMIT_BOUND],
            "refusals": [R_CE_NO_EXECUTABLE_DEPTH, R_CE_SETTLEMENT_UNRESOLVED,
                         R_CE_IDENTITY_UNRESOLVED, R_CE_NO_PROBABILITY,
                         R_CE_NO_LIMIT, R_CE_FEES_NOT_IDENTIFIED,
                         R_CE_SIZE_BELOW_ONE_CONTRACT,
                         R_CE_CASH_WAIT_EV_NOT_POSITIVE, R_CE_GATE_ERROR],
            "kalshi_read_path": kalshi_read_path_status(),
            "paper_only": True}
