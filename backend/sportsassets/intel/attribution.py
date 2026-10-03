"""E · ALPHA VS EXECUTION ATTRIBUTION (SHADOW). Where a position's money
came from: the model, the execution, the management, or the settlement.

ONE ADDITIVE IDENTITY, PER POSITION, RECONCILED TO CASH. With q the entry
quantity, p the decision's probability, d the price the decision planned to
pay (its acquisition VWAP), v the entry fills' VWAP, f the entry fees and
pi the per-contract payoff of the held contract (settlement, or the
venue-verified outcome when the position was sold out before settling):

  model edge         q (p - d)            what the model said was there
  slippage           q (v - d)            paid above (+) / below (-) plan
  fees               f
  execution edge     -(slippage + fees)   what execution added or cost
  executable edge    q (p - v)            the edge at the fill VWAP
  management         sum over SELL fills of (net proceeds - qty * pi)
                     + sum over HEDGE legs of (payout - cost)
                     -- Xavier's actions versus holding to settlement; a
                     position with no action is a MEASURED 0 (held)
  settlement         q (pi - p) when the settlement is EXCEPTIONAL
                     (VOID_REFUND, SETTLED_AT_VENUE_PRICE), else 0
  outcome variance   q (pi - p) when the settlement is ORDINARY (WON/LOST)
                     -- the coin, not anybody's skill

  model + execution + management + settlement + variance
      = q (pi - v) - f + management = realized P&L

`cash_pnl_usd` is the same P&L from the cash legs alone (payouts + sale
proceeds - purchase costs) and `reconciles` says whether the two agree to a
cent. A component that cannot be measured is null with its reason; the
identity is then not claimed.

PAPER positions come from paper_decisions -> paper_orders (ENTRY) ->
paper_fills / paper_settlements. ACTUAL positions come from
execution_intents -> execmirror_orders -> execmirror_fills (venue fills,
LONG-side wire prices converted to cost space by the intent's side); their
payoff is the paper settlement of the SAME contract (same group, market and
side), because it is the same contract's outcome.
"""
from __future__ import annotations

from . import common as C

VERSION = "INTEL_ATTRIBUTION_V1"
ORDINARY = ("WON", "LOST")
EXCEPTIONAL = ("VOID_REFUND", "SETTLED_AT_VENUE_PRICE")
SELL_ROLES = ("EXIT", "REDUCE", "STANDING_PROTECTION")
RECONCILE_TOLERANCE_USD = 0.01


def decision_probability(dec: dict):
    for k, basis in (("p_blended", "P_BLENDED"), ("p_pinnacle", "P_PINNACLE"),
                     ("p_internal", "P_INTERNAL")):
        v = C.num(dec.get(k))
        if v is not None:
            return v, basis
    return None, None


def decision_price(dec: dict):
    """(price per contract in cost space, basis) the decision planned."""
    econ = C.jload(dec.get("economics")) or {}
    acq = econ.get("acquisition") if isinstance(econ, dict) else None
    v = C.num((acq or {}).get("vwap")) if isinstance(acq, dict) else None
    if v is not None:
        return v, "DECISION_PLANNED_ACQUISITION_VWAP"
    lv = (econ.get("levels") or []) if isinstance(econ, dict) else []
    if lv and isinstance(lv[0], dict) and C.num(lv[0].get("price")) is not None:
        return C.num(lv[0]["price"]), "DECISION_BEST_LEVEL_PRICE"
    v = C.num(dec.get("limit_price"))
    if v is not None:
        return v, "DECISION_LIMIT_PRICE"
    return None, None


def payoff_of(settlement: dict | None, valuation: dict | None):
    """(payoff per contract, settlement outcome/class, basis)."""
    if settlement:
        pp = C.num(settlement.get("payout_per_contract"))
        if pp is not None:
            return pp, str(settlement.get("outcome")), "PAPER_SETTLEMENT"
    if valuation and valuation.get("outcome_known") and valuation.get(
            "outcome") in (0, 1) and valuation.get("outcome_basis") in (
            "VENUE_SETTLEMENT_PRICE", "VENUE_REPORTED_OUTCOME"):
        o = int(valuation["outcome"])
        return float(o), ("WON" if o == 1 else "LOST"), (
            "VENUE_VERIFIED_VALUATION_OUTCOME")
    return None, None, None


