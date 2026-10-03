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


def _pnl_block(totals, expected_total, markets) -> dict:
    """Paper / scale beside live, in total. WITH NO MIRRORED MARKET THERE IS
    NO P&L: every total is null with the reason, never 0.0 -- a zero would
    read as "traded and broke even", which is not what happened."""
    if not markets:
        return {"status": "UNAVAILABLE", "paper_total": None,
                "expected_live_total": None, "live_total": None,
                "difference": None, "tracking_ratio": None, "markets": [],
                "why": ("no mirrored entry or hedge has been sent to the venue "
                        "yet, so there is no live position and no paper group "
                        "to compare: P&L is unavailable, not zero")}
    return {"status": "AVAILABLE",
            "paper_total": _f(totals["paper_pnl"]),
            "expected_live_total": _f(expected_total),
            "live_total": _f(totals["live_pnl"]),
            "difference": _f(totals["live_pnl"] - expected_total),
            "tracking_ratio": (_f(totals["live_pnl"] / expected_total, 4)
                               if expected_total else None),
            "markets": markets}


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
                     # NOTHING SENT, NOTHING FILLED: an order the venue never
                     # received has no fill quantity or fee to report, so it
                     # is null (unavailable), never the column's default 0.
                     "venue_state": r["venue_state"],
                     "filled_qty": (_f(r["cum_qty"]) if r["venue_order_id"] else None),
                     "avg_px": _f(live_px),
                     "fees_usd": (_f(r["fees_usd"]) if r["venue_order_id"] else None),
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
        "pnl": _pnl_block(totals, expected_total, markets),
        "orders": orders,
        "events": [dict(e) for e in await conn.fetch(
            "SELECT at, mirror_id, paper_order_id, kind, detail FROM execmirror_events"
            " ORDER BY event_id DESC LIMIT 60")],
    }


# ═════════════════════════════════════════════════════════════════════
# SMALL LIVE · PAPER vs ACTUAL, ONE ROW PER ORDER THE MIRROR CONSIDERED
# ═════════════════════════════════════════════════════════════════════
#
# Management must never have to infer which figure is simulated and which
# is actual. Every row carries three sections that are never merged:
#
#   decision  the paper decision record (paper_decisions), as recorded;
#   paper     label SIMULATED -- the paper order and its simulated fills;
#   actual    label ACTUAL    -- the live venue order derived from it, the
#             exclusion if none was sent, and venue-confirmed fills only;
#
# plus `difference` (computed only when both sides exist) and `management`
# (Xavier and Audrey records). A figure with no source is null, which the
# page renders as "unavailable" -- never a placeholder zero.

SMALL_LIVE_VIEWS = ("paper", "polymarket", "kalshi")
SMALL_LIVE_STATUSES = ("open", "filled", "closed", "refused")
SMALL_LIVE_MAX = 500
LIVE_VENUE = "POLYMARKET"

# Order-lifecycle buckets, one definition used by the filter, the counts and
# every row. `filled` = ended with at least one fill (fully or partly);
# `closed` = ended with no fill; `refused` = excluded or rejected.
ACTUAL_BUCKET_SQL = (
    "CASE WHEN m.state IN ('EXCLUDED','REJECTED') THEN 'refused'"
    " WHEN m.state IN ('FILLED','CANCELLED','EXPIRED') AND m.cum_qty > 0 THEN 'filled'"
    " WHEN m.state IN ('FILLED','CANCELLED','EXPIRED') THEN 'closed'"
    " ELSE 'open' END")
PAPER_BUCKET_SQL = (
    "CASE WHEN p.order_id IS NULL THEN NULL"
    " WHEN p.state = 'REJECTED' THEN 'refused'"
    " WHEN p.state IN ('FILLED','EXPIRED','CANCELED') AND p.filled_qty > 0 THEN 'filled'"
    " WHEN p.state IN ('FILLED','EXPIRED','CANCELED') THEN 'closed'"
    " ELSE 'open' END")
PAPER_JOIN_SQL = ("LEFT JOIN paper_orders p ON p.order_id = "
                  "coalesce(m.paper_order_id, m.detail->>'for_paper_order')")
STATUS_DEFINITIONS = {
    "open": "not yet ended: planned, submitting, unresolved, resting or partly filled",
    "filled": "ended with at least one fill (fully or partly filled)",
    "closed": "ended (cancelled or expired) with no fill",
    "refused": "excluded before submission (with the reason) or rejected by the venue / simulator",
}
UNAVAILABLE_ACTUAL_XAVIER = (
    "unavailable: no Xavier record of the ACTUAL position exists for this "
    "group (one is written only after a venue-confirmed live fill)")


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _ms_between(a, b):
    """b - a in whole milliseconds, or None when either end is missing."""
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000)


