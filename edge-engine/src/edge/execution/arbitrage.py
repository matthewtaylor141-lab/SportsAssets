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
        r = adapter.place_order(leg.token, leg.price, sets, preview=False,
                                tif="TIME_IN_FORCE_FILL_OR_KILL")
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
        r = adapter.place_order(leg.token, min(ceiling, 0.99), sets,
                                preview=False,
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
