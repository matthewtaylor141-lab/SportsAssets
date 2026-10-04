"""LAB-F DRIFT SENTINEL: EVERY STATISTIC ON A KNOWN DISTRIBUTION OR AN EXACT,
HAND-DERIVABLE VALUE (sportsassets/lab/drift_stats.py; pure, no I/O).

Reference values come from closed forms where one exists (chi-square with 1
and 2 degrees of freedom, the exact two-sample KS null for fully separated
samples, PSI / TVD / BH by hand) and otherwise were computed ONCE with
scipy 1.17.1 / numpy (named beside each constant) and pinned here -- the
production image has neither library, so the module is pure Python and these
constants are its contract.
"""
from __future__ import annotations

import math
import random

import pytest

from sportsassets.lab import drift_stats as S

# ── order statistics ─────────────────────────────────────────────────

def test_quantile_is_type_7_linear_interpolation():
    assert S.quantile([1.0, 2.0, 3.0, 4.0], 0.25) == 1.75     # numpy 1.75
    assert S.quantile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert S.quantile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert S.quantile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0
    xs = sorted([3, 1, 4, 1, 5, 9, 2, 6])
    assert S.quantile(xs, 0.9) == pytest.approx(6.9, abs=1e-12)  # numpy
    assert S.quantile([], 0.5) is None
    with pytest.raises(ValueError):
        S.quantile([1.0], 1.5)


def test_median_drops_non_finite_and_non_numeric_values():
    assert S.median([1, None, float("nan"), 3, "x", True, float("inf")]) \
        == 2.0
    assert S.median([]) is None


# ── PSI ──────────────────────────────────────────────────────────────

def test_the_psi_binning_rule_is_fixed():
    assert S.psi_bins(99, 500) == 5 and S.psi_bins(100, 100) == 10
    # the reference window's own quintiles of 1..10 (type 7)
    assert S.psi_edges([float(i) for i in range(1, 11)], 5) == \
        pytest.approx([2.8, 4.6, 6.4, 8.2])
    # a constant reference has one edge at its value
    assert S.psi_edges([2.0] * 7, 5) == [2.0]


def test_psi_from_counts_matches_the_hand_computed_value():
    # r = (.25,.25,.25,.25), c = (.4,.3,.2,.1):
    # .15 ln 1.6 + .05 ln 1.2 - .05 ln .8 - .15 ln .4
    want = (0.15 * math.log(1.6) + 0.05 * math.log(1.2)
            - 0.05 * math.log(0.8) - 0.15 * math.log(0.4))
    assert S.psi_from_counts([25, 25, 25, 25], [40, 30, 20, 10]) == \
        pytest.approx(want, abs=1e-15)
    assert want == pytest.approx(0.2282174096, abs=1e-9)
    # identical distributions: exactly zero; symmetric in its arguments
    assert S.psi_from_counts([3, 5, 2], [6, 10, 4]) == 0.0
    assert S.psi_from_counts([40, 30, 20, 10], [25, 25, 25, 25]) == \
        pytest.approx(want, abs=1e-15)


def test_psi_numeric_floors_an_empty_bin_at_eps():
    ref = [float(i) for i in range(1, 11)]          # 2 per quintile bin
    cmp_ = [1.0] * 8 + [9.0, 10.0]                  # bins: 8, 0, 0, 0, 2
    got = S.psi_numeric(ref, cmp_)
    assert got["ref_counts"] == [2, 2, 2, 2, 2]
    assert got["cmp_counts"] == [8, 0, 0, 0, 2]
    e = S.PSI_EPS
    want = (0.8 - 0.2) * math.log(0.8 / 0.2) + 3 * (e - 0.2) * math.log(
        e / 0.2)
    assert got["psi"] == pytest.approx(want, abs=1e-12)
    assert got["bins"] == 5 and got["rule_bins"] == 5
    assert got["psi_null_expectation"] == pytest.approx(4 * (0.1 + 0.1))


def test_psi_of_a_sample_against_itself_is_zero_and_a_shift_is_large():
    rng = random.Random(11)
    a = [rng.gauss(0, 1) for _ in range(500)]
    assert S.psi_numeric(a, a)["psi"] == 0.0
    b = [x + 2.0 for x in a]
    assert S.psi_numeric(a, b)["psi"] > 1.0
    assert S.psi_numeric(a, a)["bins"] == 10                  # deciles


def test_psi_categorical_uses_the_union_of_categories():
    got = S.psi_categorical({"A": 25, "B": 25, "C": 25, "D": 25},
                            {"A": 40, "B": 30, "C": 20, "D": 10})
    assert got["psi"] == pytest.approx(0.2282174096, abs=1e-9)
    got = S.psi_categorical({"A": 10}, {"B": 10})
    e = S.PSI_EPS
    assert got["psi"] == pytest.approx(2 * (1 - e) * math.log(1 / e))
    assert got["categories"] == ["A", "B"]