def _cpc(intent, price):
    """Per-contract value in collateral space (live_cash_flow's convention):
    a LONG contract is worth the price, a SHORT one its complement."""
    if price is None:
        return None
    p = _d(price)
    return (Decimal(1) - p) if "SHORT" in str(intent or "") else p


def _is_buy(intent) -> bool:
    return str(intent or "").startswith("ORDER_INTENT_BUY")


def control_summary(ctl: dict) -> dict:
    """Mirror control, as management needs it: never the full account
    fingerprint (a prefix identifies the account without publishing it)."""
    if not ctl:
        return {"state": "UNAVAILABLE", "why": "execution mirror control row missing"}
    fp = ctl.get("account_fingerprint")
    state = ("STOPPED" if ctl.get("stopped") else
             "ENABLED" if ctl.get("enabled") else "DISABLED")
    return {"state": state, "enabled": bool(ctl.get("enabled")),
            "stopped": bool(ctl.get("stopped")),
            "flatten_on_stop": bool(ctl.get("flatten_on_stop")),
            "cutover_at": _iso(ctl.get("cutover_at")),
            "cap_usd_per_order": _f(ctl.get("max_order_usd")),
            "scale": _f(ctl.get("scale")),
            "rounding": ctl.get("rounding"),
            "account_fingerprint_prefix": (str(fp)[:8] if fp else None),
            "revision": ctl.get("revision"),
            "updated_at": _iso(ctl.get("updated_at")),
            "actor": ctl.get("actor")}


def account_summary(snap) -> dict:
    if not snap:
        return {"status": "UNAVAILABLE", "at": None, "balances": None,
                "positions_count": None, "open_orders_count": None,
                "reconciled": None,
                "why": "no account snapshot of the mirror account has been recorded yet"}
    bal = _js(snap["balances"]) or []
    pos = _js(snap["positions"]) or []
    rec = _js(snap["reconciliation"]) or {}
    first = bal[0] if bal and isinstance(bal[0], dict) else {}
    return {"status": "AVAILABLE", "at": _iso(snap["at"]),
            "balances": bal,
            "buying_power_usd": _f(first.get("buyingPower")) if first.get("buyingPower") is not None else None,
            "current_balance_usd": (_f(first.get("currentBalance"))
                                    if first.get("currentBalance") is not None else None),
            "positions_count": len(pos) if isinstance(pos, list) else None,
            "open_orders_count": snap["open_orders"],
            "reconciled": rec.get("reconciled") if rec else None,
            "reconciliation_differences": (rec.get("differences") or {}) if rec else None}


def venues(ctl: dict, snap) -> list:
    """Every venue management may ask about, with its real state. KALSHI has
    no credentials and no live adapter in this system: it is listed so its
    absence is stated, and nothing about it is ever filled in."""
    c = control_summary(ctl)
    pm_status = ("UNAVAILABLE" if c.get("state") == "UNAVAILABLE" else
                 "MIRROR_" + c["state"])
    pm_why = {"MIRROR_ENABLED": "the 1:%s mirror is enabled and copies qualifying paper orders"
              % (int(ctl.get("scale") or 1000) if ctl else 1000),
              "MIRROR_DISABLED": "the mirror is disabled: no new live order is placed",
              "MIRROR_STOPPED": "emergency stop engaged: no new live order is placed",
              "UNAVAILABLE": "execution mirror control row missing"}[pm_status]
    return [
        {"venue": "POLYMARKET", "label": "Polymarket US", "connected": True,
         "status": pm_status, "why": pm_why,
         "account_fingerprint_prefix": c.get("account_fingerprint_prefix"),
         "last_account_snapshot_at": _iso(snap["at"]) if snap else None},
        {"venue": "KALSHI", "label": "Kalshi", "connected": False,
         "status": "NOT_CONNECTED", "why": "credentials not configured",
         "detail": ("No Kalshi credentials or live adapter are configured; no "
                    "Kalshi order, fill, balance or position exists in this "
                    "system, so none is shown."),
         "account_fingerprint_prefix": None, "last_account_snapshot_at": None},
    ]


