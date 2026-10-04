"""THE COUNTERFACTUALS OF ONE REPLAYED DECISION (pure; no I/O).

Per decision, from its point-in-time components and the outcome read at the
horizon (reconstruct.py):

  CURRENT_ACTION               what the historical PAPER chain did: the
                               realized P&L of its position (V1 identity),
                               0 for a refusal or an entry that never filled
  R30_CANONICAL_ACTION         the replayed canonical intent: the same order
                               when it reproduces the historical one; NO new
                               INVESTMENT exposure when no canonical intent
                               can be built (the R30 invariant
                               NO_CANONICAL_DECISION_INTENT =>
                               NO_NEW_INVESTMENT_EXPOSURE)
  ROI_ONLY_ALLOCATION          the realized result per filled dollar scaled
  CAPITAL_HOUR_ALLOCATION      to the benchmark allocation (capacity- and
  ALLIE_ALLOCATION_SHADOW      rail-clamped by attribution_v2.clamp_to_rails),
                               LINEAR_SCALING_WITHIN_CAPACITY; for a refused
                               qualified opportunity the HYPOTHETICAL
                               decision-time result scaled the same way
  NO_TRADE                     0 P&L, 0 capital-hours
  HOLD_TO_SETTLEMENT           the entry fills held to the contract's own
                               settlement (sales and hedges removed)
  ACTUAL_XAVIER_MANAGEMENT     the realized result (Xavier's actions as taken)
  CANONICAL_XAVIER_MANAGEMENT  canonical_intent.management_action on each
                               review's point-in-time inputs (its recorded
                               selection, evidence state, protection, standing
                               orders, candidates, open quantity); equal to the
                               actual result when every review's canonical
                               action matches the action taken, else the hold
                               result plus each canonical sale priced at the
                               review's own book-walk price (labelled), or
                               UNAVAILABLE with the reason

Every value is measured with its basis or None with its reason.
"""
from __future__ import annotations

from .. import canonical_intent as CI
from ..intel import attribution_v2 as V2
from ..intel import common as IC
from ..lost_opportunity import classify as LC
from ..profitability import economics as EC

num = IC.num
jl = IC.jload
FRESH = "FRESH_CURRENT_PROBABILITY"
COUNTERFACTUALS = ("CURRENT_ACTION", "R30_CANONICAL_ACTION",
                   "ROI_ONLY_ALLOCATION", "CAPITAL_HOUR_ALLOCATION",
                   "ALLIE_ALLOCATION_SHADOW", "NO_TRADE",
                   "HOLD_TO_SETTLEMENT", "ACTUAL_XAVIER_MANAGEMENT",
                   "CANONICAL_XAVIER_MANAGEMENT")
SCALING = "LINEAR_SCALING_OF_THE_REALIZED_RESULT_PER_FILLED_DOLLAR_WITHIN_" \
          "CAPACITY"
HYPO = "HYPOTHETICAL"


def cf(pnl, ch=None, *, basis=None, why=None, **extra) -> dict:
    out = {"pnl_usd": None if pnl is None else round(float(pnl), 9),
           "capital_hours": None if ch is None else round(float(ch), 9),
           "basis": basis, "why": None if pnl is not None else (
               why or "NOT_MEASURED")}
    if pnl is not None and ch:
        out["profit_per_capital_hour"] = round(float(pnl) / float(ch), 12)
    out.update(extra)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE POSITION FROM ITS RECORDS
# ═════════════════════════════════════════════════════════════════════

