"""'Same or better price' is an owner requirement with no instrument.

Stated in his own words and repeated:

    "I want to copy as many of their actual trades (buys and sells) as
     possible (same or better price)"

The only thing measuring it was one median on the status page:

    percentile_cont(0.5) ... ORDER BY (fill_price - his_price) * 100

and that number is wrong for an entire class of copy. On a SHORT the
venue's fill_price names the LONG leg while his_price is what he paid
for the short leg, so the difference is not slippage — it is roughly
(1 - 2p). On the six shorts we filled it read as about 66 cents of
slippage that never happened. Same misdenomination family as
`spent = filled * fill_price`; one more place it was left unconverted.
"""

import pytest

from sportsassets.analytics import price_fidelity as pf

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"


@pytest.fixture(autouse=True)
def _short_model_armed(monkeypatch):
    from sportsassets import live_executor as le

    monkeypatch.setattr(le, "short_model_confirmed", lambda: True)


class TestTheEdgeIsSignedTheRightWay:
    def test_paying_less_than_him_is_positive(self):
        assert pf.fill_edge(0.50, 0.45, LONG) == pytest.approx(0.05)

    def test_paying_more_than_him_is_negative(self):
        assert pf.fill_edge(0.50, 0.55, LONG) == pytest.approx(-0.05)

    def test_matching_him_is_zero(self):
        assert pf.fill_edge(0.50, 0.50, LONG) == 0.0


class TestTheShortDenomination:
    """The whole reason this module exists."""

    def test_a_short_is_scored_on_what_it_actually_COST(self):
        """He paid 0.23 for the short leg. Our fill came back at 0.89
        naming the LONG leg, so it cost us 0.11. We beat him by 12
        cents — the old formula called it 66 cents of slippage."""
        assert pf.fill_edge(0.23, 0.89, SHORT) == pytest.approx(0.12)
        naive = 0.89 - 0.23
        assert naive == pytest.approx(0.66)

    def test_the_long_formula_is_unchanged_for_longs(self):
        assert pf.fill_edge(0.23, 0.89, LONG) == pytest.approx(-0.66)

    def test_it_reuses_fill_cash_rather_than_restating_the_rule(self):
        import inspect

        src = inspect.getsource(pf.fill_edge)
        assert "fill_cash" in src, \
            "a second copy of the denomination rule is how the first "\
            "one got missed"

    def test_a_disarmed_short_model_falls_back_with_everything_else(
            self, monkeypatch):
        from sportsassets import live_executor as le

        monkeypatch.setattr(le, "short_model_confirmed", lambda: False)
        assert pf.fill_edge(0.23, 0.89, SHORT) == pytest.approx(-0.66)


class TestUnmeasurableIsNotNeutral:
    """A pile of zeros drags every average toward 'we matched him
    exactly', which is the most flattering possible wrong answer."""

    @pytest.mark.parametrize("hp,fp", [
        (None, 0.5), (0.5, None), (0.0, 0.5), (0.5, 0.0),
        (1.0, 0.5), (0.5, 1.0), ("x", 0.5), (0.5, "x"),
    ])
    def test_it_returns_none_not_zero(self, hp, fp):
        assert pf.fill_edge(hp, fp, LONG) is None

    def test_unmeasurable_rows_are_counted_separately(self):
        r = pf.assess([
            {"his_price": 0.5, "fill_price": 0.45, "intent": LONG,
             "filled_shares": 10},
            {"his_price": None, "fill_price": 0.45, "intent": LONG,
             "filled_shares": 10},
        ])
        assert r["n"] == 1
        assert r["unmeasurable"] == 1

    def test_all_unmeasurable_says_so_rather_than_scoring_zero(self):
        r = pf.assess([{"his_price": None, "fill_price": None}])
        assert "NO MEASURABLE FILLS" in r["verdict"]
        assert "at_or_better_share" not in r


class TestItReportsADistributionNotAMedian:
    """'Same or better on 70%' and 'on 99%' have the same median when
    the tail is one-sided, and the tail is where the money is."""

    def _rows(self, edges):
        return [{"his_price": 0.50, "fill_price": 0.50 - e,
                 "intent": LONG, "filled_shares": 100} for e in edges]

    def test_the_at_or_better_share_is_reported(self):
        r = pf.assess(self._rows([0.05, 0.05, 0.05, -0.05]))
        assert r["at_or_better"] == 3
        assert r["at_or_better_share"] == 0.75

    def test_an_exact_match_counts_as_at_or_better(self):
        r = pf.assess(self._rows([0.0]))
        assert r["at_or_better"] == 1

    def test_a_sub_tick_difference_counts_as_the_same_price(self):
        r = pf.assess(self._rows([-0.0005]))
        assert r["at_or_better"] == 1

    def test_the_worst_fill_is_always_reported(self):
        r = pf.assess(self._rows([0.05] * 99 + [-0.30]))
        assert r["worst_edge_cents"] == pytest.approx(-30.0, abs=0.01)
        assert r["at_or_better_share"] == 0.99

    def test_the_median_of_two_is_not_the_worse_of_two(self):
        """Nearest-rank would report the worse fill as the median on
        exactly the small samples this runs on first."""
        r = pf.assess(self._rows([0.05, -0.05]))
        assert r["median_edge_cents"] == pytest.approx(0.0, abs=0.01)


class TestTheDollarWeightingIsTheHeadline:
    def test_a_bad_price_on_a_big_clip_outweighs_a_good_one_on_a_small(self):
        rows = [
            {"his_price": 0.50, "fill_price": 0.45, "intent": LONG,
             "filled_shares": 10},      # +5c on 10 shares  = +$0.50
            {"his_price": 0.50, "fill_price": 0.55, "intent": LONG,
             "filled_shares": 1000},    # -5c on 1000       = -$50.00
        ]
        r = pf.assess(rows)
        assert r["at_or_better_share"] == 0.5
        assert r["dollar_edge_vs_his_price"] == pytest.approx(-49.5)

    def test_it_normalises_per_DOLLAR_deployed_not_per_share(self):
        """A cent saved on a 4-cent contract is a far bigger edge than
        a cent saved on a 90-cent one, and only the dollar denominator
        says so."""
        cheap = pf.assess([{"his_price": 0.05, "fill_price": 0.04,
                            "intent": LONG, "filled_shares": 100}])
        dear = pf.assess([{"his_price": 0.90, "fill_price": 0.89,
                           "intent": LONG, "filled_shares": 100}])
        assert cheap["dollar_edge_vs_his_price"] == pytest.approx(
            dear["dollar_edge_vs_his_price"])
        assert cheap["edge_per_100_deployed"] > \
            dear["edge_per_100_deployed"] * 5

    def test_deployed_uses_the_short_denomination_too(self):
        r = pf.assess([{"his_price": 0.23, "fill_price": 0.89,
                        "intent": SHORT, "filled_shares": 100}])
        assert r["deployed"] == pytest.approx(11.0)


class TestTheQueryReadsTheIntent:
    def test_it_uses_the_shared_intent_expression(self):
        import inspect

        src = inspect.getsource(pf.cohort_fidelity)
        assert "ORDER_INTENT_SQL" in src, \
            "without the intent every short is scored with long math"

    def test_the_sleeve_is_isolated_from_desk_and_underdog(self):
        import inspect

        src = inspect.getsource(pf.cohort_fidelity)
        assert "NOT IN ('manual', 'underdog')" in src

    def test_only_actual_fills_are_scored(self):
        import inspect

        src = inspect.getsource(pf.cohort_fidelity)
        assert "fill_price IS NOT NULL" in src
        assert "filled_shares, 0) > 0" in src
