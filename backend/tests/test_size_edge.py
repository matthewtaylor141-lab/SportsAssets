"""His edge by the size of his bet (analytics/size_edge.py).

The dust floor refuses copies under $10; whether his sub-$10 probes carry
edge is a number on his resolved book, read here with the same
ratio-estimator interval and thirty-game floor every verdict uses.
"""
import asyncio

from sportsassets.analytics import size_edge as se


def _row(size, price, payout, key):
    return {"size": size, "price": price, "payout": payout, "event_key": key}


def test_buckets_are_by_his_stake_not_his_share_count():
    assert se.bucket_of(0.5 * 10) == "<$10"          # 10 shares at 50c = $5
    assert se.bucket_of(10.0) == "$10-50"
    assert se.bucket_of(49.99) == "$10-50"
    assert se.bucket_of(50.0) == "$50-250"
    assert se.bucket_of(250.0) == ">=$250"
    assert se.bucket_of(1e9) == ">=$250"
    assert se.bucket_of(0) is None and se.bucket_of(-3) is None
    assert se.bucket_of("x") is None


def test_each_bucket_is_its_own_interval_and_the_shares_sum_to_one():
    rows = []
    for i in range(36):                                   # 36 games of $5 probes, winners
        rows.append(_row(10, 0.5, 1.0 if i % 4 else 0.0, f"g{i}"))
    for i in range(36):                                   # 36 games of $100 bets, losers
        rows.append(_row(200, 0.5, 0.0 if i % 4 else 1.0, f"h{i}"))
    out = se.score(rows)
    small, big = out["buckets"]["<$10"], out["buckets"]["$50-250"]
    assert small["n"] == 36 and small["clusters"] == 36
    assert small["roi"] > 0 and small["ci95"][0] > 0
    assert small["verdict"] == "POSITIVE at 95%"
    assert big["roi"] < 0 and big["verdict"] == "NEGATIVE at 95%"
    shares = [b["stake_share"] for b in out["buckets"].values() if b["stake_share"]]
    assert abs(sum(shares) - 1.0) < 1e-9
    assert out["buckets"]["$10-50"]["n"] == 0
    assert out["all"]["n"] == 72
    assert out["reading"].startswith("SMALL PROBES EARN")


def test_under_thirty_games_the_small_bucket_is_provisional():
    rows = [_row(10, 0.5, 1.0, f"g{i}") for i in range(12)]
    out = se.score(rows)
    assert out["buckets"]["<$10"]["verdict"].startswith("PROVISIONAL")
    assert out["reading"].startswith("NOT DEMONSTRATED")


def test_a_flat_small_bucket_past_the_floor_is_named_flat():
    """First live read: rn1 +0.04% [-3.8%, +3.9%] on 4,903 games is a
    tight band around nothing, not an open question."""
    rows = [_row(10, 0.5, 1.0 if i % 2 else 0.0, f"g{i}") for i in range(200)]
    out = se.score(rows)
    b = out["buckets"]["<$10"]
    assert b["clusters"] == 200 and b["ci95"][0] < 0 < b["ci95"][1]
    assert out["reading"].startswith("SMALL PROBES ARE FLAT")
    assert "costs nothing proven" in out["reading"]


def test_a_losing_small_bucket_says_the_floor_is_right():
    rows = [_row(10, 0.5, 0.0 if i % 5 else 1.0, f"g{i}") for i in range(40)]
    out = se.score(rows)
    assert out["buckets"]["<$10"]["ci95"][1] < 0
    assert out["reading"].startswith("SMALL PROBES LOSE")


def test_legs_of_one_game_are_one_cluster():
    rows = [_row(10, 0.5, 1.0, "one-game") for _ in range(40)]
    out = se.score(rows)
    assert out["buckets"]["<$10"]["clusters"] == 1
    assert out["buckets"]["<$10"]["verdict"] != "POSITIVE at 95%"


def test_bad_rows_are_dropped_not_zero_filled():
    rows = [_row(10, 0.5, 1.0, "g1"), _row(10, None, 1.0, "g2"),
            _row(10, 0.5, None, "g3"), _row(0, 0.5, 1.0, "g4"),
            _row(10, 1.2, 1.0, "g5"), {"size": "x", "price": 0.5, "payout": 1}]
    assert se.score(rows)["n_rows"] == 1


def test_the_cohort_reads_resolved_buys_and_reports_unresolvable_payouts():
    class _Pool:
        def __init__(self):
            self.sql = []

        async def fetch(self, sql, *a):
            self.sql.append((sql, a))
            return [{"size": 10.0, "price": 0.5, "outcome_index": 0,
                     "resolved_prices": "[1, 0]", "event_key": "g1"},
                    {"size": 10.0, "price": 0.5, "outcome_index": 5,
                     "resolved_prices": "[1, 0]", "event_key": "g2"}]

    pool = _Pool()
    out = asyncio.run(se.cohort_size_edge(pool, "RN1", 30))
    assert out["whale"] == "rn1" and out["days"] == 30
    assert out["unresolvable_payout"] == 1 and out["n_rows"] == 1
    sql, args = pool.sql[0]
    assert "t.side = 'BUY'" in sql and "COALESCE(m.resolved, false) = true" in sql
    assert args == ("rn1", 30)
