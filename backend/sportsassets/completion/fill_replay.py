"""THE DIGITAL TWIN'S IOC SEMANTICS, REPAIRED (completion readiness, 2026-10-07).

THE MISMATCH CLASS (Profitability Stack V1 receipt, twin run 37643890985: 788
recorded paper orders, fill agreement 70.05%): 200 orders the twin FILLED
were EXPIRED/IOC in PAPER, and 36 the twin CANCELLED were FILLED in PAPER;
the twin's first fills led PAPER's by a median 10.7 s. The package twin
crossed an IOC at activation against the LAST BOOK OBSERVED BEFORE
ACTIVATION (stale; possibly observed before the decision itself) and
cancelled it 1 ms later. PAPER_SIM_V1 -- the semantics every recorded PAPER
outcome was produced under -- evaluates a marketable order on the FIRST
READABLE BOOK OBSERVED AT OR AFTER eligible_at (decided_at + the frozen
delay) and at or before expires_at, walks only whole-cent levels within the
limit, consumes each (market, side, price, observation) level at most once,
and EXPIRES with no fill when no readable book arrives in the window. The
optimistic twin therefore (a) filled on quotes that were gone by the first
eligible book, (b) cancelled orders whose first eligible book crossed, and
(c) treated off-cent levels as executable.

REPAIRED SEMANTICS (this module): exactly PAPER_SIM_V1's marketable rule,
replayed from the recorded books. Agreement is reported against the
recorded PAPER outcomes on the same recorded books: it certifies that the
replay reproduces PAPER's execution semantics -- NOT venue execution, which
no recorded data here can certify. Until agreement >= TARGET on a fresh
receipt, no twin P&L is reported (CERTIFIED False).

Pure, stdlib only. No database, no venue, no order path.
"""
from __future__ import annotations

from collections import Counter

VERSION = "BETTOR_TWIN_IOC_REPAIRED_V1"
TARGET = 0.95
QTY_TOL = 1e-6
SEMANTICS = {
    "marketable": ("first readable book observed at/after eligible_at and "
                   "at/before expires_at; none -> EXPIRED, no fill"),
    "levels": "whole-cent wire prices only, best first, within the limit",
    "side": ("BUY_LONG lifts offers; BUY_SHORT hits bids at 1-bid; SELL "
             "LONG hits bids; SELL SHORT lifts offers at 1-offer"),
    "consumption": "each (market, ladder, wire price, observation) once",
    "partials": "IOC partial allowed, remainder released; FOK all or none",
}
#: the mismatch taxonomy (of the optimistic twin, and of any residual)
M_STALE = "STALE_PRE_ACTIVATION_BOOK_CROSSED"
M_EARLY_CANCEL = "CANCELLED_BEFORE_FIRST_ELIGIBLE_BOOK"
M_OFF_CENT = "OFF_CENT_LEVEL_TREATED_EXECUTABLE"
M_NO_BOOK = "NO_RECORDED_BOOK_IN_WINDOW"
M_DEPTH = "RECORDED_DEPTH_TRUNCATED_TOP5"
M_CONSUMED = "LIQUIDITY_CONSUMED_BY_OTHER_PAPER_ORDERS"
M_QTY = "FILLED_QUANTITY_DIFFERS"
M_OTHER = "UNCLASSIFIED"


def _cent(px) -> bool:
    try:
        return abs(round(float(px) * 100) - float(px) * 100) < 1e-6
    except (TypeError, ValueError):
        return False


def ladder(book: dict, *, direction: str, side: str,
           whole_cent: bool = True) -> tuple:
    """(ladder name, [(price paid or received, qty, wire px)] best first)."""
    bids = [(float(x["px"]), float(x.get("qty") or 0)) for x in
            (book.get("bids") or []) if x and x.get("px") is not None]
    asks = [(float(x["px"]), float(x.get("qty") or 0)) for x in
            (book.get("asks") or []) if x and x.get("px") is not None]
    bids.sort(key=lambda t: -t[0])
    asks.sort(key=lambda t: t[0])
    buy, short = str(direction).upper() == "BUY", str(side).upper() == "SHORT"
    if buy and not short:
        name, lv = "offers", [(p, q, p) for p, q in asks]
    elif buy and short:
        name, lv = "bids", [(round(1.0 - p, 10), q, p) for p, q in bids]
    elif not short:
        name, lv = "bids", [(p, q, p) for p, q in bids]
    else:
        name, lv = "offers", [(round(1.0 - p, 10), q, p) for p, q in asks]
    if whole_cent:
        lv = [x for x in lv if _cent(x[2])]
    return name, lv


