"""THE KERNEL, CHECKED AGAINST ANSWERS THAT EXIST WITHOUT IT.

A test that fits a model and asserts the loss went down would pass on a
kernel with a sign error in the gradient. So wherever a closed form
exists, the fit is checked against the closed form; where one does not,
it is checked against a property that a wrong implementation breaks:

    logistic on a balanced constant     intercept is exactly log-odds
    logistic on separable data          the ridge keeps it finite
    logistic recovers a known beta      simulated from the model itself
    isotonic                            monotone, and exact on a known
                                        violation
    boosting                            beats the base rate it started
                                        from, and each stump is readable
    hazard                              recovers a constant hazard, and
                                        censored subjects do NOT count
                                        as negatives
    round trip                          to_dict -> load -> same numbers

Every fixture here is SYNTHETIC and none of it is venue data.
"""
from __future__ import annotations

import math

import pytest

from sportsassets.learn import kernel as K
from sportsassets.learn import metrics as M


def _rows(vals, name="x"):
    return [{name: v} for v in vals]


# ── logistic regression ──────────────────────────────────────────────

class TestRidgeLogistic:

    def test_intercept_is_the_log_odds_when_no_feature_informs(self):
        """A feature that carries nothing leaves the intercept at the
        base rate -- the one number a fit must never get wrong."""
        rows = [{"x": 1.0} for _ in range(100)]
        y = [1.0] * 30 + [0.0] * 70
        m = K.Ridge(["x"], l2=1.0).fit(rows, y)
        assert m.converged
        # x is constant -> scale 1.0, centred to 0, contributes nothing.
        assert abs(m.predict({"x": 1.0}) - 0.30) < 1e-6
        assert abs(m.intercept - math.log(0.3 / 0.7)) < 1e-6

    def test_it_recovers_a_coefficient_it_generated(self):
        """Simulate labels FROM the model, then fit and compare. A sign
        error, a missing standardisation or a bad Hessian all fail."""
        true_b0, true_b1 = -0.5, 2.0
        rows, y = [], []
        # A deterministic sweep, and each x contributes its expected
        # number of positives -- no randomness, exact expectations.
        for k in range(201):
            x = -3.0 + 6.0 * k / 200.0
            p = 1.0 / (1.0 + math.exp(-(true_b0 + true_b1 * x)))
            rows.append({"x": x}); y.append(p)      # fractional label
        m = K.Ridge(["x"], l2=1e-6, max_iter=200).fit(rows, y)
        raw = m.weights_on_raw_scale()
        assert abs(raw["x"] - true_b1) < 0.05
        assert abs(raw["__intercept__"] - true_b0) < 0.05

    def test_separable_data_does_not_run_away(self):
        """Perfect separation sends an unpenalised fit to infinity. The
        ridge is what keeps it finite, and finite is the requirement."""
        rows = _rows([-3, -2, -1, 1, 2, 3])
        y = [0, 0, 0, 1, 1, 1]
        m = K.Ridge(["x"], l2=1.0).fit(rows, y)
        assert all(math.isfinite(c) for c in m.coef)
        assert math.isfinite(m.intercept)
        assert m.predict({"x": 3.0}) > m.predict({"x": -3.0})

    def test_a_missing_feature_is_refused_not_zeroed(self):
        m = K.Ridge(["a", "b"], l2=1.0).fit(
            [{"a": 1.0, "b": 0.0}, {"a": 0.0, "b": 1.0}], [1, 0])
        with pytest.raises(KeyError):
            m.predict({"a": 1.0})

    def test_a_none_value_is_refused_not_treated_as_zero(self):
        """THE FAILURE THIS PINS. Zero is a real value for almost every
        feature here -- zero spread, zero inventory, zero recent flow --
        so silently substituting it turns an absent measurement into a
        confident one."""
        with pytest.raises(ValueError, match="missing in this row"):
            K.Ridge(["x"], l2=1.0).fit([{"x": 1.0}, {"x": None}], [1, 0])

    def test_duplicate_feature_names_are_refused(self):
        with pytest.raises(ValueError, match="duplicate"):
            K.Ridge(["x", "x"])

    def test_weights_move_the_base_rate(self):
        rows = [{"x": 0.0} for _ in range(4)]
        m = K.Ridge(["x"], l2=1.0).fit(rows, [1, 1, 0, 0],
                                       weights=[3, 3, 1, 1])
        assert abs(m.predict({"x": 0.0}) - 0.75) < 1e-6

    def test_round_trip_preserves_every_prediction(self):
        rows = [{"x": float(i), "z": float(i % 3)} for i in range(40)]
        y = [1 if (i % 3 == 0) else 0 for i in range(40)]
        m = K.Ridge(["x", "z"], l2=0.5).fit(rows, y)
        back = K.load(m.to_dict())
        for r in rows:
            assert abs(m.predict(r) - back.predict(r)) < 1e-15

    def test_a_model_from_another_kernel_version_is_refused(self):
        m = K.Ridge(["x"], l2=1.0).fit(_rows([0, 1]), [0, 1])
        d = m.to_dict()
        d["kernel"] = "SOMETHING_ELSE_V9"
        with pytest.raises(ValueError, match="kernel"):
            K.load(d)

    def test_the_fit_is_bit_identical_across_runs(self):
        rows = [{"x": float(i % 7), "y": float(i % 5)} for i in range(120)]
        lab = [1 if (i % 4 == 0) else 0 for i in range(120)]
        a = K.Ridge(["x", "y"], l2=0.3).fit(rows, lab).to_dict()
        b = K.Ridge(["x", "y"], l2=0.3).fit(rows, lab).to_dict()
        assert a == b


