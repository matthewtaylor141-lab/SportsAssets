"""Dutch-book execution: end owning the whole set, or none of it.

The profit is guaranteed by arithmetic — exactly one outcome in a partition
pays $1 — but only once every leg is owned. Own three legs of four and the
guarantee inverts: a naked directional position at a price nobody judged.

Every test here is a way that could happen.
"""

import pytest

from edge.analysis.consistency import DutchBook, Leg
from edge.execution.arbitrage import (MAX_BELIEVABLE_PROFIT, execute_dutch_book)


class FakeVenue:
    """Records orders; `fails` names tokens whose FOK is killed."""

    def __init__(self, fails=(), fill_price=None, complete_fails=()):
        self.fails = set(fails)
        self.complete_fails = set(complete_fails)
        self.orders = []
        self.fill_price = fill_price

    def place_order(self, token, price, quantity, preview=True, tif=None):
        self.orders.append({"token": token, "price": price, "qty": quantity,
                            "tif": tif})
        attempt = sum(1 for o in self.orders if o["token"] == token)
        killed = token in self.fails and attempt == 1
        if token in self.complete_fails:
            killed = True
        if killed:
            return {"ok": False, "status": "killed", "count": 0, "price": price}
        return {"ok": True, "status": "filled", "count": quantity,
                "price": self.fill_price or price}


def _book(prices=(0.40, 0.35, 0.20), sizes=(100, 100, 100)):
    legs = tuple(Leg(outcome=f"o{i}", token=f"t{i}", price=p, size=s)
                 for i, (p, s) in enumerate(zip(prices, sizes)))
    return DutchBook(event="E", kind="moneyline", legs=legs,
                     cost=sum(prices), sets=min(sizes))


# ── the happy path ──────────────────────────────────────────────────────

def test_a_complete_set_is_bought_and_the_profit_is_arithmetic():
    v = FakeVenue()
    r = execute_dutch_book(adapter=v, book=_book(), sets=10, dry_run=False)
    assert r.ok and r.complete and r.status == "complete"
    # paid 0.95/set, one leg pays 1.00 -> 0.05/set * 10 sets
    assert r.paid == pytest.approx(0.95)
    assert r.profit == pytest.approx(0.5)
    assert all(o["tif"] == "TIME_IN_FORCE_FILL_OR_KILL" for o in v.orders)


def test_the_thinnest_leg_is_attempted_first():
    """If the leg most likely to fail fails, it should fail while we own
    nothing — costing the opportunity and nothing else."""
    v = FakeVenue()
    execute_dutch_book(adapter=v, book=_book(sizes=(500, 50, 200)), sets=10,
                       dry_run=False)
    assert [o["token"] for o in v.orders][:3] == ["t1", "t2", "t0"]


# ── failures that must leave us flat ────────────────────────────────────

def test_the_first_leg_failing_leaves_us_owning_nothing():
    """Thinnest-first only protects us if a failure ENDS the sequence. An
    earlier draft kept buying the remaining legs after the first was killed,
    which is the worst of both: the leg most likely to fail fails, and we
    take the position anyway."""
    v = FakeVenue(complete_fails={"t2"})     # t2 is thinnest -> attempted first
    book = _book(sizes=(300, 300, 10))
    r = execute_dutch_book(adapter=v, book=book, sets=5, dry_run=False)
    assert not r.ok and r.status == "no_fills"
    assert r.legs_filled == 0
    assert len(v.orders) == 1, "stopped after the first failure, bought nothing"


# ── the case that actually matters ──────────────────────────────────────

def test_a_late_leg_failure_is_completed_at_market_not_abandoned():
    """Holding two of three legs is a naked position. Completing at a worse
    price is a bounded, immediate, known cost; holding is unbounded and
    unknown until resolution."""
    v = FakeVenue(fails={"t0"})        # fails once, succeeds on the retry
    # t0 deepest -> attempted LAST, so the first two legs are already owned.
    r = execute_dutch_book(adapter=v, book=_book(sizes=(300, 100, 200)),
                           sets=10, dry_run=False)
    assert r.ok and r.complete
    assert r.status == "completed_with_slip"
    # The completion buy was placed above the original quote.
    retry = [o for o in v.orders if o["token"] == "t0"][-1]
    assert retry["price"] > 0.40


def test_completion_may_book_a_loss_and_that_is_the_point():
    v = FakeVenue(fails={"t0"})
    # 0.62 + 0.35 + 0.20 = 1.17 modelled... use a book that only just works,
    # so a 10c completion slip pushes the set past $1.
    r = execute_dutch_book(adapter=v,
                           book=_book(prices=(0.40, 0.35, 0.23),
                                      sizes=(300, 100, 200)),
                           sets=1, dry_run=False)
    assert r.ok and r.complete
    assert r.paid > 1.0
    assert r.profit < 0            # bounded, deliberate, and known now
    assert r.profit > -0.2         # ...and small


def test_an_uncompletable_set_is_loud_and_names_the_exposure():
    """The one case that leaves a real position nobody decided to take."""
    v = FakeVenue(complete_fails={"t0"})
    r = execute_dutch_book(adapter=v, book=_book(sizes=(300, 100, 200)),
                           sets=10, dry_run=False)
    assert not r.ok
    assert r.status == "INCOMPLETE_EXPOSED"
    assert set(r.exposed) == {"o1", "o2"}


# ── refusals ────────────────────────────────────────────────────────────

def test_an_implausible_book_is_refused_as_a_data_error():
    """Real dutch books on a liquid venue are worth cents. A 20c one is a
    mapping error, a resolution mismatch, or a stale quote — and executing
    it is how you discover which."""
    fat = _book(prices=(0.30, 0.25, 0.20))     # cost 0.75 -> 25c "profit"
    assert fat.profit_per_set > MAX_BELIEVABLE_PROFIT
    v = FakeVenue()
    r = execute_dutch_book(adapter=v, book=fat, sets=10, dry_run=False)
    assert not r.ok and r.status == "implausible_profit"
    assert v.orders == []          # nothing was sent


def test_dry_run_sends_no_orders():
    v = FakeVenue()
    r = execute_dutch_book(adapter=v, book=_book(), sets=10, dry_run=True)
    assert r.ok and r.status == "dry_run" and v.orders == []


def test_a_partial_fill_counts_as_a_failed_leg():
    """Fill-or-kill means all or nothing. A short fill is the venue breaking
    its own contract, and assuming otherwise would leave us holding a
    fraction of a set while believing we hold a whole one."""
    class Short(FakeVenue):
        def place_order(self, token, price, quantity, preview=True, tif=None):
            r = super().place_order(token, price, quantity, preview, tif)
            if token == "t0" and r["count"]:
                r["count"] = quantity - 1
            return r

    v = Short()
    r = execute_dutch_book(adapter=v, book=_book(sizes=(300, 100, 200)),
                           sets=10, dry_run=False)
    # t0 never counts as filled, so it is completed or reported exposed —
    # never silently treated as owned.
    assert r.status in ("completed_with_slip", "INCOMPLETE_EXPOSED")
