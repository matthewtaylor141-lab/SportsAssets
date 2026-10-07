"""DIGITAL TWIN replay of BETTOR's recorded paper decisions.

Input: research/ps_twin_v1.sql -- every ENTRY / EXIT / REDUCE paper order with
the decision behind it (decided_at), its recorded paper fills, and every venue
book observation of its market from 180 s before the decision to 60 s after
the order ended; plus (optional) settlements from ps_orders_v1.

Decision logic: RECORDED_PRODUCTION_DECISIONS. Each order is the output of the
actual BETTOR decision logic at decided_at; the twin re-executes that intent
(side, limit, qty, time in force) against the recorded books in strict event
time. Re-running the full engine offline would need every upstream feed at
each instant, which the records do not hold.

Each order replays in its own DigitalTwin so its latencies are its own:
  submit latency = eligible_at - decided_at   (PAPER SIMULATOR, labelled)
  IOC            = cancel 1 ms after activation (after the activation cross)
  GTD            = cancel at the first book event at / after expires_at, with
                   the paper simulator's median cancel latency
No-lookahead is checked, not assumed: at every decision the twin view's book
must have been observed at or before the decision time.

What the twin cannot model from these records is reported: there is no trade
tape, so queue consumption by trades (and thus resting partial fills) is
UNMEASURED; the package's activation cross fills the full remainder at the
displayed touch, so depth-limited partial fills are flagged where the touch
size was smaller than the order.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from common import event_identity, window
from bettor_profit_stack.digital_twin import (DigitalTwin, EventType, MarketEvent, OrderIntent,
                                              TwinConfig)


def oriented_top(book: dict, side: str):
    bids, asks = book.get("bids") or [], book.get("asks") or []
    bb = bids[0] if bids else None
    ba = asks[0] if asks else None
    if side == "SHORT":
        bid = None if ba is None else (1.0 - ba["px"], float(ba.get("qty") or 0))
        ask = None if bb is None else (1.0 - bb["px"], float(bb.get("qty") or 0))
    else:
        bid = None if bb is None else (bb["px"], float(bb.get("qty") or 0))
        ask = None if ba is None else (ba["px"], float(ba.get("qty") or 0))
    return bid, ask


def replay_order(o: dict, *, cancel_latency_s: float, settlement: dict | None = None) -> dict:
    side = o.get("side") or "LONG"
    mkt = "%s:%s" % (o["slug"], side)
    decided = o.get("decided") or o.get("created")
    if decided is None or o.get("limit") is None or not o.get("qty"):
        return {"order_id": o["order_id"], "status": "NOT_REPLAYABLE", "reason": "NO_DECISION_TIME_OR_INTENT"}
    decided = float(decided)
    seq = 0
    events = []
    for b in o.get("books") or []:
        bid, ask = oriented_top(b, side)
        seq += 1
        events.append(MarketEvent(float(b["at"]), seq, EventType.BOOK, mkt, {
            "bid": bid[0] if bid else None, "bid_qty": bid[1] if bid else 0.0,
            "ask": ask[0] if ask else None, "ask_qty": ask[1] if ask else 0.0}))
    seq += 1
    events.append(MarketEvent(decided, seq, EventType.DECISION, mkt, {"order_id": o["order_id"]}))
    if settlement and settlement.get("payout") is not None and settlement.get("at"):
        seq += 1
        events.append(MarketEvent(float(settlement["at"]), seq, EventType.SETTLEMENT, mkt,
                                  {"payout": float(settlement["payout"])}))
    submit = max(0.0, float(o.get("eligible") or decided) - decided)
    ioc = (o.get("tif") or "").upper() == "IOC"
    cfg = TwinConfig(submit_latency_s=submit,
                     cancel_latency_s=(submit + 0.001) if ioc else cancel_latency_s)
    twin = DigitalTwin(cfg)
    expires = o.get("expires")
    state = {"placed": False, "cancel_sent": False, "lookahead": 0, "book_at_decision": None}
    oid = "twin:" + o["order_id"]
    sdir = "BUY" if o.get("direction") == "BUY" else "SELL"

    def strategy(view, e):
        out = []
        if e.kind == EventType.DECISION and not state["placed"]:
            bk = view.book(mkt)
            if bk is not None and bk.observed_at is not None:
                state["book_at_decision"] = bk.observed_at
                if bk.observed_at > view.now + 1e-9:
                    state["lookahead"] += 1
            out.append(OrderIntent("PLACE", mkt, sdir, float(o["limit"]), float(o["qty"]), oid))
            state["placed"] = True
            if ioc:
                out.append(OrderIntent("CANCEL", mkt, sdir, order_id=oid))
                state["cancel_sent"] = True
        elif (state["placed"] and not state["cancel_sent"] and not ioc and expires is not None
              and e.ts >= float(expires)):
            out.append(OrderIntent("CANCEL", mkt, sdir, order_id=oid))
            state["cancel_sent"] = True
        return out

    rec = twin.replay(events, strategy)
    so = twin.orders.get(oid)
    tf = [f for f in rec.fills if f["order_id"] == oid]
    pf = o.get("fills") or []
    p_qty = sum(float(f["qty"]) for f in pf)
    p_vwap = (sum(float(f["qty"]) * float(f["price"]) for f in pf) / p_qty) if p_qty else None
    t_qty = sum(f["qty"] for f in tf)
    t_vwap = (sum(f["qty"] * f["price"] for f in tf) / t_qty) if t_qty else None
    touch_short = False
    if tf:
        last = None
        for e in events:
            if e.kind == EventType.BOOK and e.ts <= tf[0]["ts"]:
                last = e
        q_at = last.payload["ask_qty" if sdir == "BUY" else "bid_qty"] if last else None
        touch_short = q_at is not None and q_at < float(o["qty"])
    return {"order_id": o["order_id"], "status": "REPLAYED", "role": o.get("role"),
            "strategy": o.get("strategy"), "event": event_identity(o)[0], "tif": o.get("tif"),
            "book_events": sum(1 for e in events if e.kind == EventType.BOOK),
            "books_before_decision": sum(1 for e in events if e.kind == EventType.BOOK and e.ts <= decided),
            "no_lookahead_violations": state["lookahead"] + rec.no_lookahead_violations,
            "submit_latency_s": submit,
            "twin_state": so.state if so else None, "twin_qty": t_qty, "twin_vwap": t_vwap,
            "twin_first_fill": tf[0]["ts"] if tf else None, "twin_fill_reasons": [f["reason"] for f in tf],
            "paper_state": o.get("state"), "paper_qty": p_qty, "paper_vwap": p_vwap,
            "paper_first_fill": float(pf[0]["at"]) if pf else None,
            "touch_smaller_than_order": touch_short,
            "settlements": rec.settlements}


def run(orders: list[dict], *, settlements: dict, cancel_latency_s: float) -> dict:
    res = [replay_order(o, cancel_latency_s=cancel_latency_s, settlement=settlements.get(o["order_id"]))
           for o in orders]
    rep = [r for r in res if r["status"] == "REPLAYED"]
    agree = Counter()
    pxd, dt = [], []
    for r in rep:
        tf, pf = r["twin_qty"] > 0, r["paper_qty"] > 0
        agree["%s_%s" % ("TWIN_FILL" if tf else "TWIN_NOFILL", "PAPER_FILL" if pf else "PAPER_NOFILL")] += 1
        if tf and pf:
            pxd.append(r["twin_vwap"] - r["paper_vwap"])
            dt.append(r["twin_first_fill"] - r["paper_first_fill"])
    n = len(rep)
    match = agree["TWIN_FILL_PAPER_FILL"] + agree["TWIN_NOFILL_PAPER_NOFILL"]
    q = lambda xs: (None if not xs else {"n": len(xs), "mean": float(np.mean(xs)),
                                         "median": float(np.median(xs)),
                                         "p10": float(np.percentile(xs, 10)),
                                         "p90": float(np.percentile(xs, 90))})
    return {
        "module": "DIGITAL_TWIN",
        "decision_logic_mode": "RECORDED_PRODUCTION_DECISIONS",
        "decision_rows": {"orders_input": len(orders), "replayed": n,
                          "not_replayable": dict(Counter(r.get("reason") for r in res if r["status"] != "REPLAYED"))},
        "unique_independent_events": len({r["event"] for r in rep} - {None}),
        "window": window(o.get("decided") or o.get("created") for o in orders),
        "no_lookahead_violations": sum(r["no_lookahead_violations"] for r in rep),
        "decisions_with_no_book_before_decision": sum(1 for r in rep if r["books_before_decision"] == 0),
        "book_events_replayed": sum(r["book_events"] for r in rep),
        "fill_agreement": dict(agree),
        "fill_agreement_rate": (match / n) if n else None,
        "twin_minus_paper_vwap": q(pxd),
        "twin_minus_paper_first_fill_s": q(dt),
        "twin_fill_paper_nofill_by_paper_state": dict(Counter(
            "%s/%s" % (r["paper_state"], r["tif"]) for r in rep if r["twin_qty"] > 0 and r["paper_qty"] == 0)),
        "twin_nofill_paper_fill_by_twin_state": dict(Counter(
            "%s/%s" % (r["twin_state"], r["tif"]) for r in rep if r["twin_qty"] == 0 and r["paper_qty"] > 0)),
        "touch_smaller_than_order_on_twin_fill": sum(1 for r in rep if r["touch_smaller_than_order"]),
        "latency_basis": {"submit": "PAPER_SIMULATOR eligible_at - decided_at, per order",
                          "cancel_s": cancel_latency_s, "cancel_basis": "PAPER_SIMULATOR median"},
        "unmeasured": {"trade_tape": "NO_TRADE_RECORDS: resting queue consumption and resting partial fills",
                       "depth_partial_fill_at_cross": "PACKAGE_FILLS_FULL_REMAINDER_AT_TOUCH; flagged above"},
        "by_role": {k: dict(Counter("%s/%s" % (r["twin_qty"] > 0, r["paper_qty"] > 0)
                                    for r in rep if r["role"] == k)) for k in ("ENTRY", "EXIT", "REDUCE")},
        "sample": [{k: v for k, v in r.items() if k != "settlements"} for r in rep[:20]],
    }
