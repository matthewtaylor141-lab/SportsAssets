"""Position mirroring, the pure part (owner order 2026-09-02, "go for it,
let's get this working").

The copy sleeve reacted to a whale's FILLS one at a time, each fill
judged by a rule (never-add, one-per-game, complement-buy-is-exit). A
whale who runs a two-sided book -- RN1 on Nakashima v Michelsen: 66
buys, 64 as a maker, 28,162 matched pairs at a combined 0.8915 and a
24,423-share residual -- is unreadable that way: the rules copied one
clip, sold it in six pieces, and threw away 55 of his 66 fills.

The mirror reads his POSITION instead. Per whale and market it keeps
one number, his net holding, and our job is to hold a fixed fraction of
it. Every entry, add, hedge and exit is the same operation: move toward
the target.

This module is the arithmetic, kept pure so every rule is testable
without a venue or a database:

  * net_positions(fills)  -> his running position per token from the
                            fills we ingest (BUY adds, SELL subtracts)
  * his_net(long, other)  -> the signed net on our netting venue, in
                            LONG-token shares: long minus the other side
  * opening_burst(...)    -> what his first move on a market amounts to
                            (all his buys inside BURST_S of the first),
                            the anchor the ratio is set against
  * mirror_ratio(...)     -> MEASURE_CLIP_USD / median opening burst,
                            clamped to [RATIO_MIN, COPY_RATIO_MAX]
  * target_shares(...)    -> ratio x his_net, capped by dollars at the
                            mark, whole shares, shorts optional
  * plan(...)             -> given target, ledger and venue positions:
                            the one order we WOULD place (side, qty,
                            price) or the reason for none, with the
                            dead band, the flatten exception, the
                            would-fill read against the live book

The shadow worker logs plan() per tick and places nothing (phase P0).
Phase P1 executes the same plan long-only under every existing breaker;
phase P2 admits negative targets through the short gate.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

from .roster_rules import MEASURE_CLIP_USD

# A whale's opening move is a BURST of fills, not one fill (RN1 loaded
# 10,654 Michelsen across 12 fills in three minutes; 7 of them under $6).
# Everything inside this window of his first BUY on a market is one
# decision, and that is what the $50 clip is set against.
BURST_S = 60.0
# The ratio is bounded: never more than one-for-one with him (the
# executor's COPY_RATIO_MAX), never so small that his ordinary move
# rounds to nothing for us.
RATIO_MAX = 1.0
RATIO_MIN = 1e-4
# Per-market net exposure cap at the mark, in dollars: the promoted clip
# ceiling reused as the mirror's per-market bound. When it binds the
# target is SCALED, not truncated on one side.
MARKET_NET_CAP_USD = 250.0
# Dead band: a move under one whole share, or under this many dollars at
# the mark, is not worth an order -- unless it takes the book to zero.
MIN_MOVE_USD = 5.0
# Hysteresis: skip moves smaller than this fraction of the target.
MIN_MOVE_FRAC = 0.02


@dataclass
class Fill:
    asset: str
    side: str           # BUY / SELL
    size: float
    price: float
    ts: float           # epoch seconds
    condition_id: str | None = None
    tx_hash: str | None = None


def net_positions(fills: Iterable[Fill | dict]) -> dict[str, float]:
    """His running position per token from fills: BUY adds, SELL
    subtracts, floored at zero (a SELL beyond what the ledger saw him
    buy is a fill we missed, not a short -- Polymarket global cannot
    short a token). Order-independent."""
    pos: dict[str, float] = {}
    for f in fills:
        d = f if isinstance(f, dict) else f.__dict__
        asset = str(d.get("asset") or "")
        if not asset:
            continue
        try:
            size = float(d.get("size") or 0.0)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        sign = 1.0 if str(d.get("side") or "").upper() == "BUY" else -1.0
        pos[asset] = pos.get(asset, 0.0) + sign * size
    return {a: max(0.0, round(v, 6)) for a, v in pos.items()}


def his_net(long_shares: float, other_shares: float) -> float:
    """The signed net on a netting venue, in long-token shares. His
    matched pairs cancel; what is left is the directional residual."""
    return round(float(long_shares or 0.0) - float(other_shares or 0.0), 6)


def opening_burst(fills: Iterable[Fill | dict], burst_s: float = BURST_S) -> float:
    """Dollars of his BUYS inside `burst_s` of his first BUY on the
    market (any token). Zero when he never bought."""
    buys = []
    for f in fills:
        d = f if isinstance(f, dict) else f.__dict__
        if str(d.get("side") or "").upper() != "BUY":
            continue
        try:
            ts = float(d.get("ts"))
            notional = float(d.get("size") or 0.0) * float(d.get("price") or 0.0)
        except (TypeError, ValueError):
            continue
        if notional > 0:
            buys.append((ts, notional))
    if not buys:
        return 0.0
    t0 = min(ts for ts, _ in buys)
    return round(sum(n for ts, n in buys if ts <= t0 + burst_s), 4)


def mirror_ratio(bursts: Iterable[float], clip_usd: float = MEASURE_CLIP_USD) -> dict:
    """ratio = clip / median opening burst over his recent markets, so
    his typical first move maps to the measuring clip. Needs at least
    MIN_MARKETS markets; otherwise the ratio is None and the mirror does
    nothing (fail closed on an unknown scale)."""
    xs = sorted(float(b) for b in bursts if b and float(b) > 0)
    out: dict[str, Any] = {"n": len(xs), "anchor_usd": None, "ratio": None,
                           "clip_usd": float(clip_usd), "anchor_usd_weighted": None,
                           "ratio_weighted": None}
    if len(xs) < MIN_MARKETS:
        out["why"] = f"fewer than {MIN_MARKETS} markets with an opening burst"
        return out
    anchor = statistics.median(xs)
    out["anchor_usd"] = round(anchor, 2)
    out["ratio"] = round(min(RATIO_MAX, max(RATIO_MIN, float(clip_usd) / anchor)), 6)
    # THE DOLLAR-WEIGHTED ANCHOR, reported beside the median (first shadow
    # hour, 2026-09-02): RN1 opened 19,742 markets in 30 days with a
    # median burst of $25.60, so the median-anchored ratio clamps to 1.0
    # and the $250 cap does all the sizing on the markets that carry his
    # money. This is the burst size at which half of his opening dollars
    # sit in LARGER bursts -- where the money is, not where the count is.
    total = sum(xs)
    acc = 0.0
    weighted = xs[-1]
    for b in reversed(xs):
        acc += b
        if acc >= total / 2.0:
            weighted = b
            break
    out["anchor_usd_weighted"] = round(weighted, 2)
    out["ratio_weighted"] = round(min(RATIO_MAX, max(RATIO_MIN, float(clip_usd) / weighted)), 6)
    return out


MIN_MARKETS = 10


def target_shares(ratio: float | None, net: float, mark: float | None,
                  allow_short: bool = False,
                  cap_usd: float = MARKET_NET_CAP_USD) -> dict:
    """Our target in long-token shares. Whole shares. A negative net is
    a short of the long token: refused (target 0) unless shorts are
    allowed. The dollar cap at the mark scales the target down, never
    truncates one side."""
    if ratio is None or ratio <= 0:
        return {"target": 0, "raw": 0.0, "capped": False, "why": "no ratio"}
    raw = float(ratio) * float(net or 0.0)
    if raw < 0 and not allow_short:
        return {"target": 0, "raw": round(raw, 4), "capped": False,
                "why": "short side not admitted"}
    capped = False
    if mark is not None and 0.0 < float(mark) < 1.0 and cap_usd > 0:
        px = float(mark) if raw >= 0 else 1.0 - float(mark)
        max_sh = cap_usd / px
        if abs(raw) > max_sh:
            raw = max_sh if raw > 0 else -max_sh
            capped = True
    tgt = int(raw) if raw >= 0 else -int(-raw)      # toward zero, whole shares
    return {"target": tgt, "raw": round(raw, 4), "capped": capped, "why": None}


@dataclass
class Book:
    bid: float | None = None
    ask: float | None = None


@dataclass
class Plan:
    side: str | None            # BUY_LONG / SELL_LONG / None
    qty: int
    price: float | None         # the price we would rest at (his level)
    reason: str                 # why (or why not)
    would_fill: bool | None = None
    detail: dict = field(default_factory=dict)


def plan(target: int, ledger: int, venue: int | None, book: Book,
         his_last_px: float | None, mark: float | None) -> Plan:
    """The one order we WOULD place to move from what we hold toward the
    target. Fail closed on any disagreement between ledger and venue.

      * venue None (unreadable) -> nothing
      * venue != ledger         -> frozen: a position the book cannot
                                   explain is reconciled first, never
                                   traded against
      * |delta| < 1 share       -> nothing
      * |delta x mark| < MIN_MOVE_USD and not a flatten -> nothing
      * |delta| < MIN_MOVE_FRAC x |target| and not a flatten -> nothing
      * buy: rest at min(his last price, best bid) -- join his level,
             never above him; would_fill if the ask is at/under it
      * sell: rest at max(his equivalent price, best ask); would_fill
              if the bid is at/over it
    """
    if venue is None:
        return Plan(None, 0, None, "venue unreadable")
    if int(venue) != int(ledger):
        return Plan(None, 0, None, "frozen: venue and ledger disagree",
                    detail={"venue": venue, "ledger": ledger})
    delta = int(target) - int(ledger)
    if delta == 0:
        return Plan(None, 0, None, "on target")
    flatten = int(target) == 0
    if abs(delta) < 1:
        return Plan(None, 0, None, "under one share")
    if mark is not None and abs(delta) * float(mark) < MIN_MOVE_USD and not flatten:
        return Plan(None, 0, None, "under the dollar dead band",
                    detail={"delta": delta})
    if target != 0 and abs(delta) < MIN_MOVE_FRAC * abs(int(target)) and not flatten:
        return Plan(None, 0, None, "inside hysteresis", detail={"delta": delta})
    # THE BOOK SIDE WE JOIN IS REQUIRED (review round one): a buy rests
    # at or under the bid, a sell at or over the ask; without that side
    # of the book there is no price to rest at, and a price off a stale
    # fill alone would be counted a fill against a book it never saw.
    # `would_fill` here is the IMMEDIATE read -- the book already at or
    # through his level at this tick ("marketable now"); the shadow
    # worker resolves the real question, whether the book CAME to the
    # resting price, against the next reading of the market.
    if delta > 0:
        if book.bid is None or not (0.0 < book.bid < 1.0):
            return Plan("BUY_LONG", delta, None, "no price to rest at")
        cands = [p for p in (his_last_px, book.bid) if p is not None and 0.0 < p < 1.0]
        px = round(min(cands), 4)
        wf = (book.ask is not None and book.ask <= px)
        return Plan("BUY_LONG", delta, px, "increase toward target", wf,
                    {"delta": delta})
    # decrease: sell the long leg at his equivalent price or better
    if book.ask is None or not (0.0 < book.ask < 1.0):
        return Plan("SELL_LONG", -delta, None, "no price to rest at")
    cands = [p for p in (his_last_px, book.ask) if p is not None and 0.0 < p < 1.0]
    px = round(max(cands), 4)
    wf = (book.bid is not None and book.bid >= px)
    return Plan("SELL_LONG", -delta, px, "flatten" if flatten else "reduce toward target",
                wf, {"delta": delta})


__all__ = ["Fill", "Book", "Plan", "net_positions", "his_net", "opening_burst",
           "mirror_ratio", "target_shares", "plan", "BURST_S", "MARKET_NET_CAP_USD",
           "MIN_MOVE_USD", "MIN_MOVE_FRAC", "MIN_MARKETS"]
