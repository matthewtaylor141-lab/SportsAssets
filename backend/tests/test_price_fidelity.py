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


class TestSubCentSlippageIsVisible:
    """The instrument quantized our cost to the whole cent.

    fill_edge asked for our per-share cost by calling
    fill_cash(1.0, price, intent), and fill_cash ends in
    round(shares * per, 2) -- so a one-share call rounded the RATE to a
    cent before subtracting the whale's price. Everything assess()
    produces is built on that: at_or_better, at_or_better_share, the
    median/p10/p90/worst edge in cents, dollar_edge_vs_his_price,
    edge_per_100_deployed, and the owner-facing verdict string.

    Production fill prices are not cent-aligned -- submit_fok returns
    round(notional / filled, 4), a VWAP across executions, and the
    venue's own receipts in this repo include 0.6853. So this was live
    on ordinary fills.

    Every price in the twenty-one tests above this one is a whole cent,
    which is exactly why the suite could not see it. These use the
    prices production actually produces.
    """

    def test_a_half_cent_overpay_is_no_longer_invisible(self):
        e = pf.fill_edge(0.68, 0.6849, None)
        assert e is not None
        assert e == pytest.approx(-0.0049, abs=1e-9), \
            "a real overpay reported as exactly zero"

    def test_it_does_not_FABRICATE_slippage_either(self):
        """The error ran both ways: 0.6853 against 0.68 is a true
        -0.53c, and the quantizer reported -1.00c."""
        e = pf.fill_edge(0.68, 0.6853, None)
        assert e == pytest.approx(-0.0053, abs=1e-9)
        assert e > -0.01

    def test_the_epsilon_can_actually_fire_now(self):
        """SAME_PRICE_EPS is 0.001 -- a tenth of a cent. Against a
        cent-quantized input it could never be the deciding rule."""
        assert pf.SAME_PRICE_EPS == 0.001
        e = pf.fill_edge(0.68, 0.6805, None)
        assert abs(e) < pf.SAME_PRICE_EPS

    def test_the_day_that_read_as_perfect(self):
        """70 fills of 365 shares, his 0.68, our VWAP 0.6849. This
        reported at_or_better 70/70, median +0.00c, worst +0.00c and
        '$+0.00 versus paying exactly what he paid'."""
        rows = [{"his_price": 0.68, "fill_price": 0.6849,
                 "intent": None, "filled_shares": 365.0}
                for _ in range(70)]
        b = pf.assess(rows)
        assert b["at_or_better"] == 0, \
            "70 fills of real slippage still scoring as at-or-better"
        assert b["dollar_edge_vs_his_price"] == pytest.approx(-125.19,
                                                              abs=0.5)
        assert b["median_edge_cents"] == pytest.approx(-0.49, abs=0.01)

    def test_a_genuinely_better_fill_still_scores_better(self):
        rows = [{"his_price": 0.68, "fill_price": 0.6751,
                 "intent": None, "filled_shares": 365.0}]
        b = pf.assess(rows)
        assert b["at_or_better"] == 1
        assert b["dollar_edge_vs_his_price"] > 0

    def test_the_short_denomination_is_preserved(self):
        """A short's cost is (1 - price). The split must not have
        quietly turned every fill into long math."""
        from sportsassets import live_executor as le

        assert le.cost_per_share(0.30, None) == pytest.approx(0.30)
        if le.short_model_confirmed():
            assert le.cost_per_share(
                0.30, "ORDER_INTENT_BUY_SHORT") == pytest.approx(0.70)


class TestTheTwoCallersStayInAgreement:
    def test_fill_cash_is_cost_per_share_times_shares(self):
        """One definition. fill_cash keeps its cent rounding, which is
        right for a dollar total; a caller wanting a RATE takes it
        unrounded from cost_per_share."""
        from sportsassets import live_executor as le

        for px in (0.6849, 0.6853, 0.23, 0.995, 0.005):
            for sh in (1.0, 365.0, 1086.0):
                assert le.fill_cash(sh, px, None) == pytest.approx(
                    round(sh * le.cost_per_share(px, None), 2),
                    abs=1e-9)

    def test_fill_cash_still_rounds_dollars(self):
        from sportsassets import live_executor as le

        assert le.fill_cash(1086.0, 0.89, None) == pytest.approx(966.54)
        assert le.fill_cash(781.0, 0.6853, None) == pytest.approx(535.22)

    def test_fill_edge_no_longer_routes_through_fill_cash(self):
        import inspect

        # CODE only. The comment above the fix names fill_cash on
        # purpose, to record what it used to call.
        src = inspect.getsource(pf.fill_edge)
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("#"))
        assert "fill_cash" not in code
        assert "cost_per_share" in code
