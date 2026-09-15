"""Our own copies cluster by game, exactly like the whales' do.

merge_pnl was corrected on 2026-08-30 so a whale's three legs on one
match count as one result. The identical error lived here, on the
number that decides whether WE are proven — and this is the more
dangerous of the two, because it is the statistic that would hand out
the stamp of approval.

These pins hold the correction to being a strict generalisation, and
hold the projection honest about the one case that matters most: a
negative point estimate, from which no sample size demonstrates profit.
"""

import math

from sportsassets.analytics.proof import Z95, assess, roi_with_ci


def _rows(triples):
    return [{"stake": s, "pnl": p, "event_key": k} for s, p, k in triples]


def _iid_se(triples):
    n = len(triples)
    ts = sum(s for s, _, _ in triples)
    tp = sum(p for _, p, _ in triples)
    r = tp / ts
    ss = sum((p - r * s) ** 2 for s, p, _ in triples)
    return math.sqrt(n / (n - 1) * ss) / ts


class TestItIsAStrictGeneralisation:
    def test_one_copy_per_game_reproduces_the_old_interval(self):
        t = [(100.0, 6.0, "g1"), (250.0, -11.0, "g2"),
             (80.0, 9.0, "g3"), (400.0, -5.0, "g4")]
        got = roi_with_ci(_rows(t))
        assert got["clusters"] == 4
        assert abs(got["se"] - round(_iid_se(t), 6)) < 1e-9
        assert abs(got["deff"] - 1.0) < 1e-6

    def test_rows_with_no_event_key_stay_singletons(self):
        # An unjoined copy must NOT be merged with other unjoined
        # copies — that would invent correlation and widen the interval
        # on rows that share nothing but a failed join.
        t = [(100.0, 6.0, ""), (250.0, -11.0, ""), (80.0, 9.0, "")]
        got = roi_with_ci(_rows(t))
        assert got["clusters"] == 3
        assert abs(got["se"] - round(_iid_se(t), 6)) < 1e-9

    def test_the_point_estimate_never_moves(self):
        flat = [(100.0, 5.0, "a"), (100.0, -3.0, "b"), (100.0, 8.0, "c")]
        clumped = [(100.0, 5.0, "x"), (100.0, -3.0, "x"), (100.0, 8.0, "x")]
        assert roi_with_ci(_rows(flat))["roi"] == \
               roi_with_ci(_rows(clumped))["roi"]


class TestCopiesOfOneGameAreOneResult:
    def test_three_legs_of_one_match_widen_the_interval(self):
        t = []
        for i in range(40):
            v = 5.0 if i < 30 else -9.0
            for _ in range(3):
                t.append((100.0, v, f"g{i}"))
        flat = [(s, p, f"u{j}") for j, (s, p, _) in enumerate(t)]

        iid = roi_with_ci(_rows(flat))
        got = roi_with_ci(_rows(t))
        assert iid["clusters"] == 120 and got["clusters"] == 40
        assert got["roi"] == iid["roi"]
        assert got["se"] > iid["se"]
        assert 1.6 < got["deff"] < 1.9

    def test_a_proven_verdict_can_be_withdrawn_by_it(self):
        t = []
        for i in range(40):
            v = 5.0 if i < 30 else -9.0
            for _ in range(3):
                t.append((100.0, v, f"g{i}"))
        flat = [(s, p, f"u{j}") for j, (s, p, _) in enumerate(t)]
        assert "PROVEN POSITIVE" in assess(_rows(flat))["verdict"]
        assert "INSUFFICIENT" in assess(_rows(t))["verdict"]


class TestTheProjectionCannotOversell:
    def test_a_negative_estimate_promises_nothing(self):
        # THE CASE WE ARE ACTUALLY IN. required_n takes abs(), so a
        # negative ROI used to yield a finite "n needed" that reads as
        # progress toward being proven profitable. It is not.
        # Values must VARY or the dispersion is zero and the projection
        # block is skipped for an unrelated reason.
        t = [(100.0, -9.0 if i % 3 else 2.0, f"g{i}") for i in range(30)]
        got = assess(_rows(t), target_edge=0.02)
        assert got["roi"] < 0
        assert got["observed_provable"] is False
        assert got["n_needed_at_observed"] is None
        assert "no sample size" in got["observed_note"]

    def test_a_positive_estimate_gets_a_real_number(self):
        t = [(100.0, 4.0 if i % 3 else -6.0, f"g{i}") for i in range(60)]
        got = assess(_rows(t), target_edge=0.02)
        if got["roi"] > 0:
            assert got["observed_provable"] is True
            assert got["n_needed_at_observed"] > 0

    def test_sigma_is_derived_from_the_clustered_residuals(self):
        # The projection must be sized off the SAME dispersion as the
        # interval it is projecting toward. Sizing off the iid sigma
        # would promise a proof date the interval can never reach.
        t = []
        for i in range(30):
            for _ in range(3):
                t.append((100.0, 5.0 if i < 20 else -8.0, f"g{i}"))
        got = roi_with_ci(_rows(t))
        # se = sigma * sqrt(g) / tot_s  <=>  sigma = se * tot_s / sqrt(g)
        g, ts = got["clusters"], got["staked"]
        implied = got["se"] * ts / math.sqrt(g)
        assert abs(got["sigma_per_dollar"] - round(implied * g / ts, 6)) < 1e-6

    def test_the_interval_still_uses_the_same_z(self):
        t = [(100.0, 4.0, "a"), (250.0, -9.0, "b"), (80.0, 12.0, "c")]
        got = roi_with_ci(_rows(t))
        lo, hi = got["ci95"]
        assert abs((hi - lo) / 2 - Z95 * got["se"]) < 1e-5