def _decision_section(r: dict) -> dict:
    keys = ("decision_id", "decided_at", "strategy", "policy_version", "verdict",
            "refusal", "p_pinnacle", "executable_price", "executable_price_source",
            "limit_price", "proposed_qty", "gross_edge_pp", "edge_at_executable_pp",
            "expected_fees_usd", "net_ev_usd", "net_ev_per_contract_usd",
            "net_ev_source", "valuation_id", "provider", "market_label")
    if r.get("d_id") is None:
        out = {k: None for k in keys}
        out["present"] = False
        if r.get("p_order_id") is None:
            out["why"] = ("mirror-generated %s order: it copies no paper decision"
                          % r["role"])
        elif r.get("p_decision_id"):
            out["decision_id"] = r["p_decision_id"]
            out["why"] = "the paper order names a decision that is not in paper_decisions"
        else:
            out["why"] = "the paper order names no paper decision"
        # The paper order's own decision instant and strategy ARE recorded;
        # they are shown with their source rather than hidden.
        if r.get("p_order_id") is not None:
            out["decided_at"] = _iso(r.get("p_decided_at"))
            out["decided_at_source"] = "paper_orders.decided_at"
            out["strategy"] = r.get("p_strategy")
            out["policy_version"] = (_js(r.get("p_label")) or {}).get("policy_version")
        return out
    pd = _js(r.get("d_policy_decision"))
    pd = pd if isinstance(pd, dict) else {}
    eco = _js(r.get("d_economics"))
    eco = eco if isinstance(eco, dict) else {}
    acq = eco.get("acquisition") if isinstance(eco.get("acquisition"), dict) else {}
    pin = _js(r.get("d_pinnacle"))
    pin = pin if isinstance(pin, dict) else {}
    net_ev, src = pd.get("net_expected_profit_usd"), "policy_decision.net_expected_profit_usd"
    if net_ev is None and acq.get("expected_net_profit_usd") is not None:
        net_ev, src = acq["expected_net_profit_usd"], "economics.acquisition.expected_net_profit_usd"
    if net_ev is None:
        src = None
    if acq.get("vwap") is not None:
        px, px_src = acq["vwap"], "economics.acquisition.vwap"
    elif r.get("d_limit") is not None:
        px, px_src = r["d_limit"], "paper_decisions.limit_price"
    else:
        px, px_src = None, None
    edge_exec = pd.get("edge_at_vwap_pp")
    if edge_exec is None:
        edge_exec = acq.get("edge_at_vwap_pp")
    fees = pd.get("fees_usd")
    if fees is None:
        fees = acq.get("fees_usd")
    qty = r.get("d_qty")
    lab = _js(r.get("d_label"))
    return {"present": True, "decision_id": r["d_id"],
            "decided_at": _iso(r["d_decided_at"]),
            "decided_at_source": "paper_decisions.decided_at",
            "strategy": r.get("d_strategy"),
            "policy_version": r.get("d_policy_version"),
            "verdict": r.get("d_verdict"), "refusal": r.get("d_refusal"),
            "p_pinnacle": _f(r.get("d_p_pinnacle"), 9),
            "executable_price": _f(px), "executable_price_source": px_src,
            "limit_price": _f(r.get("d_limit")), "proposed_qty": _f(qty),
            "gross_edge_pp": _f(pd.get("gross_edge_pp")),
            "edge_at_executable_pp": _f(edge_exec),
            "expected_fees_usd": _f(fees),
            "net_ev_usd": _f(net_ev),
            "net_ev_per_contract_usd": (_f(_d(net_ev) / _d(qty))
                                        if net_ev is not None and qty else None),
            "net_ev_source": src,
            "valuation_id": r.get("d_valuation_id"),
            "provider": pin.get("provider"),
            "market_label": lab if lab else None}


def _paper_section(r: dict, gp: dict) -> dict:
    if r.get("p_order_id") is None:
        return {"label": "SIMULATED", "present": False, "order_id": None,
                "status": None,
                "why": ("mirror-generated %s order: there is no paper order "
                        "behind it" % r["role"])}
    n = r.get("pf_n") or 0
    pnl, pnl_why = None, None
    if gp is None:
        pnl_why = "paper group not found"
    elif gp["realized"]:
        pnl = _f(gp["cash"])
    else:
        pnl_why = ("paper group not yet closed or settled: no realized paper "
                   "P&L, and an open paper position is not marked here")
    return {"label": "SIMULATED", "present": True,
            "order_id": r["p_order_id"], "role": r.get("p_role"),
            "group_id": r.get("group_id"),
            "decided_at": _iso(r.get("p_decided_at")),
            "qty": _f(r.get("p_qty")), "limit_price": _f(r.get("p_limit")),
            "wire_price": _f(r.get("p_wire")),
            "order_type": r.get("p_order_type"), "tif": r.get("p_tif"),
            "state": r.get("p_state"), "status": r.get("paper_bucket"),
            "terminal_reason": r.get("p_terminal_reason"),
            "simulated_fill_count": n,
            # The paper order's own filled quantity is a recorded fact
            # (0 is a real 0); price and fees exist only once it filled.
            "simulated_filled_qty": _f(r.get("p_filled_qty")),
            "avg_simulated_fill_price": _f(r.get("pf_avg_px")) if n else None,
            "avg_simulated_fill_wire_price": _f(r.get("pf_avg_wire")) if n else None,
            "simulated_fees_usd": _f(r.get("pf_fees")) if n else None,
            "first_simulated_fill_at": _iso(r.get("pf_first_at")),
            "group_realized_pnl_usd": pnl,
            "group_pnl_why_unavailable": pnl_why}