def _ok(price, limit, buy) -> bool:
    return price <= limit + 1e-9 if buy else price >= limit - 1e-9


def first_eligible_book(order: dict):
    el = order.get("eligible") or order.get("decided") or order.get("created")
    ex = order.get("expires")
    for b in sorted(order.get("books") or [], key=lambda x: float(x["at"])):
        at = float(b["at"])
        if el is not None and at < float(el):
            continue
        if ex is not None and at > float(ex):
            break
        return b
    return None


def replay_marketable(order: dict, ledger: dict | None = None) -> dict:
    """One IOC / FOK order under the repaired semantics."""
    ledger = {} if ledger is None else ledger
    buy = str(order.get("direction")).upper() == "BUY"
    limit, qty = order.get("limit"), float(order.get("qty") or 0)
    if limit is None or qty <= 0:
        return {"state": "NOT_REPLAYABLE", "qty": 0.0, "vwap": None,
                "why": "NO_LIMIT_OR_QTY"}
    b = first_eligible_book(order)
    if b is None:
        return {"state": "EXPIRED", "qty": 0.0, "vwap": None,
                "why": M_NO_BOOK, "book_at": None}
    name, lv = ladder(b, direction=order.get("direction"),
                      side=order.get("side") or "LONG")
    left, takes = qty, []
    for price, avail, wire in lv:
        if left <= QTY_TOL or not _ok(price, float(limit), buy):
            break
        key = (order.get("slug"), name, wire, float(b["at"]))
        free = max(0.0, avail - ledger.get(key, 0.0))
        take = min(left, free)
        if take > QTY_TOL:
            takes.append((price, take, key))
            left -= take
    filled = qty - left
    fok = str(order.get("tif") or "").upper() == "FOK"
    if fok and left > QTY_TOL:
        takes, filled = [], 0.0
    for _p, t, key in takes:
        ledger[key] = ledger.get(key, 0.0) + t
    vwap = (sum(p * t for p, t, _k in takes) / filled) if filled > QTY_TOL \
        else None
    state = ("FILLED" if filled >= qty - QTY_TOL else
             "PARTIAL" if filled > QTY_TOL else "EXPIRED")
    return {"state": state, "qty": round(filled, 6), "vwap": vwap,
            "book_at": float(b["at"]), "why": None if takes else
            "FIRST_ELIGIBLE_BOOK_DID_NOT_CROSS"}


def optimistic_replay(order: dict) -> dict:
    """The package twin's IOC, for diagnosis only: the last book observed at
    or before activation, any level (off-cent included), cancel at once."""
    el = order.get("eligible") or order.get("decided")
    pre = [b for b in order.get("books") or []
           if el is not None and float(b["at"]) <= float(el)]
    if not pre:
        return {"qty": 0.0, "off_cent_only": False}
    b = max(pre, key=lambda x: float(x["at"]))
    buy = str(order.get("direction")).upper() == "BUY"
    _n, lv = ladder(b, direction=order.get("direction"),
                    side=order.get("side") or "LONG", whole_cent=False)
    left, cent_fill = float(order.get("qty") or 0), 0.0
    got = 0.0
    for price, avail, wire in lv:
        if left <= QTY_TOL or not _ok(price, float(order["limit"]), buy):
            break
        t = min(left, avail)
        got += t
        left -= t
        if _cent(wire):
            cent_fill += t
    return {"qty": got, "off_cent_only": got > QTY_TOL and cent_fill <= QTY_TOL}


def _paper_qty(order: dict) -> float:
    return sum(float(f.get("qty") or 0) for f in order.get("fills") or [])


def classify(order: dict, repaired: dict) -> str | None:
    """None when the repaired twin agrees with PAPER (fill/no-fill and
    quantity); else the residual mismatch class."""
    pq, tq = _paper_qty(order), float(repaired.get("qty") or 0)
    if (pq > QTY_TOL) == (tq > QTY_TOL) and abs(pq - tq) <= max(
            QTY_TOL, 1e-6 * max(pq, 1.0)):
        return None
    if (pq > QTY_TOL) == (tq > QTY_TOL):
        return M_QTY
    if repaired.get("why") == M_NO_BOOK:
        return M_NO_BOOK
    b = first_eligible_book(order)
    if b is not None and tq <= QTY_TOL and pq > QTY_TOL:
        # PAPER filled beyond what the recorded top 5 levels show
        if len(b.get("bids") or []) >= 5 or len(b.get("asks") or []) >= 5:
            return M_DEPTH
    if tq > QTY_TOL and pq <= QTY_TOL:
        return M_CONSUMED
    return M_OTHER


