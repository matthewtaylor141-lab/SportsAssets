"""Latency has been the standing excuse. Make it a measurement.

Every account of why our copies earn less than the whales they mirror
has run through latency, and it has never been measured on our own
settled money. What existed was TRUEEDGE-FAST, which re-prices paper
copies at a reaction time we did not have. That is arithmetic on one
book, not two arms of an experiment, and it cannot answer the question.

The chain lane's 429 on 2026-08-31 created the arms by accident: the
same whale, roster, gates and clip, detected at -0.65s before and
~310s after. /api/admin/latency-cohort splits the settled book on that.

The danger is that the split LOOKS causal and is not — the lane is
collinear with the calendar, so a bad week lands entirely in one arm.
These tests pin the properties that stop it being read that way:

  * the buckets partition the rows; none is double-counted or lost
  * bucketing is on the COPY lag, the interval that priced the fill,
    not on the detection lag
  * the endpoint states the confound itself when the arms share no day
  * it reads and cannot write
"""
import ast
import asyncio
import inspect

from sportsassets.api import app as app_mod


def _node():
    tree = ast.parse(inspect.getsource(app_mod))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.name == "admin_latency_cohort":
            return n
    raise AssertionError("admin_latency_cohort not found")


def _code_only():
    n = _node()
    body = n.body[1:] if (n.body and isinstance(n.body[0], ast.Expr)
                          and isinstance(n.body[0].value, ast.Constant)) \
        else n.body
    return "\n".join(ast.unparse(x) for x in body)


class _Row(dict):
    def keys(self):
        return list(super().keys())


class _Pool:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries: list[str] = []

    async def fetch(self, sql, *a):
        self.queries.append(sql)
        return self.rows

    async def fetchrow(self, sql, *a):
        self.queries.append(sql)
        return None

    async def fetchval(self, sql, *a):
        self.queries.append(sql)
        return None

    async def execute(self, sql, *a):  # pragma: no cover - must not run
        self.queries.append(sql)
        raise AssertionError("this endpoint must not write")


def _call(rows, **kw):
    pool = _Pool(rows)

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        return asyncio.run(app_mod.admin_latency_cohort(**kw)), pool
    finally:
        app_mod.get_pool = orig


def _r(stake=100.0, pnl=0.0, lane="chain", day="2026-08-26",
       det_lag=-0.65, copy_lag=2.0, event_key=None, whale="rn1"):
    return _Row(stake=stake, pnl=pnl, whale=whale, event_key=event_key,
                lane=lane, day=day, det_lag=det_lag, copy_lag=copy_lag)


# --------------------------------------------------------- the partition

def test_every_row_lands_in_exactly_one_bucket():
    """A bucket scheme that overlaps double-counts money and one that
    gaps loses it. Either way the arms stop summing to the book."""
    rows = [_r(copy_lag=x) for x in
            (0.0, 4.9, 5.0, 29.9, 30.0, 119.9, 120.0, 299.9, 300.0, 5000.0)]
    out, _ = _call(rows)
    total = sum(b["n"] for b in out["by_copy_lag"].values())
    assert total == len(rows) == out["n_settled"]


def test_the_boundaries_are_half_open_upward():
    """Exactly 5.0s belongs to 5-30s, not to 0-5s. Pinned because the
    5s edge is the one TRUEEDGE-FAST counterfactuals against, so an
    off-by-one there moves rows between the arms being compared."""
    out, _ = _call([_r(copy_lag=5.0)])
    assert out["by_copy_lag"]["0-5s"]["n"] == 0
    assert out["by_copy_lag"]["5-30s"]["n"] == 1


def test_an_unbounded_tail_bucket_catches_the_poller_lane():
    out, _ = _call([_r(copy_lag=310.0), _r(copy_lag=98000.0)])
    assert out["by_copy_lag"]["300s+"]["n"] == 2


# ------------------------------------------- the lag that actually prices

def test_bucketing_is_on_the_copy_lag_not_the_detection_lag():
    """THE MUTATION THAT MATTERS. A gate that sits on a fast decode
    still buys late, and it is the his-trade-to-our-trade interval that
    set the price we paid. Bucketing on detection would file this row
    as a 0-5s copy and credit the fast arm with a slow fill."""
    out, _ = _call([_r(det_lag=-0.65, copy_lag=310.0)])
    assert out["by_copy_lag"]["300s+"]["n"] == 1
    assert out["by_copy_lag"]["0-5s"]["n"] == 0


def test_a_row_with_no_copy_lag_is_dropped_from_the_buckets_not_bucketed_as_zero():
    """A NULL placed_at or ts means we do not know when we bought. Zero
    is the most flattering possible guess, so it must not be the
    default."""
    out, _ = _call([_r(copy_lag=None), _r(copy_lag=1.0)])
    assert out["by_copy_lag"]["0-5s"]["n"] == 1
    assert sum(b["n"] for b in out["by_copy_lag"].values()) == 1


