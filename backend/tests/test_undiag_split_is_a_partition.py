"""The undiagnosed split must PARTITION the residual, not re-attribute it.

`undiagnosed` was a residual — `n - listed - 0ev` — and with the roster
cut to one book it became the largest unattributed number in the funnel
(rn1: 23,005). The endpoint's own comment already conceded one shape
inside it is ours to win (a diag whose LATER query found events), and
then never counted it, so the whole residual read as unknown.

Splitting a residual is the easiest place in this file to silently move
money-shaped numbers around. These tests pin the two properties that
make the split safe to read:

  1. later_ev + undiag_other == undiagnosed, EXACTLY, for every row
     shape and for the per-whale copy.
  2. The five pre-existing winnable counters are byte-identical to what
     they were before the split existed — a row that was
     listed_mapper_fail must not become later_ev.

Property 2 is the one that matters for the ceiling: `listed_mapper_fail`
is what the owner's coverage question is answered from, and if the new
filter stole rows from it the answer would silently shrink.
"""
import asyncio

import pytest

from sportsassets.api import app as app_mod


class _Rec(dict):
    """asyncpg rows are mappings; the endpoint reads them by key."""


class _Pool:
    """Serves the three reads api_copy_unmapped makes, in order."""

    def __init__(self, rows, shapes=()):
        self.rows = rows
        self.shapes = list(shapes)
        self.queries = []

    async def fetch(self, sql, *a):
        self.queries.append(sql)
        # Dispatch on the shape query's own SELECT, never on a word
        # that also appears in the main query's comments.
        if "left(lo.error" in sql:
            return self.shapes
        return self.rows

    async def fetchrow(self, sql, *a):
        self.queries.append(sql)
        return _Rec(rows=0, catalog_has_token=0)


def _row(**kw):
    """A grouped funnel row with every counter defaulted to zero."""
    base = dict(whale="rn1", slug="nba-lal-bos-2026-08-31", reason="unmapped",
                n=0, n_7d=0, n_listed=0, n_0ev=0, n_listed_7d=0, n_0ev_7d=0,
                n_exact404=0, n_exact404_unlisted=0, n_exact404_7d=0,
                n_exact404_unlisted_7d=0, n_later_ev=0, n_later_ev_7d=0)
    base.update(kw)
    return _Rec(base)


def _call(rows, shapes=()):
    pool = _Pool(rows, shapes)

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        return asyncio.run(app_mod.api_copy_unmapped(days=0))
    finally:
        app_mod.get_pool = orig


# ---------------------------------------------------------------- partition

@pytest.mark.parametrize("n,listed,zev,later", [
    (100, 0, 0, 0),        # everything unexplained
    (100, 0, 0, 100),      # everything winnable-by-later-ev
    (100, 30, 20, 25),     # the mixed case
    (100, 50, 50, 0),      # no residual at all
    (1, 0, 0, 1),          # single row
    (23005, 0, 0, 9000),   # rn1-shaped
])
def test_later_ev_plus_other_equals_undiagnosed(n, listed, zev, later):
    out = _call([_row(n=n, n_listed=listed, n_0ev=zev, n_later_ev=later)])
    w = out["winnable"]
    assert w["later_ev"] + w["undiag_other"] == w["undiagnosed"], (
        f"split is not a partition: {w['later_ev']} + "
        f"{w['undiag_other']} != {w['undiagnosed']}")


def test_partition_holds_per_whale_too():
    """The per-whale copy is a SEPARATE accumulation of the same terms —
    a partition that holds roster-wide can still be wrong per whale."""
    out = _call([
        _row(whale="rn1", n=100, n_listed=10, n_0ev=5, n_later_ev=40),
        _row(whale="hrh", n=60, n_listed=20, n_0ev=0, n_later_ev=10),
    ])
    assert out["by_whale_winnable"], "per-whale split disappeared"
    for wh in out["by_whale_winnable"]:
        assert wh["later_ev"] + wh["undiag_other"] == wh["undiagnosed"], (
            f"{wh['whale']}: per-whale split is not a partition")