def position_of(rec: dict) -> dict:
    """The decision's paper position from the outcome records: entry fills,
    sales, hedge legs, payoff, plan quantity, terminal state."""
    dec, out = rec["decision"], rec["outcome"]
    slug, side = dec.get("us_market_slug"), str(dec.get("holding_side"))
    fills = out.get("fills") or []
    orders = out.get("orders") or []
    entry = [f for f in fills if f.get("role") == "ENTRY"
             and str(f.get("direction")) == "BUY"]
    if entry:
        slug, side = entry[0]["us_market_slug"], str(entry[0]["holding_side"])
    sells = [f for f in fills if str(f.get("direction")) == "SELL"
             and f.get("us_market_slug") == slug
             and str(f.get("holding_side")) == side]
    setts = {(s["group_id"], s["us_market_slug"], str(s["holding_side"])): s
             for s in sorted(out.get("settlements") or [],
                             key=lambda s: (s.get("version") or 0))}
    gid = (out.get("group_ids") or [None])[0]
    legs: dict = {}
    for f in fills:
        if f.get("role") == "HEDGE" and str(f.get("direction")) == "BUY":
            k = (f["us_market_slug"], str(f["holding_side"]))
            lg = legs.setdefault(k, {"qty": 0.0, "cost_usd": 0.0})
            lg["qty"] += num(f["qty"]) or 0.0
            lg["cost_usd"] += (num(f.get("gross_usd")) or 0.0) + (
                num(f.get("fee_usd")) or 0.0)
    hedges = []
    for (hs, hside), lg in legs.items():
        st = setts.get((gid, hs, hside))
        lg["payoff_per_contract"] = (num(st["payout_per_contract"])
                                     if st else None)
        hedges.append(lg)
    own = setts.get((gid, slug, side))
    contract = rec.get("contract_settlement")
    payoff, outcome, pbasis = None, None, None
    if own is not None:
        payoff, outcome, pbasis = (num(own["payout_per_contract"]),
                                   own.get("outcome"), "PAPER_SETTLEMENT")
    elif contract is not None:
        ppc, b = LC.side_payout({"holding_side": side}, contract)
        if ppc is not None:
            payoff, outcome, pbasis = ppc, contract.get("outcome"), (
                "CONTRACT_SETTLEMENT_" + b)
    if orders:
        plan = sum(num(o.get("qty")) or 0.0 for o in orders)
        plan_basis = "ENTRY_ORDER_QTY (the legacy sizing actually ordered)"
        terminal = all(o.get("state_at_clock") not in (None,
                                                       "OPEN_AT_THE_CLOCK")
                       for o in orders)
    elif dec.get("verdict") == "ENTER" and out.get("refusal"):
        plan, terminal = 0.0, True
        plan_basis = "ORDER_REFUSED_BY_PAPER_RISK:%s" % (
            out["refusal"].get("code"))
    elif dec.get("verdict") == "ENTER":
        plan, terminal = None, None
        plan_basis = "ENTER_WITHOUT_A_RECORDED_ORDER_OR_REFUSAL"
    else:
        plan, terminal, plan_basis = 0.0, True, "REFUSED_BY_THE_POLICY"
    q = sum(num(f["qty"]) or 0.0 for f in entry)
    v = (sum((num(f["qty"]) or 0.0) * (num(f["price"]) or 0.0)
             for f in entry) / q) if q > 0 else None
    fees = sum(num(f.get("fee_usd")) or 0.0 for f in entry)
    return {"group_id": gid, "slug": slug, "side": side,
            "entry": [{"qty": f["qty"], "price": f["price"],
                       "fee_usd": f.get("fee_usd"), "t": f["filled_at"]}
                      for f in entry],
            "sells": [{"qty": f["qty"], "price": f["price"],
                       "fee_usd": f.get("fee_usd"), "t": f["filled_at"]}
                      for f in sells],
            "hedges": hedges, "payoff": payoff, "outcome": outcome,
            "payoff_basis": pbasis, "own_settlement": own,
            "plan_qty": plan, "plan_basis": plan_basis,
            "entry_terminal": terminal, "q": q, "v": v, "fees": fees}