def test_psi_null_expectation_formula():
    assert S.psi_null_expectation(10, 100, 50) == pytest.approx(
        9 * (0.01 + 0.02))
    assert S.psi_null_expectation(1, 10, 10) == 0.0
    assert S.psi_null_expectation(5, 0, 10) == float("inf")


# ── two-sample KS ────────────────────────────────────────────────────

def test_ks_fully_separated_samples_have_the_exact_lattice_p_value():
    # P(D >= 1) = 2 / C(n + m, n): the two monotone paths along the edges
    got = S.ks_2samp([1, 2, 3], [4, 5, 6])
    assert got["d"] == 1.0 and got["method"] == "EXACT_LATTICE_PATHS"
    assert got["p"] == pytest.approx(2 / 20, abs=1e-15)   # scipy 0.1
    got = S.ks_2samp([1, 2], [3, 4])
    assert got["p"] == pytest.approx(1 / 3, abs=1e-15)    # scipy 1/3


def test_ks_matches_scipy_exact_on_fixed_samples():
    a = [0.1 * i for i in range(10)]
    b = [0.1 * i + 0.35 for i in range(12)]
    got = S.ks_2samp(a, b)
    assert got["d"] == pytest.approx(0.5, abs=1e-15)
    # scipy.stats.ks_2samp(a, b, method='exact') = 0.09270296267200293
    assert got["p"] == pytest.approx(0.09270296267200293, rel=1e-12)
    got = S.ks_2samp(list(range(30)), [x + 10.5 for x in range(25)])
    assert got["d"] == pytest.approx(0.36666666666666664, abs=1e-15)
    # scipy exact = 0.03854313485743154
    assert got["p"] == pytest.approx(0.03854313485743154, rel=1e-12)


def test_ks_identical_samples_and_ties():
    got = S.ks_2samp([1, 1, 2, 2], [1, 1, 2, 2])
    assert got["d"] == 0.0 and got["p"] == 1.0
    # D is computed at the pooled values, ties stepped together: at 2.0
    # F_a = 3/3 and F_b = 1/2, so |3 x 2 - 1 x 3| = 3 and D = 3 / 6
    assert S.ks_statistic_num([1.0, 2.0, 2.0], [2.0, 3.0]) == 3
    assert S.ks_2samp([1.0, 2.0, 2.0], [2.0, 3.0])["d"] == 0.5
    with pytest.raises(ValueError):
        S.ks_2samp([], [1.0])


def test_the_kolmogorov_distribution_known_values():
    # scipy.stats.kstwobign.sf
    assert S.kolmogorov_q(1.0) == pytest.approx(0.26999967167735456,
                                                rel=1e-12)
    assert S.kolmogorov_q(1.36) == pytest.approx(0.049485876755377876,
                                                 rel=1e-12)
    assert S.kolmogorov_q(0.5) == pytest.approx(0.9639452436648751,
                                                rel=1e-12)
    assert S.kolmogorov_q(0.1) == 1.0 and S.kolmogorov_q(0.0) == 1.0


def test_large_samples_use_the_stephens_corrected_asymptotic():
    a = [float(i) for i in range(600)]
    b = [float(i + 30) for i in range(600)]
    got = S.ks_2samp(a, b)
    assert got["method"] == "ASYMPTOTIC_KOLMOGOROV_STEPHENS"
    ne = 600 * 600 / 1200.0
    lam = (math.sqrt(ne) + 0.12 + 0.11 / math.sqrt(ne)) * got["d"]
    assert got["p"] == S.kolmogorov_q(lam)
    assert got["d"] == pytest.approx(30 / 600.0, abs=1e-12)


# ── chi-square ───────────────────────────────────────────────────────

def test_chi2_sf_closed_forms_and_reference_values():
    for x in (0.1, 1.0, 3.0, 7.5, 20.0):
        assert S.chi2_sf(x, 2) == pytest.approx(math.exp(-x / 2), rel=1e-13)
        assert S.chi2_sf(x, 1) == pytest.approx(math.erfc(math.sqrt(x / 2)),
                                                rel=1e-12)
    # scipy.stats.chi2.sf
    assert S.chi2_sf(11.0705, 5) == pytest.approx(0.0499999554280436,
                                                  rel=1e-12)
    assert S.chi2_sf(30, 10) == pytest.approx(0.000856641210775301,
                                              rel=1e-12)
    assert S.chi2_sf(100, 50) == pytest.approx(3.454931382984871e-05,
                                               rel=1e-12)
    assert S.chi2_sf(0.5, 3) == pytest.approx(0.9188914116546758, rel=1e-12)
    assert S.chi2_sf(0.0, 4) == 1.0
    with pytest.raises(ValueError):
        S.chi2_sf(1.0, 0)


