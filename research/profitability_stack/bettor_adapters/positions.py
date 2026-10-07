"""Settled paper positions reconstructed from the paper order record.

A position is one ENTRY decision's order group (group_id): the ENTRY buy and
every SELL that closed part of it (STANDING_PROTECTION, EXIT, REDUCE), plus the
settlement of whatever quantity was still held. Realized P&L is cash only:
   - entry cost - entry fees + sell proceeds - sell fees + held qty x payout.
A position is SETTLED when nothing is left held (sold out) or the remaining
quantity has a recorded paper settlement; anything else is OPEN and excluded.
Historical PAPER rows are read, never changed.
"""
from __future__ import annotations

from collections import defaultdict

from common import event_identity, family_of, orient, regime_of, sport_of, VENUE_PMUS

EPS = 1e-6


def build_positions(orders: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in orders:
        groups[r["group_id"]].append(r)
    out = []
    for gid, rs in groups.items():
        entries = [r for r in rs if r["role"] == "ENTRY" and (r.get("fill_qty") or 0) > 0]
        if len(entries) != 1:
            continue
        e = entries[0]
        q = float(e["fill_qty"])
        entry_px = float(e["vwap"])
        entry_fee = float(e.get("fees") or 0.0)
        sells = [r for r in rs if r.get("direction") == "SELL" and (r.get("fill_qty") or 0) > 0]
        sold = sum(float(r["fill_qty"]) for r in sells)
        proceeds = sum(float(r["fill_qty"]) * float(r["vwap"]) for r in sells)
        sell_fees = sum(float(r.get("fees") or 0.0) for r in sells)
        held = max(0.0, q - sold)
        payout = e.get("settle_payout")
        if held > EPS and payout is None:
            status = "OPEN"
        else:
            status = "SETTLED"
        settle_cash = held * float(payout) if (held > EPS and payout is not None) else 0.0
        realized = -q * entry_px - entry_fee + proceeds - sell_fees + settle_cash
        ev, src = event_identity(e)
        book = orient(e.get("sub_bid"), e.get("sub_ask"), e.get("side"))
        out.append({
            "group_id": gid, "decision_id": e.get("decision_id"), "strategy": e.get("strategy"),
            "slug": e["slug"], "side": e.get("side"), "event": ev, "event_src": src,
            "sport": sport_of(e), "family": family_of(e), "venue": VENUE_PMUS,
            "regime": regime_of(e.get("decided") or e.get("created"), e.get("start")),
            "status": status, "qty": q, "entry_px": entry_px, "entry_fee": entry_fee,
            "limit": e.get("limit"), "p": e.get("p_pin"),
            "touch_ask": book[1] if book else None, "touch_mid": 0.5 * (book[0] + book[1]) if book else None,
            "sold_qty": sold, "sell_vwap": proceeds / sold if sold > EPS else None,
            "sell_fees": sell_fees, "held_at_settlement": held,
            "payout": None if payout is None else float(payout),
            "outcome": e.get("settle_outcome"), "settled_at": e.get("settled"),
            "entered_at": e.get("first_fill"), "realized_pnl": realized,
            "realized_per_contract": realized / q, "cost": q * entry_px + entry_fee,
        })
    return out
