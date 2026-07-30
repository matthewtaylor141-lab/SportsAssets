"""Model-free edge: when the venue contradicts itself.

Polymarket US runs a SEPARATE book per outcome. One game carries a moneyline
per side, a spread ladder, a totals ladder, first-five-innings and per-inning
markets — all functions of one underlying distribution, all quoted
independently. Independently-quoted books drift into mutual contradiction,
and contradiction is money that does not require being right about anything.

If a complete, mutually exclusive set of outcomes can be bought for less than
$1 in total, one of them WILL pay $1 at resolution. The profit is arithmetic.
It does not care that our fair value is thirty seconds stale, it does not
care whether Pinnacle is sharper than us, and it does not suffer adverse
selection — the usual objections to this whole strategy simply do not apply.

THE ONE WAY THIS GOES WRONG is buying a set that is not actually complete.
"Arsenal" plus "Over 2.5" for 90c is not an arbitrage, it is two unrelated
bets. So completeness is never inferred from prices or counts — the caller
must supply the partition, and this module refuses anything it cannot prove
covers every outcome exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass

# A set that only just clears $1 is not an opportunity: one tick of movement
# between reading the book and the last leg filling erases it.
DEFAULT_MIN_PROFIT = 0.01


@dataclass(frozen=True)
class Leg:
    outcome: str
    token: str
    price: float
    size: float       # contracts available at that price


@dataclass(frozen=True)
class DutchBook:
    """A complete outcome set purchasable for less than it must pay out."""

    event: str
    kind: str                 # 'moneyline' | 'total 8.5' | 'spread 1.5' ...
    legs: tuple[Leg, ...]
    cost: float               # total paid per complete set, incl. fees
    sets: int                 # complete sets the displayed depth supports

    @property
    def profit_per_set(self) -> float:
        """Guaranteed payout is exactly $1 per set: one leg resolves true."""
        return round(1.0 - self.cost, 4)

    @property
    def roi(self) -> float:
        return round(self.profit_per_set / self.cost, 4) if self.cost else 0.0

    @property
    def stake(self) -> float:
        return round(self.cost * self.sets, 2)


def find_dutch_book(event: str, kind: str, legs: list[Leg],
                    expected_outcomes: int, fee_per_contract: float = 0.0,
                    min_profit: float = DEFAULT_MIN_PROFIT) -> DutchBook | None:
    """A riskless set, or None.

    expected_outcomes is the size of the TRUE partition — 2 for a total, 3
    for a soccer moneyline with a draw. If we hold fewer legs than that we
    are missing a side, and buying the ones we have is a directional bet
    wearing an arbitrage costume. That check is the whole safety of this
    module and it is deliberately not inferrable from the legs themselves.
    """
    if expected_outcomes < 2 or len(legs) != expected_outcomes:
        return None
    if len({leg.token for leg in legs}) != len(legs):
        return None                      # the same market counted twice
    if any(leg.price <= 0 or leg.price >= 1 or leg.size <= 0 for leg in legs):
        return None
    cost = sum(leg.price + fee_per_contract for leg in legs)
    if 1.0 - cost < min_profit:
        return None
    # One complete set needs one contract of every leg, so the thinnest leg
    # sets the size. Fractional contracts cannot be bought.
    sets = int(min(leg.size for leg in legs))
    if sets < 1:
        return None
    return DutchBook(event=event, kind=kind, legs=tuple(legs),
                     cost=round(cost, 4), sets=sets)


def ladder_violations(rungs: dict[float, float]) -> list[tuple[float, float]]:
    """Points where a monotone ladder is priced backwards.

    P(margin > 2.5) can never exceed P(margin > 1.5); P(total > 9.5) can
    never exceed P(total > 8.5). A ladder that says otherwise has one rung
    mispriced RELATIVE TO ANOTHER MARKET ON THE SAME VENUE — again with no
    reference to our own model.

    rungs: {point: price of the 'over/more than' side}. Returns the pairs
    that contradict, cheaper-point first.
    """
    out = []
    points = sorted(rungs)
    for lo, hi in zip(points, points[1:]):
        # Strictly harder to achieve, so it must not cost more.
        if rungs[hi] > rungs[lo] + 1e-9:
            out.append((lo, hi))
    return out
