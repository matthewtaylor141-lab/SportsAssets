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

import datetime as dt
import json
from decimal import Decimal

from . import order_state_truth as OST

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
        """SELECT m.*, p.order_id AS sibling_paper_order_id,
                  p.state AS paper_state, p.filled_qty AS paper_filled_qty,
                  p.label AS paper_label,
                  (SELECT sum(f.qty * f.price) / nullif(sum(f.qty), 0)
                     FROM paper_fills f WHERE f.order_id = p.order_id) AS paper_avg_px,
                  (SELECT sum(f.fee_usd) FROM paper_fills f
                    WHERE f.order_id = p.order_id) AS paper_fees
             FROM execmirror_orders m %s
            ORDER BY m.created_at DESC LIMIT $1""" % PAPER_JOIN_SQL, limit)
    orders = []
    for r in rows:
        r = dict(r)
        det = _js(r.get("detail"))
        live_px = r.get("avg_px")
        orders.append({
            "mirror_id": r["mirror_id"],
            "paper_order_id": r["paper_order_id"] or r.get("sibling_paper_order_id"),
            "execution_intent_id": r.get("execution_intent_id"),
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
        """SELECT count(*) FROM execmirror_orders
            WHERE paper_order_id IS NOT NULL OR execution_intent_id IS NOT NULL""") or 0
    mirrored = await conn.fetchval(
        """SELECT count(*) FROM execmirror_orders
            WHERE (paper_order_id IS NOT NULL OR execution_intent_id IS NOT NULL)
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
        "title": "LEGACY MIRROR VALIDATION · execution mirror · 1:%s" % int(scale),
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
#: The PAPER side of an ACTUAL row: the paper order it follows (exits and
#: protection), or -- ONE DECISION -> PAPER + ACTUAL -- the ENTRY paper order
#: of the same decision as its execution intent (a sibling, never a parent).
SIBLING_PAPER_SQL = (
    "coalesce(m.paper_order_id, m.detail->>'for_paper_order',"
    " (SELECT ps.order_id FROM execution_intents ei JOIN paper_orders ps"
    "    ON ps.decision_id = ei.decision_id AND ps.role = 'ENTRY'"
    "   WHERE ei.intent_id = m.execution_intent_id LIMIT 1))")
PAPER_JOIN_SQL = "LEFT JOIN paper_orders p ON p.order_id = " + SIBLING_PAPER_SQL
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
    pm_why = {"MIRROR_ENABLED": ("the actual lane is enabled: each qualified investment "
                                 "decision is executed at 1:%s beside its paper sibling"
                                 % (int(ctl.get("scale") or 1000) if ctl else 1000)),
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
            "refusal", "p_pinnacle", "p_blended", "p_internal", "executable_price",
            "executable_price_source",
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
            # the probability the paper decision acted on (research model +
            # Pinnacle blend) and the research model's own, as recorded
            "p_blended": _f(r.get("d_p_blended"), 9),
            "p_internal": _f(r.get("d_p_internal"), 9),
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


def _actual_pnl(gl) -> dict:
    """ACTUAL group P&L from venue records only -- never paper, never summed
    with paper. REALIZED when every live contract bought has been sold back
    (cash flow of the venue fills, fees included); otherwise the latest
    Xavier review's mark of the open ACTUAL position (UNREALIZED, as of that
    review); otherwise null with the reason."""
    if not gl or not gl.get("live_bought"):
        return {"group_pnl_usd": None, "group_pnl_kind": None,
                "group_pnl_as_of": None, "group_pnl_source": None,
                "group_pnl_why_unavailable": (
                    "no venue-confirmed fill in this group: there is no ACTUAL "
                    "position, so no ACTUAL P&L")}
    if gl["live_held"] == 0:
        return {"group_pnl_usd": _f(gl["live_cash"]), "group_pnl_kind": "REALIZED",
                "group_pnl_as_of": _iso(gl.get("last_fill_at")),
                "group_pnl_source": "execmirror_fills (venue fills: proceeds - cost - fees)",
                "group_pnl_why_unavailable": None}
    if gl.get("unrealized_usd") is not None:
        return {"group_pnl_usd": _f(gl["unrealized_usd"]), "group_pnl_kind": "UNREALIZED_MARK",
                "group_pnl_as_of": _iso(gl.get("review_at")),
                "group_pnl_source": "smalllive_reviews.unrealized_usd (exit side of a fresh venue BBO)",
                "group_pnl_why_unavailable": None}
    return {"group_pnl_usd": None, "group_pnl_kind": None, "group_pnl_as_of": None,
            "group_pnl_source": None,
            "group_pnl_why_unavailable": (
                "ACTUAL position still open and no Xavier review has marked it "
                "with a fresh venue quote: unrealized P&L unavailable")}


def _actual_section(r: dict, scale, ctl: dict | None = None, gl: dict | None = None) -> dict:
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
    fp = (ctl or {}).get("account_fingerprint")
    out = {"label": "ACTUAL", "venue": LIVE_VENUE,
            "account": "execution mirror account (Polymarket US)",
            # a prefix identifies the retail account without publishing it
            "account_fingerprint_prefix": (str(fp)[:8] if fp else None),
            # the mirror's own live intent, derived from the paper order
            "mirror_intent": {"mirror_id": r["mirror_id"], "role": r["role"],
                              "intent": r["intent"], "order_type": r.get("order_type"),
                              "tif": r.get("tif"), "post_only": r.get("post_only"),
                              "created_at": _iso(r.get("created_at")),
                              "source": "execmirror_orders"},
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
    out.update(_actual_pnl(gl))
    return out


def _difference(r: dict, paper: dict, actual: dict, scale) -> dict:
    scale = _d(scale)
    excluded_qty = _f(r.get("scaled_qty")) if actual["excluded"] else None
    decided = r.get("d_decided_at") or r.get("paper_decided_at") or r.get("p_decided_at")
    out = {"rounded_qty": actual["rounding_delta"],
           # ROUNDING: the paper quantity / scale, the whole-contract live
           # quantity the mirror intended, and the difference between them
           "scaled_qty": actual["scaled_qty"],
           "intended_live_qty": (None if actual["excluded"] else actual["intended_qty"]),
           # LATENCY from recorded instants only; null where an end is absent
           "decision_to_submit_ms": _ms_between(decided, r.get("submit_started_at")),
           "submit_to_ack_ms": _ms_between(r.get("submit_started_at"), r.get("accepted_at")),
           "submit_latency_ms_recorded": actual["submit_latency_ms"],
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
           "groups_pnl": {}, "paper_protection": {}, "live_protection": {},
           "groups_live": {}, "assessments": {}, "theses": {}, "value_add": {},
           "xavier_schema": False, "management_policy": None}
    if groups:
        await _xavier_management_records(conn, groups, out)
        for h in await conn.fetch(
                """SELECT handoff_id, group_id, owner, confirmed_qty, outstanding_qty,
                          first_fill_at FROM paper_handoffs WHERE group_id = ANY($1)""", groups):
            out["handoffs"][h["group_id"]] = dict(h)
        for v in await conn.fetch(
                """SELECT DISTINCT ON (r.group_id) r.group_id, r.review_id, r.reviewed_at,
                          r.trigger, r.recommendation, r.refusal, r.measure,
                          r.measure->>'evidence_state' AS evidence_state,
                          r.measure->>'probability_limitation' AS probability_limitation,
                          r.exceptional, r.selection, r.exposure,
                          s.config -> 'cadence' ->> 'xavier_backstop_s' AS backstop_s
                     FROM paper_xavier_reviews r
                     LEFT JOIN paper_sessions s ON s.session_id = r.session_id
                    WHERE r.group_id = ANY($1)
                    ORDER BY r.group_id, r.reviewed_at DESC, r.review_id DESC""", groups):
            out["reviews"][v["group_id"]] = dict(v)
        # PROTECTION, read from the orders and fills themselves: a resting
        # protective order is NOT filled protection, so the two are separate.
        # The raw states of each bucket come from the ONE shared mapping
        # (order_state_truth): standing = RESTING / PARTIAL (a requested
        # cancel can still fill), pending = PROPOSED / SUBMITTED / UNKNOWN,
        # and only a fill-bearing state's own filled quantity is filled.
        for g in await conn.fetch(
                """SELECT g AS group_id,
                          (SELECT coalesce(sum(o.qty - o.filled_qty), 0) FROM paper_orders o
                            WHERE o.group_id = g AND o.role = 'STANDING_PROTECTION'
                              AND o.state = ANY($2::text[])) AS resting,
                          (SELECT count(*) FROM paper_orders o
                            WHERE o.group_id = g AND o.role = 'STANDING_PROTECTION'
                              AND o.state = ANY($2::text[])) AS resting_orders,
                          (SELECT coalesce(sum(o.qty - o.filled_qty), 0) FROM paper_orders o
                            WHERE o.group_id = g AND o.role = 'STANDING_PROTECTION'
                              AND o.state = ANY($3::text[])) AS pending,
                          (SELECT coalesce(sum(f.qty), 0) FROM paper_fills f
                            WHERE f.group_id = g AND f.role = 'STANDING_PROTECTION') AS filled
                     FROM unnest($1::text[]) AS g""", groups,
                OST.raw_states(OST.SRC_PAPER, OST.STANDING_STATES),
                OST.raw_states(OST.SRC_PAPER, OST.PENDING_STATES)):
            out["paper_protection"][g["group_id"]] = dict(g)
        for g in await conn.fetch(
                """SELECT g AS group_id,
                          (SELECT coalesce(sum(m.live_qty - m.cum_qty), 0) FROM execmirror_orders m
                            WHERE m.group_id = g AND m.role = 'STANDING_PROTECTION'
                              AND m.state = ANY($2::text[])) AS resting,
                          (SELECT count(*) FROM execmirror_orders m
                            WHERE m.group_id = g AND m.role = 'STANDING_PROTECTION'
                              AND m.state = ANY($2::text[])) AS resting_orders,
                          (SELECT coalesce(sum(m.live_qty - m.cum_qty), 0) FROM execmirror_orders m
                            WHERE m.group_id = g AND m.role = 'STANDING_PROTECTION'
                              AND m.state = ANY($3::text[])) AS pending,
                          (SELECT coalesce(sum(m.cum_qty), 0) FROM execmirror_orders m
                            WHERE m.group_id = g AND m.role = 'STANDING_PROTECTION'
                              AND m.state = ANY($4::text[])) AS filled
                     FROM unnest($1::text[]) AS g""", groups,
                OST.raw_states(OST.SRC_MIRROR, OST.STANDING_STATES),
                OST.raw_states(OST.SRC_MIRROR, OST.PENDING_STATES),
                OST.raw_states(OST.SRC_MIRROR, OST.FILL_BEARING_STATES)):
            out["live_protection"][g["group_id"]] = dict(g)
        # ACTUAL inventory and cash, from venue fills only
        fills_by_group: dict = {}
        for f in await conn.fetch(
                """SELECT group_id, intent, qty, price, fee_usd, observed_at
                     FROM execmirror_fills WHERE group_id = ANY($1)""", groups):
            fills_by_group.setdefault(f["group_id"], []).append(dict(f))
        for g, fl in fills_by_group.items():
            bought = sum(_d(f["qty"]) for f in fl if _is_buy(f["intent"]))
            sold = sum(_d(f["qty"]) for f in fl if not _is_buy(f["intent"]))
            out["groups_live"][g] = {"live_bought": bought, "live_held": bought - sold,
                                     "live_cash": live_cash_flow(fl),
                                     "last_fill_at": max(f["observed_at"] for f in fl)}
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
            out["groups_pnl"][g["group_id"]] = {"realized": realized, "cash": g["cash"],
                                                "fills": g["fills"], "open_qty": g["open_qty"],
                                                "settled": bool(g["settled"])}
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
                                  paper_recommendation, paper_review_id,
                                  resting_protection_qty, filled_protection_qty,
                                  committed_exit_qty, detail, quote,
                                  detail->>'evidence_state' AS evidence_state,
                                  detail->'probability_evidence'->>'probability_limitation'
                                      AS probability_limitation
                             FROM smalllive_reviews WHERE handoff_id = ANY($1)
                            ORDER BY handoff_id, reviewed_at DESC, review_id DESC""", hids):
                    out["live_reviews"][v["handoff_id"]] = dict(v)
            for g, lh in out["live_handoffs"].items():
                lv = out["live_reviews"].get(lh["handoff_id"])
                if g in out["groups_live"] and lv is not None:
                    out["groups_live"][g]["unrealized_usd"] = lv["unrealized_usd"]
                    out["groups_live"][g]["review_at"] = lv["reviewed_at"]
        if await conn.fetchval("SELECT to_regclass('smalllive_reconciliations') IS NOT NULL"):
            for c in await conn.fetch(
                    """SELECT group_id, status, discrepancies, reconciled_at, changed_at, chain
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


async def _xavier_management_records(conn, groups: list, out: dict) -> None:
    """Xavier's management record for the groups (migration 206): the
    latest assessment per (group, PAPER/ACTUAL), the entry theses, the
    latest value-add, and the management-policy record. Read only; absent
    tables leave the section empty with the reason."""
    from .agents import xavier_management as XM
    from .agents import xavier_small_live_policy as XSP
    out["management_policy"] = await XSP.load_review_record(conn)
    if not await XM.has_schema(conn):
        return
    out["xavier_schema"] = True
    for r in await conn.fetch(
            """SELECT DISTINCT ON (group_id, position_kind) assessment_id, review_id,
                      group_id, position_kind, assessed_at, trigger, review_latency_s,
                      latency_bound_s, within_bound, evidence_state, probability,
                      probability_source, probability_age_s, thesis_state, recommendation,
                      discretionary_permitted, reallocate, thesis_id,
                      to_jsonb(x) -> 'valuation' AS valuation
                 FROM xavier_management_assessments x WHERE group_id = ANY($1)
                ORDER BY group_id, position_kind, assessed_at DESC, assessment_id DESC""",
            groups):
        out["assessments"][(r["group_id"], r["position_kind"])] = dict(r)
    for r in await conn.fetch(
            """SELECT thesis_id, group_id, position_kind, entry_probability,
                      probability_source, entry_ev_usd, entered_at, evidence_expires_at,
                      thesis_expires_at, expiry_basis
                 FROM xavier_entry_theses WHERE group_id = ANY($1)""", groups):
        out["theses"][(r["group_id"], r["position_kind"])] = dict(r)
    for v in await conn.fetch(
            """SELECT DISTINCT ON (thesis_id) thesis_id, group_id, position_kind, status,
                      incremental, computed_at
                 FROM xavier_value_add WHERE group_id = ANY($1)
                ORDER BY thesis_id, (status = 'FINAL') DESC, computed_at DESC""", groups):
        out["value_add"][(v["group_id"], v["position_kind"])] = dict(v)


def _xavier_management(g, mg: dict) -> dict:
    """The management section's Xavier record for one group (paper and
    actual): evidence and thesis state, the recommendation, the shadow
    REALLOCATE, review latency against its bound, value-add and the
    management-policy record. Unavailable is null with the reason."""
    pol = mg.get("management_policy")
    out = {"management_policy": pol,
           "management_policy_status": (pol or {}).get("status"),
           "management_policy_approved": bool((pol or {}).get("approved")),
           "href": "/api/command/xavier/management"}
    import time as _time

    from . import xavier_freshness as XF
    from .agents import xavier_management as XM
    for kind in ("PAPER", "ACTUAL"):
        a = (mg.get("assessments") or {}).get((g, kind))
        t = (mg.get("theses") or {}).get((g, kind))
        v = (mg.get("value_add") or {}).get((g, kind))
        re_ = _js(a.get("reallocate")) if a else {}
        # THE RECOMMENDATION RE-JUDGED NOW (owner P0): the action only while
        # CURRENT, the state otherwise; the stored word stays recorded
        fr = None if a is None else XF.of_assessment(
            dict(a, assessed_at=(a["assessed_at"].timestamp()
                                 if hasattr(a["assessed_at"], "timestamp")
                                 else a["assessed_at"]),
                 valuation=_js(a.get("valuation")) or None),
            now=_time.time(), limit_s=XM._config_limit())
        out[kind.lower()] = {
            "recommendation_state": fr["recommendation_state"] if fr else None,
            "recorded_recommendation": a["recommendation"] if a else None,
            "freshness": fr,
            "assessment_id": a["assessment_id"] if a else None,
            "reviewed_at": _iso(a["assessed_at"]) if a else None,
            "trigger": a["trigger"] if a else None,
            "review_latency_s": _f(a["review_latency_s"], 3) if a else None,
            "latency_bound_s": _f(a["latency_bound_s"], 3) if a else None,
            "within_bound": a["within_bound"] if a else None,
            "evidence_state": a["evidence_state"] if a else None,
            "probability": _f(a["probability"], 9) if a else None,
            "thesis_state": (a["thesis_state"] if a else
                             "NO_ENTRY_THESIS" if t is None else None),
            "thesis_id": t["thesis_id"] if t else None,
            "entry_probability": _f(t["entry_probability"], 9) if t else None,
            "entry_ev_usd": _f(t["entry_ev_usd"]) if t else None,
            "thesis_expires_at": _iso(t["thesis_expires_at"]) if t else None,
            "evidence_expires_at": _iso(t["evidence_expires_at"]) if t else None,
            "recommendation": fr["display_recommendation"] if fr else None,
            "discretionary_permitted": a["discretionary_permitted"] if a else None,
            "reallocate_shadow": ({"recommended": bool(re_.get("recommended")),
                                   "blocker": re_.get("blocker"), "mode": "SHADOW"}
                                  if a else None),
            "value_add_status": v["status"] if v else None,
            "value_add_incremental": _js(v["incremental"]) if v else None,
            "why_unavailable": (None if a else
                                "no Xavier management record for this group yet"
                                if mg.get("xavier_schema") else
                                "migration 206 not applied")}
    return out


PROBABILITY_EVIDENCE_STATES = ("FRESH_CURRENT_PROBABILITY",
                               "STALE_ENTRY_TIME_PROBABILITY",
                               "PROBABILITY_UNAVAILABLE")
# Read defensively: the evidence-state field is a recent addition to Xavier's
# review record; whichever of these keys a review carries is shown with the
# exact place it was read from. Absent -> null ("unavailable"), never guessed
# from other fields.
PROBABILITY_EVIDENCE_KEYS = ("probability_evidence_state", "evidence_state",
                             "probability_evidence", "probability_state",
                             "probability_freshness")
RECONCILIATION_MEANING = {
    "MATCHED": "the paper and actual chains agree",
    "DISCREPANCY": "Audrey found a difference between the paper and actual chains (listed)",
    "PENDING": "a live order in the group is still working: not yet final",
    "NOT_MIRRORED": ("paper only: every mirror row was excluded before submission, "
                     "nothing was sent to the venue and no actual leg exists by design"),
}
PROTECTION_RULE = ("a resting protective order is NOT filled protection: the standing "
                   "(resting) quantity and the filled protection quantity are shown "
                   "separately and never added together. " + OST.RULE + ". "
                   + OST.POSITION_BASIS)


def protection_quantities(*, held, resting, filled, pending=None) -> dict:
    """THE PROTECTION LEDGER OF ONE BOOK'S GROUP, from the aggregates. A
    standing protective SALE of the held side has already taken its FILLED
    quantity out of the holding, so the position it protects is held + filled
    and the unprotected quantity is that minus the filled protection -- the
    RESTING quantity never reduces it. Pure."""
    h, r, f = _d(held), _d(resting), _d(filled)
    position = h + f if h > 0 or f > 0 else Decimal(0)
    return {"position_qty": _f(position),
            "standing_resting_qty": _f(r),
            "filled_protection_qty": _f(f),
            "pending_submission_qty": None if pending is None else _f(_d(pending)),
            "unprotected_qty": _f(max(Decimal(0), position - f))}


def _evidence_state(docs) -> tuple:
    """(value, source) of the first probability evidence-state field found in
    the given (name, json) documents, else (None, None)."""
    for name, doc in docs:
        doc = _js(doc)
        if not isinstance(doc, dict):
            continue
        for k in PROBABILITY_EVIDENCE_KEYS:
            v = doc.get(k)
            if isinstance(v, dict):
                v, k = v.get("state"), k + ".state"
            if isinstance(v, str) and v:
                return v, "%s.%s" % (name, k)
    return None, None


def _probability_freshness(v, lv) -> dict:
    docs = []
    if v:
        docs += [("paper_xavier_reviews.measure", v.get("measure")),
                 ("paper_xavier_reviews.exceptional", v.get("exceptional")),
                 ("paper_xavier_reviews.selection", v.get("selection"))]
    if lv:
        docs += [("smalllive_reviews.detail", lv.get("detail"))]
    state, src = _evidence_state(docs)
    m = _js(v.get("measure")) if v else {}
    m = m if isinstance(m, dict) else {}
    why = None
    if state is None:
        why = ("no Xavier review of this group yet" if not (v or lv) else
               "the latest Xavier review records no probability evidence-state field")
    return {"evidence_state": state, "evidence_state_source": src,
            "evidence_state_recognised": (state in PROBABILITY_EVIDENCE_STATES
                                          if state else None),
            "review_id": (v or {}).get("review_id") or (lv or {}).get("review_id"),
            "reviewed_at": _iso((v or {}).get("reviewed_at") or (lv or {}).get("reviewed_at")),
            # the review's own measure, as recorded (supporting detail only)
            "measure_source": m.get("source"),
            "measure_stale": m.get("stale") if isinstance(m.get("stale"), bool) else None,
            "measure_p": _f(m.get("p"), 9) if isinstance(m.get("p"), (int, float)) else None,
            "measure_why": m.get("why"),
            "why_unavailable": why}


def _protection(g, mg) -> dict:
    gp = mg["groups_pnl"].get(g) or {}
    pp = mg["paper_protection"].get(g)
    gl = mg["groups_live"].get(g) or {}
    lp = mg["live_protection"].get(g)
    if pp is not None and gp.get("fills"):
        open_q = _d(gp.get("open_qty"))
        q = protection_quantities(held=open_q, resting=pp["resting"],
                                  filled=pp["filled"], pending=pp.get("pending"))
        paper = {"label": "PAPER POSITION (SIMULATED)", "open_qty": _f(open_q),
                 "position_qty": q["position_qty"],
                 "standing_resting_qty": q["standing_resting_qty"],
                 "standing_resting_orders": pp["resting_orders"],
                 "pending_submission_qty": q["pending_submission_qty"],
                 "filled_protection_qty": q["filled_protection_qty"],
                 "unprotected_qty": q["unprotected_qty"],
                 "source": ("paper_orders (role STANDING_PROTECTION, still resting) / "
                            "paper_fills (role STANDING_PROTECTION)"),
                 "why_unavailable": None}
    else:
        paper = {"label": "PAPER POSITION (SIMULATED)", "open_qty": None,
                 "position_qty": None, "pending_submission_qty": None,
                 "standing_resting_qty": None, "standing_resting_orders": None,
                 "filled_protection_qty": None, "unprotected_qty": None, "source": None,
                 "why_unavailable": ("no simulated fill in this group: there is no paper "
                                     "position to protect")}
    if lp is not None and gl.get("live_bought"):
        held = _d(gl["live_held"])
        q = protection_quantities(held=held, resting=lp["resting"],
                                  filled=lp["filled"], pending=lp["pending"])
        actual = {"label": "ACTUAL POSITION", "held_qty": _f(held),
                  "position_qty": q["position_qty"],
                  "standing_resting_qty": q["standing_resting_qty"],
                  "standing_resting_orders": lp["resting_orders"],
                  "pending_submission_qty": q["pending_submission_qty"],
                  "filled_protection_qty": q["filled_protection_qty"],
                  "unprotected_qty": q["unprotected_qty"],
                  "source": ("execmirror_orders (role STANDING_PROTECTION: resting = "
                             "OPEN / PARTIALLY_FILLED / CANCEL_REQUESTED remainder; "
                             "filled = venue cum qty)"),
                  "why_unavailable": None}
    else:
        actual = {"label": "ACTUAL POSITION", "held_qty": None,
                  "position_qty": None,
                  "standing_resting_qty": None, "standing_resting_orders": None,
                  "pending_submission_qty": None, "filled_protection_qty": None,
                  "unprotected_qty": None, "source": None,
                  "why_unavailable": ("no venue-confirmed fill in this group: there is no "
                                      "ACTUAL position to protect")}
    return {"paper": paper, "actual": actual, "rule": PROTECTION_RULE}


def _next_review(g, h, v, lh, lv, mg, now) -> dict:
    gp = mg["groups_pnl"].get(g) or {}
    paper = {"due_by": None, "basis": None, "overdue_at_read": None, "why_unavailable": None}
    if not h:
        paper["why_unavailable"] = "no paper position under Xavier (no handoff): no review is scheduled"
    elif gp.get("realized"):
        paper["why_unavailable"] = "the paper position is closed or settled: no further review"
    elif not v:
        paper["why_unavailable"] = "no Xavier review of the paper position recorded yet"
    elif v.get("backstop_s") in (None, ""):
        paper["why_unavailable"] = ("the paper session's configuration records no "
                                    "cadence.xavier_backstop_s, so no due time can be derived")
    else:
        sec = float(v["backstop_s"])
        due = v["reviewed_at"] + dt.timedelta(seconds=sec)
        paper.update({"due_by": _iso(due), "overdue_at_read": now > due,
                      "basis": ("latest paper review at %s + the scheduled backstop of %g s "
                                "(paper_sessions.config.cadence.xavier_backstop_s); sooner on "
                                "a new fill or market event" % (_iso(v["reviewed_at"]), sec))})
    actual = {"due_by": None, "basis": None, "overdue_at_read": None, "why_unavailable": None}
    if not lh:
        actual["why_unavailable"] = "no ACTUAL position under Xavier (no live handoff): no review is scheduled"
    elif lh.get("state") != "OPEN":
        actual["why_unavailable"] = "the ACTUAL position is closed: no further review"
    elif not lv:
        actual["why_unavailable"] = ("live handoff recorded; no review of the ACTUAL position "
                                     "yet (first one on the next mirror tick)")
    else:
        from .execmirror import MANAGEMENT_EVERY_S
        due = lv["reviewed_at"] + dt.timedelta(seconds=MANAGEMENT_EVERY_S)
        actual.update({"due_by": _iso(due), "overdue_at_read": now > due,
                       "basis": ("latest ACTUAL review at %s + the per-position review "
                                 "cadence of %g s (execmirror.MANAGEMENT_EVERY_S; sooner on a "
                                 "change of held quantity), while the mirror service runs -- "
                                 "also while the lane is disabled"
                                 % (_iso(lv["reviewed_at"]), MANAGEMENT_EVERY_S))})
    return {"paper": paper, "actual": actual}


def _link(key, label, state, ref=None, why=None) -> dict:
    return {"link": key, "label": label, "state": state, "ref": ref, "why": why}


def _chain(r: dict, paper: dict, actual: dict, mg: dict) -> dict:
    """Paper / live chain completeness for one row: each link PRESENT,
    ABSENT (expected but not on record) or NOT_APPLICABLE (with why)."""
    g = r.get("group_id")
    P, A, N = "PRESENT", "ABSENT", "NOT_APPLICABLE"
    links = []
    has_po = r.get("p_order_id") is not None
    if r.get("d_id"):
        links.append(_link("decision", "Paper decision", P, r["d_id"]))
    elif not has_po:
        links.append(_link("decision", "Paper decision", N, None,
                           "mirror-generated %s order: it copies no paper decision" % r["role"]))
    elif r["role"] not in ("ENTRY", "HEDGE") and not r.get("p_decision_id"):
        links.append(_link("decision", "Paper decision", N, None,
                           "a %s order follows Xavier's management of the position, not "
                           "an entry decision" % r["role"]))
    else:
        links.append(_link("decision", "Paper decision", A, r.get("p_decision_id"),
                           "the paper order names no decision found in paper_decisions"))
    links.append(_link("paper_order", "Paper order", P, r["p_order_id"]) if has_po else
                 _link("paper_order", "Paper order", N, None,
                       "mirror-generated %s order: no paper order behind it" % r["role"]))
    if not has_po:
        links.append(_link("paper_fill", "Paper fill (simulated)", N, None, "no paper order"))
    elif paper.get("simulated_fill_count"):
        links.append(_link("paper_fill", "Paper fill (simulated)", P,
                           "%d fill(s)" % paper["simulated_fill_count"]))
    else:
        links.append(_link("paper_fill", "Paper fill (simulated)", A, None,
                           "no simulated fill (paper order %s)" % (r.get("p_state") or "state unknown")))
    links.append(_link("mirror_intent", "Mirror intent", P, r["mirror_id"]))
    if actual["venue_order_id"]:
        links.append(_link("venue_order", "Venue order", P, actual["venue_order_id"]))
    elif actual["excluded"]:
        links.append(_link("venue_order", "Venue order", N, None,
                           "not sent: excluded (%s)" % r.get("exclusion")))
    else:
        links.append(_link("venue_order", "Venue order", A, None,
                           "no venue order id on record (state %s)" % r["state"]))
    if not actual["venue_order_id"]:
        links.append(_link("venue_fill", "Venue fill", N, None, "no venue order"))
    elif r.get("lf_n"):
        links.append(_link("venue_fill", "Venue fill", P, "%d fill(s)" % r["lf_n"]))
    else:
        links.append(_link("venue_fill", "Venue fill", A, None,
                           actual.get("why_no_fill_figures") or "no venue fill recorded"))
    h = mg["handoffs"].get(g) if g else None
    gp = mg["groups_pnl"].get(g) or {}
    if h:
        links.append(_link("xavier_paper_handoff", "Xavier handoff · paper", P, h["handoff_id"]))
    elif gp.get("fills"):
        links.append(_link("xavier_paper_handoff", "Xavier handoff · paper", A, None,
                           "the group has simulated fills but no Xavier handoff"))
    else:
        links.append(_link("xavier_paper_handoff", "Xavier handoff · paper", N, None,
                           "no simulated fill in the group"))
    lh = mg["live_handoffs"].get(g) if g else None
    gl = mg["groups_live"].get(g) or {}
    if lh:
        links.append(_link("xavier_actual_handoff", "Xavier handoff · actual", P, lh["handoff_id"]))
    elif gl.get("live_bought"):
        links.append(_link("xavier_actual_handoff", "Xavier handoff · actual", A, None,
                           "the group has venue fills but no Xavier handoff of the ACTUAL position"))
    else:
        links.append(_link("xavier_actual_handoff", "Xavier handoff · actual", N, None,
                           "no venue-confirmed buy fill in the group"))
    rec = mg["reconciliations"].get(g) if g else None
    if rec:
        links.append(_link("reconciliation", "Audrey reconciliation", P, rec["status"]))
    elif not g:
        links.append(_link("reconciliation", "Audrey reconciliation", N, None, "no paper group"))
    else:
        links.append(_link("reconciliation", "Audrey reconciliation", A, None,
                           "no Audrey reconciliation of this group on record"))
    absent = [x["link"] for x in links if x["state"] == A]
    return {"links": links, "complete": not absent, "absent": absent,
            "present_count": sum(1 for x in links if x["state"] == P),
            "not_applicable_count": sum(1 for x in links if x["state"] == N),
            "basis": ("decision -> paper order -> paper fill -> mirror intent -> venue "
                      "order -> venue fill -> Xavier handoff -> Audrey reconciliation; "
                      "NOT_APPLICABLE links are absent by design, with the reason")}


def _gated_review(v: dict) -> dict:
    """A paper review's recommendation re-judged at read time
    (xavier_freshness.of_review on its own recorded measure)."""
    import time as _time

    from . import xavier_freshness as XF
    ra = v.get("reviewed_at")
    from .agents import xavier_management as XM
    return XF.of_review(dict(v, reviewed_at=(ra.timestamp() if hasattr(
        ra, "timestamp") else ra)), now=_time.time(),
        limit_s=XM._config_limit())


def _management_section(r: dict, mg: dict, now=None) -> dict:
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
          # re-judged now (owner P0): the action only while CURRENT
          "latest_recommendation": _gated_review(v)["display_recommendation"]
          if v else None,
          "latest_recommendation_state": _gated_review(v)[
              "recommendation_state"] if v else None,
          "latest_recorded_recommendation": v["recommendation"] if v else None,
          # the probability's freshness on that review (paper_xavier E_*)
          "latest_probability_evidence_state": v.get("evidence_state") if v else None,
          "latest_probability_limitation": v.get("probability_limitation") if v else None,
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
              "latest_probability_evidence_state": lv.get("evidence_state") if lv else None,
              "latest_probability_limitation": (lv.get("probability_limitation")
                                                if lv else None),
              "why_unavailable": None if lv else "handoff recorded; no review of the actual position yet"}
    else:
        xa = {"label": "ACTUAL POSITION", "present": False, "handoff_id": None,
              "latest_review_id": None, "latest_action": None,
              "why_unavailable": UNAVAILABLE_ACTUAL_XAVIER}
    if lh:
        xa["paper_recommendation_followed"] = lv["paper_recommendation"] if lv else None
    now = now or dt.datetime.now(dt.timezone.utc)
    chain_link = None
    if rec:
        ch = _js(rec.get("chain"))
        for ln in (ch.get("links") or []) if isinstance(ch, dict) else []:
            if isinstance(ln, dict) and ln.get("mirror_id") == r.get("mirror_id"):
                chain_link = ln
                break
    return {"xavier_paper": xp, "xavier_actual": xa,
            "xavier_management": _xavier_management(g, mg),
            "probability_freshness": _probability_freshness(v, lv),
            "protection": _protection(g, mg),
            "next_review": _next_review(g, h, v, lh, lv, mg, now),
            "audrey_findings": findings,
            "audrey_findings_why_empty": (None if findings else
                                          "no Audrey finding names this decision, order or group"),
            "audrey_reconciliation": ({"status": rec["status"],
                                       "meaning": RECONCILIATION_MEANING.get(rec["status"]),
                                       "paper_only": rec["status"] == "NOT_MIRRORED",
                                       "reconciled_at": _iso(rec["reconciled_at"]),
                                       "status_changed_at": _iso(rec.get("changed_at")),
                                       "discrepancies": _js(rec["discrepancies"]) or [],
                                       "audrey_chain_link": chain_link}
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


# ═════════════════════════════════════════════════════════════════════
# ONE DECISION -> PAPER + ACTUAL (owner correction 2026-10-03)
# ═════════════════════════════════════════════════════════════════════

def _tl_s(tl: dict, key: str):
    """A timeline instant in epoch seconds (nanoseconds when recorded)."""
    v = (tl or {}).get(key) or {}
    if v.get("utc_ns") is not None:
        return v["utc_ns"] / 1e9
    return v.get("utc_s") if isinstance(v.get("utc_s"), (int, float)) else None


def _ms(a, b):
    return None if a is None or b is None else round((b - a) * 1000.0, 3)


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 3)


LATENCY_LEGS = (
    ("provider_to_receipt_ms", "pinnapi_provider_ts", "pinnapi_receipt"),
    ("receipt_to_decision_ms", "pinnapi_receipt", "decision_complete"),
    ("decision_to_intent_ms", "decision_complete", "intent_created"),
    ("decision_to_submit_ms", "decision_complete", "submit_start"),
    ("intent_to_submit_ms", "intent_created", "submit_start"),
    ("submit_to_ack_ms", "submit_start", "ack"),
    ("ack_to_first_fill_ms", "ack", "first_fill_seen"),
    ("decision_to_first_fill_ms", "decision_complete", "first_fill_seen"),
    ("book_check_ms", "book_check_start", "book_check_end"))


def _settlement_admission(adm: dict, facts: dict) -> dict:
    sa = adm.get("settlement") or {}
    st = facts.get("settlement") or {}
    if not sa and not st:
        return {"status": "UNAVAILABLE", "why": "no admission facts on this intent"}
    return {"status": sa.get("status") or "UNAVAILABLE",
            "compatibility": sa.get("compatibility", st.get("compatibility")),
            "overall_established": sa.get("overall_established",
                                          st.get("overall_established")),
            "blockers": sa.get("blockers", st.get("blockers")),
            "research_disclosure": sa.get("research_disclosure",
                                          st.get("research_disclosure")),
            "research_disclosure_counts_for_actual": False}


def _book_admission(adm: dict, facts: dict) -> dict:
    ba = adm.get("book_currency") or {}
    bc = (facts.get("book") or {}).get("book_currency") or {}
    if not ba and not bc:
        return {"status": "UNAVAILABLE", "why": "no admission facts on this intent"}
    return {"status": ba.get("status") or "UNAVAILABLE",
            "verdict": ba.get("verdict", bc.get("verdict")),
            "rule": ba.get("rule", bc.get("rule")),
            "approved_live_rules": ba.get("approved_live_rules"),
            "why": ba.get("why")}


def _decision_pnl(mg: dict, g) -> dict:
    """SIMULATED and ACTUAL P&L of the decision's group, side by side and
    never summed; unavailable is null with the reason."""
    p = mg["groups_pnl"].get(g) or {}
    lv = mg["groups_live"].get(g) or {}
    sim = None if not p.get("realized") else _f(p.get("cash"))
    act = None
    if lv and lv.get("live_held") == 0 and lv.get("live_cash") is not None:
        act = _f(lv.get("live_cash"))
    return {"simulated_realized_usd": sim,
            "simulated_why_unavailable": (None if sim is not None else
                                          "no realized paper P&L yet (open, unfilled "
                                          "or unsettled)"),
            "actual_realized_usd": act,
            "actual_unrealized_usd": _f(lv.get("unrealized_usd")) if lv else None,
            "actual_why_unavailable": (None if act is not None else
                                       "no closed actual position (no venue fill, or "
                                       "inventory still held)"),
            "never_summed": True}


def _decision_management(mg: dict, g) -> dict:
    rv = mg["reviews"].get(g) or {}
    lh = mg["live_handoffs"].get(g) or {}
    lr = mg["live_reviews"].get(lh.get("handoff_id")) if lh else None
    lr = lr or {}
    pp = mg["paper_protection"].get(g) or {}
    lp = mg["live_protection"].get(g) or {}
    nxt = None
    if rv.get("reviewed_at") is not None and rv.get("backstop_s"):
        try:
            nxt = _iso(rv["reviewed_at"] + dt.timedelta(seconds=float(rv["backstop_s"])))
        except (TypeError, ValueError):
            nxt = None
    gr = _gated_review(rv) if rv else None
    return {"xavier_recommendation": (gr["display_recommendation"]
                                      if gr else None),
            "xavier_recommendation_state": (gr["recommendation_state"]
                                            if gr else None),
            "xavier_recorded_recommendation": rv.get("recommendation"),
            "xavier_actual_action": lr.get("action"),
            "probability_evidence_state": (lr.get("evidence_state")
                                           or rv.get("evidence_state")),
            "probability_limitation": (lr.get("probability_limitation")
                                       or rv.get("probability_limitation")),
            "alternatives": (_js(rv.get("selection")) or {}).get("alternatives")
            if rv.get("selection") is not None else None,
            "standing_protection": {"paper_resting_qty": _f(pp.get("resting")),
                                    "actual_resting_qty": _f(lp.get("resting"))},
            "filled_protection": {"paper_filled_qty": _f(pp.get("filled")),
                                  "actual_filled_qty": _f(lp.get("filled"))},
            "protection_rule": PROTECTION_RULE,
            "last_review_at": _iso(rv.get("reviewed_at")),
            "next_review_by": nxt,
            "xavier_management": _xavier_management(g, mg),
            "why_unavailable": (None if rv or lr else
                                "no Xavier review for this group yet")}


def _decision_audit(mg: dict, g, r: dict) -> dict:
    rc = mg["reconciliations"].get(g) or {}
    disc = _js(rc.get("discrepancies")) if rc else None
    return {"audrey_status": rc.get("status") or None,
            "meaning": RECONCILIATION_MEANING.get(rc.get("status")),
            "discrepancies": disc,
            "reconciled_at": _iso(rc.get("reconciled_at")),
            "paper_vs_actual_price_diff": (
                None if r.get("pf_avg_px") is None or r.get("m_avg_px") is None
                else _f(_d(r["m_avg_px"]) - _d(r["pf_avg_px"]))),
            "why_unavailable": (None if rc else
                                "Audrey has not reconciled this group yet")}


async def decisions(conn, *, limit: int = 50) -> dict:
    """EACH QUALIFIED DECISION WITH ITS TWO SIBLINGS: SIMULATED (paper) and
    ACTUAL (Polymarket retail), from the ONE execution intent linking them,
    with the decision -> venue latency chain and its p50/p95/p99. Unavailable
    is null with the reason, never zero; paper and actual are never summed."""
    if not await conn.fetchval("SELECT to_regclass('execution_intents') IS NOT NULL"):
        return {"status": "UNAVAILABLE", "why": "migration 199 not applied", "rows": []}
    ctl = dict(await conn.fetchrow("SELECT * FROM execmirror_control WHERE id = 1") or {})
    rows = [dict(r) for r in await conn.fetch(
        """SELECT i.*, d.p_pinnacle, d.verdict, d.policy_decision,
                  p.order_id AS p_order_id, p.state AS p_state, p.qty AS p_qty,
                  p.filled_qty AS p_filled, pf.avg_px AS pf_avg_px, pf.fees AS pf_fees,
                  m.mirror_id, m.state AS m_state, m.venue_order_id, m.venue_state,
                  m.live_qty AS m_live_qty, m.cum_qty AS m_cum, m.avg_px AS m_avg_px,
                  m.fees_usd AS m_fees, m.submit_started_at, m.accepted_at,
                  m.latency_ms AS m_latency_ms
             FROM execution_intents i
             LEFT JOIN paper_decisions d ON d.decision_id = i.decision_id
             LEFT JOIN paper_orders p ON p.decision_id = i.decision_id AND p.role = 'ENTRY'
             LEFT JOIN LATERAL (
                 SELECT sum(f.qty * f.price) / nullif(sum(f.qty), 0) AS avg_px,
                        sum(f.fee_usd) AS fees
                   FROM paper_fills f WHERE f.order_id = p.order_id) pf ON true
             LEFT JOIN execmirror_orders m ON m.execution_intent_id = i.intent_id
            ORDER BY i.created_at DESC LIMIT $1""", max(1, min(int(limit), 200)))]
    out, legs = [], {name: [] for name, _, _ in LATENCY_LEGS}
    mg = await _management(conn, [{"group_id": r["group_id"], "d_id": r["decision_id"],
                                   "mirror_id": r.get("mirror_id")} for r in rows])
    for r in rows:
        tl = _js(r["timeline"]) or {}
        lat = {name: _ms(_tl_s(tl, a), _tl_s(tl, b)) for name, a, b in LATENCY_LEGS}
        for k, v in lat.items():
            legs[k].append(v)
        pdx = _js(r.get("policy_decision")) or {}
        ev = _js(r["evidence"]) or {}
        facts = ev.get("admission_facts") or {}
        prob = facts.get("probability") or {}
        lel = _js(r.get("live_eligibility")) or {}
        adm = dict(lel.get("admission") or {})
        if adm and lel.get("strategy_approved") is False:
            # the evidence may pass, but a paper-only strategy is never admitted
            adm["verdict"] = "NOT_ADMISSIBLE"
            adm["refusals"] = ["STRATEGY_NOT_LIVE_ELIGIBLE"] + list(adm.get("refusals") or [])
        g = r["group_id"]
        out.append({
            "execution_intent_id": r["intent_id"],
            "decision": {"decision_id": r["decision_id"], "strategy": r["strategy"],
                         "policy_version": r["policy_version"],
                         "decided_at": _iso(r["decided_at"]), "market": r["us_market_slug"],
                         "intent": r["order_intent"],
                         "probability": _f(r.get("p_pinnacle") if r.get("p_pinnacle")
                                           is not None else prob.get("p")),
                         "probability_authority": ((ev.get("probability_authority") or {}).get(
                             "basis") or prob.get("authority_basis")),
                         "gross_edge_pp": _f(pdx.get("gross_edge_pp")),
                         "net_expected_profit_usd": _f(pdx.get("net_expected_profit_usd")),
                         "probability_evidence": (ev.get("probability_authority") or {}).get(
                             "evidence") or prob.get("evidence"),
                         "probability_age_s": _f(prob.get("age_s")),
                         "probability_age_limit_s": _f(prob.get("limit_s")),
                         "probability_fresh": (None if not prob else
                                               prob.get("qualified") is True),
                         "settlement_admission": _settlement_admission(adm, facts),
                         "book_currency_admission": _book_admission(adm, facts)},
            "simulated": {"label": "SIMULATED",
                          "target_qty": _f(r["paper_target_qty"]),
                          "paper_order_id": r.get("p_order_id"),
                          "state": r.get("p_state"),
                          "filled_qty": _f(r.get("p_filled")),
                          "avg_fill_price": _f(r.get("pf_avg_px")),
                          "fees_usd": _f(r.get("pf_fees")),
                          "why_unavailable": (None if r.get("p_order_id") else
                                              "no paper order written (yet, or refused)")},
            "actual": {"label": "ACTUAL", "venue": LIVE_VENUE,
                       "account_fingerprint_prefix": (ctl.get("account_fingerprint") or "")[:8] or None,
                       "live_eligible": r["live_eligible"],
                       "admission": {
                           "status": adm.get("verdict") or "UNAVAILABLE",
                           "version": adm.get("version"),
                           "refusals": adm.get("refusals"),
                           "facts_digest": adm.get("digest"),
                           "why_unavailable": (None if adm else
                                               "intent written before admission was "
                                               "recorded (pre-200)")},
                       "state": r["actual_state"], "refusal": r["actual_refusal"],
                       "target_raw_qty": _f(r["live_raw_qty"]),
                       "rounded_qty": r["live_qty"],
                       "rounding_delta": _f(r["rounding_delta"]),
                       "submitted_at": _iso(r.get("submit_started_at")),
                       "venue_order_id": r.get("venue_order_id"),
                       "acknowledged_at": _iso(r.get("accepted_at")),
                       "venue_state": r.get("venue_state"),
                       "filled_qty": _f(r.get("m_cum")) if r.get("mirror_id") else None,
                       "avg_fill_price": _f(r.get("m_avg_px")),
                       "fees_usd": _f(r.get("m_fees")) if r.get("mirror_id") else None,
                       "submit_latency_ms": r.get("m_latency_ms")},
            "latency_ms": lat,
            "pnl": _decision_pnl(mg, g),
            "management": _decision_management(mg, g),
            "audit": _decision_audit(mg, g, r)})
    stats = {name: {"n": len([x for x in xs if x is not None]),
                    "p50": _pct(xs, .50), "p95": _pct(xs, .95), "p99": _pct(xs, .99)}
             for name, xs in legs.items()}
    return {"status": "OK", "basis": (
        "ONE qualified decision -> ONE execution intent -> PAPER (simulated) and "
        "ACTUAL (Polymarket retail) as siblings; the actual order never waits "
        "for the paper order"), "latency_ms": stats, "rows": out}


LAUNCH_STATES = ("STOPPED", "DISABLED", "ACTIVE")


async def launch_state(conn, ctl: dict | None = None) -> dict:
    """LAUNCH CONTROL: everything that decides whether an ACTUAL order can be
    sent, read from the serving process and the database. Read-only; every
    unavailable item is null with its reason."""
    import os
    from . import actual_admission as AA
    ctl = ctl if ctl is not None else dict(
        await conn.fetchrow("SELECT * FROM execmirror_control WHERE id = 1") or {})
    out: dict = {"serving_build": os.environ.get("RENDER_GIT_COMMIT"),
                 "serving_build_why_unavailable": (
                     None if os.environ.get("RENDER_GIT_COMMIT")
                     else "RENDER_GIT_COMMIT is not set in this process")}
    lane = ("DISABLED" if not ctl.get("enabled") else
            "STOPPED" if ctl.get("stopped") else "ACTIVE")
    out["actual_lane"] = {
        "state": lane, "enabled": bool(ctl.get("enabled")),
        "stopped": bool(ctl.get("stopped")),
        "scale": _f(ctl.get("scale")), "max_order_usd": _f(ctl.get("max_order_usd")),
        "account_fingerprint_prefix": (ctl.get("account_fingerprint") or "")[:12] or None,
        "control_revision": ctl.get("revision"),
        "activation": ("owner action: execmirror-enable (resets stopped) only after "
                       "a fresh read-only retail reconciliation")}
    approved = await approved_live_book_rules(conn)
    out["book_currency"] = {
        "approved_live_rules": sorted(approved["rules"]),
        "source": approved["source"],
        "artifacts": approved.get("artifacts"),
        "admits_actual": bool(approved["rules"]),
        "why": (None if approved["rules"] else
                "no live book-currency rule is approved: every actual order is "
                "refused ADMISSION_BOOK_CURRENCY_NOT_LIVE_ADMISSIBLE")}
    try:
        from .agents import xavier_small_live_policy as XP
        out["xavier_management_policy"] = await XP.load_view(conn)
    except Exception as exc:                                    # noqa: BLE001
        out["xavier_management_policy"] = {"status": None,
                                           "why": "unreadable: %s" % type(exc).__name__}
    try:
        from . import market_data_identity as MDI
        inv = MDI.inventory()
        out["market_data"] = {"verdict": inv.get("verdict"),
                              "usable_for_market_data": inv.get("usable_for_market_data"),
                              "institutional_credential": inv.get(
                                  "institutional_market_data_credential")}
    except Exception as exc:                                    # noqa: BLE001
        out["market_data"] = {"verdict": None, "why": "unreadable: %s" % type(exc).__name__}
    try:
        from . import institutional_stream as IS
        d = IS.digest()
        out["market_data"]["institutional_stream"] = {
            "state": (d.get("start") or {}).get("state"),
            "why": (d.get("start") or {}).get("why")}
    except Exception as exc:                                    # noqa: BLE001
        out["market_data"]["institutional_stream"] = {
            "state": None, "why": "unreadable: %s" % type(exc).__name__}
    blockers = []
    if await conn.fetchval("SELECT to_regclass('execution_intents') IS NOT NULL"):
        blockers = [{"actual_state": r["actual_state"], "refusal": r["actual_refusal"],
                     "n": r["n"], "last_at": _iso(r["last_at"])}
                    for r in await conn.fetch(
                        """SELECT actual_state, actual_refusal, count(*) AS n,
                                  max(created_at) AS last_at
                             FROM execution_intents
                            WHERE created_at > now() - interval '24 hours'
                            GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12""")]
    out["intents_last_24h"] = blockers
    ready = (lane == "ACTIVE" and bool(approved["rules"]))
    out["actual_orders_possible_now"] = ready
    out["why_not"] = [w for w in (
        "the actual lane is %s" % lane if lane != "ACTIVE" else None,
        "no approved live book-currency rule" if not approved["rules"] else None)
        if w]
    return out


async def approved_live_book_rules(conn) -> dict:
    """The live book-currency rules admission accepts -- the SAME reader the
    actual lane uses (`live_rule_artifacts.approved_live_book_rules`: the code
    constant plus rules an owner approved whose stored hash matches the
    code's) -- and each artifact's state. Fail closed to the constant."""
    from . import actual_admission as AA
    try:
        from . import live_rule_artifacts as LRA
        rules = set(await LRA.approved_live_book_rules(conn))
        try:
            arts = await LRA.describe(conn)
        except Exception as exc:                                # noqa: BLE001
            arts = {"why": "unreadable: %s" % type(exc).__name__}
        return {"rules": rules, "artifacts": arts,
                "source": "CODE_CONSTANT_AND_OWNER_APPROVED_ARTIFACTS"}
    except Exception as exc:                                    # noqa: BLE001
        return {"rules": set(AA.APPROVED_LIVE_BOOK_RULES),
                "source": "CODE_CONSTANT (artifact read failed: %s)" % type(exc).__name__}


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
                      d.p_blended AS d_p_blended, d.p_internal AS d_p_internal,
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
    now = dt.datetime.now(dt.timezone.utc)
    out_rows = []
    for r in rows:
        paper = _paper_section(r, mg["groups_pnl"].get(r.get("group_id")))
        actual = _actual_section(r, scale, ctl, mg["groups_live"].get(r.get("group_id")))
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
            "management": _management_section(r, mg, now),
            "chain": _chain(r, paper, actual, mg)})
    counts = await _counts(conn)
    return {
        "title": "Legacy Mirror Validation · Paper vs Actual",
        "basis": ("ONE qualified decision -> PAPER and ACTUAL, as siblings. PAPER is "
                  "SIMULATED: the paper order and its simulated fills. ACTUAL is the "
                  "live venue: the separate retail account's order at the decision's "
                  "target quantity / %s (nearest whole contract), submitted without "
                  "waiting for the paper order, with fills read only from the venue's "
                  "own order records. The two are never summed. A missing figure is "
                  "null (shown as 'unavailable'), never zero." % int(_d(scale))),
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
        "decisions": await decisions(conn),
        "launch": await launch_state(conn, ctl),
        "rows": out_rows,
    }