# ── isotonic calibration ─────────────────────────────────────────────

class TestIsotonic:

    def test_it_pools_an_inversion_and_leaves_the_rest(self):
        """The classic PAV case, worked by hand: the middle two are out
        of order and pool to their mean; the ends do not move."""
        c = K.Isotonic().fit([1.0, 2.0, 3.0, 4.0], [0.0, 1.0, 0.0, 1.0])
        # Block means 0.0, 0.5, 0.5, 1.0, each through the shared shrink
        # (4m + 0.5) / 5. The ORDERING is what PAV decides and it is
        # unchanged; only the certainty at the ends is given up.
        assert abs(c.predict(1.0) - 0.1) < 1e-12
        assert abs(c.predict(2.0) - 0.5) < 1e-12
        assert abs(c.predict(3.0) - 0.5) < 1e-12
        assert abs(c.predict(4.0) - 0.9) < 1e-12

    def test_ties_are_pooled_before_the_pav_pass(self):
        """Two rows at the same score with different labels are ONE
        point at their mean, not an ordering violation."""
        c = K.Isotonic().fit([1.0, 1.0, 2.0], [0.0, 1.0, 1.0])
        assert abs(c.predict(1.0) - 0.5) < 1e-12          # 0.5 is fixed
        assert abs(c.predict(2.0) - 0.875) < 1e-12        # (3 + 0.5) / 4

    def test_the_curve_is_monotone_everywhere(self):
        scores = [i / 50.0 for i in range(51)]
        labels = [1 if (i % 3) else 0 for i in range(51)]
        c = K.Isotonic().fit(scores, labels)
        got = [c.predict(s) for s in scores]
        assert all(got[i] <= got[i + 1] + 1e-12 for i in range(len(got) - 1))

    def test_it_is_flat_outside_the_observed_range(self):
        """Extrapolating a calibration curve invents a probability for
        a region nobody observed."""
        c = K.Isotonic().fit([0.2, 0.8], [0.0, 1.0])
        assert c.predict(-5.0) == c.predict(0.2)
        assert c.predict(5.0) == c.predict(0.8)

    def test_it_actually_repairs_a_miscalibrated_model(self):
        """A model that is right about ORDER and wrong about MAGNITUDE
        is the case calibration exists for, so it is measured: the
        calibrated scores must be closer to the truth."""
        truth = [i / 200.0 for i in range(201)]
        # A model reporting half the real probability: perfect ranking,
        # useless magnitudes.
        scores = [t * 0.5 for t in truth]
        c = K.Isotonic().fit(scores, truth)
        before = sum((scores[i] - truth[i]) ** 2 for i in range(201))
        after = sum((c.predict(scores[i]) - truth[i]) ** 2
                    for i in range(201))
        assert after < before / 10.0

    def test_round_trip(self):
        c = K.Isotonic().fit([1.0, 2.0, 3.0], [0.0, 1.0, 1.0])
        back = K.load(c.to_dict())
        for s in (0.5, 1.0, 1.5, 2.5, 9.0):
            assert abs(c.predict(s) - back.predict(s)) < 1e-15

    # ── the certainty claim PAV makes, and must not ──────────────────

    def test_a_block_of_zeros_is_not_a_probability_of_zero(self):
        """THE FERRARI REGRESSION. A pooled block of zeros used to come
        out at exactly 0.0, and six held-out rows that got that value
        went on to complete. -log(0) is not a score, it is a bug with a
        number attached."""
        c = K.Isotonic().fit([float(i) for i in range(40)],
                             [0.0] * 20 + [1.0] * 20)
        assert c.predict(0.0) > 0.0
        assert c.predict(39.0) < 1.0
        assert abs(c.predict(0.0) - 0.5 / 41.0) < 1e-12
        assert abs(c.predict(39.0) - 40.5 / 41.0) < 1e-12

    def test_no_fitted_value_is_ever_exactly_zero_or_one(self):
        for labels in ([0.0] * 30, [1.0] * 30, [0.0] * 15 + [1.0] * 15,
                       [float(i % 2) for i in range(30)]):
            c = K.Isotonic().fit([float(i) for i in range(30)], labels)
            assert all(0.0 < v < 1.0 for v in c.y), labels[:3]

    def test_the_floor_states_the_resolution_the_sample_has(self):
        """More calibration rows earn a lower floor; fewer do not."""
        small = K.Isotonic().fit([float(i) for i in range(10)], [0.0] * 10)
        large = K.Isotonic().fit([float(i) for i in range(1000)],
                                 [0.0] * 1000)
        assert large.y[0] < small.y[0]
        assert abs(small.y[0] - 0.5 / 11.0) < 1e-12
        assert abs(large.y[0] - 0.5 / 1001.0) < 1e-12

    def test_the_correction_cannot_reorder_the_curve(self):
        """A PER-BLOCK Laplace correction shrinks a two-row block harder
        than a thousand-row one and can lift the first above the second.
        The shared-n map cannot, and this is the case that shows it:
        two zeros at the bottom, then a large block just above."""
        scores = [0.0, 0.1] + [1.0 + i * 0.001 for i in range(1000)]
        labels = [0.0, 0.0] + [1.0 if i < 50 else 0.0 for i in range(1000)]
        c = K.Isotonic().fit(scores, labels)
        got = [c.predict(s) for s in scores]
        assert all(got[i] <= got[i + 1] + 1e-12 for i in range(len(got) - 1))
        # and the naive per-block rule would NOT have been monotone here
        naive_small = (0.0 * 2 + 0.5) / (2 + 1.0)
        naive_large = (50.0 + 0.5) / (1000 + 1.0)
        assert naive_small > naive_large

    def test_an_artifact_frozen_before_the_correction_is_not_moved(self):
        """`from_dict` reproduces stored values verbatim, so a model
        frozen earlier scores exactly as it did. The absence of the
        shrinkage record is reported, not silently filled in."""
        old = {"kind": "ISOTONIC", "kernel": K.VERSION,
               "x": [0.1, 0.9], "y": [0.0, 1.0], "n_rows": 200}
        c = K.Isotonic.from_dict(old)
        assert c.predict(0.05) == 0.0 and c.predict(2.0) == 1.0
        assert c.shrinkage is None
        assert c.to_dict()["shrinkage"] is None

    def test_the_shrinkage_record_is_carried_and_round_trips(self):
        c = K.Isotonic().fit([1.0, 2.0, 3.0], [0.0, 1.0, 1.0])
        d = c.to_dict()
        assert d["shrinkage"]["n"] == 3
        assert abs(d["shrinkage"]["floor"] - 0.125) < 1e-12
        assert abs(d["shrinkage"]["ceiling"] - 0.875) < 1e-12
        assert K.Isotonic.from_dict(d).shrinkage == d["shrinkage"]


