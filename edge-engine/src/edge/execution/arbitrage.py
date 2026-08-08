"""Dutch-book execution: buy a complete outcome set for less than it pays.

The profit is guaranteed by arithmetic — exactly one outcome in a partition
resolves true, paying $1 — but ONLY once every leg is owned. That condition
is the whole engineering problem, and it is why this module exists separately
from the detector.

    Legs owned    Position
    ----------    --------
    all           arbitrage. $1 arrives at resolution, we paid less. Done.
    some          NOT an arbitrage. A naked directional bet we never wanted,
                  at a price we never judged, on an outcome we have no view
                  on. The "guaranteed" profit is now an open loss.

So the failure mode is not "we miss some profit", it is "we accidentally
take a position". Everything below is built around never ending a cycle
holding a partial set.

Three properties, in order of importance:

1. COMPLETION IS THE PRIORITY, NOT PRICE. If a leg fails after others have
   filled, we buy the missing legs at whatever the book asks, up to a
   bounded ceiling. Completing at a small loss beats holding a partial set:
   the loss is capped at (paid - 1.00) per set and known immediately, while
   a naked leg is unbounded and unknown until resolution.

2. WE STAY BUY-ONLY. Completion buys the missing outcomes; it never sells
   the ones we hold. That keeps the audited hold-to-resolution accounting
   intact and means a rescue cannot itself need a liquid exit.

3. THE RISKIEST LEG GOES FIRST. Legs are attempted thinnest-depth first. If
   the one most likely to fail fails, it fails while we own nothing, which
   costs us the opportunity and nothing else.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# A completion buy may pay up to this much above the leg's quoted price
# before we stop and escalate. Past it we are no longer rescuing an
# arbitrage, we are chasing a market that has moved away from us.
MAX_COMPLETION_SLIP = 0.10

# Refuse any book claiming more than this. Real dutch books on a liquid
# venue are worth cents; a "20c arbitrage" is a mapping error, a resolution
# mismatch, or a stale quote wearing a costume. Same reasoning as
# max_believable_edge, and the same failure it prevents.
MAX_BELIEVABLE_PROFIT = 0.10


@dataclass
class ArbResult:
    ok: bool
    status: str
    event: str
    legs_filled: int
    legs_total: int
    paid: float = 0.0          # total cash out across all legs, per set
    sets: int = 0
    profit: float = 0.0        # realised at settlement: sets * (1 - paid)
    orders: list = field(default_factory=list)
    exposed: list = field(default_factory=list)   # legs held without the set

    @property
    def complete(self) -> bool:
        return self.legs_filled == self.legs_total


def _ordered_legs(legs):
    """Thinnest depth first: attempt the leg most likely to fail while a
    failure still costs us nothing but the opportunity."""
    return sorted(legs, key=lambda l: l.size)


def _safe_place(place_fn, *args, **kwargs) -> dict:
    """An exception is a failed leg, never an aborted sequence.

    Audit 2026-08-04: place_order was unguarded, so a network timeout
    after leg 1 filled raised straight past the completion path — legs
    owned at the venue, no completion attempt, no ledger record, no
    alarm. Exactly the unrecorded-naked-position class this module
    exists to prevent. A thrown order becomes {"ok": False} so the
    abort/complete/record logic downstream always runs.
    """
    try:
        return place_fn(*args, **kwargs) or {}
    except Exception as exc:  # noqa: BLE001 — the sequence must decide, not die
        log.error("order exception treated as failed leg: %s: %s",
                  type(exc).__name__, exc)
        return {"ok": False, "status": f"exception_{type(exc).__name__}"}


def execute_dutch_book(*, adapter, book, sets: int, dry_run: bool = True,
                       max_completion_slip: float = MAX_COMPLETION_SLIP,
                       now: float | None = None) -> ArbResult:
    """Buy every leg of `book`, or end owning none of it.

    `sets` is how many complete sets to buy. Sizing and caps are the
    caller's job — this function's only contract is all-or-nothing.
    """
    now = now or time.time()
    legs = _ordered_legs(book.legs)
    res = ArbResult(ok=False, status="", event=book.event,
                    legs_filled=0, legs_total=len(legs), sets=sets)

    if book.profit_per_set > MAX_BELIEVABLE_PROFIT:
        res.status = "implausible_profit"
        log.warning("refusing %s: claims %.3f profit/set — mapping or "
                    "resolution mismatch, not free money",
                    book.event, book.profit_per_set)
        return res
    if sets <= 0 or not legs:
        res.status = "nothing_to_do"
        return res
    if dry_run:
        res.status, res.ok = "dry_run", True
        res.paid, res.profit = book.cost, round(sets * book.profit_per_set, 4)
        return res

    filled: list = []
    missing: list = []
    for i, leg in enumerate(legs):
        r = _safe_place(adapter.place_order, leg.token, leg.price, sets,
                        preview=False, tif="TIME_IN_FORCE_FILL_OR_KILL")
        # Tag the result with its leg so the caller can ledger-record every
        # fill — including the ones from books that never completed.
        r = {**(r or {}), "token": leg.token}
        res.orders.append(r)
        # Fill-or-kill means a leg fills completely or not at all. A partial
        # here would be the venue violating its own contract; treat anything
        # short of the full size as a failure rather than assuming.
        if r.get("ok") and float(r.get("count") or 0) >= sets:
            filled.append(leg)
            res.paid += float(r.get("price") or leg.price)
            continue
        # STOP. Buying the remaining legs now would deepen a position we
        # already know is not going to be an arbitrage at the quoted prices.
        # Ordering thinnest-first only protects us if a failure ends the
        # sequence — otherwise the leg most likely to fail fails, and we buy
        # everything else anyway, which is the worst of both.
        missing = list(legs[i:])
        break

    res.legs_filled = len(filled)
    if not missing:
        res.ok, res.status = True, "complete"
        res.profit = round(sets * (1.0 - res.paid), 4)
        return res

    if not filled:
        # Nothing owned. The opportunity is gone and we are flat, which is
        # exactly the outcome the ordering above is designed to produce.
        res.status = "no_fills"
        return res

    # We own part of a set. Completion is now the only priority; price is
    # secondary to not holding a naked leg.
    log.warning("%s: %d/%d legs filled — completing at market",
                book.event, len(filled), len(legs))
    for leg in list(missing):
        ceiling = round(leg.price + max_completion_slip, 4)
        r = _safe_place(adapter.place_order, leg.token, min(ceiling, 0.99),
                        sets, preview=False,
                        tif="TIME_IN_FORCE_FILL_OR_KILL")
        r = {**(r or {}), "token": leg.token}
        res.orders.append(r)
        if r.get("ok") and float(r.get("count") or 0) >= sets:
            filled.append(leg)
            missing.remove(leg)
            res.paid += float(r.get("price") or ceiling)

    res.legs_filled = len(filled)
    if not missing:
        # Completed, possibly above the modelled cost. Profit may be
        # negative — that is the bounded, known outcome we chose over an
        # unbounded unknown one.
        res.ok, res.status = True, "completed_with_slip"
        res.profit = round(sets * (1.0 - res.paid), 4)
        if res.profit < 0:
            log.warning("%s: completed at a loss of %.4f/set — bounded and "
                        "deliberate", book.event, -res.profit)
        return res

    # Could not complete inside the ceiling. This is the one case that
    # leaves us exposed, and it must be loud: it is a real position nobody
    # decided to take.
    res.status = "INCOMPLETE_EXPOSED"
    res.exposed = [l.outcome for l in filled]
    log.error("%s: EXPOSED — hold %s without %s. Not an arbitrage; a "
              "directional position that needs a human decision.",
              book.event, res.exposed, [l.outcome for l in missing])
    return res


# ── cross-venue: the same partition, one leg per venue ──────────────────
#
# Buy Team A on one venue and Team B on the other when the fee-loaded asks
# sum under $1. The arithmetic is the dutch book's; the engineering is NOT,
# because the two venues' order semantics differ:
#
#   Polymarket US : FOK limit — atomic. Fills completely or not at all.
#   Kalshi        : short-expiry limit — NOT atomic. May partially fill.
#
# So ordering is by ATOMICITY, not by depth: the non-atomic venue goes
# FIRST. Whatever count it actually fills (0..N) becomes the set size, and
# the atomic FOK venue closes exactly that count. A partial first leg
# shrinks the arbitrage instead of breaking it; a failed first leg costs
# nothing. The FOK closer is the only step that can strand us, and it gets
# one full-price attempt plus one capped completion attempt before the
# position is named EXPOSED.

@dataclass
class XVLeg:
    adapter: object
    token: str
    outcome: str      # feed team name — for the ledger and the alarm
    price: float
    size: float       # displayed depth, contracts

    def fee(self, price: float | None = None) -> float:
        fn = getattr(self.adapter, "taker_fee", None)
        return float(fn(price if price is not None else self.price)) if fn else 0.0


def _xv_place(leg: XVLeg, price: float, count: int) -> dict:
    """Venue-shaped taker buy. Adapters do not share an order signature."""
    import uuid

    name = getattr(leg.adapter, "name", "")
    if name == "kalshi":
        return _safe_place(leg.adapter.place_order, leg.token, price, count,
                           client_order_id=str(uuid.uuid4()), taker=True)
    return _safe_place(leg.adapter.place_order, leg.token, price, count,
                       preview=False, tif="TIME_IN_FORCE_FILL_OR_KILL")


def cross_venue_cost(legs: list[XVLeg]) -> float:
    """Fee-loaded cost of one set. Kalshi's taker fee is real money
    (~1.75c at 50c) and omitting it is how a guaranteed profit becomes a
    guaranteed loss."""
    return round(sum(l.price + l.fee() for l in legs), 4)


def execute_cross_venue(*, event: str, legs: list[XVLeg], max_sets: int,
                        dry_run: bool = True,
                        max_completion_slip: float = MAX_COMPLETION_SLIP,
                        complements: dict | None = None,
                        ) -> ArbResult:
    """Buy one contract-set across venues, or end holding a SETTLED set.

    Caller guarantees: exactly one leg per outcome of a TWO-way partition,
    legs on DIFFERENT venues, both venues settling on the same game result
    (no-tie sports only — the allowlist lives with the caller).

    `complements` maps a leg's token -> the SAME-VENUE XVLeg for the other
    outcome. It is the guarantee's last line (owner directive 2026-08-08:
    "guaranteed needs to be guaranteed"): if the cross-venue closer cannot
    fill even paying up, the position is RELOCKED on the first leg's own
    venue — a complete set at a small bounded cost — instead of held naked
    to settlement, which is how this sleeve went 1-9.
    """
    cost = cross_venue_cost(legs)
    profit_per_set = round(1.0 - cost, 4)
    res = ArbResult(ok=False, status="", event=event, legs_filled=0,
                    legs_total=len(legs), sets=0)
    if len(legs) != 2 or len({id(l.adapter) for l in legs}) != 2:
        res.status = "not_cross_venue"
        return res
    if profit_per_set > MAX_BELIEVABLE_PROFIT:
        res.status = "implausible_profit"
        log.warning("refusing %s: claims %.3f/set across venues — resolution "
                    "mismatch or stale quote, not free money",
                    event, profit_per_set)
        return res
    sets = min(max_sets, *(int(l.size) for l in legs))
    if sets < 1:
        res.status = "nothing_to_do"
        return res
    res.sets = sets
    if dry_run:
        res.ok, res.status = True, "dry_run"
        res.paid, res.profit = cost, round(sets * profit_per_set, 4)
        return res

    # Scarce book first: the thin side is the one that vanishes; the deep
    # side rarely misses as the closer. Equal depth falls back to the
    # atomicity ordering (PMUS FOK last).
    ordered = sorted(legs, key=lambda l: (float(l.size),
                                          getattr(l.adapter, "name", "")
                                          == "polymarket-us"))
    first, closer = ordered[0], ordered[1]

    r1 = {**_xv_place(first, first.price, sets), "token": first.token}
    res.orders.append(r1)
    got = int(float(r1.get("count") or 0)) if r1.get("ok") else 0
    if got < 1:
        res.status = "no_fills"   # flat: the only cost was the opportunity
        return res
    res.legs_filled = 1
    # Accumulate what the venue REPORTS, not what we quoted — telemetry
    # that steers min_profit must not be calibrated on flattering numbers.
    px1 = float(r1.get("price") or first.price)
    res.paid += px1 + first.fee()
    if got < sets:
        log.warning("%s: first leg partial %d/%d — closing the smaller set",
                    event, got, sets)
        res.sets = sets = got

    # Closer: escalate price, filling only the REMAINDER each attempt (a
    # partial first attempt plus a full-size retry used to overfill).
    ceiling = min(round(closer.price + max_completion_slip, 2), 0.99)
    filled2, paid2 = 0, 0.0
    for px in (closer.price, ceiling):
        need = sets - filled2
        if need <= 0:
            break
        r = {**_xv_place(closer, px, need), "token": closer.token}
        res.orders.append(r)
        g = int(float(r.get("count") or 0)) if r.get("ok") else 0
        if g > 0:
            filled2 += g
            paid2 += g * (float(r.get("price") or px) + closer.fee(px))
    if filled2 >= sets:
        res.legs_filled = 2
        res.paid = round(res.paid + paid2 / sets, 4)
        res.ok = True
        res.status = ("complete" if len(res.orders) == 2
                      else "completed_with_slip")
        res.profit = round(sets * (1.0 - res.paid), 4)
        if res.profit < 0:
            log.warning("%s: cross-venue completed at %.4f/set loss — bounded "
                        "and deliberate", event, -res.profit)
        return res

    # RELOCK: the cross-venue closer is gone. Buy the other outcome on the
    # FIRST leg's own venue — a complete (single-venue) set at a bounded
    # cost, typically the spread. Never a naked hold by choice.
    need = sets - filled2
    comp = (complements or {}).get(first.token)
    if comp is not None:
        book = None
        try:
            book = comp.adapter.get_book(comp.token, comp.token)
        except Exception:  # noqa: BLE001
            book = None
        ask = (book.asks[0].price if book is not None and book.asks
               else comp.price)
        relock_px = min(round(ask + 0.03, 2), 0.99)
        r4 = {**_xv_place(comp, relock_px, need), "token": comp.token}
        res.orders.append(r4)
        g4 = int(float(r4.get("count") or 0)) if r4.get("ok") else 0
        if g4 >= need:
            res.legs_filled = 2
            # Conservative blend: naked-turned-relocked sets pay out $1
            # like any set; cost = first leg + (closer fills + relock).
            total_cost = (sets * (px1 + first.fee()) + paid2
                          + g4 * (relock_px + comp.fee(relock_px)))
            res.paid = round(total_cost / sets, 4)
            res.ok, res.status = True, "relocked_same_venue"
            res.profit = round(sets * 1.0 - total_cost, 4)
            log.warning("%s: RELOCKED %d set(s) on %s at %.2f — bounded "
                        "%.4f net, no exposure", event, g4,
                        getattr(comp.adapter, "name", "?"), relock_px,
                        res.profit)
            return res

    res.status = "INCOMPLETE_EXPOSED"
    res.exposed = [f"{getattr(first.adapter, 'name', '?')}:{first.outcome}"
                   f" x{sets - filled2}"]
    log.error("%s: EXPOSED — hold %s without a complement anywhere. "
              "Arb fires are frozen until reviewed.", event, res.exposed)
    return res
