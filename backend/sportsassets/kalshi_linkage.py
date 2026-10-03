"""KALSHI LIVE INTENT <-> PAPER RECORD LINKAGE, AND THE TWO VIEWS OF IT.

Building blocks only -- NOT wired into any runner.

  link record   one Kalshi live intent tied to the paper decision, order and
                fills it was derived from, with execmirror_orders' sizing
                columns (paper_qty, scale, raw_scaled_qty, rounded_qty,
                rounding_delta, venue_minimum, live_notional, exclusion).
  Xavier        the ACTUAL position on Kalshi: venue, ticker, side, count,
                average price, fees -- from venue FILLS only. An accepted
                order is not a fill and moves nothing here.
  Audrey        the paper-vs-live reconciliation diff per link: quantity,
                price, fees, timing, and every missing link in either
                direction.

Paper and live ledgers stay separate: this module reads paper rows handed
to it and writes nothing.
"""
from __future__ import annotations

from decimal import Decimal

from . import execmirror as EM
from . import kalshi_account as KA
from . import kalshi_orders as KO

VERSION = "KALSHI_LINKAGE_V1"
VENUE = "KALSHI"


def link_id(paper_order_id: str) -> str:
    return "kl:" + str(paper_order_id)


def build_link(paper_order: dict, plan: dict, *, paper_decision_id=None,
               paper_fill_ids=(), mirror_id: str | None = None) -> dict:
    rounded = int(plan.get("live_qty") or 0)
    px = plan.get("price")
    return {
        "link_id": link_id(paper_order["order_id"]),
        "mirror_id": mirror_id or paper_order.get("mirror_id"),
        "venue": VENUE,
        "paper_decision_id": paper_decision_id or paper_order.get("decision_id"),
        "paper_order_id": paper_order["order_id"],
        "paper_fill_ids": list(paper_fill_ids),
        "group_id": paper_order.get("group_id"),
        "role": paper_order.get("role"),
        "us_market_slug": paper_order.get("us_market_slug"),
        "ticker": plan.get("ticker"),
        "holding": plan.get("holding"), "contract_side": plan.get("contract_side"),
        "action": plan.get("action"), "intent": plan.get("intent"),
        "paper_qty": plan.get("paper_qty", _dec(paper_order.get("qty"))),
        "scale": plan.get("scale"),
        "raw_scaled_qty": plan.get("raw_scaled_qty"),
        "rounded_qty": rounded,
        "rounding_delta": plan.get("rounding_delta"),
        "venue_minimum": plan.get("venue_minimum", KO.VENUE_MINIMUM),
        "paper_wire_price": plan.get("paper_wire_price"),
        "kalshi_price": px,
        "price_rounding_delta": plan.get("price_rounding_delta"),
        "live_notional": (px * rounded) if (px is not None and rounded) else None,
        "fee_estimate": plan.get("fee_estimate"),
        "exclusion": plan.get("exclusion"),
        "state": plan.get("state"),
        "client_order_id": plan.get("client_order_id"),
        "venue_order_id": None,
        "paper_decided_at": paper_order.get("decided_at"),
    }


def _dec(v):
    return None if v is None else Decimal(str(v))


def _fills(rows) -> list:
    out = []
    for r in rows or []:
        out.append(r if r.get("parsed") else KA.parse_fill(r))
    return out


# ── Xavier: actual Kalshi positions from venue fills ─────────────────

def xavier_positions(fill_rows) -> list:
    """Per (ticker, side): net count (buys - sells), average BUY price, fees
    the venue stated (fees_complete False when any fill stated none), number
    of fills and the last fill time. Zero-count rows are omitted."""
    agg: dict = {}
    seen = set()
    for f in _fills(fill_rows):
        if not f.get("trade_id") or f["trade_id"] in seen:
            continue
        if f.get("count") is None or f.get("price") is None:
            continue
        seen.add(f["trade_id"])
        k = (f["ticker"], f.get("side") or "yes")
        a = agg.setdefault(k, {"bought": Decimal(0), "sold": Decimal(0),
                               "buy_cost": Decimal(0), "fees": Decimal(0),
                               "fees_complete": True, "fills": 0, "last": None})
        if f["action"] == "sell":
            a["sold"] += f["count"]
        else:
            a["bought"] += f["count"]
            a["buy_cost"] += f["count"] * f["price"]
        if f.get("fee_usd") is None:
            a["fees_complete"] = False
        else:
            a["fees"] += f["fee_usd"]
        a["fills"] += 1
        t = f.get("created_time")
        if t and (a["last"] is None or str(t) > str(a["last"])):
            a["last"] = t
    out = []
    for (ticker, side), a in sorted(agg.items()):
        count = a["bought"] - a["sold"]
        if count == 0:
            continue
        out.append({"venue": VENUE, "ticker": ticker, "side": side,
                    "count": count,
                    "avg_price": (a["buy_cost"] / a["bought"]).quantize(
                        Decimal("0.0001")) if a["bought"] else None,
                    "fees_usd": a["fees"], "fees_complete": a["fees_complete"],
                    "fills": a["fills"], "last_fill_at": a["last"],
                    "source": "VENUE_FILLS"})
    return out