def economics(rec: dict, pos: dict) -> dict:
    """profitability.economics.compute_position on the position's events
    (capital path, capital-hours, release) and the HOLD_TO_SETTLEMENT
    counterfactual's."""
    dec = rec["decision"]
    orders = rec["outcome"].get("orders") or []
    events = ([dict(kind="BUY", t=e["t"], qty=e["qty"], price=e["price"],
                    fee_usd=e.get("fee_usd")) for e in pos["entry"]]
              + [dict(kind="SELL", t=e["t"], qty=e["qty"], price=e["price"],
                      fee_usd=e.get("fee_usd")) for e in pos["sells"]])
    own = pos["own_settlement"]
    res = None
    if orders:
        o = min(orders, key=lambda o: o["created_at"])
        if num(o.get("reserved_usd")):
            res = {"t": o["created_at"], "usd": num(o["reserved_usd"])}
    base = {"book": "PAPER", "position_key": "replay:%s" % (
        pos["group_id"] or dec["decision_id"]), "venue": "POLYMARKET",
        "group_id": pos["group_id"], "us_market_slug": pos["slug"],
        "holding_side": pos["side"], "strategy": dec.get("strategy"),
        "events": events, "reservation": res,
        "probability": rec["ev"]["p"], "probability_basis":
        rec["ev"]["p_basis"], "event_start_at": rec.get("event_start_at"),
        "event_start_basis": rec.get("event_start_basis"),
        "decision_id": dec["decision_id"]}
    pos_rec = dict(base, settlement=(
        {"payout_per_contract": num(own["payout_per_contract"]),
         "t": own["settled_at"], "outcome": own.get("outcome"),
         "basis": "PAPER_SETTLEMENT"} if own else None))
    lag = rec.get("lag_samples") or []
    actual = EC.compute_position(pos_rec, lag_samples=lag)
    hold = None
    contract = rec.get("contract_settlement")
    if pos["entry"] and pos["payoff"] is not None:
        t_set = (own or contract or {}).get("settled_at")
        hold_rec = dict(base, events=[e for e in events if e["kind"] == "BUY"],
                        position_key="cf:HOLD:" + base["position_key"],
                        settlement={"payout_per_contract": pos["payoff"],
                                    "t": t_set, "outcome": pos["outcome"],
                                    "basis": pos["payoff_basis"]})
        if t_set is not None:
            hold = EC.compute_position(hold_rec, lag_samples=lag,
                                       book="COUNTERFACTUAL",
                                       counterfactual_kind="HOLD_TO_"
                                       "SETTLEMENT")
    return {"actual": actual, "hold": hold}


# ═════════════════════════════════════════════════════════════════════
# MANAGEMENT: actual vs canonical, per review
# ═════════════════════════════════════════════════════════════════════

TAKEN_CLASS = {
    "SUBMIT_EXIT": CI.ACT_EXIT, "SUBMIT_REDUCE": CI.ACT_REDUCE,
    "CANCEL_STANDING_BEFORE_EXIT": CI.ACT_CANCEL_FIRST,
    "KEEP_STANDING": CI.ACT_PROTECT, "CANCEL_FOR_REPLACEMENT": CI.ACT_PROTECT,
    "WAIT_FOR_TERMINAL": CI.ACT_PROTECT, "PLACE_STANDING": CI.ACT_PROTECT,
    "PLACEMENT_REFUSED": CI.ACT_PROTECT, "NONE": CI.ACT_NONE}


def canonical_review(review: dict) -> dict:
    """canonical_intent.management_action on the review's recorded,
    point-in-time inputs, beside the action actually taken."""
    sel = jl(review.get("selection")) or {}
    meas = jl(review.get("measure")) or {}
    alts = jl(review.get("alternatives")) or {}
    std = jl(review.get("standing")) or {}
    exp = jl(review.get("exposure")) or {}
    act = jl(review.get("action")) or {}
    taken = str(act.get("taken") or "NONE")
    actual = TAKEN_CLASS.get(taken)
    if actual is None and taken.startswith("SUBMIT_"):
        actual = CI.ACT_EXIT if "EXIT" in taken else CI.ACT_REDUCE
    base = {"review_id": review["review_id"],
            "reviewed_at": review.get("reviewed_at"), "taken": taken,
            "actual_action": actual or "UNCLASSIFIED:%s" % taken}
    state = meas.get("evidence_state")
    if state is None:
        return dict(base, canonical_action=None, match=None,
                    why="REVIEW_RECORDS_NO_EVIDENCE_STATE (a review written "
                        "before the freshness contract): the canonical "
                        "management action is not reconstructable")
    chosen = sel.get("mechanical_selection", sel.get("selected"))
    prot = std.get("protective_price") or {}
    cand = next((c for c in alts.get("candidates") or []
                 if c.get("action") == chosen), None)
    decided = CI.management_action(
        chosen=chosen, fresh=state == FRESH, stale=bool(meas.get("stale")),
        p_missing=meas.get("p") is None,
        protection_ok=bool(prot.get("ok")),
        standing_live=bool(std.get("live_orders")), candidate=cand,
        protective=prot, open_qty=exp.get("open_qty"))
    tl = decided.get("target_limit") or {}
    return dict(base, canonical_action=decided["action"],
                canonical_target_qty=num(decided.get("target_qty")),
                canonical_limit_price=num(tl.get("limit_price")),
                candidate_fee_usd=num((cand or {}).get("fee_usd")),
                evidence_state=state, chosen=chosen,
                match=(actual == decided["action"]), why=None)