def attribute(*, subject_id: str, book: str, p, p_basis, d, d_basis,
              entry_fills: list, sell_fills: list, hedge_legs: list,
              payoff, settlement_outcome, payoff_basis, actions: int = 0,
              extra: dict | None = None) -> dict:
    """The pure decomposition. Fills are dicts {qty, price (cost space,
    excl. fee), fee_usd}; hedge legs are {qty, cost_usd (incl. fees),
    payoff_per_contract or None}."""
    out = C.Out(book=book, subject_id=subject_id, version=VERSION,
                label=C.LABEL)
    out.update(extra or {})
    q = sum(C.num(f["qty"]) or 0.0 for f in entry_fills)
    out["entry_qty"] = C.rnd(q)
    out.put("p_decision", C.rnd(p), "DECISION_CARRIES_NO_PROBABILITY")
    out["p_basis"] = p_basis
    out.put("decision_price", C.rnd(d), "DECISION_CARRIES_NO_PLANNED_PRICE")
    out["decision_price_basis"] = d_basis
    if q <= 0:
        why = "NO_ENTRY_FILL"
        for k in ("fill_vwap", "model_edge_pc", "executable_edge_pc",
                  "slippage_pc", "fees_usd", "model_edge_usd",
                  "executable_edge_usd", "slippage_usd", "execution_edge_usd",
                  "management_usd", "settlement_usd", "outcome_variance_usd",
                  "realized_pnl_usd", "cash_pnl_usd"):
            out.put(k, None, why)
        out["reconciles"] = None
        out["settlement_class"] = None
        return out
    v = sum((C.num(f["qty"]) or 0.0) * (C.num(f["price"]) or 0.0)
            for f in entry_fills) / q
    fees = sum(C.num(f.get("fee_usd")) or 0.0 for f in entry_fills)
    out.put("fill_vwap", C.rnd(v))
    out.put("fees_usd", C.rnd(fees))
    out["fee_pc"] = C.rnd(fees / q)
    no_p = "DECISION_CARRIES_NO_PROBABILITY"
    no_d = "DECISION_CARRIES_NO_PLANNED_PRICE"
    out.put("executable_edge_pc", None if p is None else C.rnd(p - v), no_p)
    out.put("executable_edge_usd", None if p is None else C.rnd(q * (p - v)),
            no_p)
    out.put("model_edge_pc", None if (p is None or d is None)
            else C.rnd(p - d), no_p if p is None else no_d)
    out.put("model_edge_usd", None if (p is None or d is None)
            else C.rnd(q * (p - d)), no_p if p is None else no_d)
    out.put("slippage_pc", None if d is None else C.rnd(v - d), no_d)
    out.put("slippage_usd", None if d is None else C.rnd(q * (v - d)), no_d)
    out.put("execution_edge_usd", None if d is None
            else C.rnd(-(q * (v - d)) - fees), no_d)

    # ── management: Xavier's actions vs holding to settlement ──────────
    out["management_actions"] = int(actions)
    out["sell_fills"] = len(sell_fills)
    out["hedge_legs"] = len(hedge_legs)
    mgmt = 0.0
    mgmt_why = None
    for s in sell_fills:
        if payoff is None:
            mgmt_why = "POSITION_NOT_SETTLED_HOLD_COUNTERFACTUAL_UNKNOWN"
            break
        sq = C.num(s["qty"]) or 0.0
        mgmt += sq * (C.num(s["price"]) or 0.0) - (C.num(s.get("fee_usd"))
                                                   or 0.0) - sq * payoff
    for h in hedge_legs:
        hp = C.num(h.get("payoff_per_contract"))
        if hp is None:
            mgmt_why = mgmt_why or "HEDGE_LEG_NOT_SETTLED"
            continue
        mgmt += (C.num(h["qty"]) or 0.0) * hp - (C.num(h["cost_usd"]) or 0.0)
    if mgmt_why:
        out.put("management_usd", None, mgmt_why)
    else:
        out.put("management_usd", C.rnd(mgmt))
        out["management_basis"] = ("NO_MANAGEMENT_ACTION_HELD_TO_SETTLEMENT"
                                   if not sell_fills and not hedge_legs
                                   else "ACTIONS_VS_HOLD_TO_SETTLEMENT")

    # ── settlement vs ordinary, and the coin ──────────────────────────
    out["settlement_outcome"] = settlement_outcome
    out["payoff_per_contract"] = C.rnd(payoff)
    out["payoff_basis"] = payoff_basis
    if payoff is None:
        out["settlement_class"] = None
        out.put("settlement_usd", None, "POSITION_NOT_SETTLED")
        out.put("outcome_variance_usd", None, "POSITION_NOT_SETTLED")
    elif p is None:
        out["settlement_class"] = ("EXCEPTIONAL" if settlement_outcome
                                   in EXCEPTIONAL else "ORDINARY")
        out.put("settlement_usd", None, no_p)
        out.put("outcome_variance_usd", None, no_p)
    elif settlement_outcome in EXCEPTIONAL:
        out["settlement_class"] = "EXCEPTIONAL"
        out.put("settlement_usd", C.rnd(q * (payoff - p)))
        out.put("outcome_variance_usd", 0.0)
    else:
        out["settlement_class"] = "ORDINARY"
        out.put("settlement_usd", 0.0)
        out.put("outcome_variance_usd", C.rnd(q * (payoff - p)))

    # ── realized, two ways ────────────────────────────────────────────
    if payoff is None or out["management_usd"] is None:
        out.put("realized_pnl_usd", None,
                out["unmeasured"].get("management_usd")
                or "POSITION_NOT_SETTLED")
        out.put("cash_pnl_usd", None, "POSITION_NOT_SETTLED")
        out["reconciles"] = None
        return out
    realized = q * (payoff - v) - fees + out["management_usd"]
    out.put("realized_pnl_usd", C.rnd(realized))
    sold = sum(C.num(s["qty"]) or 0.0 for s in sell_fills)
    cash = ((q - sold) * payoff
            + sum((C.num(s["qty"]) or 0.0) * (C.num(s["price"]) or 0.0)
                  - (C.num(s.get("fee_usd")) or 0.0) for s in sell_fills)
            - (q * v + fees)
            + sum((C.num(h["qty"]) or 0.0) * C.num(h["payoff_per_contract"])
                  - (C.num(h["cost_usd"]) or 0.0) for h in hedge_legs))
    out.put("cash_pnl_usd", C.rnd(cash))
    parts = [out.get(k) for k in ("model_edge_usd", "execution_edge_usd",
                                  "management_usd", "settlement_usd",
                                  "outcome_variance_usd")]
    if any(x is None for x in parts):
        out["reconciles"] = abs(realized - cash) <= RECONCILE_TOLERANCE_USD
        out["identity_claimed"] = False
    else:
        out["identity_claimed"] = True
        out["reconciles"] = (abs(sum(parts) - realized)
                             <= RECONCILE_TOLERANCE_USD
                             and abs(realized - cash)
                             <= RECONCILE_TOLERANCE_USD)
    return out