# ── Audrey: paper vs live per link ───────────────────────────────────

def _vwap(rows, qty_key, px_key):
    q = sum((Decimal(str(r[qty_key])) for r in rows), Decimal(0))
    if not q:
        return Decimal(0), None
    c = sum((Decimal(str(r[qty_key])) * Decimal(str(r[px_key])) for r in rows),
            Decimal(0))
    return q, (c / q).quantize(Decimal("0.0001"))


def audrey_diff(links: list, paper_fills: list, live_fill_rows: list) -> dict:
    """paper_fills: paper_fills rows (fill_id, order_id, qty, price -- the
    HELD side's price, fee_usd, filled_at). live_fill_rows: venue fills."""
    live = [f for f in _fills(live_fill_rows) if f.get("count") is not None]
    by_order_live: dict = {}
    for f in live:
        by_order_live.setdefault(f["order_id"], []).append(f)
    by_order_paper: dict = {}
    for p in paper_fills:
        by_order_paper.setdefault(p["order_id"], []).append(p)
    rows, issues = [], []
    linked_orders = {lk.get("venue_order_id") for lk in links if lk.get("venue_order_id")}
    linked_paper = {lk["paper_order_id"] for lk in links}
    for lk in links:
        pf = by_order_paper.get(lk["paper_order_id"], [])
        lf = by_order_live.get(lk.get("venue_order_id"), []) if lk.get("venue_order_id") else []
        pq, ppx = _vwap(pf, "qty", "price")
        lq, lpx = _vwap(lf, "count", "price")
        scale = Decimal(str(lk.get("scale") or 1000))
        if lk.get("action") == "sell":
            # an exit is sized off LIVE inventory (execmirror.plan_sell), not
            # off the paper quantity / scale: its expectation is the plan's
            expect_live = int(lk.get("rounded_qty") or 0) if pq else 0
        else:
            _, expect_live, _ = EM.scale_qty(pq, scale) if pq else (None, 0, None)
        pfee = sum((Decimal(str(p.get("fee_usd") or 0)) for p in pf), Decimal(0))
        lfee_known = all(f.get("fee_usd") is not None for f in lf)
        lfee = sum((f["fee_usd"] for f in lf if f.get("fee_usd") is not None),
                   Decimal(0))
        pt = min((KO.epoch_s(p.get("filled_at")) for p in pf
                  if KO.epoch_s(p.get("filled_at")) is not None), default=None)
        lt = min((KO.epoch_s(f.get("created_time")) for f in lf
                  if KO.epoch_s(f.get("created_time")) is not None), default=None)
        codes = []
        if lk.get("exclusion"):
            codes.append("EXCLUDED:%s" % lk["exclusion"])
        elif not lk.get("venue_order_id"):
            codes.append("NO_LIVE_ORDER")
        elif pq and not lf:
            codes.append("NO_LIVE_FILL")
        if lf and not pf:
            codes.append("LIVE_WITHOUT_PAPER_FILL")
        if lf and not lfee_known:
            codes.append("LIVE_FEE_UNSTATED")
        if lf and Decimal(expect_live) != lq:
            codes.append("QTY_DIFF")
        rows.append({
            "link_id": lk["link_id"], "paper_order_id": lk["paper_order_id"],
            "venue_order_id": lk.get("venue_order_id"), "ticker": lk.get("ticker"),
            "paper_qty": pq, "expected_live_qty": expect_live, "live_qty": lq,
            "qty_diff": lq - Decimal(expect_live),
            "paper_price": ppx, "live_price": lpx,
            "price_diff": (lpx - ppx) if (lpx is not None and ppx is not None) else None,
            "paper_fee_scaled": (pfee / scale).quantize(Decimal("0.000001")),
            "live_fee": lfee if lfee_known else None,
            "fee_diff": ((lfee - pfee / scale).quantize(Decimal("0.000001"))
                         if lf and lfee_known else None),
            "paper_first_fill_at": pt, "live_first_fill_at": lt,
            "timing_s": (lt - pt) if (lt is not None and pt is not None) else None,
            "codes": codes})
    for oid, fs in by_order_live.items():
        if oid not in linked_orders:
            issues.append({"code": "UNLINKED_LIVE_FILL", "order_id": oid,
                           "trade_ids": [f["trade_id"] for f in fs]})
    for oid in by_order_paper:
        if oid not in linked_paper:
            issues.append({"code": "PAPER_FILL_WITHOUT_LINK", "paper_order_id": oid})
    return {"version": VERSION, "rows": rows, "missing_links": issues,
            "clean": not issues and all(not r["codes"] for r in rows)}
