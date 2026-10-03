"""VENUE SELECTION FOR ONE QUALIFIED BETTOR OPPORTUNITY (pure).

Given one opportunity the paper system has already qualified, and each
venue's VERIFIED executable economics, choose where the live copy goes --
by net expected value after every known cost, never by preference:

    net EV = gross EV at the venue's best price
             - expected slippage (walk the venue's book for the size)
             - entry fee (the venue's own fee function, per level)
             - exit cost estimate (per contract, venue-supplied)
             - other known costs

A venue is ELIGIBLE only when its contract mapping AND its settlement
compatibility are ESTABLISHED, its book is fresh, its costs are all known,
its book fills at least its minimum size at or under the limit, and its
net EV is strictly positive. Then:

    two eligible   the higher net EV wins; an exact tie is REFUSED
                   (choosing either would be a preference)
    one eligible   it is used
    none           REFUSED, with every venue's reasons

An unknown cost is never a zero: a venue whose fee function or exit cost is
absent is ineligible, with the reason named.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable

VERSION = "VENUE_SELECTION_V1"
CHOSEN = "CHOSEN"
REFUSED = "REFUSED"


def _d(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def walk_book(asks, count: int, limit) -> dict:
    """Take ask levels (price ascending) at or under `limit` until `count`
    contracts are filled. Returns the per-level fills, filled count, VWAP
    and slippage versus the best level."""
    lim = _d(limit)
    levels = sorted(((_d(p), _d(q)) for p, q in asks or []
                     if _d(p) is not None and _d(q) is not None and _d(q) > 0),
                    key=lambda x: x[0])
    want = Decimal(int(count))
    taken, filled, cost = [], Decimal(0), Decimal(0)
    for p, q in levels:
        if lim is not None and p > lim:
            break
        if filled >= want:
            break
        take = min(q, want - filled).to_integral_value(rounding="ROUND_FLOOR")
        if take <= 0:
            continue
        taken.append((p, take))
        filled += take
        cost += p * take
    best = taken[0][0] if taken else None
    vwap = (cost / filled) if filled else None
    slip = (cost - best * filled) if filled else Decimal(0)
    return {"levels": taken, "filled": int(filled), "cost": cost, "best": best,
            "vwap": vwap, "slippage": slip}


def evaluate(opportunity: dict, venue: dict) -> dict:
    """Per-venue net EV with the reasons it is (in)eligible."""
    name = venue.get("venue")
    reasons = []
    if venue.get("mapping_verdict") != "ESTABLISHED":
        reasons.append("MAPPING_NOT_ESTABLISHED")
    if venue.get("settlement_verdict") != "ESTABLISHED":
        reasons.append("SETTLEMENT_NOT_ESTABLISHED")
    age, max_age = _d(venue.get("book_age_s")), _d(venue.get("max_book_age_s"))
    if age is None or max_age is None or age > max_age:
        reasons.append("BOOK_NOT_FRESH")
    fee_fn: Callable | None = venue.get("fee_fn")
    if fee_fn is None:
        reasons.append("FEE_UNKNOWN")
    exit_cost = _d(venue.get("exit_cost_per_contract"))
    if exit_cost is None:
        reasons.append("EXIT_COST_UNKNOWN")
    other = _d(venue.get("other_costs", 0))
    if other is None:
        reasons.append("OTHER_COSTS_UNKNOWN")
    fair = _d(opportunity.get("fair_probability"))
    if fair is None or not (Decimal(0) < fair < Decimal(1)):
        reasons.append("FAIR_PROBABILITY_INVALID")
    w = walk_book(venue.get("asks"), int(opportunity.get("count") or 0),
                  opportunity.get("limit_price"))
    min_count = int(venue.get("min_count") or 1)
    if w["filled"] < min_count:
        reasons.append("INSUFFICIENT_DEPTH_AT_LIMIT")
    out = {"venue": name, "filled": w["filled"], "best": w["best"],
           "vwap": w["vwap"], "slippage": w["slippage"], "entry_fee": None,
           "exit_cost": None, "other_costs": other, "gross_ev": None,
           "net_ev": None}
    if not reasons:
        fee = sum((Decimal(str(fee_fn(int(q), p))) for p, q in w["levels"]),
                  Decimal(0))
        gross = (fair - w["best"]) * w["filled"]
        ex = exit_cost * w["filled"]
        net = gross - w["slippage"] - fee - ex - other
        out.update(entry_fee=fee, exit_cost=ex, gross_ev=gross, net_ev=net)
        if net <= 0:
            reasons.append("NET_EV_NOT_POSITIVE")
    out["eligible"] = not reasons
    out["reasons"] = reasons
    return out


def select(opportunity: dict, venues: list) -> dict:
    evals = [evaluate(opportunity, v) for v in venues]
    ok = [e for e in evals if e["eligible"]]
    base = {"version": VERSION, "opportunity_id": opportunity.get("opportunity_id"),
            "evaluations": evals}
    if not ok:
        return dict(base, decision=REFUSED, venue=None,
                    reasons={e["venue"]: e["reasons"] for e in evals}
                    or {"_": ["NO_VENUES"]})
    if len(ok) == 1:
        return dict(base, decision=CHOSEN, venue=ok[0]["venue"],
                    why="ONLY_ELIGIBLE_VENUE", net_ev=ok[0]["net_ev"])
    ok.sort(key=lambda e: e["net_ev"], reverse=True)
    if ok[0]["net_ev"] == ok[1]["net_ev"]:
        return dict(base, decision=REFUSED, venue=None,
                    reasons={"_": ["EQUAL_NET_EV_NO_PREFERENCE"]})
    return dict(base, decision=CHOSEN, venue=ok[0]["venue"],
                why="HIGHER_NET_EV", net_ev=ok[0]["net_ev"],
                runner_up={"venue": ok[1]["venue"], "net_ev": ok[1]["net_ev"]})