def _actual_section(r: dict, scale) -> dict:
    det = _js(r.get("detail"))
    det = det if isinstance(det, dict) else {}
    params = det.get("params") if isinstance(det.get("params"), dict) else {}
    excluded = r["state"] == "EXCLUDED"
    sent = r.get("venue_order_id") is not None or r["state"] in (
        "SUBMITTING", "UNKNOWN", "REJECTED")
    # A venue order record has been READ when the poll stored its state or a
    # fill; until then the fill quantity is unknown, not zero.
    read = r.get("venue_order_id") is not None and (
        r.get("venue_state") is not None or r.get("last_polled_at") is not None
        or _d(r.get("cum_qty")) > 0)
    live_qty = r.get("live_qty")
    wire = r.get("wire_price")
    sub_px = None
    if sent:
        pv = (params.get("price") or {}).get("value") if isinstance(params.get("price"), dict) else None
        sub_px = _f(pv) if pv is not None else _f(wire)
    notional = None
    if not excluded and live_qty and wire is not None:
        notional = _f(_cpc(r["intent"], wire) * Decimal(live_qty))
    lf_n = r.get("lf_n") or 0
    filled = _d(r.get("cum_qty")) if read else None
    avg = r.get("avg_px") if read and filled else None
    filled_notional = (_f(_cpc(r["intent"], avg) * filled)
                       if avg is not None and filled else (0.0 if read and filled == 0 else None))
    why = None
    if excluded:
        why = "not sent: excluded (%s)" % r["exclusion"]
    elif r["state"] == "PLANNED":
        why = "planned, not yet submitted"
    elif sent and not read:
        why = "sent; the venue order record has not been read yet"
    return {"label": "ACTUAL", "venue": LIVE_VENUE,
            "account": "execution mirror account (Polymarket US)",
            "scale": _f(scale),
            "scaled_qty": _f(r.get("scaled_qty")),
            "intended_qty": live_qty,
            "rounding_delta": _f(r.get("rounding_delta")),
            "state": r["state"], "status": r.get("actual_bucket"),
            "excluded": excluded, "exclusion": r.get("exclusion"),
            "exclusion_detail": ({k: det.get(k) for k in
                                  ("why", "cost_usd", "cap_usd", "buying_power_usd",
                                   "held", "committed", "live_eligibility",
                                   "revalidation", "time_in_force")
                                  if k in det} if excluded else None),
            "would_have_cost_usd": (_f(det.get("cost_usd"))
                                    if excluded and det.get("cost_usd") is not None else None),
            "submitted": sent,
            "intended_notional_usd": notional,
            "venue_order_id": r.get("venue_order_id"),
            "submitted_price": sub_px,
            "submit_started_at": _iso(r.get("submit_started_at")),
            "acknowledged_at": _iso(r.get("accepted_at")),
            "venue_state": r.get("venue_state"),
            "filled_qty": _f(filled),
            "avg_fill_price": _f(avg),
            "fees_usd": _f(r.get("fees_usd")) if read else None,
            "filled_notional_usd": filled_notional,
            "venue_fill_count": lf_n if read else None,
            "first_fill_observed_at": _iso(r.get("lf_first_at")),
            "submit_latency_ms": r.get("latency_ms"),
            "decision_to_accept_ms": det.get("decision_to_accept_ms"),
            "error": (_js(r.get("error")) or None),
            "why_no_fill_figures": why,
            "fill_source": ("VENUE_ORDER_RECORD" if read else None)}