def summarize(rows: list) -> dict:
    """Totals per book over measured values only, with counts of nulls."""
    out = {}
    keys = ("model_edge_usd", "execution_edge_usd", "slippage_usd",
            "fees_usd", "management_usd", "settlement_usd",
            "outcome_variance_usd", "realized_pnl_usd")
    for book in C.BOOKS:
        mine = [r for r in rows if r["book"] == book]
        s = {"positions": len(mine),
             "reconciling": sum(1 for r in mine if r.get("reconciles")),
             "not_reconciling": sum(1 for r in mine
                                    if r.get("reconciles") is False)}
        for k in keys:
            vals = [r[k] for r in mine if r.get(k) is not None]
            s[k] = C.rnd(sum(vals)) if vals else None
            s[k + "_measured_n"] = len(vals)
            if not vals:
                s.setdefault("unmeasured", {})[k] = (
                    "NO_POSITION_WITH_A_MEASURED_VALUE")
        out[book] = s
    out["summed_across_books"] = False
    return out


# ═════════════════════════════════════════════════════════════════════
# THE READ
# ═════════════════════════════════════════════════════════════════════

PAPER_DECISIONS_SQL = """
    SELECT decision_id, decided_at, valuation_id, us_market_slug,
           holding_side, p_pinnacle, p_blended, p_internal, limit_price,
           economics, strategy
      FROM paper_decisions
     WHERE verdict = 'ENTER' AND decided_at >= to_timestamp($1)
       AND ($2::text IS NULL OR account_id = $2)
     ORDER BY decided_at DESC LIMIT $3
"""


