import pytest

from edge.ledger.positions import Fill, Position


def test_buy_hold_resolve_win():
    p = Position()
    p.apply(Fill("BUY", 1000, 0.61))
    assert p.resolve(1.0) == pytest.approx(1000 * 0.39)
    assert p.resolved and p.shares == 0


def test_sell_realizes_on_sale():
    p = Position()
    p.apply(Fill("BUY", 100, 0.50))
    assert p.apply(Fill("SELL", 40, 0.70)) == pytest.approx(40 * 0.20)
    assert p.resolve(0.0) == pytest.approx(60 * -0.50)


def test_oversell_clamped():
    p = Position()
    p.apply(Fill("BUY", 10, 0.50))
    assert p.apply(Fill("SELL", 25, 0.60)) == pytest.approx(10 * 0.10)
    assert p.shares == 0
