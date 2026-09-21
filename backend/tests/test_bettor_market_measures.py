"""Measurements that need no order of ours -- and their honesty.

The standing claim was that BETTOR's economics are unmeasurable until
a BETTOR order rests. That is true of p_fill, queue position, realized
duration and realized P&L. It is NOT true of market activity,
unconditional quote markout or opportunity persistence.

These tests pin two things equally hard:

  1. the measures are RIGHT on data that can answer them; and
  2. they say NOT_IDENTIFIED, with a reason, on data that cannot --
     which is the case for the research capture as it stands.
"""
from __future__ import annotations

from sportsassets import bettor_market_measures as mm


def pt(slug, at, bid, ask, vol=1000.0, state="MARKET_STATE_OPEN"):
    return {"slug": slug, "at": at, "bid": bid, "ask": ask, "vol": vol,
            "state": state}


def ramp(slug, n, *, step=10.0, bid=0.40, ask=0.44, dmid=0.0, dvol=0.0):
    out = []
    for i in range(n):
        out.append(pt(slug, 1_000_000.0 + i * step, round(bid + i * dmid, 6),
                      round(ask + i * dmid, 6), vol=1000.0 + i * dvol))
    return out


# ── series construction ──────────────────────────────────────────────

class TestSeriesConstruction:

    def test_it_reads_both_the_capture_and_the_journal_spellings(self):
        rows = [{"market_id": "m", "observed_at":
                 "2026-09-21T00:00:00+00:00", "yes_bid": "0.40",
                 "yes_ask": "0.44", "stats_shares_traded": "12"},
                {"slug": "m", "at": "2026-09-21T00:00:10+00:00",
                 "bid": "0.41", "ask": "0.45", "vol": "15"}]
        got = mm.to_series(rows)
        assert got["markets"] == 1 and got["points"] == 2

    def test_a_row_without_a_clock_is_dropped_by_name(self):
        got = mm.to_series([{"slug": "m", "bid": 0.4, "ask": 0.44}])
        assert got["dropped"]["no_clock"] == 1
        assert got["points"] == 0

    def test_an_unparsable_clock_is_not_time_zero(self):
        got = mm.to_series([{"slug": "m", "at": "whenever",
                             "bid": 0.4, "ask": 0.44}])
        assert got["dropped"]["no_clock"] == 1

    def test_points_come_back_in_time_order(self):
        got = mm.to_series([pt("m", 30.0, .4, .44), pt("m", 10.0, .4, .44)])
        ats = [p["at"] for p in got["series"]["m"]]
        assert ats == sorted(ats)


# ── coverage: can anything be measured at all ────────────────────────

class TestCoverageIsCheckedBeforeAnythingIsComputed:
    """A measure over one observation per market is not a small
    sample; it is a category error."""

    def test_one_observation_per_market_yields_no_pairs(self):
        s = mm.to_series([pt("m%d" % i, 1.0, .4, .44)
                          for i in range(500)])["series"]
        cov = mm.coverage(s)
        assert cov["markets"] == 500
        assert cov["consecutive_pairs"] == 0
        assert cov["markets_seen_once_only"] == 500
        assert cov["sufficient_for_a_number"] is False

    def test_a_real_series_is_sufficient(self):
        s = mm.to_series(ramp("m", 60))["series"]
        assert mm.coverage(s)["sufficient_for_a_number"] is True


# ── activity ─────────────────────────────────────────────────────────

