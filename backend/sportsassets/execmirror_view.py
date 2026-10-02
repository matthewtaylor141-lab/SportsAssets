"""THE LIVE EXECUTION MIRROR · 1:1,000 AS MANAGEMENT SEES IT (read-only).

Per mirrored paper order: the paper order and its expected scaled quantity,
the live venue order (id, accepted quantity, standing status), the venue's
fills, fees, submission latency, exclusions and the execution difference.
Per market and in total: paper P&L / scale beside actual live P&L, with the
difference split into quantity rounding, fill shortfall, price and fees.

Paper and live ledgers stay separate: paper figures come from paper tables,
live figures from venue-confirmed fills and the venue's account snapshot.
"""
from __future__ import annotations

import json
from decimal import Decimal

TRAINING_HINTS = ("EXPLOR", "TRAIN")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, "") else Decimal(0)
    except Exception:                                          # noqa: BLE001
        return Decimal(0)


def _f(v, nd=6):
    return None if v is None else round(float(v), nd)


def _js(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:                                      # noqa: BLE001
            return {}
    return v or {}


def strategy_kind(strategy, label) -> dict:
    s = str(strategy or "").upper()
    lab = _js(label)
    training = any(h in s for h in TRAINING_HINTS) or bool(
        lab.get("training") or lab.get("negative_ev"))
    out = {"kind": "TRAINING" if training else "INVESTMENT", "strategy": strategy}
    if training:
        out["disclosure"] = (lab.get("disclosure") or
                             "Training order: described at decision time as "
                             "negative expected value; placed to learn, not to "
                             "earn.")
    return out


def live_cash_flow(fills) -> Decimal:
    """Cash out of the account for live fills (buys negative, sells
    positive, fees negative), in the venue's collateral space."""
    cash = Decimal(0)
    for f in fills:
        q, px, fee = _d(f["qty"]), _d(f["price"]), _d(f["fee_usd"])
        intent = str(f["intent"])
        if intent == "ORDER_INTENT_BUY_LONG":
            cash -= q * px
        elif intent == "ORDER_INTENT_BUY_SHORT":
            cash -= q * (1 - px)
        elif intent == "ORDER_INTENT_SELL_LONG":
            cash += q * px
        elif intent == "ORDER_INTENT_SELL_SHORT":
            cash += q * (1 - px)
        cash -= fee
    return cash


def decompose(*, paper_pnl, paper_entry_qty, scale, live_target_qty,
              live_filled_qty, live_pnl, live_fees, paper_fees) -> dict:
    """Live P&L minus paper P&L / scale, explained.

    per-contract paper P&L x (live target - paper qty / scale) = rounding;
    per-contract paper P&L x (live filled - live target)      = fill shortfall;
    -(live fees - paper fees / scale)                           = fees;
    the remainder                                               = price / timing."""
    scale = _d(scale)
    expected = _d(paper_pnl) / scale
    pq = _d(paper_entry_qty)
    per = (_d(paper_pnl) + _d(paper_fees)) / pq if pq else Decimal(0)
    rounding = per * (_d(live_target_qty) - pq / scale)
    fill = per * (_d(live_filled_qty) - _d(live_target_qty))
    fees = -(_d(live_fees) - _d(paper_fees) / scale)
    diff = _d(live_pnl) - expected
    price = diff - rounding - fill - fees
    return {"expected_live_pnl": _f(expected), "live_pnl": _f(live_pnl),
            "difference": _f(diff),
            "explained": {"quantity_rounding": _f(rounding),
                          "fill_shortfall": _f(fill), "fees": _f(fees),
                          "price_and_timing": _f(price)},
            "tracking_ratio": (_f(_d(live_pnl) / expected, 4)
                               if expected else None)}


async def view(conn, *, limit: int = 200) -> dict:
    ctl = dict(await conn.fetchrow("SELECT * FROM execmirror_control WHERE id = 1") or {})
    scale = _d(ctl.get("scale") or 1000)
    snap = await conn.fetchrow(
        "SELECT * FROM execmirror_snapshots ORDER BY at DESC LIMIT 1")
    rows = await conn.fetch(
        """SELECT m.*, p.state AS paper_state, p.filled_qty AS paper_filled_qty,
                  p.label AS paper_label,
                  (SELECT sum(f.qty * f.price) / nullif(sum(f.qty), 0)
                     FROM paper_fills f WHERE f.order_id = m.paper_order_id) AS paper_avg_px,
                  (SELECT sum(f.fee_usd) FROM paper_fills f
                    WHERE f.order_id = m.paper_order_id) AS paper_fees
             FROM execmirror_orders m LEFT JOIN paper_orders p
               ON p.order_id = m.paper_order_id
            ORDER BY m.created_at DESC LIMIT $1""", limit)
    orders = []
    for r in rows:
        r = dict(r)
        det = _js(r.get("detail"))
        live_px = r.get("avg_px")
        orders.append({
            "mirror_id": r["mirror_id"], "paper_order_id": r["paper_order_id"],
            "decided_at": r["paper_decided_at"], "role": r["role"],
            "strategy": strategy_kind(r.get("strategy"), r.get("paper_label")),
            "market": r["us_market_slug"], "group_id": r["group_id"],
            "intent": r["intent"], "order_type": r["order_type"], "tif": r["tif"],
            "post_only": r["post_only"], "wire_price": _f(r["wire_price"]),
            "good_till": r["good_till"],
            "paper": {"qty": _f(r["paper_qty"]), "state": r.get("paper_state"),
                      "filled_qty": _f(r.get("paper_filled_qty")),
                      "avg_px": _f(r.get("paper_avg_px")),
                      "fees_usd": _f(r.get("paper_fees"))},
            "expected_scaled_qty": _f(r["scaled_qty"]),
            "live": {"qty": r["live_qty"], "rounding_delta": _f(r["rounding_delta"]),
                     "state": r["state"], "exclusion": r["exclusion"],
                     "exclusion_detail": ({k: det.get(k) for k in
                                           ("why", "cost_usd", "cap_usd",
                                            "buying_power_usd", "held", "committed")
                                           if k in det} if r["exclusion"] else None),
                     "venue_order_id": r["venue_order_id"],
                     "venue_state": r["venue_state"], "filled_qty": _f(r["cum_qty"]),
                     "avg_px": _f(live_px), "fees_usd": _f(r["fees_usd"]),
                     "submit_latency_ms": r["latency_ms"],
                     "decision_to_accept_ms": det.get("decision_to_accept_ms"),
                     "error": _js(r.get("error")) or None},
            "difference": {
                "price_per_contract": (_f(_d(live_px) - _d(r.get("paper_avg_px")))
                                       if live_px is not None and r.get("paper_avg_px") is not None
                                       else None),
                "fill_ratio_vs_paper": (_f(_d(r["cum_qty"]) * scale / _d(r["paper_filled_qty"]), 4)
                                        if r.get("paper_filled_qty") else None)},
        })
    counts = {f"{r['state']}{(':' + r['exclusion']) if r['exclusion'] else ''}": r["n"]
              for r in await conn.fetch(
                  "SELECT state, exclusion, count(*) AS n FROM execmirror_orders GROUP BY 1, 2")}
    eligible = await conn.fetchval(
        """SELECT count(*) FROM execmirror_orders WHERE paper_order_id IS NOT NULL""") or 0
    mirrored = await conn.fetchval(
        """SELECT count(*) FROM execmirror_orders WHERE paper_order_id IS NOT NULL
             AND state <> 'EXCLUDED'""") or 0

    # ── P&L: per market, paper / scale beside live ──
    markets = []
    totals = {"paper_pnl": Decimal(0), "live_pnl": Decimal(0)}
    positions = {p.get("slug"): p for p in (_js(snap["positions"]) if snap else [])} if snap else {}
    for m in await conn.fetch(
            """SELECT us_market_slug, array_agg(DISTINCT group_id) AS groups
                 FROM execmirror_orders WHERE group_id IS NOT NULL
                  AND role IN ('ENTRY','HEDGE') AND state <> 'EXCLUDED'
                GROUP BY 1"""):
        groups = [g for g in m["groups"] if g]
        slug = m["us_market_slug"]
        led = await conn.fetchrow(
            """SELECT coalesce(sum(cash_delta_usd) FILTER (WHERE kind IN ('FILL','SALE','SETTLEMENT')), 0) AS cash
                 FROM paper_ledger WHERE group_id = ANY($1)""", groups)
        pf = await conn.fetchrow(
            """SELECT coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bq,
                      coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sq,
                      coalesce(sum(fee_usd), 0) AS fees
                 FROM paper_fills WHERE group_id = ANY($1)""", groups)
        settled = await conn.fetchval(
            "SELECT count(*) FROM paper_settlements WHERE group_id = ANY($1)", groups)
        fills = await conn.fetch(
            "SELECT * FROM execmirror_fills WHERE group_id = ANY($1)", groups)
        live_cash = live_cash_flow(fills)
        pos = positions.get(slug) or {}
        net = abs(_d(pos.get("netPosition")))
        cash_value = _d(pos.get("cashValue"))
        mark = (cash_value / net) if net else None
        paper_open = _d(pf["bq"]) - _d(pf["sq"]) if not settled else Decimal(0)
        paper_mark_value = paper_open * mark if (mark is not None and paper_open > 0) else Decimal(0)
        paper_pnl = _d(led["cash"]) + paper_mark_value
        live_open_value = cash_value if net else Decimal(0)
        realized_settle = _d(pos.get("realized")) if pos.get("expired") else Decimal(0)
        live_pnl = live_cash + live_open_value + realized_settle
        tgt = await conn.fetchval(
            """SELECT coalesce(sum(live_qty), 0) FROM execmirror_orders
                WHERE group_id = ANY($1) AND role IN ('ENTRY','HEDGE') AND state <> 'EXCLUDED'""",
            groups)
        filled = sum(_d(f["qty"]) for f in fills if str(f["intent"]).startswith("ORDER_INTENT_BUY"))
        live_fees = sum(_d(f["fee_usd"]) for f in fills)
        markets.append({"market": slug, "groups": groups,
                        "paper": {"pnl": _f(paper_pnl), "entry_qty": _f(pf["bq"]),
                                  "open_qty": _f(paper_open), "fees": _f(pf["fees"]),
                                  "settled": bool(settled)},
                        "live": {"pnl": _f(live_pnl), "entry_target_qty": int(tgt),
                                 "entry_filled_qty": _f(filled), "open_qty": _f(net),
                                 "fees": _f(live_fees), "mark": _f(mark)},
                        "comparison": decompose(
                            paper_pnl=paper_pnl, paper_entry_qty=pf["bq"], scale=scale,
                            live_target_qty=tgt, live_filled_qty=filled,
                            live_pnl=live_pnl, live_fees=live_fees,
                            paper_fees=pf["fees"])})
        totals["paper_pnl"] += paper_pnl
        totals["live_pnl"] += live_pnl
    expected_total = totals["paper_pnl"] / scale
    bal = (_js(snap["balances"]) if snap else []) or []
    return {
        "title": "Live execution mirror · 1:%s" % int(scale),
        "basis": ("Paper experiment is the decision source; live orders are "
                  "placed on a separate Polymarket US account at paper "
                  "quantity / %s (nearest whole contract). Live fills come only "
                  "from the venue's order records." % int(scale)),
        "control": {k: ctl.get(k) for k in ("enabled", "stopped", "flatten_on_stop",
                                           "stop_done_at", "scale", "rounding",
                                           "cutover_at", "account_fingerprint",
                                           "max_order_usd", "actor", "revision",
                                           "updated_at")},
        "account": ({"at": snap["at"], "balances": bal,
                     "positions": list(positions.values()),
                     "open_orders": snap["open_orders"],
                     "reconciliation": _js(snap["reconciliation"])}
                    if snap else {"status": "UNAVAILABLE",
                                  "why": "no account snapshot recorded yet"}),
        "coverage": {"paper_orders_seen": eligible, "mirrored": mirrored,
                     "excluded": eligible - mirrored,
                     "mirrored_pct": _f(Decimal(mirrored) * 100 / eligible, 1) if eligible else None,
                     "by_state": counts},
        "pnl": {"paper_total": _f(totals["paper_pnl"]),
                "expected_live_total": _f(expected_total),
                "live_total": _f(totals["live_pnl"]),
                "difference": _f(totals["live_pnl"] - expected_total),
                "tracking_ratio": (_f(totals["live_pnl"] / expected_total, 4)
                                   if expected_total else None),
                "markets": markets},
        "orders": orders,
        "events": [dict(e) for e in await conn.fetch(
            "SELECT at, mirror_id, paper_order_id, kind, detail FROM execmirror_events"
            " ORDER BY event_id DESC LIMIT 60")],
    }
