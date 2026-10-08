"""A symbol the registry stops assigning releases its instrument record too.

Review finding (2026-10-08, with the plane OOM-cycling at 2 GiB on
7fd4574e): SizedBooks.unwant() popped the book from _markets but left the
symbol's refdata record (scales, state, product id) in _instruments, so in
subscribe-all mode every symbol ever assigned stayed resident. Bounded by the
distinct symbols ever assigned (~113k POLYMARKET_US registry rows), so it is a
retention defect of tens of MB, not the whole working set; the fix must not
cost coverage: a symbol assigned again gets its record back on the same sync,
because the caller passes refdata for EVERY assigned row on each assignment
sync (workers/universal_market_plane.py)."""
from __future__ import annotations

from sportsassets import institutional_stream as IS
from sportsassets.market_plane.sharded_stream import Manager

REF = {"priceScale": "1000", "fractionalQtyScale": "100", "state": "OPEN",
       "productId": "p"}


class _T:
    def __init__(self, b, tok, **kw):
        self.b, self.kw, self.subs = b, kw, []
        self.subscription_mode = IS.MODE_SUBSCRIBE_ALL

    def start(self):
        pass

    def subscribe(self, x):
        self.subs.extend(x)

    def stop(self):
        pass


def _mgr():
    return Manager(token_fn=lambda: "x", max_per_stream=100_000,
                   subscribe_all=True, clock=lambda: 1.0,
                   transport_factory=lambda b, tok, **kw: _T(b, tok, **kw))


def test_unassigned_symbols_release_their_instrument_records():
    m = _mgr()
    m.sync({"a": 0, "b": 0, "c": 0}, {s: REF for s in "abc"})
    books = m.shards[0]["books"]
    assert set(books._instruments) == {"a", "b", "c"}
    got = m.sync({"a": 0}, {"a": REF})
    assert got["removed"] == 2
    assert set(books._instruments) == {"a"}          # was {"a","b","c"}
    assert set(books._markets) == {"a"}


def test_rotation_through_the_catalogue_stays_bounded_by_the_assignment():
    """Subscribe-all rotates symbols in and out; instrument records track
    the current assignment, not every symbol ever seen."""
    m = _mgr()
    for wave in range(20):
        syms = ["w%d-%d" % (wave, i) for i in range(500)]
        m.sync({s: 0 for s in syms}, {s: REF for s in syms})
    books = m.shards[0]["books"]
    assert len(books._instruments) == 500            # not 20 * 500
    assert len(books._markets) == 500


def test_a_symbol_assigned_again_gets_its_record_back_on_the_same_sync():
    m = _mgr()
    m.sync({"held": 0, "x": 0}, {"held": REF, "x": REF})
    m.sync({"x": 0}, {"x": REF})                     # held drops out
    books = m.shards[0]["books"]
    assert "held" not in books._instruments
    m.sync({"held": 0, "x": 0}, {"held": REF, "x": REF})   # and returns
    inst = books._instruments["held"]
    assert (inst["price_scale"], inst["qty_scale"]) == (1000, 100)
    assert "held" in books._markets
