"""The inning cover: Tie(seg) + Over 0.5(seg) >= $1 payout on every
branch (owner design 2026-08-09). A cover, not a partition — the scored
tie pays both legs and the arithmetic deliberately ignores that upside."""

import tempfile

import pytest

from edge.analysis.consistency import Leg, find_cover_book
from edge.ledger.service import Ledger
from edge.shadow.runner import _try_arbitrage


def _leg(outcome, token, price, size=40):
    return Leg(outcome=outcome, token=token, price=price, size=size)


def test_cover_locks_when_the_pair_prices_under_a_dollar():
    b = find_cover_book("e", "i1", _leg("[i1] Tie", "t1", 0.53),
                        _leg("[i1] Over 0.5", "t2", 0.45))
    assert b is not None
    assert b.cost == 0.98 and b.profit_per_set == 0.02
    assert b.kind == "cover i1" and b.sets == 40


def test_cover_refuses_thin_margins_fees_and_junk():
    tie, over = _leg("[i1] Tie", "t1", 0.53), _leg("[i1] Over 0.5", "t2", 0.45)
    # At or past $1 there is nothing locked (module floor is 1c).
    assert find_cover_book("e", "i1", tie,
                           _leg("[i1] Over 0.5", "t2", 0.47)) is None
    # A Kalshi-scale fee on both legs eats the whole window.
    assert find_cover_book("e", "i1", tie, over,
                           fee_per_contract=0.0175) is None
    # Same token twice, bad price, no depth: all refused.
    assert find_cover_book("e", "i1", tie,
                           _leg("[i1] Over 0.5", "t1", 0.45)) is None
    assert find_cover_book("e", "i1", _leg("[i1] Tie", "t1", 0.0),
                           over) is None
    assert find_cover_book("e", "i1", tie,
                           _leg("[i1] Over 0.5", "t2", 0.45, size=0)) is None


class _Ev:
    home, away = "Orioles", "Rangers"
    sport_key = "baseball_mlb"
    league_code = "mlb"
    h2h = {"a": 1, "b": 2}

    def event_key(self):
        return "bal-tex"


class _Pmus:
    name = "polymarket-us"

    def place_order(self, *a, **kw):  # dry-run never reaches this
        raise AssertionError("dry run must not place")


@pytest.fixture()
def led():
    return Ledger(db_path=tempfile.mkdtemp() + "/l.sqlite3")


def _legs(adapter, tie_price=0.53, over_price=0.45, seg="i1"):
    return [
        (adapter, "m1", _leg(f"[{seg}] Tie", "tok-tie", tie_price)),
        (adapter, "m1", _leg(f"[{seg}] Over 0.5", "tok-over", over_price)),
        (adapter, "m1", _leg("Orioles", "tok-bal", 0.46)),
        (adapter, "m1", _leg("Rangers", "tok-tex", 0.56)),
    ]


def test_try_arbitrage_fires_the_cover_in_dry_run(led, monkeypatch):
    monkeypatch.setenv("EDGE_COVER_ARB", "1")
    funnel: dict = {}
    fired = _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus()),
                           expected=2, sets=1, dry_run=True, funnel=funnel,
                           max_usd=50.0)
    assert fired and funnel.get("cover_found") == 1
    assert funnel["arb_books"][0]["cost"] == 0.98


def test_cover_deploys_dark_by_default(led, monkeypatch):
    """Until the adversarial sign-off flips EDGE_COVER_ARB, a real lock
    is COUNTED and PRICED in telemetry but never bought."""
    monkeypatch.delenv("EDGE_COVER_ARB", raising=False)
    funnel: dict = {}
    fired = _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=_legs(_Pmus()),
                           expected=2, sets=1, dry_run=True, funnel=funnel,
                           max_usd=50.0)
    assert not fired and funnel.get("cover_dark") == 1
    assert funnel["cover_locks_seen"][0]["cost"] == 0.98


def test_cover_never_pairs_across_segments_or_sports(led):
    funnel: dict = {}
    # Tie in i1 but the only Over 0.5 is i2: no pair, nothing fires.
    legs = [
        (_Pmus(), "m1", _leg("[i1] Tie", "tok-tie", 0.53)),
        (_Pmus(), "m1", _leg("[i2] Over 0.5", "tok-over", 0.45)),
    ]
    assert not _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=legs,
                              expected=2, sets=1, dry_run=True,
                              funnel=funnel, max_usd=50.0)

    class _Soccer(_Ev):
        sport_key = "soccer_epl"

    # Same shape on soccer: 'Over 0.5 goals' does not mean 'someone
    # scored in a way that excludes 0-0 ties' — refused by sport gate.
    assert not _try_arbitrage(ledger=led, ev=_Soccer(),
                              venue_legs=_legs(_Pmus()), expected=3, sets=1,
                              dry_run=True, funnel={}, max_usd=50.0)


def test_cover_refuses_the_fee_loaded_venue(led):
    class _Kalshi(_Pmus):
        name = "kalshi"

        def taker_fee(self, price):
            return 0.07 * price * (1 - price)

    assert not _try_arbitrage(ledger=led, ev=_Ev(),
                              venue_legs=_legs(_Kalshi()), expected=2,
                              sets=1, dry_run=True, funnel={}, max_usd=50.0)


def test_under_half_never_masquerades_as_the_over(led):
    legs = [
        (_Pmus(), "m1", _leg("[i1] Tie", "tok-tie", 0.40)),
        (_Pmus(), "m1", _leg("[i1] Under 0.5", "tok-under", 0.40)),
    ]
    # Tie + NRFI is the owner's original (uncovered) construction — the
    # scanner must never build it: a team winning the inning beats both.
    assert not _try_arbitrage(ledger=led, ev=_Ev(), venue_legs=legs,
                              expected=2, sets=1, dry_run=True, funnel={},
                              max_usd=50.0)
