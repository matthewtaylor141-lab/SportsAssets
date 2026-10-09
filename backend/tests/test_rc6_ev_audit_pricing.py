"""EV AND PRICING METHODOLOGY AUDIT (RC6 lane ev-audit): pure pricing defects.

  §1  DE-VIG INPUTS. A decimal price is a finite number above 1.0. NaN passed
      the old `float(o) <= 1.0` check (every NaN comparison is False) and came
      back as probability NaN with no refusal; +inf priced its outcome at
      exactly 0; a boolean is an int. Each is now ODDS_NOT_A_PRICE.
  §2  THE PMUS FEE SCHEDULE AS PUBLISHED. https://docs.polymarket.us/fees,
      re-read 2026-10-09: the Table Tennis coefficient 0.10 takes effect "at
      12:00 AM ET on Wednesday, October 7, 2026" (the page read 11:59 PM ET
      Sep 30 on 2026-09-27), and the combo curve's (1 - p)^4 coefficient is
      0.06 (pinned by the page's own example: $19.25 for 1,000 at $0.50).
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from sportsassets import bettor_pinnacle_devig as D
from sportsassets import calibration_fees as CF


# ── §1 de-vig inputs ─────────────────────────────────────────────────

def _val(odds):
    now = time.time()
    contract = {"sport_family": "baseball", "market": "h2h",
                "selection": "A", "event_key": "e1"}
    quote = {"book": "pinnacle", "event_key": "e1", "outcomes": odds,
             "observed_at": now - 1.0}
    return D.valuation(contract=contract, quote=quote, now=now)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True])
def test_a_non_price_odds_value_is_refused_never_a_probability(bad):
    v = _val({"A": bad, "B": 1.9})
    assert v["probability"] is None, v
    assert D.R_BAD_ODDS in v["refusals"], v


def test_a_finite_price_above_one_still_prices():
    v = _val({"A": 2.0, "B": 2.0})
    assert v["refusals"] == [] and v["probability"] == pytest.approx(0.5)
    v = _val({"A": 1.5, "B": 2.9})
    assert 0.0 < v["probability"] < 1.0


# ── §2 the PMUS fee schedule as published on 2026-10-09 ──────────────

def test_table_tennis_takes_its_own_coefficient_at_the_published_instant():
    # 12:00 AM ET (EDT) on Wednesday 2026-10-07 is 04:00Z
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-10-07T03:59:59Z") == Decimal("0.0695")
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-10-07T04:00:00Z") == Decimal("0.10")
    # the superseded Sep-30 instant no longer moves the coefficient
    assert CF.taker_coefficient("TABLE_TENNIS",
                                "2026-10-03T12:00:00Z") == Decimal("0.0695")
    assert CF.TABLE_TENNIS_SUPERSEDED_INSTANT["superseded_by"] == \
        "12:00 AM ET on Wednesday, October 7, 2026"
    # every other sport is unchanged
    assert CF.taker_coefficient(None, "2026-10-08T00:00:00Z") == Decimal("0.0695")


def test_the_combo_curve_text_is_the_published_one_and_still_refuses():
    assert "0.06 x (1 - p)^4" in CF.COMBO_CURVE_PUBLISHED
    # the page's own example: 1,000 combo contracts at $0.50 cost $19.25
    c, p = Decimal(1000), Decimal("0.5")
    fee = c * p * (Decimal("0.0695") * (1 - p) + Decimal("0.06") * (1 - p) ** 4)
    assert fee == Decimal("19.25")
    got = CF.combo_fee(1000, "0.50")
    assert got["FEE"] is None
    assert got["BLOCKER"] == CF.R_COMBO_CURVE_NOT_IMPLEMENTED
    assert got["published_curve"] == CF.COMBO_CURVE_PUBLISHED


# ── §3 capacity: no extrapolation, no dropped sizes, no double count ──
#
# controls.capacity / readiness.capacity_points_from_rows. Production RC5
# (research read 2026-10-09): 1,717 bind evaluations in 14 days, every one
# CASH, best EV per contract -0.0006 -- so NO_POSITIVE_CAPACITY is economic
# and true. These overstatements were hidden by it and would have inflated
# the first positive frontier.

def _ev(qty, *, ev=0.03, fp=0.9, at=0.0, slug="m1", strategy="S",
        fixture="fx-1", px=0.4):
    return {"strategy": strategy, "us_market_slug": slug,
            "holding_side": "LONG", "fixture": fixture, "qty_in": qty,
            "ev": ev, "fp": fp, "net": ev * qty, "px": px, "h": 2.0,
            "at": at}


def test_a_point_claims_the_largest_size_evaluated_never_the_bucket_edge():
    from sportsassets.redteam import readiness as R
    pts = R.capacity_points_from_rows([_ev(1, at=1), _ev(2, at=2,
                                                         slug="m2")])
    assert [p["bucket"] for p in pts] == [[1, 10]]
    assert pts[0]["qty"] == 2          # not 10: no extrapolation from 1-2


def test_a_fractional_size_between_integer_edges_is_bucketed_not_dropped():
    from sportsassets.redteam import readiness as R
    pts = R.capacity_points_from_rows([_ev(10.5, at=1)])
    assert [p["bucket"] for p in pts] == [[11, 50]]
    assert pts[0]["evaluations"] == 1


def test_re_evaluations_are_one_opportunity_at_its_latest_evaluation():
    from sportsassets.redteam import readiness as R
    rows = [_ev(7, ev=-0.01, at=i) for i in range(50)] + [_ev(7, ev=0.03,
                                                              at=99)]
    p = R.capacity_points_from_rows(rows)[0]
    assert p["evaluations"] == 51 and p["opportunities"] == 1
    assert p["capital_usd"] == round(7 * 0.4, 2)
    assert p["expected_net"] == round(0.03 * 7, 4)


def test_proven_capital_counts_an_opportunity_once_and_only_when_eligible():
    from sportsassets.redteam import controls as C
    from sportsassets.redteam import readiness as R
    # two opportunities (two fixtures, so each bucket has a lower bound),
    # each evaluated at 8 AND at 40 contracts: both buckets are eligible
    rows = [_ev(8, at=1), _ev(8, at=1, slug="m2", fixture="fx-2"),
            _ev(40, at=2), _ev(40, at=2, slug="m2", fixture="fx-2")]
    pts = R.capacity_points_from_rows(rows)
    assert [p["bucket"] for p in pts] == [[1, 10], [11, 50]]
    c = C.capacity(pts, requested_usd=500000)
    # each is ONE position at its largest eligible size (40 x 0.4 = 16):
    # 32, never 3.2 + 3.2 + 16 + 16 = 38.4
    assert c["evidence"]["proven_positive_capacity_usd"] == "32.0"
    # a smaller-size point the frontier refuses on fill probability adds no
    # capital, even below the largest positive size
    low = R.capacity_points_from_rows([
        _ev(8, fp=0.5, at=1), _ev(8, fp=0.5, at=1, slug="m2",
                                  fixture="fx-2"),
        _ev(40, at=2, slug="m3", fixture="fx-3"),
        _ev(40, at=2, slug="m4", fixture="fx-4")])
    c2 = C.capacity(low, requested_usd=500000)
    assert c2["evidence"]["max_positive_qty"] == 40
    assert c2["evidence"]["proven_positive_capacity_usd"] == "32.0"


def test_a_point_refused_on_fill_probability_adds_no_proven_capital():
    """The frontier's own rule (lower bound > 0, fill probability >= 0.80,
    expected net > 0) is the only eligibility: proven capital used to skip
    the fill-probability leg for any point below the largest positive
    size."""
    from sportsassets.redteam import controls as C
    pts = [{"qty": 8, "lb_ev_per_contract": "0.03", "fill_probability": "0.5",
            "capital_hours": "1", "expected_net": "0.5", "capital_usd": "6.4"},
           {"qty": 40, "lb_ev_per_contract": "0.03",
            "fill_probability": "0.9", "capital_hours": "1",
            "expected_net": "2.4", "capital_usd": "32"}]
    c = C.capacity(pts, requested_usd=500000)
    assert c["evidence"]["max_positive_qty"] == 40
    assert c["evidence"]["proven_positive_capacity_usd"] == "32"
    assert c["evidence"]["maximum_deployment_usd"] == "32"