# ── boosted stumps ───────────────────────────────────────────────────

class TestStumps:

    def test_it_learns_a_threshold_it_was_shown(self):
        rows = [{"x": float(i)} for i in range(200)]
        y = [1 if i >= 120 else 0 for i in range(200)]
        m = K.Stumps(["x"], rounds=40, min_leaf=10).fit(rows, y)
        assert m.predict({"x": 190.0}) > 0.8
        assert m.predict({"x": 10.0}) < 0.2
        # The first split should sit near the true boundary.
        assert 100.0 <= m.trees[0][1] <= 140.0

    def test_zero_rounds_is_the_base_rate_not_a_coin_flip(self):
        rows = [{"x": float(i)} for i in range(100)]
        y = [1] * 20 + [0] * 80
        m = K.Stumps(["x"], rounds=0).fit(rows, y)
        assert abs(m.predict({"x": 5.0}) - 0.20) < 1e-9

    def test_it_stops_rather_than_padding_with_useless_trees(self):
        """min_leaf can make every split illegal. Stopping is right;
        recording rounds that did nothing would make the count a lie."""
        rows = [{"x": 1.0} for _ in range(10)]
        m = K.Stumps(["x"], rounds=25, min_leaf=4).fit(rows, [1, 0] * 5)
        assert m.to_dict()["rounds_fitted"] == 0
        assert m.to_dict()["rounds_requested"] == 25

    def test_each_stump_reads_as_a_sentence(self):
        rows = [{"spread": float(i % 9)} for i in range(180)]
        y = [1 if (i % 9) < 3 else 0 for i in range(180)]
        m = K.Stumps(["spread"], rounds=20, min_leaf=10).fit(rows, y)
        ex = m.explain()
        assert ex and "spread" in ex[0]["reads_as"]
        assert "log-odds" in ex[0]["reads_as"]

    def test_it_beats_the_base_rate_on_data_with_a_signal(self):
        rows = [{"x": float(i % 10)} for i in range(300)]
        y = [1 if (i % 10) >= 7 else 0 for i in range(300)]
        m = K.Stumps(["x"], rounds=30, min_leaf=15).fit(rows, y)
        base = K.BaseRate().fit(rows, y)
        p = m.predict_many(rows)
        b = base.predict_many(rows)
        assert M.log_loss(p, y) < M.log_loss(b, y)

    def test_round_trip(self):
        rows = [{"x": float(i)} for i in range(120)]
        y = [1 if i > 60 else 0 for i in range(120)]
        m = K.Stumps(["x"], rounds=12, min_leaf=8).fit(rows, y)
        back = K.load(m.to_dict())
        for r in rows:
            assert abs(m.predict(r) - back.predict(r)) < 1e-15


