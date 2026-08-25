"""The instrument that decides whether the strategy is proven.

This is the number the owner will act on, so the tests are about what
it must REFUSE to say as much as what it says.

The ledger today is 3,351 settled copies at -3.62% on dollar deployed.
That is not a proven-profitable strategy and no engineering confidence
substitutes for it. But it also does not settle the question, because
every copy in it was placed by a system that copied a whale's EXIT as a
doubled ENTRY — the production census now shows 79 such buys correctly
reclassified in one window. A ledger produced by a different system
does not measure this one.

So: a cohort with a stated cutoff, a real confidence interval, and a
refusal to conclude until the sample can carry one.
"""

import math

import pytest

from sportsassets.analytics import proof as P


def _rows(pairs):
    return [{"stake": s, "pnl": p} for s, p in pairs]


class TestTheRatioEstimator:
    """ROI on dollar deployed is a RATIO of two sums, not a mean. Using
    sd/sqrt(n) over per-copy ROI would weight a $3 copy the same as a
    $250 one and report a tighter interval than the data supports."""

    def test_a_perfectly_consistent_book_has_no_uncertainty(self):
        r = P.roi_with_ci(_rows([(100.0, 10.0)] * 50))
        assert r["roi"] == pytest.approx(0.10)
        assert r["se"] == pytest.approx(0.0, abs=1e-12)

    def test_a_coinflip_book_has_se_one_over_root_n(self):
        n = 400
        pairs = [(100.0, 100.0)] * (n // 2) + [(100.0, -100.0)] * (n // 2)
        r = P.roi_with_ci(_rows(pairs))
        assert r["roi"] == pytest.approx(0.0, abs=1e-12)
        assert r["se"] == pytest.approx(1 / math.sqrt(n), rel=0.01)

    def test_stake_weighting_is_real(self):
        """One big loser and many tiny winners is a losing book on
        dollar deployed, however the per-copy average reads."""
        pairs = [(1.0, 0.5)] * 100 + [(1000.0, -400.0)]
        r = P.roi_with_ci(_rows(pairs))
        assert r["roi"] < 0
        per_copy_mean = sum(p / s for s, p in pairs) / len(pairs)
        assert per_copy_mean > 0, "the unweighted read disagrees — that "\
            "is exactly why the ratio estimator is used"

    def test_the_residuals_sum_to_zero_by_construction(self):
        """On the UNROUNDED ratio. The reported `roi` is rounded to six
        places for display, and the SE is computed from the exact one —
        which is the part that matters, because a rounded ratio fed
        back into the residuals would bias the interval."""
        pairs = [(100.0, 12.0), (250.0, -30.0), (3.0, 1.0)]
        exact = sum(p for _, p in pairs) / sum(s for s, _ in pairs)
        assert sum(p - exact * s for s, p in pairs) == pytest.approx(
            0, abs=1e-9)

    def test_the_se_is_computed_from_the_exact_ratio_not_the_rounded_one(self):
        import inspect

        src = inspect.getsource(P.roi_with_ci)
        assert 'out["roi"] = round(r, 6)' in src
        assert "sum((p - r * s) ** 2" in src,             "the residuals must use `r`, not out['roi']"


class TestItRefusesToConclude:
    def test_an_empty_cohort_says_NO_SAMPLE(self):
        r = P.assess([])
        assert r["n"] == 0
        assert "NO SAMPLE" in r["verdict"]
        assert r["roi"] is None

    def test_one_copy_cannot_carry_an_interval(self):
        r = P.assess(_rows([(100.0, 40.0)]))
        assert "INSUFFICIENT" in r["verdict"]
        assert r["ci95"] is None

    def test_an_interval_containing_zero_is_not_evidence(self):
        r = P.assess(_rows([(100.0, 100.0)] * 12 + [(100.0, -100.0)] * 10))
        assert "INSUFFICIENT" in r["verdict"]
        assert r["ci95"][0] < 0 < r["ci95"][1]
        assert "not yet evidence" in r["verdict"]

    def test_a_positive_point_estimate_alone_never_reads_as_proven(self):
        r = P.assess(_rows([(100.0, 100.0)] * 12 + [(100.0, -100.0)] * 10))
        assert r["roi"] > 0
        assert "PROVEN" not in r["verdict"]


class TestWhatItWillSay:
    def test_a_genuinely_positive_book_is_called_proven(self):
        r = P.assess(_rows([(100.0, 6.0), (100.0, 4.0)] * 400))
        assert r["ci95"][0] > 0
        assert r["verdict"].startswith("PROVEN POSITIVE")

    def test_a_genuinely_negative_book_says_STOP(self):
        """A losing strategy proven at 95% is the single most important
        thing this instrument can say, and it must not be softened."""
        r = P.assess(_rows([(100.0, -6.0), (100.0, -4.0)] * 400))
        assert r["ci95"][1] < 0
        assert "PROVEN NEGATIVE" in r["verdict"]
        assert "STOP AND DIAGNOSE" in r["verdict"]


class TestTheSampleSizeProjection:
    """'How many more' is the number that turns 'are we there yet' into
    a date."""

    def test_a_thinner_edge_needs_a_bigger_sample(self):
        assert P.required_n(1.0, 0.01) > P.required_n(1.0, 0.05)

    def test_it_scales_as_one_over_edge_squared(self):
        a, b = P.required_n(1.0, 0.02), P.required_n(1.0, 0.01)
        assert b / a == pytest.approx(4.0, rel=0.01)

    def test_it_sizes_for_POWER_not_just_significance(self):
        """An interval that excludes zero only half the time it should
        is not a plan."""
        powered = P.required_n(1.0, 0.02, power=True)
        bare = P.required_n(1.0, 0.02, power=False)
        assert powered > bare * 1.9

    def test_a_two_percent_edge_at_unit_dispersion_is_thousands(self):
        n = P.required_n(1.0, 0.02)
        assert 15000 < n < 25000

    def test_degenerate_inputs_return_none_not_zero(self):
        assert P.required_n(0.0, 0.02) is None
        assert P.required_n(1.0, 0.0) is None
        assert P.required_n("x", 0.02) is None

    def test_it_sizes_against_the_TARGET_not_our_noisy_estimate(self):
        """Sizing against a point estimate near zero demands an absurd
        sample precisely when the estimate is least trustworthy."""
        rows = _rows([(100.0, 100.0)] * 11 + [(100.0, -100.0)] * 11)
        r = P.assess(rows, target_edge=0.03)
        assert r["n_needed_at_target"] == P.required_n(
            r["sigma_per_dollar"], 0.03)


class TestItCannotFabricateASample:
    def test_zero_stake_rows_are_dropped_and_counted(self):
        r = P.roi_with_ci(_rows([(100.0, 10.0), (0.0, 5.0), (-3.0, 1.0)]))
        assert r["n"] == 1
        assert r["dropped_rows"] == 2

    def test_unparseable_rows_are_dropped_not_zeroed(self):
        r = P.roi_with_ci([{"stake": "x", "pnl": 1}, {"stake": 10, "pnl": 1}])
        assert r["n"] == 1
        assert r["dropped_rows"] == 1

    def test_a_dropped_row_never_becomes_a_zero_pnl_win(self):
        base = P.roi_with_ci(_rows([(100.0, 10.0)] * 10))
        with_junk = P.roi_with_ci(
            _rows([(100.0, 10.0)] * 10 + [(0.0, 0.0)] * 90))
        assert base["roi"] == with_junk["roi"]
        assert base["se"] == with_junk["se"]


class TestTheCohortBoundaryIsInTheOpen:
    """A cohort boundary chosen quietly is how a bad result gets tuned
    away by moving the start date."""

    def test_the_cutoff_is_a_named_constant(self):
        assert P.COHORT_START.startswith("2026-08-25")

    def test_the_query_only_counts_copies_placed_after_it(self):
        import inspect

        src = inspect.getsource(P.cohort_assess)
        assert "lo.placed_at >= $1" in src

    def test_the_sleeve_is_isolated_from_desk_and_underdog(self):
        import inspect

        src = inspect.getsource(P.cohort_assess)
        assert "NOT IN ('manual', 'underdog')" in src

    def test_the_response_states_the_cutoff_and_why(self):
        import inspect

        src = inspect.getsource(P.cohort_assess)
        assert '"cohort_start"' in src
        assert "doubled entry" in src


class TestTheEndpointKeepsTheHistoryOnThePage:
    """Showing only the clean cohort would be the same move as choosing
    the cutoff quietly. The contaminated history is the REASON a cohort
    exists, so it is reported beside it."""

    def _src(self):
        import inspect

        from sportsassets.api import app as A

        return inspect.getsource(A.admin_proof)

    def test_all_time_is_reported_too(self):
        assert "all_time_including_contaminated" in self._src()

    def test_it_is_labelled_contaminated(self):
        assert "CONTAMINATED" in self._probe()

    def test_the_benchmark_is_the_whales_own_return(self):
        s = self._src()
        assert "whale_merge_pnl" in s
        assert "whale_roi_on_entries" in s

    def test_it_sizes_against_the_benchmark_not_our_estimate(self):
        s = self._src()
        assert "n_needed_at_target" in s
        assert "least trustworthy" in s

    def _probe(self):
        from pathlib import Path

        from sportsassets.api import app as A

        root = Path(A.__file__).resolve().parents[3]
        return (root / ".github/workflows/engine-diagnostic.yml").read_text()

    def test_the_probe_prints_the_verdict_and_the_cutoff(self):
        y = self._probe()
        assert "PROOF verdict:" in y
        assert "PROOF cohort_start=" in y

    def test_the_probe_prints_how_many_more_are_needed(self):
        assert "still to go" in self._probe()


class TestTheBenchmarkCannotTakeTheEndpointDown:
    """The first version computed the whale benchmark inline. That is
    the heaviest query in the system — seven whales, up to 600,000
    fills each, swisstony alone at 283,748 — and it took the endpoint
    with it. The 2026-08-25 probe read, on the same line:

        MERGEHTTP code=502     PROOF unavailable

    The instrument that answers "are we profitable" became unavailable
    exactly when it mattered, because I hung it off the most expensive
    thing the API does."""

    def _src(self):
        import inspect

        from sportsassets.api import app as A

        return inspect.getsource(A.admin_proof)

    def test_the_endpoint_does_not_run_the_merge_replay(self):
        # CODE ONLY — the comment above the fix names the function it
        # removed, on purpose, so a grep over raw source would flag the
        # explanation as the defect.
        code = "\n".join(l for l in self._src().splitlines()
                         if not l.strip().startswith("#"))
        assert "whale_merge_pnl" not in code

    def test_it_reads_a_published_value_instead(self):
        assert "whale_edge_benchmark" in self._src()

    def test_a_missing_benchmark_degrades_rather_than_fails(self):
        s = self._src()
        assert "no published benchmark yet" in s
        assert "the verdict is unaffected" in s

    def test_a_worker_publishes_it(self):
        import inspect

        from sportsassets.workers import analytics as an

        src = inspect.getsource(an.publish_whale_benchmark)
        assert "whale_edge_benchmark" in src
        assert "whale_merge_pnl" in src

    def test_the_publisher_is_rate_limited_well_below_the_cycle(self):
        from sportsassets.workers import analytics as an

        assert an.BENCHMARK_EVERY_S >= 1800

    def test_the_publisher_never_kills_the_analytics_loop(self):
        import inspect

        from sportsassets.workers import analytics as an

        src = inspect.getsource(an.publish_whale_benchmark)
        assert "except Exception" in src
        assert "never kills the loop" in src

    def test_it_is_actually_called_from_the_cycle(self):
        import inspect

        from sportsassets.workers import analytics as an

        assert "await publish_whale_benchmark()" in inspect.getsource(an.main)


class TestTheProbeNamesItsFailures:
    def _probe(self):
        from pathlib import Path

        from sportsassets.api import app as A

        root = Path(A.__file__).resolve().parents[3]
        return (root / ".github/workflows/engine-diagnostic.yml").read_text()

    def test_the_status_code_is_printed(self):
        assert "PROOFHTTP code=" in self._probe()

    def test_the_body_is_printed_when_it_will_not_parse(self):
        assert "PROOFBODY" in self._probe()