def _difference(r: dict, paper: dict, actual: dict, scale) -> dict:
    scale = _d(scale)
    excluded_qty = _f(r.get("scaled_qty")) if actual["excluded"] else None
    out = {"rounded_qty": actual["rounding_delta"],
           "excluded_scaled_qty": excluded_qty,
           "submitted_minus_paper_wire_price": None,
           "live_minus_paper_fill_wire_price": None,
           "price_adverse_per_contract": None,
           "slippage_vs_submitted_adverse_per_contract": None,
           "expected_live_qty_from_paper_fill": None,
           "live_minus_expected_qty": None,
           "live_fees_minus_scaled_paper_fees_usd": None,
           "fee_per_contract_live_minus_paper_usd": None,
           "decision_to_accept_ms": actual["decision_to_accept_ms"],
           "paper_first_fill_to_live_ack_ms": None,
           "paper_first_fill_to_live_first_fill_ms": None,
           "basis": ("computed only where both sides exist; prices compared in "
                     "the venue's wire space; 'adverse' is positive when the "
                     "live side did worse (paid more on a buy, received less "
                     "on a sell)")}
    if not paper.get("present"):
        out["why"] = paper.get("why")
        return out
    sign = Decimal(1) if _is_buy(r["intent"]) else Decimal(-1)
    if actual["submitted_price"] is not None and paper.get("wire_price") is not None:
        out["submitted_minus_paper_wire_price"] = _f(
            _d(actual["submitted_price"]) - _d(paper["wire_price"]))
    live_avg = actual["avg_fill_price"]
    paper_avg = paper.get("avg_simulated_fill_wire_price")
    if live_avg is not None and paper_avg is not None:
        out["live_minus_paper_fill_wire_price"] = _f(_d(live_avg) - _d(paper_avg))
        out["price_adverse_per_contract"] = _f(
            sign * (_cpc(r["intent"], live_avg) - _cpc(r["intent"], paper_avg)))
    if live_avg is not None and actual["submitted_price"] is not None:
        out["slippage_vs_submitted_adverse_per_contract"] = _f(
            sign * (_cpc(r["intent"], live_avg) - _cpc(r["intent"], actual["submitted_price"])))
    pf = paper.get("simulated_filled_qty")
    if pf is not None and paper.get("simulated_fill_count"):
        exp = _d(pf) / scale
        out["expected_live_qty_from_paper_fill"] = _f(exp)
        if actual["filled_qty"] is not None:
            out["live_minus_expected_qty"] = _f(_d(actual["filled_qty"]) - exp)
        if actual["fees_usd"] is not None and paper.get("simulated_fees_usd") is not None:
            out["live_fees_minus_scaled_paper_fees_usd"] = _f(
                _d(actual["fees_usd"]) - _d(paper["simulated_fees_usd"]) / scale)
            if actual["filled_qty"] and _d(pf):
                out["fee_per_contract_live_minus_paper_usd"] = _f(
                    _d(actual["fees_usd"]) / _d(actual["filled_qty"])
                    - _d(paper["simulated_fees_usd"]) / _d(pf))
    out["paper_first_fill_to_live_ack_ms"] = _ms_between(
        r.get("pf_first_at"), r.get("accepted_at"))
    out["paper_first_fill_to_live_first_fill_ms"] = _ms_between(
        r.get("pf_first_at"), r.get("lf_first_at"))
    return out


async def _management(conn, rows: list) -> dict:
    """Xavier and Audrey records for the rows, keyed by group / subject."""
    groups = sorted({r["group_id"] for r in rows if r.get("group_id")})
    subjects = sorted({s for r in rows for s in (
        r.get("d_id"), r.get("p_decision_id"), r.get("p_order_id"),
        r.get("group_id"), r.get("mirror_id")) if s})
    out = {"handoffs": {}, "reviews": {}, "findings": {}, "live_handoffs": {},
           "live_reviews": {}, "reconciliations": {}, "smalllive_schema": False,
           "groups_pnl": {}}
    if groups:
        for h in await conn.fetch(
                """SELECT handoff_id, group_id, owner, confirmed_qty, outstanding_qty,
                          first_fill_at FROM paper_handoffs WHERE group_id = ANY($1)""", groups):
            out["handoffs"][h["group_id"]] = dict(h)
        for v in await conn.fetch(
                """SELECT DISTINCT ON (group_id) group_id, review_id, reviewed_at,
                          trigger, recommendation, refusal
                     FROM paper_xavier_reviews WHERE group_id = ANY($1)
                    ORDER BY group_id, reviewed_at DESC, review_id DESC""", groups):
            out["reviews"][v["group_id"]] = dict(v)
        for g in await conn.fetch(
                """SELECT g AS group_id,
                          (SELECT count(*) FROM paper_settlements s WHERE s.group_id = g) AS settled,
                          (SELECT count(*) FROM paper_fills f WHERE f.group_id = g) AS fills,
                          (SELECT coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0)
                                - coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0)
                             FROM paper_fills f WHERE f.group_id = g) AS open_qty,
                          (SELECT sum(cash_delta_usd) FROM paper_ledger l
                            WHERE l.group_id = g
                              AND l.kind IN ('FILL','SALE','SETTLEMENT','CORRECTION')) AS cash
                     FROM unnest($1::text[]) AS g""", groups):
            realized = bool(g["fills"]) and (bool(g["settled"]) or _d(g["open_qty"]) == 0) \
                and g["cash"] is not None
            out["groups_pnl"][g["group_id"]] = {"realized": realized, "cash": g["cash"]}
        if await conn.fetchval(
                "SELECT to_regclass('smalllive_handoffs') IS NOT NULL"
                " AND to_regclass('smalllive_reviews') IS NOT NULL"):
            out["smalllive_schema"] = True
            for h in await conn.fetch(
                    """SELECT handoff_id, group_id, owner, state, live_held, live_bought,
                              avg_entry_px, fees_usd, first_live_fill_at
                         FROM smalllive_handoffs WHERE venue = $1 AND group_id = ANY($2)""",
                    LIVE_VENUE, groups):
                out["live_handoffs"][h["group_id"]] = dict(h)
            hids = [h["handoff_id"] for h in out["live_handoffs"].values()]
            if hids:
                for v in await conn.fetch(
                        """SELECT DISTINCT ON (handoff_id) handoff_id, review_id, reviewed_at,
                                  action, live_held, mark_value_usd, unrealized_usd,
                                  paper_recommendation
                             FROM smalllive_reviews WHERE handoff_id = ANY($1)
                            ORDER BY handoff_id, reviewed_at DESC, review_id DESC""", hids):
                    out["live_reviews"][v["handoff_id"]] = dict(v)
        if await conn.fetchval("SELECT to_regclass('smalllive_reconciliations') IS NOT NULL"):
            for c in await conn.fetch(
                    """SELECT group_id, status, discrepancies, reconciled_at
                         FROM smalllive_reconciliations WHERE group_id = ANY($1)""", groups):
                out["reconciliations"][c["group_id"]] = dict(c)
    if subjects:
        for f in await conn.fetch(
                """SELECT finding_id, found_at, kind, severity, subject
                     FROM paper_audrey_findings WHERE subject = ANY($1)
                    ORDER BY found_at DESC LIMIT 1000""", subjects):
            out["findings"].setdefault(f["subject"], []).append(
                {"finding_id": f["finding_id"], "found_at": _iso(f["found_at"]),
                 "kind": f["kind"], "severity": f["severity"], "subject": f["subject"]})
    return out