def management(rec: dict, pos: dict, realized, hold_pnl) -> dict:
    reviews = [canonical_review(r) for r in rec["outcome"].get("reviews")
               or []]
    unrec = [r for r in reviews if r["canonical_action"] is None]
    diverge = [r for r in reviews if r["match"] is False]
    out = {"reviews": len(reviews), "canonical_unavailable": len(unrec),
           "divergent": len(diverge), "per_review": reviews[:50],
           "recorded_canonical_intents": len(
               rec["outcome"].get("mgmt_intents") or [])}
    if not pos["entry"]:
        out["canonical"] = cf(0.0, 0.0, basis="NO_POSITION_TO_MANAGE")
        return out
    if realized is None:
        out["canonical"] = cf(None, why="ACTUAL_RESULT_NOT_REALIZED_BY_THE_"
                              "HORIZON")
        return out
    if unrec:
        out["canonical"] = cf(None, why="%d_REVIEWS_WITHOUT_A_RECONSTRUCTABLE"
                              "_CANONICAL_ACTION: %s" % (len(unrec),
                                                          unrec[0]["why"]))
        return out
    if not diverge:
        out["canonical"] = cf(realized, basis=(
            "CANONICAL_MATCHED_EVERY_REVIEW (%d): the actual result"
            % len(reviews)))
        return out
    if hold_pnl is None or pos["payoff"] is None:
        out["canonical"] = cf(None, why="HOLD_RESULT_UNAVAILABLE_FOR_THE_"
                              "DIVERGENT_PATH")
        return out
    held = pos["q"]
    pnl = hold_pnl
    for r in reviews:
        if r["canonical_action"] not in (CI.ACT_EXIT, CI.ACT_REDUCE):
            if r["canonical_action"] == CI.ACT_PROTECT and \
                    r["actual_action"] != CI.ACT_PROTECT:
                out["canonical"] = cf(None, why=(
                    "PROTECTIVE_FILL_COUNTERFACTUAL_NOT_PRICEABLE (review %s)"
                    % r["review_id"]))
                return out
            continue
        qty = min(r["canonical_target_qty"] or 0.0, held)
        px, fee = r["canonical_limit_price"], r["candidate_fee_usd"]
        if qty <= 0:
            continue
        if px is None or fee is None:
            out["canonical"] = cf(None, why=(
                "CANONICAL_SALE_NOT_PRICEABLE (review %s: %s)" % (
                    r["review_id"], "no walk price" if px is None
                    else "no recorded fee on the candidate")))
            return out
        pnl += qty * (px - pos["payoff"]) - fee
        held -= qty
    out["canonical"] = cf(pnl, basis=(
        "HOLD_TO_SETTLEMENT + each canonical sale at the review's own "
        "book-walk price (ASSUMED_FILLED_AT_WALK_PRICE)"))
    return out


# ═════════════════════════════════════════════════════════════════════
# LOST OPPORTUNITY
# ═════════════════════════════════════════════════════════════════════

def lost_opportunity(rec: dict, pos: dict) -> dict:
    dec, out = rec["decision"], rec["outcome"]
    if dec.get("verdict") != "ENTER":
        v = rec.get("valuation_row") or {}
        d = dict(dec, source=LC.PAPER_SOURCE, valuation={
            k: v.get(k) for k in ("venue", "payout_event",
                                  "settlement_comparison", "sport_family",
                                  "event_key", "buy_intent")}
            if v else None)
        got = LC.classify(d)
        hyp = _hyp(d, rec["ev"], rec.get("contract_settlement"))
        return {"classification": got["classification"],
                "attribution": got.get("attribution"),
                "reason": got.get("reason"), "defect": got.get("defect"),
                "classifier_version": LC.VERSION,
                "hypothetical_pnl": hyp,
                "note": "the class reads the decision-time record only; the "
                        "settlement prices the HYPOTHETICAL beside it"}
    if not out.get("orders"):
        ref = out.get("refusal")
        if ref and ref.get("capital"):
            return {"classification": "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION",
                    "refusal": ref.get("code")}
        if ref:
            return {"classification": "ORDER_REFUSED_BY_PAPER_RISK",
                    "refusal": ref.get("code")}
        return {"classification": "ENTER_WITHOUT_RECORDED_ORDER_OR_REFUSAL"}
    if pos["q"] <= 0:
        return {"classification": ("ENTERED_NOT_FILLED" if
                                   pos["entry_terminal"] else
                                   "ENTRY_PENDING_AT_THE_HORIZON")}
    return {"classification": "TAKEN"}