class TestActivity:

    def test_with_no_pairs_it_refuses_rather_than_returning_zero(self):
        s = mm.to_series([pt("m%d" % i, 1.0, .4, .44)
                          for i in range(400)])["series"]
        out = mm.activity(s)
        assert out["evidence_class"] == mm.NOT_IDENTIFIED
        assert "no interval exists to difference" in out["why"]

    def test_it_differences_the_counter_and_prices_each_interval(self):
        s = mm.to_series(ramp("m", 3, step=10.0, dvol=100.0))["series"]
        out = mm.activity(s, min_pairs=1)
        assert out["evidence_class"] == mm.PUBLIC_COUNTER_DELTA
        assert out["pairs"] == 2
        assert out["shares"] == 200.0
        # mid is 0.42 throughout, so the notional is exact.
        assert out["notional_usd"] == 84.0
        assert out["effective_price_usd"] == 0.42
        assert out["covered_seconds"] == 20.0

    def test_one_interval_is_not_a_measurement(self):
        """The capture holds exactly one qualifying pair; reporting a
        number from it would put an evidence class on a coincidence."""
        s = mm.to_series([pt("m", 0.0, .4, .44, vol=10.0),
                          pt("m", 10.0, .4, .44, vol=99.0)])["series"]
        out = mm.activity(s)
        assert out["evidence_class"] == mm.NOT_IDENTIFIED
        assert out["pairs"] == 1
        assert out["threshold"] == mm.MIN_PAIRS_FOR_A_NUMBER

    def test_a_backwards_step_is_counted_not_clipped(self):
        """max(0, delta) would hide the fact that the counter is not
        what we assumed, which is the whole question."""
        s = mm.to_series([pt("m", 0.0, .4, .44, vol=500.0),
                          pt("m", 10.0, .4, .44, vol=100.0),
                          pt("m", 20.0, .4, .44, vol=150.0)])["series"]
        out = mm.activity(s, min_pairs=1)
        assert out["counter_went_backwards"] == 1
        assert out["shares"] == 50.0
        assert "not monotonically cumulative" in out["counter_semantics"]

    def test_an_unchanged_counter_is_its_own_count(self):
        s = mm.to_series(ramp("m", 4, dvol=0.0))["series"]
        out = mm.activity(s, min_pairs=1)
        assert out["unchanged"] == 3 and out["shares"] == 0.0

    def test_the_daily_extrapolation_carries_its_own_warning(self):
        s = mm.to_series(ramp("m", 3, step=10.0, dvol=100.0))["series"]
        out = mm.activity(s, min_pairs=1)
        assert out["extrapolated_usd_per_day"] == round(84.0 / 20 * 86400, 2)
        assert "assumes the unobserved time trades like" in out[
            "extrapolation_warning"]
        assert out["covered_seconds"] == 20.0


# ── markout ──────────────────────────────────────────────────────────

class TestHypotheticalMarkout:

    def test_it_is_never_labelled_adverse_selection(self):
        out = mm.markout(mm.to_series(ramp("m", 80, dmid=0.001))["series"])
        assert out["conditional_on_fill"] is False
        assert out["is_adverse_selection"] is False
        assert "CONDITIONAL ON BEING FILLED" in out["note"]

    def test_a_rising_mid_is_favourable_to_a_resting_buy(self):
        # step 10 s, mid rises 0.001 per step -> +0.003 over 30 s, plus
        # the half-spread the quote sits below the mid.
        s = mm.to_series(ramp("m", 200, step=10.0, dmid=0.001))["series"]
        out = mm.markout(s, side="buy")
        h = out["horizons"]["30s"]
        assert h["evidence_class"] == mm.PUBLIC_OBSERVATION
        assert h["mean_usd_per_share"] > 0

    def test_the_drift_is_the_part_that_flips_with_the_side(self):
        """Both sides show a POSITIVE markout in a drifting market,
        because a quote sits half a spread from the mid and that
        half-spread is what a maker is paid. Only the DRIFT component
        is adverse to one side, and reporting the single number alone
        is how a rising market gets read as good for both sides."""
        s = mm.to_series(ramp("m", 200, step=10.0, dmid=0.001))["series"]
        buy = mm.markout(s, side="buy")["horizons"]["30s"]
        sell = mm.markout(s, side="sell")["horizons"]["30s"]
        assert buy["mean_usd_per_share"] > 0
        assert sell["mean_usd_per_share"] > 0      # the half-spread
        assert buy["mean_drift_usd_per_share"] > 0
        assert sell["mean_drift_usd_per_share"] < 0
        assert abs(buy["mean_half_spread_usd_per_share"] - 0.02) < 1e-9
        assert abs(sell["mean_half_spread_usd_per_share"] - 0.02) < 1e-9

    def test_markout_is_exactly_half_spread_plus_drift(self):
        s = mm.to_series(ramp("m", 200, step=10.0, dmid=0.001))["series"]
        h = mm.markout(s, side="buy")["horizons"]["60s"]
        assert abs(h["mean_usd_per_share"]
                   - (h["mean_half_spread_usd_per_share"]
                      + h["mean_drift_usd_per_share"])) < 1e-6

    def test_a_horizon_with_too_few_pairs_says_so(self):
        s = mm.to_series(ramp("m", 5, step=10.0))["series"]
        h = mm.markout(s)["horizons"]["300s"]
        assert h["evidence_class"] == mm.NOT_IDENTIFIED
        assert "fewer than" in h["why"]

    def test_a_far_away_point_does_not_answer_a_near_horizon(self):
        """Without a tolerance a 24-hour gap is reported as a
        10-second markout."""
        s = mm.to_series([pt("m", 0.0, .4, .44),
                          pt("m", 86400.0, .9, .94)])["series"]
        out = mm.markout(s)
        assert all(v["n"] == 0 for v in out["horizons"].values())
        assert out["evidence_class"] == mm.NOT_IDENTIFIED