async def load_paper(conn, *, now, days=60.0, account_id=C.PAPER_ACCOUNT,
                     limit=5000) -> list:
    from . import reads as R

    decs = [dict(r) for r in await conn.fetch(
        PAPER_DECISIONS_SQL, float(now) - days * 86400.0, account_id,
        int(limit))]
    groups = await R.entry_groups(conn, [d["decision_id"] for d in decs])
    gids = sorted(set(groups.values()))
    fills = [dict(r) for r in await conn.fetch(
        "SELECT group_id, role, direction, holding_side, us_market_slug, "
        "       qty, price, fee_usd, gross_usd FROM paper_fills "
        " WHERE group_id = ANY($1::text[])", gids)] if gids else []
    setts = await R.latest_settlements(conn, group_ids=gids)
    sidx = {(s["group_id"], s["us_market_slug"], str(s["holding_side"])): s
            for s in setts.values()}
    acts = {r["group_id"]: int(r["n"]) for r in await conn.fetch(
        "SELECT group_id, count(*) AS n FROM paper_xavier_reviews "
        " WHERE group_id = ANY($1::text[]) AND action IS NOT NULL "
        " GROUP BY group_id", gids)} if gids else {}
    vals = await R.valuations_by_id(conn, [d.get("valuation_id")
                                           for d in decs])
    by_group: dict = {}
    for f in fills:
        by_group.setdefault(f["group_id"], []).append(f)
    out = []
    for d in decs:
        g = groups.get(d["decision_id"])
        if not g:
            continue
        gf = by_group.get(g, [])
        entry = [f for f in gf if f["role"] == "ENTRY"
                 and f["direction"] == "BUY"]
        slug = entry[0]["us_market_slug"] if entry else d.get(
            "us_market_slug")
        side = str(entry[0]["holding_side"]) if entry else str(
            d.get("holding_side"))
        sells = [f for f in gf if f["direction"] == "SELL"
                 and f["us_market_slug"] == slug
                 and str(f["holding_side"]) == side]
        legs: dict = {}
        for f in gf:
            if f["role"] == "HEDGE" and f["direction"] == "BUY":
                k = (f["us_market_slug"], str(f["holding_side"]))
                lg = legs.setdefault(k, {"qty": 0.0, "cost_usd": 0.0})
                lg["qty"] += C.num(f["qty"]) or 0.0
                lg["cost_usd"] += (C.num(f["gross_usd"]) or 0.0) + (
                    C.num(f["fee_usd"]) or 0.0)
        hedge_legs = []
        for (hs, hside), lg in legs.items():
            st = sidx.get((g, hs, hside))
            lg["payoff_per_contract"] = (C.num(st["payout_per_contract"])
                                         if st else None)
            hedge_legs.append(lg)
        val = vals.get(int(d["valuation_id"])) if d.get(
            "valuation_id") is not None else None
        payoff, outcome, pbasis = payoff_of(sidx.get((g, slug, side)), val)
        p, p_basis = decision_probability(d)
        dp, d_basis = decision_price(d)
        out.append(attribute(
            subject_id=g, book="PAPER", p=p, p_basis=p_basis, d=dp,
            d_basis=d_basis,
            entry_fills=[{"qty": f["qty"], "price": f["price"],
                          "fee_usd": f["fee_usd"]} for f in entry],
            sell_fills=[{"qty": f["qty"], "price": f["price"],
                         "fee_usd": f["fee_usd"]} for f in sells],
            hedge_legs=hedge_legs, payoff=payoff,
            settlement_outcome=outcome, payoff_basis=pbasis,
            actions=acts.get(g, 0),
            extra={"decision_id": d["decision_id"], "group_id": g,
                   "strategy": d.get("strategy"), "us_market_slug": slug,
                   "holding_side": side,
                   "decided_at": C.epoch(d.get("decided_at"))}))
    return out


