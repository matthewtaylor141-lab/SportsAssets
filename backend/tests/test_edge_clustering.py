"""The lot is not the unit of independence.

A whale who backs the moneyline, the spread and the total on one game
holds three lots driven by ONE random variable. The iid ratio-estimator
divided the variance by roughly the number of legs, so the interval came
out too narrow — in exactly the direction that funds people.

Measured on simulated books with four correlated legs per game and a
TRUE EDGE OF ZERO, the iid formula rejected the null 13.8% of the time
against a nominal 5%: the "95%" gate was really firing at about 86%,
before any multiplicity correction at all.

These pins hold the fix to being a strict generalisation — same point
estimate, same algebra, identical answer when every cluster holds one
lot — so it can never silently become a different statistic.
"""

import math

from sportsassets.analytics.merge_pnl import _Z95, _lot_interval


def _o(lots):
    """Build the accumulator dict from [(stake, pnl, cluster_key), ...]."""
    o = {"lots": 0, "lot_s": 0.0, "lot_p": 0.0,
         "lot_ss": 0.0, "lot_pp": 0.0, "lot_ps": 0.0, "clus": {}}
    for s, p, k in lots:
        o["lots"] += 1
        o["lot_s"] += s
        o["lot_p"] += p
        o["lot_ss"] += s * s
        o["lot_pp"] += p * p
        o["lot_ps"] += p * s
        c = o["clus"].get(k)
        if c is None:
            o["clus"][k] = [s, p]
        else:
            c[0] += s
            c[1] += p
    return o


def _iid_se(lots):
    """The pre-2026-08-30 formula, computed independently here so the
    pins compare against arithmetic rather than against the code."""
    n = len(lots)
    ts = sum(s for s, _, _ in lots)
    tp = sum(p for _, p, _ in lots)
    r = tp / ts
    ss = sum((p - r * s) ** 2 for s, p, _ in lots)
    return math.sqrt(n / (n - 1) * ss) / ts


class TestItIsAStrictGeneralisation:
    def test_one_lot_per_cluster_reproduces_the_old_number_exactly(self):
        lots = [(100.0, 4.0, "a"), (250.0, -9.0, "b"),
                (80.0, 12.0, "c"), (400.0, -3.0, "d"),
                (55.0, 7.5, "e")]
        got = _lot_interval(_o(lots))
        assert got["edge_clusters"] == 5
        assert abs(got["edge_se"] - round(_iid_se(lots), 6)) < 1e-9, \
            "with G == n the cluster formula IS the iid formula"
        assert abs(got["edge_deff"] - 1.0) < 1e-6

    def test_the_point_estimate_never_moves(self):
        # Clustering changes the INTERVAL, never the return itself.
        flat = [(100.0, 5.0, "g1"), (100.0, -3.0, "g2"),
                (100.0, 8.0, "g3"), (100.0, -1.0, "g4")]
        clumped = [(100.0, 5.0, "g1"), (100.0, -3.0, "g1"),
                   (100.0, 8.0, "g2"), (100.0, -1.0, "g2")]
        assert _lot_interval(_o(flat))["edge_roi"] == \
               _lot_interval(_o(clumped))["edge_roi"]


class TestCorrelatedLegsWidenTheInterval:
    def test_same_game_legs_that_win_together_widen_it(self):
        # Four legs, two games, both legs of each game moving the same
        # way — the shape of a whale betting a side and its spread.
        clumped = [(100.0, 9.0, "gameA"), (100.0, 11.0, "gameA"),
                   (100.0, -8.0, "gameB"), (100.0, -10.0, "gameB")]
        spread = [(100.0, 9.0, "1"), (100.0, 11.0, "2"),
                  (100.0, -8.0, "3"), (100.0, -10.0, "4")]
        wide = _lot_interval(_o(clumped))
        narrow = _lot_interval(_o(spread))
        assert wide["edge_se"] > narrow["edge_se"], \
            "correlated legs must not buy false precision"
        assert wide["edge_deff"] > 1.0
        assert wide["edge_clusters"] == 2 and narrow["edge_clusters"] == 4

    def test_a_verdict_can_flip_from_proven_to_not(self):
        # THE CASE THAT MATTERS. 40 games, three legs each, all three
        # legs of a game settling together — 30 games win 5, 10 games
        # lose 9. Mean +1.5 per leg on 100 staked, so R = +1.5%.
        #
        # Read as 120 independent lots that is PROFITABLE at 95%. Read
        # as 40 games it is NOT DEMONSTRATED, because the between-game
        # variance is the only variance there ever was. Nothing about
        # the book changed; only the claim about what is independent.
        lots = []
        for i in range(40):
            v = 5.0 if i < 30 else -9.0
            for _ in range(3):
                lots.append((100.0, v, f"g{i}"))
        flat = [(s, p, str(j)) for j, (s, p, _) in enumerate(lots)]

        iid = _lot_interval(_o(flat))
        assert "PROFITABLE at 95%" in iid["edge_verdict"]
        assert iid["edge_clusters"] == 120

        got = _lot_interval(_o(lots))
        assert "NOT DEMONSTRATED" in got["edge_verdict"], \
            "three legs of one game are one observation, not three"
        assert got["edge_clusters"] == 40
        assert got["edge_roi"] == iid["edge_roi"]      # the return is the same
        assert got["edge_se"] > iid["edge_se"]
        # sqrt(3)-ish: three perfectly-correlated legs per cluster
        assert 1.6 < got["edge_deff"] < 1.9

    def test_offsetting_legs_narrow_it_and_that_is_correct(self):
        # A hedge inside one game is genuinely less risky than two
        # independent bets. The estimator must be allowed to say so,
        # or it is not a variance estimator, it is a penalty.
        hedged = [(100.0, 10.0, "g"), (100.0, -10.0, "g"),
                  (100.0, 4.0, "h"), (100.0, -4.0, "h")]
        got = _lot_interval(_o(hedged))
        assert got["edge_deff"] < 1.0


class TestTheShapeNeverChanges:
    def test_thin_books_still_refuse_and_still_carry_the_new_keys(self):
        got = _lot_interval({"lots": 1, "lot_s": 10.0, "lot_p": 1.0,
                             "lot_ss": 100.0, "lot_pp": 1.0,
                             "lot_ps": 10.0, "clus": {"a": [10.0, 1.0]}})
        assert "INSUFFICIENT" in got["edge_verdict"]
        assert got["edge_clusters"] == 0 and got["edge_deff"] is None

    def test_a_caller_that_supplies_no_clusters_still_works(self):
        # Older callers pass no "clus" at all; they must keep the iid
        # answer rather than crash or silently return a bare estimate.
        lots = [(100.0, 4.0, "a"), (250.0, -9.0, "b"), (80.0, 12.0, "c")]
        o = _o(lots)
        del o["clus"]
        got = _lot_interval(o)
        assert abs(got["edge_se"] - round(_iid_se(lots), 6)) < 1e-9
        assert got["edge_clusters"] == 0

    def test_the_interval_still_uses_the_same_z(self):
        lots = [(100.0, 4.0, "a"), (250.0, -9.0, "b"), (80.0, 12.0, "c")]
        got = _lot_interval(_o(lots))
        lo, hi = got["edge_ci95"]
        assert abs((hi - lo) / 2 - _Z95 * got["edge_se"]) < 1e-5
