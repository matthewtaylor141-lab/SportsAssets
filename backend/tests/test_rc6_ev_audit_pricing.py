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
