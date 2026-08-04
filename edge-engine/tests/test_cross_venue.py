"""Cross-venue arbitrage: one leg per venue, all-or-nothing by construction.

The venues' order semantics differ — Polymarket US FOK is atomic, Kalshi's
short-expiry limit can partially fill — so the executor orders legs by
ATOMICITY: the non-atomic venue goes first and whatever it actually fills
becomes the set size, closed exactly by the FOK venue. Fees are inside the
cost: Kalshi's 0.07*p*(1-p) taker fee is real money and omitting it is how
a guaranteed profit becomes a guaranteed loss.
"""

import pytest

from edge.execution.arbitrage import (
    XVLeg,
    cross_venue_cost,
    execute_cross_venue,
)


class _Venue:
    def __init__(self, name, fills=None, fee=0.0, explode=False):
        self.name = name
        self.calls = []
        self._fills = list(fills or [])
        self._fee = fee
        self._explode = explode

    def taker_fee(self, price):
        return self._fee * price * (1.0 - price) if self._fee else 0.0

    def place_order(self, token, price, count, **kwargs):
        self.calls.append({"token": token, "price": price, "count": count})
        if self._explode:
            raise RuntimeError("venue down")
        filled = self._fills.pop(0) if self._fills else count
        return {"ok": filled > 0, "count": filled, "price": price,
                "status": "filled" if filled else "unfilled"}


def _legs(pm_fills=None, k_fills=None, pm_price=0.55, k_price=0.40,
          k_explode=False, size=50):
    pm = _Venue("polymarket-us", fills=pm_fills)
    ka = _Venue("kalshi", fills=k_fills, fee=0.07, explode=k_explode)
    return (pm, ka,
            [XVLeg(adapter=pm, token="pm-tok", outcome="Lakers",
                   price=pm_price, size=size),
             XVLeg(adapter=ka, token="KXTOK", outcome="Celtics",
                   price=k_price, size=size)])


def test_cost_includes_the_kalshi_fee():
    _, _, legs = _legs()
    # 0.55 + 0.40 + 0.07*0.40*0.60 = 0.9668
    assert cross_venue_cost(legs) == pytest.approx(0.9668, abs=1e-4)


def test_complete_set_fires_kalshi_first_then_fok_closes():
    pm, ka, legs = _legs()
    res = execute_cross_venue(event="e", legs=legs, max_sets=3, dry_run=False)
    assert res.ok and res.status == "complete"
    assert ka.calls and pm.calls, "both venues must be hit"
    assert res.sets == 3
    # Non-atomic venue strictly before the FOK closer.
    assert res.orders[0]["token"] == "KXTOK"
    assert res.profit == pytest.approx(3 * (1.0 - res.paid), abs=1e-6)


def test_kalshi_partial_shrinks_the_set_instead_of_stranding_it():
    pm, ka, legs = _legs(k_fills=[2])
    res = execute_cross_venue(event="e", legs=legs, max_sets=5, dry_run=False)
    assert res.ok and res.sets == 2
    assert pm.calls[0]["count"] == 2, "FOK closes exactly what actually filled"


def test_first_leg_zero_fill_costs_nothing():
    pm, ka, legs = _legs(k_fills=[0])
    res = execute_cross_venue(event="e", legs=legs, max_sets=3, dry_run=False)
    assert res.status == "no_fills"
    assert not pm.calls, "the closer must never fire without the first leg"


def test_closer_failure_retries_capped_then_names_the_exposure():
    pm, ka, legs = _legs(pm_fills=[0, 0])
    res = execute_cross_venue(event="e", legs=legs, max_sets=1, dry_run=False)
    assert res.status == "INCOMPLETE_EXPOSED"
    assert len(pm.calls) == 2, "one full-price try + one capped completion"
    assert pm.calls[1]["price"] <= 0.99
    assert res.exposed and "kalshi" in res.exposed[0]


def test_an_exploding_venue_is_a_failed_leg_not_a_crash():
    pm, ka, legs = _legs(k_explode=True)
    res = execute_cross_venue(event="e", legs=legs, max_sets=1, dry_run=False)
    assert res.status == "no_fills"
    assert not pm.calls


def test_same_venue_legs_are_refused():
    pm, _, _ = _legs()
    legs = [XVLeg(adapter=pm, token="a", outcome="A", price=0.4, size=9),
            XVLeg(adapter=pm, token="b", outcome="B", price=0.4, size=9)]
    res = execute_cross_venue(event="e", legs=legs, max_sets=1, dry_run=False)
    assert res.status == "not_cross_venue"


def test_implausible_profit_is_a_mapping_error_not_free_money():
    _, _, legs = _legs(pm_price=0.30, k_price=0.30)
    res = execute_cross_venue(event="e", legs=legs, max_sets=1, dry_run=False)
    assert res.status == "implausible_profit"


def test_dry_run_places_nothing_and_reports_the_economics():
    pm, ka, legs = _legs()
    res = execute_cross_venue(event="e", legs=legs, max_sets=2, dry_run=True)
    assert res.ok and res.status == "dry_run"
    assert not pm.calls and not ka.calls


def test_feed_team_resolution_joins_the_venues():
    from edge.shadow.runner import _xv_feed_team

    class _Ev:
        home, away = "Los Angeles Lakers", "Boston Celtics"

    assert _xv_feed_team("Los Angeles Lakers", _Ev) == "Los Angeles Lakers"
    assert _xv_feed_team("[h1] Los Angeles Lakers", _Ev) is None, "segments out"
    assert _xv_feed_team("Over 210.5", _Ev) is None, "totals are not teams"
    assert _xv_feed_team("Draw", _Ev) is None
