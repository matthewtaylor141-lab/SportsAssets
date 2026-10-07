"""STRUCTURAL ARB on BETTOR's contract registry + venue books.

Input: research/ps_arb_v1.sql -- per (event, 5-minute bucket) every registry
leg with its ontology meaning, rules sha, registry settlement state and the
latest book inside the bucket (bid / ask / top qty), plus venue counts.

Equivalence is NEVER inferred from labels. A basket is ELIGIBLE only when
every leg's settlement identity is PROVEN in the Settlement Rule Registry and
the payoff vectors are built from that proven identity. Everything else is
reported as a DIAGNOSTIC with the exact reason it is not eligible, priced
after fees (BETTOR's published schedule) and capped at displayed top-of-book
depth, so the receipt shows what the books say without claiming a lock.

Checks run on every bucket:
  * same-book complement: a PMUS SHORT is the complement view of the one LONG
    book (independent_short_book = false), so YES + NO costs ask + (1 - bid);
  * implication bounds: same event / subject / metric / period / operator
    TOTAL with lines L1 < L2 -> OVER(L2) implies OVER(L1), so a bid on the
    stronger leg above the ask on the weaker one is a bound violation;
  * exhaustive baskets: legs whose ontology states mutually-exclusive,
    exhaustive outcomes of one event (3-way winner with a draw leg) are
    solved with the package superhedge LP;
  * cross-venue: Kalshi contracts in the registry with no book -> UNMEASURED.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict

from common import fee_per_contract, iso, window
from bettor_profit_stack.structural_arb import (Contract, complement_pair, implication_violation,
                                                solve_superhedge)

#: the only registry state (migration 312, market_plane.settlement) that proves
#: the venue's settlement rules are the ones priced. PROVEN_DIFFERENT_BUT_PRICED
#: proves a DIFFERENCE and is never eligible; everything else is unproven.
PROVEN = "SETTLEMENT_PROVEN_COMPATIBLE"


def leg_proven(leg: dict) -> bool:
    return leg.get("settlement_state") == PROVEN


def _fee(price, at):
    f, why = fee_per_contract(price, "TAKER", at)
    return f, why


def complement_check(leg: dict, at: str) -> dict:
    bid, ask = leg.get("bid"), leg.get("ask")
    if bid is None or ask is None:
        return {"contract_id": leg["contract_id"], "status": "UNMEASURED", "reason": "NO_BOOK"}
    indep = (leg.get("sides") or {}).get("independent_short_book")
    yes, no = float(ask), 1.0 - float(bid)
    fy, wy = _fee(yes, at)
    fn, wn = _fee(no, at)
    if fy is None or fn is None:
        return {"contract_id": leg["contract_id"], "status": "UNMEASURED", "reason": wy if fy is None else wn}
    p = complement_pair(yes, no, fy, fn)
    return {"contract_id": leg["contract_id"], "independent_short_book": indep,
            "structural_note": "SAME_BOOK_COMPLEMENT_COST_IS_1_PLUS_SPREAD" if indep is False else None,
            **asdict(p)}


def _total_key(leg):
    m = leg.get("meaning") or {}
    if m.get("operator") != "TOTAL" or m.get("line") is None:
        return None
    return (m.get("event_id"), m.get("subject_type"), m.get("subject_id"), m.get("competition"),
            m.get("metric"), m.get("period"), m.get("raw_market_type"))


def implication_checks(legs: list[dict], at: str) -> list[dict]:
    """OVER(L2) => OVER(L1) for L1 < L2 on the same total. Sell the stronger
    at its bid, buy the weaker at its ask; costs = both taker fees."""
    out = []
    groups = defaultdict(list)
    for L in legs:
        k = _total_key(L)
        side = ((L.get("sides") or {}).get("LONG") or {}).get("side_norm")
        if k and side == "over" and L.get("bid") is not None and L.get("ask") is not None:
            groups[k].append(L)
    for k, ls in groups.items():
        ls = sorted(ls, key=lambda x: float(x["meaning"]["line"]))
        for i in range(len(ls)):
            for j in range(i + 1, len(ls)):
                weak, strong = ls[i], ls[j]
                fw, _ = _fee(float(weak["ask"]), at)
                fs, _ = _fee(float(strong["bid"]), at)
                if fw is None or fs is None:
                    out.append({"stronger": strong["contract_id"], "weaker": weak["contract_id"],
                                "status": "UNMEASURED", "reason": "FEE_SCHEDULE_REFUSED"})
                    continue
                v = implication_violation(float(strong["bid"]), float(weak["ask"]), fw + fs)
                v.update(stronger=strong["contract_id"], weaker=weak["contract_id"],
                         executable_qty=min(float(strong.get("bid_q") or 0), float(weak.get("ask_q") or 0)),
                         eligible=leg_proven(strong) and leg_proven(weak),
                         ineligible_reason=None if (leg_proven(strong) and leg_proven(weak))
                         else "SETTLEMENT_IDENTITY_NOT_PROVEN")
                out.append(v)
    return out


def exhaustive_check(legs: list[dict], at: str) -> dict | None:
    """Only when the ontology itself states a complete partition: winner legs
    of one event that include an explicit draw leg and one leg per side."""
    win = [L for L in legs if (L.get("meaning") or {}).get("metric") == "WINNER"
           and L.get("ask") is not None]
    sides = {(L.get("meaning") or {}).get("side") or (L.get("meaning") or {}).get("subject_id")
             for L in win}
    has_draw = any("draw" in str(L["contract_id"]).lower() for L in win)
    if len(win) < 3 or not has_draw or len(sides) < 3:
        return None
    n = len(win)
    cs = []
    for i, L in enumerate(win):
        f, why = _fee(float(L["ask"]), at)
        if f is None:
            return {"status": "UNMEASURED", "reason": why}
        cs.append(Contract(L["contract_id"], L["venue"], float(L["ask"]),
                           tuple(1.0 if s == i else 0.0 for s in range(n)),
                           max_qty=float(L.get("ask_q") or 0), fee_per_contract=f,
                           settlement_key=L.get("rules_sha") or ""))
    p = solve_superhedge(cs)
    ok = all(leg_proven(L) for L in win)
    return {"legs": [c.contract_id for c in cs], **asdict(p), "eligible": ok and p.executable,
            "ineligible_reason": None if ok else "PARTITION_FROM_LABELS_SETTLEMENT_NOT_PROVEN"}


def run(rows: list[dict], venue_counts: dict) -> dict:
    states = Counter()
    comp, impl, exh = [], [], []
    eligible_baskets = 0
    for r in rows:
        at = iso(r.get("bucket"))
        legs = r.get("legs") or []
        for L in legs:
            states[L.get("settlement_state")] += 1
            comp.append(complement_check(L, at))
        impl += implication_checks(legs, at)
        e = exhaustive_check(legs, at)
        if e:
            exh.append(e)
            eligible_baskets += 1 if e.get("eligible") else 0
    eligible_baskets += sum(1 for v in impl if v.get("eligible") and v.get("candidate"))
    events = {r["event_id"] for r in rows}
    comp_measured = [c for c in comp if "guaranteed_profit" in c]
    reasons = Counter()
    if not any(s == PROVEN for s in states):
        reasons["NO_LEG_HAS_PROVEN_SETTLEMENT_IDENTITY"] += 1
    if not any(c.get("executable") for c in comp_measured):
        reasons["NO_COMPLEMENT_LOCK_AFTER_FEES"] += 1
    if not impl:
        reasons["NO_SAME_TOTAL_MULTI_LINE_PAIRS_IN_WINDOW"] += 1
    if not exh:
        reasons["NO_ONTOLOGY_STATED_EXHAUSTIVE_PARTITION_WITH_BOOKS"] += 1
    kalshi = venue_counts.get("KALSHI")
    return {
        "module": "STRUCTURAL_ARB",
        "decision_rows": {"event_buckets_scanned": len(rows),
                          "legs_scanned": sum(len(r.get("legs") or []) for r in rows)},
        "unique_independent_events": len(events),
        "window": window(r.get("bucket") for r in rows),
        "registry_settlement_states_of_scanned_legs": dict(states),
        "eligible_locked_baskets": eligible_baskets,
        "complement": {
            "checked": len(comp), "measured": len(comp_measured),
            "locked_after_fees": sum(1 for c in comp_measured if c.get("executable")),
            "best_guaranteed_profit": max((c["guaranteed_profit"] for c in comp_measured), default=None),
            "median_cost": sorted(c["total_cost"] for c in comp_measured)[len(comp_measured) // 2]
            if comp_measured else None},
        "implication_bounds": {"pairs": len(impl),
                               "violations_after_costs": sum(1 for v in impl if v.get("candidate")),
                               "eligible_violations": sum(1 for v in impl if v.get("candidate") and v.get("eligible")),
                               "rows": impl[:50]},
        "exhaustive_baskets": {"checked": len(exh), "rows": exh[:50]},
        "cross_venue": {"status": "UNMEASURED",
                        "reason": "KALSHI_CONTRACTS_IN_REGISTRY_HAVE_NO_RECORDED_BOOKS",
                        "kalshi_registry": kalshi,
                        "pmus_registry": venue_counts.get("POLYMARKET_US")},
        "result": "NO_ELIGIBLE_STRUCTURAL_ARB" if eligible_baskets == 0 else "ELIGIBLE_BASKETS_FOUND_SHADOW_ONLY",
        "reasons": dict(reasons),
    }