def test_chi_square_homogeneity_exact_value():
    got = S.chi_square_homogeneity({"A": 30, "B": 20, "C": 10},
                                   {"A": 15, "B": 25, "C": 20})
    assert got["chi2"] == pytest.approx(80 / 9, abs=1e-12)
    assert got["df"] == 2
    # df = 2: the survival function is exp(-x / 2) exactly
    assert got["p"] == pytest.approx(math.exp(-40 / 9), rel=1e-12)
    assert got["p"] == pytest.approx(0.011743628457021359, rel=1e-12)


def test_rare_categories_are_pooled_to_expected_five():
    # need = 5 x 200 / 100 = 10 pooled units per category: R1 (5) and R3
    # (4) go to OTHER; OTHER (9) is still short, so it absorbs the smallest
    # kept category, R2 (11)
    ref = {"A": 50, "B": 40, "R1": 5, "R2": 5}
    cmp_ = {"A": 40, "B": 50, "R3": 4, "R2": 6}
    cells = S.pool_categories(ref, cmp_)
    assert [c[0] for c in cells] == ["A", "B", S.OTHER]
    assert cells[-1][1:] == (10.0, 10.0)
    for _lab, r, c in cells:
        assert (r + c) * 100 / 200.0 >= 5.0


def test_a_short_other_absorbs_the_smallest_kept_category():
    ref = {"A": 50, "B": 48, "R1": 1, "R2": 1}
    cmp_ = {"A": 30, "B": 66, "R3": 2, "R2": 2}
    cells = S.pool_categories(ref, cmp_)
    # OTHER (6) < 10 absorbs A (80), the smallest kept category
    assert [c[0] for c in cells] == ["B", S.OTHER]
    assert cells[-1][1:] == (52.0, 34.0)


def test_one_category_after_pooling_has_nothing_to_compare():
    got = S.chi_square_homogeneity({"A": 40}, {"A": 30})
    assert got["df"] == 0 and got["p"] == 1.0 and got["chi2"] == 0.0
    assert got["note"] == "ONE_CATEGORY_AFTER_POOLING_NO_MIX_TO_COMPARE"


def test_total_variation_distance():
    assert S.tvd({"A": 1, "B": 1}, {"A": 3, "B": 1}) == pytest.approx(0.25)
    assert S.tvd({"A": 5}, {"B": 2}) == 1.0
    assert S.tvd({"A": 2, "B": 2}, {"B": 1, "A": 1}) == 0.0


# ── Benjamini-Hochberg ───────────────────────────────────────────────

def test_bh_q_values_by_hand():
    # sorted 0.005, 0.01, 0.03, 0.04 (m = 4): 0.02, 0.02, 0.04, 0.04
    assert S.bh_adjust([0.01, 0.04, 0.03, 0.005]) == pytest.approx(
        [0.02, 0.04, 0.04, 0.02])
    # step-up monotonicity: a large p cannot raise a smaller one's q
    assert S.bh_adjust([0.04, 0.041, 0.9]) == pytest.approx(
        [0.0615, 0.0615, 0.9])
    assert S.bh_adjust([]) == []
    assert S.bh_adjust([0.7]) == [0.7]
    qs = S.bh_adjust([0.3, 0.2, 0.9, 0.01, 0.6])
    for p, q in zip([0.3, 0.2, 0.9, 0.01, 0.6], qs):
        assert p <= q <= 1.0


def test_bh_q_is_never_below_p_under_float_rounding():
    ps = [0.1 * k / 3 for k in range(1, 31)]
    for p, q in zip(ps, S.bh_adjust(ps)):
        assert q >= p


# ── bootstrap ────────────────────────────────────────────────────────

def test_bootstrap_of_constant_samples_is_exact():
    got = S.bootstrap_quantile_shift([1.0] * 50, [3.0] * 40, 0.5, seed=7)
    assert got["shift"] == 2.0 and got["lo"] == 2.0 and got["hi"] == 2.0
    assert got["b"] == S.BOOT_B and got["level"] == 0.95


def test_bootstrap_is_reproducible_and_covers_a_known_shift():
    rng = random.Random(5)
    a = [rng.gauss(0, 1) for _ in range(400)]
    b = [rng.gauss(0.5, 1) for _ in range(400)]
    g1 = S.bootstrap_quantile_shift(a, b, 0.5, seed=S.seed_for("x", 1))
    g2 = S.bootstrap_quantile_shift(a, b, 0.5, seed=S.seed_for("x", 1))
    assert g1 == g2
    assert g1["lo"] < 0.5 < g1["hi"]
    assert g1["lo"] > 0.0          # the shift is detected
    assert g1["lo"] <= g1["shift"] <= g1["hi"]


def test_the_bootstrap_cap_is_a_systematic_subsample():
    xs = list(range(10))
    assert S.systematic_subsample(xs, 5) == [1, 3, 5, 7, 9]
    assert S.systematic_subsample(xs, 20) == xs
    big = [float(i) for i in range(5000)]
    got = S.bootstrap_quantile_shift(big, big, 0.5, seed=1, b=20, cap=1000)
    assert got["subsampled"] is True
    assert got["shift"] == 0.0