def hypothetical(rec: dict, lo: dict) -> dict:
    """The decision-time HYPOTHETICAL of an opportunity that held no
    position (a refusal, a risk-refused or unfilled entry): its recorded
    quantity at its recorded executable price against the contract's own
    settlement (lost_opportunity.classify.hypothetical_pnl)."""
    if lo.get("hypothetical_pnl") is not None:
        return lo["hypothetical_pnl"]
    return _hyp(dict(rec["decision"], source=LC.PAPER_SOURCE), rec["ev"],
                rec.get("contract_settlement"))


def _hyp(d: dict, ev: dict, st) -> dict:
    """The classifier's own HYPOTHETICAL when its economics read the record
    (Derek's policy decision / economics headline); else the same formula on
    the decision's acquisition economics (qty x payout - cost - fees)."""
    if not st:
        return {"label": HYPO, "value": None,
                "why": "MARKET_NOT_SETTLED_BY_THE_HORIZON"}
    le = LC.decision_economics(d)
    if le.get("computable"):
        return LC.hypothetical_pnl(d, le, st)
    ppc, basis = LC.side_payout(d, st)
    out = {"label": HYPO, "value": None, "why": None,
           "payout_per_contract": ppc, "payout_basis": basis,
           "economics_basis": ev.get("net_basis")}
    if ppc is None:
        out["why"] = basis
    elif ev.get("qty") is None or ev.get("cost") is None:
        out["why"] = "NO_DECISION_TIME_EXECUTABLE_PRICE_OR_QTY"
    elif ev.get("fees") is None:
        out["why"] = "FEES_NOT_ESTABLISHED_AT_DECISION"
    else:
        out["value"] = round(ev["qty"] * ppc - ev["cost"] - abs(ev["fees"]),
                             6)
    return out


# ═════════════════════════════════════════════════════════════════════
# ALL COUNTERFACTUALS OF ONE DECISION
# ═════════════════════════════════════════════════════════════════════