def diagnose_optimistic(order: dict) -> str | None:
    """Why the OPTIMISTIC twin disagreed with PAPER on this order."""
    pq = _paper_qty(order)
    o = optimistic_replay(order)
    tq = o["qty"]
    if (pq > QTY_TOL) == (tq > QTY_TOL):
        return None
    if tq > QTY_TOL and pq <= QTY_TOL:
        if o["off_cent_only"]:
            return M_OFF_CENT
        if first_eligible_book(order) is None:
            return M_NO_BOOK
        return M_STALE
    return M_EARLY_CANCEL


def _eligibility(o: dict) -> float:
    return float(o.get("eligible") or o.get("created") or 0)


class OutOfEligibilityOrder(ValueError):
    """A batch handed to `Agreement.add` holds an order eligible BEFORE one
    an earlier batch already replayed. The shared consumption ledger would
    then be applied out of PAPER's order, so the stream is refused rather
    than replayed in a different order."""


class Agreement:
    """`agreement` over a STREAM of batches (RC6.2 lane p-evcontrols).

    The twin's population -- every fresh marketable order -- only grows, so
    its read is replayed a page at a time as it arrives instead of being held
    whole in memory. Batches must arrive in eligibility order (the read pages
    on eligible_at, order_id). Within a batch the orders are sorted exactly
    as `agreement` sorts them, and the consumption ledger is shared across
    batches, as PAPER's is. Over the same orders, any split into in-order
    batches gives the report `agreement` gives on the whole list."""

    def __init__(self):
        self.ledger: dict = {}
        self.marketable = self.agree = 0
        self.replayed = self.false_fills = self.lookahead = 0
        self.residual: Counter = Counter()
        self.optimistic: Counter = Counter()
        self.events: set = set()
        self.last_eligible: float | None = None

    def add(self, orders: list) -> "Agreement":
        marketable = [o for o in orders if str(o.get("tif") or "").upper()
                      in ("IOC", "FOK")]
        marketable.sort(key=_eligibility)
        if marketable and self.last_eligible is not None and \
                _eligibility(marketable[0]) < self.last_eligible:
            raise OutOfEligibilityOrder(
                "batch starts at %r, before %r already replayed"
                % (_eligibility(marketable[0]), self.last_eligible))
        self.marketable += len(marketable)
        for o in marketable:
            self.last_eligible = _eligibility(o)
            self._one(o)
        return self

    def _one(self, o: dict) -> None:
        r = replay_marketable(o, self.ledger)
        if r["state"] == "NOT_REPLAYABLE":
            return
        self.replayed += 1
        self.events.add(str(o.get("slug") or ""))
        # the red team's certification counters (digital_twin_gate): the
        # twin FILLED where PAPER expired, and any book used from before
        # the order was eligible or after it expired (lookahead / stale)
        tq = float(r.get("qty") or 0)
        if tq > QTY_TOL and _paper_qty(o) <= QTY_TOL:
            self.false_fills += 1
        el = o.get("eligible") or o.get("decided") or o.get("created")
        ex = o.get("expires")
        bat = r.get("book_at")
        if bat is not None and ((el is not None and bat < float(el)) or (
                ex is not None and bat > float(ex))):
            self.lookahead += 1
        c = classify(o, r)
        if c is None:
            self.agree += 1
        else:
            self.residual[c] += 1
        d = diagnose_optimistic(o)
        if d is not None:
            self.optimistic[d] += 1

    def report(self) -> dict:
        replayed, agree = self.replayed, self.agree
        rate = round(agree / replayed, 6) if replayed else None
        return {"version": VERSION, "semantics": SEMANTICS,
                "marketable_orders": self.marketable, "replayed": replayed,
                "agree": agree, "fill_agreement_rate": rate,
                "target": TARGET,
                "certified": bool(rate is not None and rate >= TARGET),
                "compared": replayed,
                "optimistic_false_fills": self.false_fills,
                "optimistic_false_fill_rate": (
                    round(self.false_fills / replayed, 6)
                    if replayed else None),
                "lookahead_violations": self.lookahead,
                "residual_mismatch_taxonomy": dict(self.residual),
                "optimistic_twin_mismatch_taxonomy": dict(self.optimistic),
                "markets": len(self.events),
                # this module computes no twin P&L at all; none is reported
                # anywhere until a FRESH receipt certifies (readback decides)
                "twin_pnl_reported": False,
                "scope": ("agreement with recorded PAPER_SIM_V1 outcomes on "
                          "the same recorded books; not venue execution")}


def agreement(orders: list) -> dict:
    """The repaired twin over recorded marketable PAPER orders, in order of
    eligibility (the consumption ledger is shared, as PAPER's is)."""
    return Agreement().add(orders).report()