def test_partition_holds_across_many_grouped_rows():
    """The endpoint sums over grouped rows; a partition that holds for
    one row can break on accumulation."""
    rows = [_row(n=10 + i, n_listed=i, n_0ev=1, n_later_ev=i % 4)
            for i in range(25)]
    out = _call(rows)
    w = out["winnable"]
    assert w["later_ev"] + w["undiag_other"] == w["undiagnosed"]


# ------------------------------------------------- no silent re-attribution

def test_the_five_original_counters_are_untouched():
    """later_ev must sit BESIDE the winnable split, never inside it.
    listed_mapper_fail is what the coverage ceiling is read from."""
    r = _row(n=100, n_listed=30, n_0ev=20, n_listed_7d=7, n_0ev_7d=4,
             n_later_ev=25, n_later_ev_7d=6)
    w = _call([r])["winnable"]
    assert w["listed_mapper_fail"] == 30
    assert w["venue_unlisted"] == 20
    assert w["listed_mapper_fail_7d"] == 7
    assert w["venue_unlisted_7d"] == 4
    assert w["undiagnosed"] == 50          # still n - listed - 0ev


def test_later_ev_never_exceeds_the_residual_it_splits():
    """A later_ev count larger than the residual would mean the SQL
    filters overlap — the row was counted twice. Undiag_other going
    negative is the symptom; this asserts the invariant directly."""
    w = _call([_row(n=100, n_listed=40, n_0ev=40, n_later_ev=20)])["winnable"]
    assert w["undiag_other"] >= 0, (
        "undiag_other went negative — the later_ev filter overlaps "
        "listed/0ev, so rows are being counted in two buckets")


def test_policy_refusals_stay_out_of_the_split():
    """no_stack / never_add / one_per_game are decisions, not mapping
    misses. The reason guard already excluded them from the winnable
    buckets; the new counters must inherit that guard."""
    out = _call([_row(reason="no_stack", n=500, n_later_ev=500)])
    w = out["winnable"]
    assert w["later_ev"] == 0, "a policy refusal was counted as winnable"
    assert w["undiag_other"] == 0
    assert w["undiagnosed"] == 0


# ------------------------------------------------------------------ shapes

def test_undiag_shapes_are_surfaced():
    """Sizing the residual cannot say what it is; the shape sample is
    the part that picks the next fix."""
    out = _call([_row(n=10)],
                shapes=[_Rec(shape="unmapped: no candidates for ", n=9, n_7d=2)])
    assert out["undiag_shapes"] == [
        {"shape": "unmapped: no candidates for ", "n": 9, "n_7d": 2}]


def test_shape_query_excludes_the_already_attributed():
    """The sample must describe the RESIDUAL. If it also matched
    sides:[ or 0ev rows it would describe the buckets we already
    understand and send the next fix at a solved problem."""
    pool = _Pool([_row(n=1)])

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        asyncio.run(app_mod.api_copy_unmapped(days=0))
    finally:
        app_mod.get_pool = orig

    shape_sql = [q for q in pool.queries if "left(lo.error" in q]
    assert shape_sql, "the shape sample query never ran"
    q = shape_sql[0]
    assert "NOT LIKE '%sides:[%'" in q
    assert "NOT LIKE '%0ev%'" in q
    assert "!~ ':[1-9][0-9]*ev'" in q
    assert "LIKE 'unmapped%'" in q, "the reason guard is missing"


def test_shape_query_is_capped():
    """23,005 rows of distinct diag text is not a diagnostic."""
    pool = _Pool([_row(n=1)])

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        asyncio.run(app_mod.api_copy_unmapped(days=0))
    finally:
        app_mod.get_pool = orig
    q = [q for q in pool.queries if "left(lo.error" in q][0]
    assert "LIMIT" in q.upper()


# ------------------------------------------------------------- read-only

def test_the_endpoint_only_reads():
    """A funnel diagnostic that can write is a funnel diagnostic that
    can corrupt the funnel."""
    pool = _Pool([_row(n=1)])

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        asyncio.run(app_mod.api_copy_unmapped(days=0))
    finally:
        app_mod.get_pool = orig

    for q in pool.queries:
        up = " ".join(q.upper().split())
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ",
                     "TRUNCATE ", "CREATE "):
            assert verb not in up, f"{verb.strip()} in a diagnostic read"