# ── discrete-time hazard ─────────────────────────────────────────────

class TestHazard:

    EDGES = [60.0, 300.0, 900.0, 3600.0]

    def test_it_recovers_a_constant_hazard(self):
        """Every subject acts in bucket 0 or survives all four with a
        fixed per-bucket probability. The fitted hazard must match."""
        rows, times, obs = [], [], []
        # 25 of every 100 subjects act in each bucket they reach: a
        # constant hazard of 0.25.
        for k in range(4):
            n_act = [250, 188, 140, 106][k]
            for _ in range(n_act):
                rows.append({"x": 0.0})
                times.append(self.EDGES[k])
                obs.append(True)
        for _ in range(316):
            rows.append({"x": 0.0})
            times.append(10000.0)
            obs.append(False)
        h = K.Hazard(["x"], buckets=self.EDGES, l2=1e-6).fit(rows, times, obs)
        for k in range(4):
            assert abs(h.hazard({"x": 0.0}, k) - 0.25) < 0.05

    def test_a_censored_subject_is_not_counted_as_a_negative(self):
        """THE FAILURE THIS PINS, and the reason the expansion exists.
        A subject last seen at 100s says 'not in bucket 0'. It says
        NOTHING about buckets 1-3, and a fit that reads it as three
        more negatives will under-predict every longer horizon."""
        rows = [{"x": 0.0}] * 40
        times = [100.0] * 40
        obs = [False] * 40
        h = K.Hazard(["x"], buckets=self.EDGES, l2=1.0).fit(rows, times, obs)
        # 40 subjects, each contributing exactly ONE person-period
        # (bucket 0, survived), not four.
        assert h.n_periods == 40
        assert h.n_censored == 40

    def test_an_event_contributes_its_survivals_and_its_event(self):
        rows = [{"x": 0.0}]
        h = K.Hazard(["x"], buckets=self.EDGES, l2=1.0).fit(
            rows, [800.0], [True])          # bucket 2
        assert h.n_periods == 3             # buckets 0, 1 survived; 2 event
        assert h.n_censored == 0

    def test_p_by_is_non_decreasing_in_the_horizon(self):
        rows = [{"x": float(i % 5)} for i in range(200)]
        times = [60.0 * (1 + i % 20) for i in range(200)]
        obs = [i % 3 != 0 for i in range(200)]
        h = K.Hazard(["x"], buckets=self.EDGES).fit(rows, times, obs)
        p = [h.p_by({"x": 2.0}, k) for k in range(4)]
        assert all(p[i] <= p[i + 1] + 1e-12 for i in range(3))
        assert all(0.0 <= v <= 1.0 for v in p)

    def test_the_baseline_hazard_may_change_with_elapsed_time(self):
        """A single classifier cannot express this, which is the whole
        reason for the person-period expansion. Almost everything acts
        in bucket 0; the survivors then act slowly."""
        rows, times, obs = [], [], []
        for _ in range(400):
            rows.append({"x": 0.0}); times.append(30.0); obs.append(True)
        for _ in range(20):
            rows.append({"x": 0.0}); times.append(3000.0); obs.append(True)
        for _ in range(180):
            rows.append({"x": 0.0}); times.append(99999.0); obs.append(False)
        h = K.Hazard(["x"], buckets=self.EDGES, l2=1e-6).fit(rows, times, obs)
        assert h.hazard({"x": 0.0}, 0) > 0.5
        assert h.hazard({"x": 0.0}, 1) < 0.2

    def test_an_event_beyond_the_last_edge_is_a_survivor_not_an_event(self):
        """The model is not asked about anything past its horizon, so
        an action at 10,000s in a one-hour model is a survival."""
        h = K.Hazard(["x"], buckets=self.EDGES, l2=1.0).fit(
            [{"x": 0.0}], [10000.0], [True])
        assert h.n_periods == 4
        assert h.p_by({"x": 0.0}, 3) < 0.5

    def test_all_censored_before_the_first_edge_is_refused(self):
        with pytest.raises(ValueError, match="cannot speak to any horizon"):
            K.Hazard(["x"], buckets=self.EDGES).fit(
                [{"x": 0.0}] * 5, [0.5] * 5, [False] * 5)

    def test_bucket_edges_must_be_increasing(self):
        with pytest.raises(ValueError, match="increasing"):
            K.Hazard(["x"], buckets=[60.0, 30.0])

    def test_round_trip(self):
        rows = [{"x": float(i % 4)} for i in range(120)]
        times = [60.0 * (1 + i % 15) for i in range(120)]
        obs = [i % 2 == 0 for i in range(120)]
        h = K.Hazard(["x"], buckets=self.EDGES).fit(rows, times, obs)
        back = K.load(h.to_dict())
        for r in ({"x": 0.0}, {"x": 3.0}):
            for k in range(4):
                assert abs(h.p_by(r, k) - back.p_by(r, k)) < 1e-15

    def test_censoring_at_a_bucket_edge_survives_that_bucket(self):
        """THE BIAS THE SKLEARN CROSS-CHECK FOUND, pinned here so it
        cannot come back in an environment without sklearn.

        A subject last seen at exactly a bucket's END EDGE survived that
        whole bucket and owes it a survival. The first version indexed
        off `bucket_of`, which puts t == edge INSIDE that bucket, so
        those subjects were dropped from it -- shrinking the denominator
        and OVER-STATING the hazard, which is the direction that makes a
        trading system act when it should wait.

        Checked against Kaplan-Meier computed by hand below.
        """
        edges = [60.0, 300.0, 900.0, 3600.0]
        plan = [(60.0, True, 120), (300.0, True, 90), (300.0, False, 40),
                (900.0, True, 60), (3600.0, True, 30), (9999.0, False, 160)]
        rows, times, obs = [], [], []
        for t, ob, n in plan:
            rows += [{"x": 0.0}] * n
            times += [t] * n
            obs += [ob] * n
        h = K.Hazard(["x"], buckets=edges, l2=1e-8).fit(rows, times, obs)

        # Kaplan-Meier, by hand. A censoring tied with an event is
        # treated as occurring after it, so it IS at risk in that bucket.
        at_risk, surv, km = 500, 1.0, []
        for k, e in enumerate(edges):
            lo = edges[k - 1] if k else -1.0
            ev = sum(n for t, ob, n in plan if ob and lo < t <= e)
            cn = sum(n for t, ob, n in plan if not ob and lo < t <= e)
            surv *= (1.0 - ev / at_risk)
            km.append(1.0 - surv)
            at_risk -= (ev + cn)

        for k in range(len(edges)):
            assert abs(h.p_by({"x": 0.0}, k) - km[k]) < 1e-6, (
                "bucket %d: hazard model %.6f vs Kaplan-Meier %.6f"
                % (k, h.p_by({"x": 0.0}, k), km[k]))

        # The 40 subjects censored AT the 300s edge each owe TWO
        # survivals (buckets 0 and 1), not one.
        assert h.n_periods == (120 * 1 + 90 * 2 + 40 * 2 + 60 * 3
                               + 30 * 4 + 160 * 4)

    def test_censoring_before_the_first_edge_contributes_nothing(self):
        """It did not complete a single bucket, so it has nothing to
        say about any of them -- and must not be read as a survival."""
        edges = [60.0, 300.0]
        h = K.Hazard(["x"], buckets=edges, l2=1.0).fit(
            [{"x": 0.0}] * 3 + [{"x": 1.0}] * 5,
            [30.0] * 3 + [300.0] * 5,
            [False] * 3 + [True] * 5)
        assert h.n_censored == 3
        # 3 censored early contribute 0; 5 events in bucket 1 contribute
        # 2 each.
        assert h.n_periods == 10