# ------------------------------------------------------------- the numbers

def test_roi_is_the_ratio_of_summed_pnl_to_summed_stake_per_bucket():
    out, _ = _call([_r(stake=100.0, pnl=10.0, copy_lag=1.0),
                    _r(stake=300.0, pnl=-2.0, copy_lag=1.0)])
    b = out["by_copy_lag"]["0-5s"]
    assert b["n"] == 2
    assert b["staked"] == 400.0
    assert b["pnl"] == 8.0
    assert abs(b["roi"] - 0.02) < 1e-9


def test_a_single_copy_bucket_gets_no_interval():
    """One settled copy cannot carry a 95% interval, and a bucket that
    invented one would put a fabricated arm beside a real one."""
    out, _ = _call([_r(copy_lag=1.0)])
    assert out["by_copy_lag"]["0-5s"]["ci95"] is None


def test_copies_on_one_event_cluster_into_one_residual():
    """Three legs of one match settle on one result. Counting them as
    three independent copies narrows the interval in the direction that
    declares an arm proven."""
    same = [_r(pnl=5.0, copy_lag=1.0, event_key="nfl-kc-buf") for _ in range(3)]
    out, _ = _call(same)
    assert out["by_copy_lag"]["0-5s"]["clusters"] == 1


def test_the_median_copy_lag_is_reported_per_bucket():
    out, _ = _call([_r(copy_lag=301.0), _r(copy_lag=310.0),
                    _r(copy_lag=400.0)])
    assert out["by_copy_lag"]["300s+"]["copy_lag_p50"] == 310.0


# --------------------------------------------------------- the confound

def test_lanes_that_share_no_day_are_declared_confounded():
    """The failure this endpoint exists to prevent. Chain rows are one
    week and poller rows the next, so anything else that changed over
    those days is indistinguishable from latency."""
    out, _ = _call([_r(lane="chain", day="2026-08-26", copy_lag=1.0),
                    _r(lane="poll", day="2026-08-31", copy_lag=310.0)])
    assert out["confounded"] is True
    assert "CONFOUNDED" in out["reading"]
    assert out["shared_days"] == []


def test_lanes_that_share_a_day_are_not_declared_confounded():
    out, _ = _call([_r(lane="chain", day="2026-08-31", copy_lag=1.0),
                    _r(lane="poll", day="2026-08-31", copy_lag=310.0)])
    assert out["confounded"] is False
    assert "OVERLAPPING" in out["reading"]
    assert out["shared_days"] == ["2026-08-31"]


def test_one_lane_alone_is_not_a_comparison():
    """Absence of a second arm must not read as a result. Today the
    chain lane is dead, so a single-lane cohort is the likely case."""
    out, _ = _call([_r(lane="poll", copy_lag=310.0)])
    assert out["confounded"] is False
    assert "SINGLE LANE" in out["reading"]


def test_each_bucket_discloses_the_days_it_draws_from():
    """The confound is checked on lanes; the same trap applies bucket by
    bucket, so the raw days travel with every arm."""
    out, _ = _call([_r(day="2026-08-26", copy_lag=1.0),
                    _r(day="2026-08-27", copy_lag=1.0)])
    assert out["by_copy_lag"]["0-5s"]["days"] == ["2026-08-26", "2026-08-27"]


# ------------------------------------------------------------ the scoping

def test_the_whale_filter_is_a_bound_parameter_not_interpolated():
    _, pool = _call([], whale="rn1")
    sql = pool.queries[0]
    assert "$2" in sql
    assert "rn1" not in sql


def test_no_whale_filter_leaves_the_query_unbound():
    _, pool = _call([])
    assert "$2" not in pool.queries[0]


def test_open_positions_are_excluded_from_the_cohort():
    """A 'filled' row still holds shares: its realised partial gain is
    in pnl while the exposure it carries is not, which biases the
    headline optimistic. Same terminal-rows-only rule the proof cohort
    uses."""
    sql = pool_sql()
    assert "'settled', 'cashed_out'" in sql
    assert "lo.pnl IS NOT NULL" in sql


def test_the_desk_and_underdog_sleeves_are_not_copies():
    assert "'manual', 'underdog'" in pool_sql()


def pool_sql():
    _, pool = _call([])
    return pool.queries[0]


# ------------------------------------------------------------- read only

def test_the_endpoint_cannot_write():
    code = _code_only().lower()
    for verb in ("insert ", "update ", "delete ", "execute("):
        assert verb not in code, f"latency-cohort must not {verb.strip()}"


def test_it_is_admin_gated():
    for d in _node().decorator_list:
        if isinstance(d, ast.Call) and any(
                k.arg == "dependencies" for k in d.keywords):
            return
    raise AssertionError("latency-cohort must require admin")