def _management_section(r: dict, mg: dict) -> dict:
    g = r.get("group_id")
    h = mg["handoffs"].get(g)
    v = mg["reviews"].get(g)
    lh = mg["live_handoffs"].get(g)
    lv = mg["live_reviews"].get(lh["handoff_id"]) if lh else None
    rec = mg["reconciliations"].get(g)
    findings = []
    for s in (r.get("d_id") or r.get("p_decision_id"), r.get("p_order_id"), g,
              r.get("mirror_id")):
        for f in mg["findings"].get(s, []) if s else []:
            if f not in findings:
                findings.append(f)
    xp = {"label": "PAPER POSITION",
          "handoff_id": h["handoff_id"] if h else None,
          "owner": h["owner"] if h else None,
          "confirmed_qty": _f(h["confirmed_qty"]) if h else None,
          "latest_review_id": v["review_id"] if v else None,
          "latest_review_at": _iso(v["reviewed_at"]) if v else None,
          "latest_review_trigger": v["trigger"] if v else None,
          "latest_recommendation": v["recommendation"] if v else None,
          "why_unavailable": (None if h else
                              "no Xavier handoff for this paper group (a handoff "
                              "starts at the first simulated fill)")}
    if lh:
        xa = {"label": "ACTUAL POSITION", "present": True,
              "handoff_id": lh["handoff_id"], "owner": lh["owner"],
              "state": lh["state"], "live_held": _f(lh["live_held"]),
              "avg_entry_price": _f(lh["avg_entry_px"]),
              "latest_review_id": lv["review_id"] if lv else None,
              "latest_review_at": _iso(lv["reviewed_at"]) if lv else None,
              "latest_action": lv["action"] if lv else None,
              "mark_value_usd": _f(lv["mark_value_usd"]) if lv else None,
              "unrealized_usd": _f(lv["unrealized_usd"]) if lv else None,
              "why_unavailable": None if lv else "handoff recorded; no review of the actual position yet"}
    else:
        xa = {"label": "ACTUAL POSITION", "present": False, "handoff_id": None,
              "latest_review_id": None, "latest_action": None,
              "why_unavailable": UNAVAILABLE_ACTUAL_XAVIER}
    return {"xavier_paper": xp, "xavier_actual": xa,
            "audrey_findings": findings,
            "audrey_findings_why_empty": (None if findings else
                                          "no Audrey finding names this decision, order or group"),
            "audrey_reconciliation": ({"status": rec["status"],
                                       "reconciled_at": _iso(rec["reconciled_at"]),
                                       "discrepancies": _js(rec["discrepancies"]) or []}
                                      if rec else None),
            "audrey_reconciliation_why_unavailable": (
                None if rec else "no Audrey reconciliation of the actual chain for this group")}