# ── persistence ──────────────────────────────────────────────────────

class TestOpportunityPersistence:

    def qualifying(self, n, **kw):
        # spread 4 ticks, ask 0.44 <= 0.50, volume 1000 >= 100.
        return ramp("m", n, bid=0.40, ask=0.44, **kw)

    def test_it_is_never_called_a_quote_lifetime(self):
        out = mm.persistence(mm.to_series(self.qualifying(80))["series"])
        assert out["is_quote_lifetime"] is False
        assert "queue position is not observable" in out["note"]

    def test_a_run_that_is_still_open_is_censored_not_counted(self):
        """The series ending is a fact about us, not about the
        market."""
        out = mm.persistence(mm.to_series(self.qualifying(50))["series"])
        assert out["runs"] == 1 and out["runs_censored"] == 1
        assert out["evidence_class"] == mm.NOT_IDENTIFIED
        assert "censored by the series ending" in out["why"]

    def test_a_run_that_ends_is_measured(self):
        rows = []
        for k in range(40):
            slug = "m%d" % k
            rows += [pt(slug, 0.0, .40, .44), pt(slug, 25.0, .40, .44),
                     # ask above MAX_PRICE ends the run
                     pt(slug, 30.0, .60, .70)]
        out = mm.persistence(mm.to_series(rows)["series"])
        assert out["evidence_class"] == mm.PUBLIC_OBSERVATION
        assert out["runs_censored"] == 0
        assert out["seconds"]["median"] == 25.0

    def test_it_applies_the_FROZEN_universe_rule(self):
        import sportsassets.bettor_universe as uni
        out = mm.persistence(mm.to_series(self.qualifying(4))["series"])
        assert out["universe_rule"] == uni.UNIVERSE_VERSION
        # A one-tick book never qualifies, so there is no run at all.
        thin = mm.to_series(ramp("m", 10, bid=0.40, ask=0.405))["series"]
        assert mm.persistence(thin)["runs"] == 0


# ── the whole report, and its honesty ────────────────────────────────

class TestTheReportIsHonestAboutWhatItCannotDo:

    def test_a_sampler_that_sees_each_market_once_answers_nothing(self):
        """This is the research capture's actual shape: measured
        2026-09-21, 617 of 620 markets observed exactly once."""
        rows = [pt("m%d" % i, 1_000_000.0 + i, .40, .44) for i in range(620)]
        out = mm.measure_all(rows)
        assert out["coverage"]["consecutive_pairs"] == 0
        assert out["activity"]["evidence_class"] == mm.NOT_IDENTIFIED
        assert out["markout"]["evidence_class"] == mm.NOT_IDENTIFIED
        assert out["persistence"]["evidence_class"] == mm.NOT_IDENTIFIED

    def test_the_two_fill_conditioned_quantities_stay_out_of_reach(self):
        out = mm.measure_all(ramp("m", 200, step=10.0, dmid=0.001,
                                  dvol=10.0))
        assert "p_fill" in out["still_needs_our_orders"]
        assert any("CONDITIONAL" in s.upper()
                   for s in out["still_needs_our_orders"])
        # ...while the three that do not need an order ARE measured.
        assert out["activity"]["evidence_class"] == mm.PUBLIC_COUNTER_DELTA
        assert out["markout"]["evidence_class"] == mm.PUBLIC_OBSERVATION

    def test_describe_names_both_lists(self):
        d = mm.describe()
        assert "p_fill" in d["not_measurable_without_our_orders"]
        assert any("markout" in s
                   for s in d["measurable_without_our_orders"])
        assert "617 of 620" in d["what_the_capture_cannot_do"]