def evaluate(rec: dict) -> dict:
    dec = rec["decision"]
    pos = position_of(rec)
    eco = economics(rec, pos)
    act = eco["actual"]
    ch_actual = num(act.get("capital_hours")) if pos["entry"] else 0.0
    # the realized result (V1's definition, reconciled to cash)
    v1 = V2.A.attribute(
        subject_id=pos["group_id"] or dec["decision_id"], book="PAPER",
        p=rec["ev"]["p"], p_basis=rec["ev"]["p_basis"], d=rec["ev"]["d"],
        d_basis=rec["ev"]["d_basis"], entry_fills=pos["entry"],
        sell_fills=pos["sells"], hedge_legs=pos["hedges"],
        payoff=pos["payoff"], settlement_outcome=pos["outcome"],
        payoff_basis=pos["payoff_basis"])
    lo = lost_opportunity(rec, pos)
    out: dict = {}
    if dec.get("verdict") != "ENTER":
        realized, why = 0.0, None
        out["CURRENT_ACTION"] = cf(0.0, 0.0, action="REFUSE",
                                   basis="REFUSED: no position")
    elif not pos["entry"]:
        if pos["entry_terminal"]:
            realized, why = 0.0, None
            out["CURRENT_ACTION"] = cf(0.0, 0.0, action="ENTER",
                                       basis="NO_FILL: " + lo[
                                           "classification"])
        else:
            realized, why = None, lo["classification"]
            out["CURRENT_ACTION"] = cf(None, why=why, action="ENTER")
    else:
        realized = v1.get("realized_pnl_usd")
        why = (v1.get("unmeasured") or {}).get("realized_pnl_usd")
        out["CURRENT_ACTION"] = cf(realized, ch_actual if realized is not None
                                   else None, action="ENTER",
                                   basis="REALIZED (V1, reconciles to cash: "
                                   "%s)" % v1.get("reconciles"), why=why)
    # R30 canonical action
    ci = rec["components"]["canonical_intent"]
    sleeve = rec["sleeve"]
    if dec.get("verdict") != "ENTER":
        out["R30_CANONICAL_ACTION"] = cf(0.0, 0.0, action="NO_INTENT_REFUSE",
                                         basis="same as the policy: refuse")
    elif ci.get("status") == "BUILT":
        out["R30_CANONICAL_ACTION"] = dict(
            out["CURRENT_ACTION"], action="ENTER_WITH_CANONICAL_INTENT",
            basis="THE_SAME_ORDER: the canonical intent carries the "
                  "historical side, quantity and limit (%s)"
                  % rec["intent_parity"].get("state"))
    elif sleeve == "INVESTMENT":
        out["R30_CANONICAL_ACTION"] = cf(
            0.0, 0.0, action="NO_CANONICAL_DECISION_INTENT",
            basis="NO_CANONICAL_DECISION_INTENT => NO_NEW_INVESTMENT_"
                  "EXPOSURE: %s" % ci.get("why"))
    else:
        out["R30_CANONICAL_ACTION"] = dict(
            out["CURRENT_ACTION"], action="NO_CANONICAL_DECISION_INTENT",
            basis="non-INVESTMENT sleeve: the fail-closed invariant gates "
                  "INVESTMENT only (%s)" % ci.get("why"))
    # allocation counterfactuals (rail-clamped exactly as attribution_v2)
    rails = rec["rails"]
    # the filled position's capital, fees included (the allocations are
    # capital required = cost + fees, as R30 computes them)
    filled_usd = (pos["q"] * pos["v"] + pos["fees"]) if pos["q"] > 0 else 0.0
    hyp = hypothetical(rec, lo) if not pos["entry"] else {}
    for name, key in (("ROI_ONLY_ALLOCATION", "ROI_ONLY_RANKING"),
                      ("CAPITAL_HOUR_ALLOCATION", "CAPITAL_HOUR_RANKING"),
                      ("ALLIE_ALLOCATION_SHADOW", "ALLIE")):
        a = rec["allocations"].get(key) or {}
        k, used = V2.clamp_to_rails(num(a.get("usd")), rails)
        if k is None:
            out[name] = cf(None, why=a.get("why") or "ALLOCATION_UNMEASURED")
        elif k <= 0:
            out[name] = cf(0.0, 0.0, k_usd=0.0, rails_applied=used,
                           basis="ALLOCATES_NOTHING: %s" % (a.get("basis")))
        elif not pos["entry"]:
            cost = rec["ev"]["capital_required"]
            if hyp.get("value") is None or not cost:
                out[name] = cf(None, k_usd=k, why="HYPOTHETICAL_UNPRICED: %s"
                               % (hyp.get("why") or "no decision-time cost"))
            else:
                out[name] = cf(hyp["value"] * k / cost, k_usd=k,
                               rails_applied=used, label=HYPO,
                               basis="HYPOTHETICAL decision-time result x "
                                     "K / cost (" + SCALING + ")")
        elif realized is None:
            out[name] = cf(None, k_usd=k, why=why or "NOT_REALIZED")
        elif filled_usd <= 0:
            out[name] = cf(None, k_usd=k, why="NO_FILLED_POSITION_TO_SCALE")
        else:
            f = k / filled_usd
            out[name] = cf(realized * f, None if ch_actual is None
                           else ch_actual * f, k_usd=k, rails_applied=used,
                           basis=SCALING)
    out["NO_TRADE"] = cf(0.0, 0.0, basis="no position, no capital")
    hold = eco["hold"]
    if not pos["entry"]:
        out["HOLD_TO_SETTLEMENT"] = cf(0.0, 0.0, basis="NO_POSITION")
    elif pos["payoff"] is None:
        out["HOLD_TO_SETTLEMENT"] = cf(None, why="CONTRACT_NOT_SETTLED_BY_"
                                       "THE_HORIZON")
    else:
        hp = pos["q"] * (pos["payoff"] - pos["v"]) - pos["fees"]
        out["HOLD_TO_SETTLEMENT"] = cf(
            hp, None if hold is None else num(hold.get("capital_hours")),
            basis="entry fills held to the contract settlement (%s)"
            % pos["payoff_basis"])
    out["ACTUAL_XAVIER_MANAGEMENT"] = dict(
        out["CURRENT_ACTION"], basis="the realized result: Xavier's "
        "actions as taken")
    mg = management(rec, pos, realized if pos["entry"] else None,
                    out["HOLD_TO_SETTLEMENT"]["pnl_usd"])
    out["CANONICAL_XAVIER_MANAGEMENT"] = mg.pop("canonical")
    return {"position": pos, "economics": eco, "v1": v1,
            "counterfactuals": out, "management": mg,
            "lost_opportunity": lo, "realized_pnl_usd": realized,
            "realized_why": why, "capital_hours": ch_actual}
