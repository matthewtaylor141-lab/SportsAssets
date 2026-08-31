"""The BUY_SHORT ban was written with an exit condition. Count it.

The ban shipped 2026-08-24 after six of six BUY_SHORT fills landed on
the opposite side at the complement price. live_executor states how it
ends, in the code that installed it:

    "That converts every refusal into evidence. By morning the question
     'would the ask guard alone have caught these?' is answered by
     counting rows, not arguing."

Seven mornings passed and nothing counted them. Meanwhile the roster
was cut to rn1, whose entire open book is BUY_SHORT — so the banned
class is not a corner case for this roster, it IS the roster.

These tests pin the properties that make the count trustworthy enough
to put in front of a money-gate decision:

  * it reads and cannot write
  * a bucket that cannot be scored is reported as unscored, never
    folded into either verdict
  * the verdict is stated by the endpoint, and a MIXED result says so
    rather than rounding toward the convenient answer
"""
import ast
import asyncio
import inspect

from sportsassets.api import app as app_mod


def _node():
    tree = ast.parse(inspect.getsource(app_mod))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.name == "api_short_shadow":
            return n
    raise AssertionError("api_short_shadow not found")


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
    def __init__(self, row, samples=()):
        self.row = row
        self.samples = list(samples)
        self.queries = []

    async def fetchrow(self, sql, *a):
        self.queries.append(sql)
        return self.row

    async def fetch(self, sql, *a):
        self.queries.append(sql)
        return self.samples

    async def execute(self, sql, *a):
        self.queries.append(sql)


def _call(row, samples=(), limit=6):
    pool = _Pool(row, samples)

    async def _get_pool():
        return pool

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        return asyncio.run(app_mod.api_short_shadow(limit=limit)), pool
    finally:
        app_mod.get_pool = orig


def _row(**kw):
    base = dict(n=0, n_7d=0, n_scored=0, n_unreadable=0, n_errored=0,
                near_his=0, below=0, above=0, ratio_p50=None,
                gap_p50=None)
    base.update(kw)
    return _Row(base)


# ------------------------------------------------------------- the verdict

def test_a_clean_near_his_result_says_the_ask_guard_would_have_seen_it():
    out, _ = _call(_row(n=100, n_scored=100, near_his=95, below=3, above=2))
    assert "ask guard" in out["reading"]
    assert out["near_his_frac"] == 0.95


def test_a_clean_complement_result_says_the_ban_is_load_bearing():
    out, _ = _call(_row(n=100, n_scored=100, near_his=2, above=98))
    assert "load-bearing" in out["reading"]


def test_a_mixed_result_refuses_to_round_toward_either_answer():
    """The failure mode that matters. A 60/40 split is not evidence for
    reopening a gate that was 6-for-6 wrong, and a summary that reads
    'mostly fine' would be used as though it were."""
    out, _ = _call(_row(n=100, n_scored=100, near_his=60, above=40))
    assert "MIXED" in out["reading"]
    assert "should not be reopened" in out["reading"]


def test_no_scored_rows_answers_neither_way():
    """Absence of evidence must not read as evidence. If nothing
    carries a parseable shadow ask, the question is still open."""
    out, _ = _call(_row(n=500, n_scored=0, n_unreadable=500))
    assert "NO SCORED EVIDENCE" in out["reading"]
    assert "near_his_frac" not in out


def test_unscorable_rows_are_reported_not_absorbed():
    """An unreadable ask is its own category. Folding it into either
    bucket would move a money-gate verdict with rows that said nothing."""
    out, _ = _call(_row(n=100, n_scored=40, n_unreadable=55, n_errored=5,
                        near_his=40))
    assert out["n_unreadable"] == 55
    assert out["n_errored"] == 5
    # the fraction is over SCORED rows, not over all rows
    assert out["near_his_frac"] == 1.0


def test_the_boundary_is_not_silently_generous():
    """0.9 exactly must count as near; 0.89 must not — and the endpoint
    must not call 89% 'clean'."""
    out, _ = _call(_row(n=100, n_scored=100, near_his=89, above=11))
    assert "MIXED" in out["reading"]


# --------------------------------------------------------------- read-only

def test_it_cannot_write():
    """A diagnostic that can write is a diagnostic that can reopen the
    thing it is measuring."""
    _, pool = _call(_row(n=1, n_scored=1, near_his=1))
    for q in pool.queries:
        up = " ".join(q.upper().split())
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ",
                     "TRUNCATE ", "CREATE "):
            assert verb not in up, f"{verb.strip()} in an evidence read"


def test_it_never_flips_a_gate():
    """It must not touch ingestion_state, premap_live, the quarantine,
    or the short branch itself."""
    src = _code_only()
    for forbidden in ("ingestion_state", "premap_live", "quarantine",
                      "short_branch", "SHORT_BRANCH"):
        assert forbidden not in src, (
            f"the evidence endpoint references {forbidden}")


def test_the_sample_limit_is_bounded():
    _, pool = _call(_row(n=1), limit=10_000)
    assert "LIMIT $1" in " ".join(pool.queries)


def test_it_only_counts_the_banned_class():
    """Counting any other refusal would answer a different question."""
    _, pool = _call(_row(n=1))
    joined = " ".join(pool.queries)
    assert "short-branch-refused%" in joined
    assert "status = 'rejected'" in joined