# ── clustered uncertainty ────────────────────────────────────────────

class TestClusteredJackknife:
    """837 rows in 172 markets is 172 observations, not 837."""

    def _data(self, g_n=40, per=6):
        p, y, g = [], [], []
        for c in range(g_n):
            # Every row in a market shares its outcome -- which is the
            # whole reason the rows are not independent.
            lab = 1.0 if (c % 2) else 0.0
            for k in range(per):
                p.append(0.35 + 0.3 * lab + 0.01 * k)
                y.append(lab)
                g.append("m%03d" % c)
        return p, y, g

    def test_it_refuses_an_estimate_from_too_few_clusters(self):
        p, y, g = self._data(g_n=4, per=20)
        r = M.clustered_jackknife(p, y, g, M.auc_stat, min_groups=8)
        assert r["status"] == "INSUFFICIENT_CLUSTERS"
        assert r["n_groups"] == 4 and r["n_rows"] == 80
        assert "se_clustered" not in r

    def test_the_error_is_over_markets_not_rows(self):
        """Ten times the rows in the SAME markets must not shrink the
        interval. That is the error the function exists to prevent."""
        thin = M.clustered_jackknife(*self._data(g_n=20, per=2),
                                     stat=M.auc_stat)
        fat = M.clustered_jackknife(*self._data(g_n=20, per=20),
                                    stat=M.auc_stat)
        assert thin["status"] == fat["status"] == "OK"
        assert thin["n_rows"] == 40 and fat["n_rows"] == 400
        assert abs(thin["se_clustered"] - fat["se_clustered"]) < 1e-9

    def test_more_markets_do_shrink_it(self):
        few = M.clustered_jackknife(*self._data(g_n=10, per=6),
                                    stat=M.auc_stat)
        many = M.clustered_jackknife(*self._data(g_n=200, per=6),
                                     stat=M.auc_stat)
        assert many["se_clustered"] <= few["se_clustered"]

    def test_an_undefined_deletion_is_dropped_and_counted(self):
        """Deleting the only market that carries the positives leaves a
        single-class set, which has no AUC. That deletion is not a
        zero."""
        p = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.25, 0.35]
        y = [0.0] * 8 + [1.0, 1.0]
        g = ["a", "a", "b", "b", "c", "c", "d", "d", "e", "e"]
        r = M.clustered_jackknife(p, y, g, M.auc_stat, min_groups=3)
        assert r["n_deletions_undefined"] == 1
        assert r["n_deletions_used"] == 4

    def test_it_is_deterministic(self):
        p, y, g = self._data()
        a = M.clustered_jackknife(p, y, g, M.auc_stat)
        b = M.clustered_jackknife(p, y, g, M.auc_stat)
        assert a == b

    def test_the_skill_baseline_stays_pinned_under_deletion(self):
        """Recomputing the baseline per deletion would move the thing
        being compared against, so the spread would measure the
        baseline's wobble rather than the model's."""
        p, y, g = self._data()
        r = M.clustered_jackknife(p, y, g, M.skill_stat(0.5))
        assert r["status"] == "OK"
        full = M.skill(M.log_loss(p, y), M.log_loss([0.5] * len(p), y))
        assert abs(r["statistic"] - full["skill"]) < 1e-12

    def test_it_reports_how_concentrated_the_clusters_are(self):
        """A jackknife interval is not valid when one market carries
        most of the rows, so the share is on the result."""
        p = [0.5] * 100 + [0.6, 0.7, 0.8, 0.9] * 2
        y = [float(i % 2) for i in range(108)]
        g = ["big"] * 100 + ["g%d" % i for i in range(8)]
        r = M.clustered_jackknife(p, y, g, M.auc_stat, min_groups=5)
        assert r["largest_cluster_share"] > 0.9