def no_live_order_reason(ctl: dict, counts: dict) -> dict:
    """WHY THE ACTUAL COLUMN IS EMPTY, in plain words. Built from the control
    row and the mirror's own records -- never a guess."""
    placed = counts["live_orders_placed"]
    if placed:
        return {"live_order_placed": True, "headline": None, "reasons": []}
    reasons = []
    if not ctl:
        reasons.append("The execution mirror's control row is missing.")
    else:
        if ctl.get("stopped"):
            reasons.append("The emergency stop is engaged.")
        if not ctl.get("enabled"):
            reasons.append("The mirror is disabled, so no paper order is copied to the venue.")
        if not ctl.get("cutover_at"):
            reasons.append("No cutover has been set: the mirror has never been switched on.")
    total = counts["considered"]
    excl = counts["excluded_by_reason"]
    if ctl and ctl.get("cutover_at"):
        since = _iso(ctl["cutover_at"])
        if total == 0:
            reasons.append("No qualifying investment-policy paper order has been "
                           "decided since the cutover at %s." % since)
        elif excl and sum(excl.values()) == total:
            if set(excl) == {"STRATEGY_NOT_LIVE_ELIGIBLE"}:
                reasons.append("No qualifying investment-policy paper order since the "
                               "cutover at %s: all %d paper orders considered were "
                               "from strategies that are paper-only." % (since, total))
            else:
                reasons.append("Every paper order considered since the cutover at %s "
                               "was excluded: %s." % (since, ", ".join(
                                   "%s × %d" % (k, n) for k, n in sorted(excl.items()))))
        pending = counts["actual_status"].get("open", 0)
        if pending:
            reasons.append("%d order(s) are planned or submitting and not yet "
                           "acknowledged by the venue." % pending)
    return {"live_order_placed": False,
            "headline": "No live order has been placed yet.",
            "reasons": reasons or ["No live order has been placed yet."]}


async def _counts(conn) -> dict:
    actual = {k: 0 for k in SMALL_LIVE_STATUSES}
    for r in await conn.fetch(
            "SELECT %s AS b, count(*) AS n FROM execmirror_orders m GROUP BY 1"
            % ACTUAL_BUCKET_SQL):
        actual[r["b"]] = r["n"]
    paper = {k: 0 for k in SMALL_LIVE_STATUSES}
    for r in await conn.fetch(
            "SELECT %s AS b, count(*) AS n FROM execmirror_orders m %s"
            " WHERE p.order_id IS NOT NULL GROUP BY 1" % (PAPER_BUCKET_SQL, PAPER_JOIN_SQL)):
        paper[r["b"]] = r["n"]
    excl = {r["exclusion"]: r["n"] for r in await conn.fetch(
        "SELECT exclusion, count(*) AS n FROM execmirror_orders"
        " WHERE state = 'EXCLUDED' GROUP BY 1")}
    total = sum(actual.values())
    return {"considered": total,
            "view": {"paper": sum(paper.values()), "polymarket": total,
                     "kalshi": None},
            "view_why_null": {"kalshi": "not connected: credentials not configured"},
            "actual_status": actual, "paper_status": paper,
            "excluded_by_reason": excl,
            "live_orders_placed": await conn.fetchval(
                "SELECT count(*) FROM execmirror_orders WHERE venue_order_id IS NOT NULL"),
            "status_definitions": STATUS_DEFINITIONS}


