"""A cohort gap without an interval is a coin flip with a decimal point.

/api/admin/copy-tolerance grades Option A by splitting copies into the
fills that exist only because we paid over the whale's price (marginal)
and the ones that would have filled anyway (parity). It reported a bare
ROI per cohort and nothing else.

On 2026-09-01 that read, for rn1:

    marginal  n=339 settled=335  staked=$32280.52  roi=-0.0001
    parity    n=78  settled=64   staked=$6611.62   roi=+0.1675

and it was about to be used to move 83% of one whale's capital from one
cohort to the other. On 64 settled copies +16.75% may be nothing at all.
The endpoint could not say, because it had no interval -- and "the bigger
number wins" is how a coin flip ships as a strategy.

These tests pin the properties that make the comparison decision-grade:

  * every cohort carries a cluster-robust interval, clustered on the
    whale's event the same way the proof cohort clusters
  * a cohort too small to carry an interval says so instead of guessing
  * the COMPARISON is stated by the endpoint, and overlapping intervals
    are reported as not-established rather than as a winner
  * only SETTLED rows reach the interval -- open positions have no
    realised P&L and would silently dilute the cohort being judged
"""
import ast
import asyncio
import inspect

from sportsassets.api import app as app_mod


def _node():
    tree = ast.parse(inspect.getsource(app_mod))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.name == "api_copy_tolerance":
            return n
    raise AssertionError("api_copy_tolerance not found")


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

    async def execute(self, sql, *a):  # pragma: no cover - must not run
        raise AssertionError("copy-tolerance must not write")


def _call(rows, **kw):
    pool = _Pool(rows)

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        return asyncio.run(app_mod.api_copy_tolerance(**kw)), pool
    finally:
        app_mod.get_pool = orig


def _r(whale="rn1", his=0.50, fp=0.52, staked=100.0, pnl=0.0,
       status="settled", event_key=None):
    """Default is a MARGINAL row: fp above his price."""
    return _Row(whale=whale, his=his, fp=fp, staked=staked, pnl=pnl,
                status=status, intent="ORDER_INTENT_BUY_LONG",
                event_key=event_key)


def _rows(out, whale, cohort):
    return next(d for d in out["rows"]
                if d["whale"] == whale and d["cohort"] == cohort)


# ------------------------------------------------------- the interval

def test_every_cohort_carries_an_interval():
    out, _ = _call([_r(pnl=5.0, event_key=f"g{i}") for i in range(6)])
    d = _rows(out, "rn1", "marginal")
    assert d["ci95"] is not None
    assert len(d["ci95"]) == 2


def test_a_cohort_with_one_settled_copy_refuses_an_interval():
    """One settled copy cannot carry a 95% interval. Inventing one here
    would put a fabricated cohort beside a real one in the very
    comparison that moves capital."""
    out, _ = _call([_r(pnl=5.0), _r(status="filled")])
    d = _rows(out, "rn1", "marginal")
    assert d["ci95"] is None
    assert "NO INTERVAL" in d["verdict"]


def test_open_positions_are_excluded_from_the_interval():
    """A 'filled' row has no realised P&L. Counting it would dilute the
    cohort under judgement -- and it is the SMALLER cohort that dilutes
    fastest."""
    settled = [_r(pnl=10.0, event_key=f"g{i}") for i in range(4)]
    open_rows = [_r(status="filled", event_key=f"o{i}") for i in range(20)]
    out, _ = _call(settled + open_rows)
    d = _rows(out, "rn1", "marginal")
    assert d["settled"] == 4
    assert d["n"] == 24
    assert d["clusters"] == 4


def test_copies_on_one_event_cluster_into_one_residual():
    """Three legs of one match settle on one result. Treating them as
    three independent copies narrows the interval in the direction that
    declares a cohort proven."""
    out, _ = _call([_r(pnl=5.0, event_key="atp-topo-damas") for _ in range(3)])
    assert _rows(out, "rn1", "marginal")["clusters"] == 1


# -------------------------------------------------------- the verdict

def test_a_cohort_whose_interval_excludes_zero_above_says_it_earns():
    out, _ = _call([_r(staked=100.0, pnl=20.0, event_key=f"g{i}")
                    for i in range(8)])
    assert "EARNS at 95%" in _rows(out, "rn1", "marginal")["verdict"]


def test_a_cohort_whose_interval_excludes_zero_below_says_it_loses():
    out, _ = _call([_r(staked=100.0, pnl=-20.0, event_key=f"g{i}")
                    for i in range(8)])
    assert "LOSES at 95%" in _rows(out, "rn1", "marginal")["verdict"]