async def load_actual(conn, *, now, days=60.0, limit=5000) -> list:
    """ACTUAL (execution mirror) positions attributed against the decision
    each execution intent copied."""
    from . import reads as R

    rows = [dict(r) for r in await conn.fetch(
        "SELECT i.intent_id, i.decision_id, i.valuation_id, i.group_id, "
        "       i.us_market_slug, i.order_intent, i.holding_side, "
        "       i.limit_price, i.wire_price, i.decided_at, i.strategy "
        "  FROM execution_intents i "
        " WHERE i.decided_at >= to_timestamp($1) "
        "   AND i.actual_mirror_id IS NOT NULL "
        " ORDER BY i.decided_at DESC LIMIT $2",
        float(now) - days * 86400.0, int(limit))]
    if not rows:
        return []
    gids = sorted({r["group_id"] for r in rows if r.get("group_id")})
    fills = [dict(r) for r in await conn.fetch(
        "SELECT f.group_id, f.us_market_slug, f.intent, f.qty, f.price, "
        "       f.fee_usd, o.role "
        "  FROM execmirror_fills f JOIN execmirror_orders o "
        "    ON o.mirror_id = f.mirror_id "
        " WHERE f.group_id = ANY($1::text[])", gids)] if gids else []
    decs = {r["decision_id"]: dict(r) for r in await conn.fetch(
        "SELECT decision_id, p_pinnacle, p_blended, p_internal, economics, "
        "       limit_price FROM paper_decisions "
        " WHERE decision_id = ANY($1::text[])",
        [r["decision_id"] for r in rows if r.get("decision_id")])}
    setts = await R.latest_settlements(conn, group_ids=gids)
    sidx = {(s["group_id"], s["us_market_slug"], str(s["holding_side"])): s
            for s in setts.values()}
    vals = await R.valuations_by_id(conn, [r.get("valuation_id")
                                           for r in rows])
    out = []
    seen = set()
    for it in rows:
        g = it.get("group_id")
        if not g or g in seen:
            continue
        seen.add(g)
        side = C.side_of_intent(it.get("order_intent")) if it.get(
            "order_intent") else str(it.get("holding_side") or "LONG")
        slug = it.get("us_market_slug")
        gall = [f for f in fills if f["group_id"] == g]
        gf = [f for f in gall if f["us_market_slug"] == slug]
        entry = [{"qty": f["qty"], "price": C.cost_space(f["price"], side),
                  "fee_usd": f["fee_usd"]} for f in gf
                 if C.is_buy_intent(f["intent"]) and f.get("role") in (
                     "ENTRY", None)]
        sells = [{"qty": f["qty"], "price": C.cost_space(f["price"], side),
                  "fee_usd": f["fee_usd"]} for f in gf
                 if not C.is_buy_intent(f["intent"])]
        legs: dict = {}
        for f in gall:
            if f.get("role") == "HEDGE" and C.is_buy_intent(f["intent"]):
                hside = C.side_of_intent(f["intent"])
                lg = legs.setdefault((f["us_market_slug"], hside),
                                     {"qty": 0.0, "cost_usd": 0.0})
                q_ = C.num(f["qty"]) or 0.0
                lg["qty"] += q_
                lg["cost_usd"] += q_ * (C.cost_space(f["price"], hside)
                                        or 0.0) + (C.num(f["fee_usd"]) or 0.0)
        hedge_legs = []
        for (hs, hside), lg in legs.items():
            st = sidx.get((g, hs, hside))
            lg["payoff_per_contract"] = (C.num(st["payout_per_contract"])
                                         if st else None)
            hedge_legs.append(lg)
        dec = decs.get(it.get("decision_id")) or {}
        p, p_basis = decision_probability(dec)
        dp = C.cost_space(it.get("wire_price"), side)
        d_basis = "EXECUTION_INTENT_DECISION_WIRE"
        if dp is None:
            dp, d_basis = decision_price(dec)
        val = vals.get(int(it["valuation_id"])) if it.get(
            "valuation_id") is not None else None
        payoff, outcome, pbasis = payoff_of(sidx.get((g, slug, side)), val)
        out.append(attribute(
            subject_id=g, book="ACTUAL", p=p, p_basis=p_basis, d=dp,
            d_basis=d_basis, entry_fills=entry, sell_fills=sells,
            hedge_legs=hedge_legs, payoff=payoff, settlement_outcome=outcome,
            payoff_basis=pbasis,
            extra={"decision_id": it.get("decision_id"), "group_id": g,
                   "strategy": it.get("strategy"), "us_market_slug": slug,
                   "holding_side": side,
                   "fill_price_convention":
                       "VENUE_WIRE_PRICE_CONVERTED_BY_INTENT_SIDE",
                   "decided_at": C.epoch(it.get("decided_at"))}))
    return out