async def small_live(conn, *, view: str | None = None, status: str | None = None,
                     limit: int = 100) -> dict:
    """SMALL LIVE · PAPER vs ACTUAL (read-only).

    `view`: paper (rows with a paper order; `status` then reads the PAPER
    side) | polymarket (rows on the live venue; `status` reads the ACTUAL
    side) | kalshi (not connected: no rows, stated) | None (all rows,
    `status` reads the ACTUAL side). `status`: open | filled | closed |
    refused. Newest first, at most SMALL_LIVE_MAX rows."""
    if view is not None and view not in SMALL_LIVE_VIEWS:
        raise ValueError("view must be one of %s" % ", ".join(SMALL_LIVE_VIEWS))
    if status is not None and status not in SMALL_LIVE_STATUSES:
        raise ValueError("status must be one of %s" % ", ".join(SMALL_LIVE_STATUSES))
    limit = max(1, min(int(limit), SMALL_LIVE_MAX))
    ctl = dict(await conn.fetchrow("SELECT * FROM execmirror_control WHERE id = 1") or {})
    scale = ctl.get("scale") or 1000
    snap = await conn.fetchrow("SELECT * FROM execmirror_snapshots ORDER BY at DESC LIMIT 1")
    rows = []
    if view != "kalshi":
        where, args = [], []
        bucket = PAPER_BUCKET_SQL if view == "paper" else ACTUAL_BUCKET_SQL
        if view == "paper":
            where.append("p.order_id IS NOT NULL")
        if status:
            args.append(status)
            where.append("(%s) = $%d" % (bucket, len(args)))
        args.append(limit)
        rows = [dict(r) for r in await conn.fetch(
            """SELECT m.*, (%s) AS actual_bucket, (%s) AS paper_bucket,
                      p.order_id AS p_order_id, p.role AS p_role, p.qty AS p_qty,
                      p.limit_price AS p_limit, p.wire_price AS p_wire,
                      p.state AS p_state, p.filled_qty AS p_filled_qty,
                      p.decided_at AS p_decided_at, p.decision_id AS p_decision_id,
                      p.strategy AS p_strategy, p.label AS p_label,
                      p.order_type AS p_order_type, p.time_in_force AS p_tif,
                      p.terminal_reason AS p_terminal_reason,
                      pf.n AS pf_n, pf.avg_px AS pf_avg_px, pf.avg_wire AS pf_avg_wire,
                      pf.fees AS pf_fees, pf.first_at AS pf_first_at,
                      d.decision_id AS d_id, d.decided_at AS d_decided_at,
                      d.strategy AS d_strategy, d.policy_version AS d_policy_version,
                      d.verdict AS d_verdict, d.refusal AS d_refusal,
                      d.p_pinnacle AS d_p_pinnacle, d.limit_price AS d_limit,
                      d.proposed_qty AS d_qty, d.economics AS d_economics,
                      d.policy_decision AS d_policy_decision,
                      d.valuation_id AS d_valuation_id, d.pinnacle AS d_pinnacle,
                      d.label AS d_label,
                      lf.n AS lf_n, lf.first_at AS lf_first_at
                 FROM execmirror_orders m
                 %s
                 LEFT JOIN LATERAL (
                     SELECT count(*) AS n,
                            sum(f.qty * f.price) / nullif(sum(f.qty), 0) AS avg_px,
                            sum(f.qty * f.wire_price) / nullif(sum(f.qty), 0) AS avg_wire,
                            sum(f.fee_usd) AS fees, min(f.filled_at) AS first_at
                       FROM paper_fills f WHERE f.order_id = p.order_id) pf ON true
                 LEFT JOIN paper_decisions d ON d.decision_id = p.decision_id
                 LEFT JOIN LATERAL (
                     SELECT count(*) AS n, min(x.observed_at) AS first_at
                       FROM execmirror_fills x WHERE x.mirror_id = m.mirror_id) lf ON true
                %s
                ORDER BY m.created_at DESC, m.mirror_id DESC
                LIMIT $%d""" % (ACTUAL_BUCKET_SQL, PAPER_BUCKET_SQL, PAPER_JOIN_SQL,
                                ("WHERE " + " AND ".join(where)) if where else "",
                                len(args)), *args)]
    mg = await _management(conn, rows)
    out_rows = []
    for r in rows:
        paper = _paper_section(r, mg["groups_pnl"].get(r.get("group_id")))
        actual = _actual_section(r, scale)
        lab = _js(r.get("p_label")) if r.get("p_order_id") else _js(
            (_js(r.get("detail")) or {}).get("label"))
        out_rows.append({
            "mirror_id": r["mirror_id"], "created_at": _iso(r["created_at"]),
            "market": r["us_market_slug"], "group_id": r.get("group_id"),
            "role": r["role"], "intent": r["intent"],
            "label": lab if isinstance(lab, dict) and lab else None,
            "strategy": strategy_kind(r.get("p_strategy") or r.get("strategy"),
                                      r.get("p_label")),
            "filters": {"venue": LIVE_VENUE, "paper_status": r.get("paper_bucket"),
                        "actual_status": r.get("actual_bucket")},
            "decision": _decision_section(r),
            "paper": paper, "actual": actual,
            "difference": _difference(r, paper, actual, scale),
            "management": _management_section(r, mg)})
    counts = await _counts(conn)
    return {
        "title": "Small Live · Paper vs Actual",
        "basis": ("PAPER is SIMULATED: paper orders and simulated fills from the "
                  "paper experiment. ACTUAL is the live venue: orders placed on the "
                  "separate execution-mirror account at paper quantity / %s (nearest "
                  "whole contract), with fills read only from the venue's own order "
                  "records. The two are never summed. A missing figure is null "
                  "(shown as 'unavailable'), never zero." % int(_d(scale))),
        "filters": {"view": view, "status": status, "limit": limit,
                    "status_applies_to": ("paper" if view == "paper" else
                                          None if view == "kalshi" else "actual"),
                    "views": list(SMALL_LIVE_VIEWS),
                    "statuses": list(SMALL_LIVE_STATUSES),
                    "status_definitions": STATUS_DEFINITIONS},
        "control": control_summary(ctl),
        "account": account_summary(snap),
        "venues": venues(ctl, snap),
        "counts": counts,
        "empty_state": no_live_order_reason(ctl, counts),
        "kalshi": ({"status": "NOT_CONNECTED", "why": "credentials not configured",
                    "rows": None} if view == "kalshi" else None),
        "rows": out_rows,
    }