def test_a_cohort_straddling_zero_is_not_demonstrated():
    """The result that must never round toward the convenient answer."""
    pnls = [50.0, -50.0, 40.0, -45.0, 30.0, -35.0]
    out, _ = _call([_r(staked=100.0, pnl=p, event_key=f"g{i}")
                    for i, p in enumerate(pnls)])
    d = _rows(out, "rn1", "marginal")
    assert "NOT DEMONSTRATED" in d["verdict"]
    assert d["ci95"][0] < 0 < d["ci95"][1]


# ----------------------------------------------------- the comparison

def _two_cohorts(marg_pnl, par_pnl, k=8):
    """Marginal rows fill ABOVE his price, parity AT or below."""
    rows = [_r(his=0.50, fp=0.52, staked=100.0, pnl=marg_pnl,
               event_key=f"m{i}") for i in range(k)]
    rows += [_r(his=0.50, fp=0.50, staked=100.0, pnl=par_pnl,
                event_key=f"p{i}") for i in range(k)]
    return rows


def test_the_endpoint_states_the_comparison_not_just_two_numbers():
    out, _ = _call(_two_cohorts(0.0, 20.0))
    assert "rn1" in out["by_whale"]
    assert "reading" in out["by_whale"]["rn1"]


def test_overlapping_intervals_are_reported_as_not_established():
    """THE FAILURE THIS EXISTS TO PREVENT. Two point estimates far apart
    with overlapping intervals is one book, not two cohorts, and moving
    capital on it is acting on noise."""
    pnls_a = [60.0, -55.0, 50.0, -40.0]
    pnls_b = [-50.0, 45.0, -60.0, 70.0]
    rows = [_r(his=0.5, fp=0.52, staked=100.0, pnl=p, event_key=f"m{i}")
            for i, p in enumerate(pnls_a)]
    rows += [_r(his=0.5, fp=0.50, staked=100.0, pnl=p, event_key=f"p{i}")
             for i, p in enumerate(pnls_b)]
    out, _ = _call(rows)
    c = out["by_whale"]["rn1"]
    assert c["separated"] is False
    assert "OVERLAPPING" in c["reading"]
    assert "Do not move capital" in c["reading"]


def test_clearly_separated_cohorts_are_named_with_the_winner():
    out, _ = _call(_two_cohorts(-20.0, 20.0))
    c = out["by_whale"]["rn1"]
    assert c["separated"] is True
    assert "SEPARATED" in c["reading"]
    assert "parity" in c["reading"]


def test_a_whale_missing_one_cohort_is_incomparable_not_a_winner():
    """A whale we never paid tolerance on has no marginal cohort. That
    is an absence of evidence and must not read as parity winning."""
    out, _ = _call([_r(his=0.5, fp=0.50, pnl=20.0, event_key=f"p{i}")
                    for i in range(4)])
    assert "INCOMPARABLE" in out["by_whale"]["rn1"]["reading"]


def test_whales_are_compared_independently():
    rows = _two_cohorts(-20.0, 20.0)
    rows += [_r(whale="swisstony", his=0.5, fp=0.52, staked=100.0,
                pnl=15.0, event_key=f"sm{i}") for i in range(8)]
    rows += [_r(whale="swisstony", his=0.5, fp=0.50, staked=100.0,
                pnl=15.0, event_key=f"sp{i}") for i in range(8)]
    out, _ = _call(rows)
    assert out["by_whale"]["rn1"]["separated"] is True
    assert out["by_whale"]["swisstony"]["separated"] is False


# --------------------------------------------------------- the wiring

def test_the_event_key_is_joined_not_invented():
    _, pool = _call([])
    sql = pool.queries[0]
    assert "market_tokens" in sql and "markets" in sql
    assert "event_slug" in sql
    assert "LEFT JOIN" in sql


def test_the_cohorts_are_never_summed():
    """Blending is how a change like this gets graded harmless: parity
    dominates the count and drowns the marginal signal."""
    out, _ = _call(_two_cohorts(-20.0, 20.0))
    assert len({(d["whale"], d["cohort"]) for d in out["rows"]}) == 2
    assert all(d["cohort"] in ("marginal", "parity") for d in out["rows"])


def test_the_endpoint_cannot_write():
    src = ast.unparse(_node()).lower()
    for verb in ("insert ", "update ", "delete ", "execute("):
        assert verb not in src, f"copy-tolerance must not {verb.strip()}"
