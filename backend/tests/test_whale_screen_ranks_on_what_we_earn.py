"""A whale is ranked on what OUR copies of him earn, never on his edge.

Phase 3 of the owner's plan (2026-09-01). The candidate census only ever
ranked raw edge, and raw edge is his, not ours: rn1 is proven at +4.4%
to +6.9% on 822,000 fills and our copies of him return ~0%. The gap is
not price -- the audit settled that our entry prices beat his -- it is
WHICH of his trades we get filled on, and that number exists only for
whales we have actually traded.

These tests pin the properties that keep the screen honest:

  * three numbers per whale, READ from where they are published, never
    recomputed here
  * a whale with no settled copies is UNMEASURED -- an instruction to
    trade him at a measurement clip -- not a verdict either way
  * measured whales always outrank unmeasured ones, whatever his edge;
    a number we have not measured cannot beat one we have
  * a source that fails is reported in `errors`, not silently emptied
  * it reads and cannot write
"""
import ast
import asyncio
import inspect

import pytest

from sportsassets.api import app as app_mod


def _node():
    tree = ast.parse(inspect.getsource(app_mod))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and n.name == "admin_whale_screen":
            return n
    raise AssertionError("admin_whale_screen not found")


class _Pool:
    async def fetch(self, *a):  # pragma: no cover - sources are stubbed
        return []

    async def execute(self, *a):  # pragma: no cover - must not run
        raise AssertionError("whale-screen must not write")


def _call(monkeypatch, snap=None, fid=None, real=None,
          fid_raises=False, real_raises=False):
    from sportsassets import edge_gate
    from sportsassets.analytics import price_fidelity, proof

    monkeypatch.setattr(edge_gate, "snapshot",
                        lambda: snap if snap is not None else {"whales": {}})

    async def _fid(pool, since):
        if fid_raises:
            raise RuntimeError("column missing")
        return {"by_whale": fid or {}}

    async def _real(pool, since):
        if real_raises:
            raise RuntimeError("column missing")
        return {"by_whale": real or {}}

    monkeypatch.setattr(price_fidelity, "cohort_fidelity", _fid)
    monkeypatch.setattr(proof, "cohort_assess", _real)

    async def _get_pool():
        return _Pool()

    orig = app_mod.get_pool
    app_mod.get_pool = _get_pool
    try:
        return asyncio.run(app_mod.admin_whale_screen())
    finally:
        app_mod.get_pool = orig


def _pub(ci, roi, funded=True, fills=1000):
    return {"ci95": ci, "roi": roi, "funded": funded,
            "reason": "edge-proven-at-95" if funded else "edge-not-demonstrated",
            "fills_total": fills}


def _row(out, w):
    return next(r for r in out["rows"] if r["whale"] == w)


# ------------------------------------------------------------ verdicts

def test_a_whale_with_no_settled_copies_is_unmeasured_not_judged(monkeypatch):
    """rn1's book cannot tell us what OUR fills of him return, and
    neither can a candidate's. UNMEASURED is an instruction."""
    out = _call(monkeypatch, snap={"whales": {"cand": _pub([0.05, 0.09], 0.07)}})
    r = _row(out, "cand")
    assert r["verdict"].startswith("UNMEASURED")
    assert "measurement clip" in r["verdict"]
    assert r["realized_settled"] == 0


def test_a_realized_interval_above_zero_is_capturing(monkeypatch):
    out = _call(monkeypatch,
                snap={"whales": {"w": _pub([0.04, 0.07], 0.055)}},
                real={"w": {"n": 300, "roi": 0.03, "ci95": [0.01, 0.05],
                            "clusters": 280, "sigma_per_dollar": 0.9}})
    assert _row(out, "w")["verdict"].startswith("CAPTURING at 95%")


def test_a_realized_interval_below_zero_is_losing(monkeypatch):
    out = _call(monkeypatch,
                snap={"whales": {"w": _pub([0.04, 0.07], 0.055)}},
                real={"w": {"n": 300, "roi": -0.03, "ci95": [-0.05, -0.01],
                            "clusters": 280}})
    assert _row(out, "w")["verdict"].startswith("LOSING at 95%")


def test_a_realized_interval_straddling_zero_is_not_demonstrated(monkeypatch):
    """The rn1 case today: his edge is proven, ours is not."""
    out = _call(monkeypatch,
                snap={"whales": {"rn1": _pub([0.044, 0.069], 0.056)}},
                real={"rn1": {"n": 811, "roi": -0.0066,
                              "ci95": [-0.10, 0.087], "clusters": 700}})
    r = _row(out, "rn1")
    assert r["verdict"].startswith("NOT DEMONSTRATED")
    assert r["funded"] is True


# ------------------------------------------------------------- ranking

def test_measured_whales_always_outrank_unmeasured_ones(monkeypatch):
    """THE POINT OF THE SCREEN. A candidate with a spectacular published
    edge and zero settled copies must not outrank a whale whose realized
    number we actually hold, even a mediocre one."""
    out = _call(monkeypatch,
                snap={"whales": {"star": _pub([0.15, 0.25], 0.20),
                                 "meh": _pub([0.01, 0.03], 0.02)}},
                real={"meh": {"n": 50, "roi": 0.004, "ci95": [-0.05, 0.06],
                              "clusters": 45}})
    assert [r["whale"] for r in out["rows"]] == ["meh", "star"]


def test_measured_whales_rank_by_realized_lower_bound(monkeypatch):
    out = _call(monkeypatch,
                snap={"whales": {"a": _pub([0.05, 0.09], 0.07),
                                 "b": _pub([0.01, 0.02], 0.015)}},
                real={"a": {"n": 100, "roi": -0.02, "ci95": [-0.1, 0.06]},
                      "b": {"n": 100, "roi": 0.03, "ci95": [-0.05, 0.11]}})
    assert [r["whale"] for r in out["rows"]] == ["b", "a"]


def test_a_bound_on_many_copies_outranks_a_point_on_three(monkeypatch):
    """ROUND THREE: +1% on n=3 sorted above +0.5% on n=800. Capital
    follows what is demonstrated, not what is loudest."""
    out = _call(monkeypatch,
                snap={"whales": {"loud": _pub([0.05, 0.09], 0.07),
                                 "proven": _pub([0.01, 0.02], 0.015)}},
                real={"loud": {"n": 3, "roi": 0.01, "ci95": [-0.40, 0.42],
                               "clusters": 3},
                      "proven": {"n": 800, "roi": 0.005, "ci95": [0.001, 0.009],
                                 "clusters": 700}})
    assert [r["whale"] for r in out["rows"]] == ["proven", "loud"]


def test_below_thirty_games_the_verdict_is_provisional_with_no_projection(monkeypatch):
    out = _call(monkeypatch,
                snap={"whales": {"w": _pub([0.04, 0.08], 0.06)}},
                real={"w": {"n": 6, "roi": 0.30, "ci95": [0.05, 0.55],
                            "clusters": 6, "sigma_per_dollar": 0.9}})
    r = _row(out, "w")
    assert r["verdict"].startswith("PROVISIONAL (games<30)")
    assert "CAPTURING" in r["verdict"]
    assert r["n_needed_at_observed"] is None and r["n_still_needed"] is None


def test_unmeasured_whales_rank_by_his_lower_bound(monkeypatch):
    out = _call(monkeypatch,
                snap={"whales": {"x": _pub([0.02, 0.10], 0.06),
                                 "y": _pub([0.05, 0.07], 0.06)}})
    assert [r["whale"] for r in out["rows"]] == ["y", "x"]


# ------------------------------------------------- the three numbers

def test_the_price_differential_is_per_dollar_and_positive_means_cheaper(monkeypatch):
    out = _call(monkeypatch,
                snap={"whales": {"w": _pub([0.04, 0.08], 0.06)}},
                fid={"w": {"dollar_edge_vs_his_price": 50.0, "deployed": 1000.0}})
    r = _row(out, "w")
    assert r["price_diff_per_dollar"] == 0.05
    assert r["net_edge_ci95_at_his_selection"] == [0.09, 0.13]


def test_no_deployed_dollars_means_no_differential(monkeypatch):
    out = _call(monkeypatch,
                snap={"whales": {"w": _pub([0.04, 0.08], 0.06)}},
                fid={"w": {"dollar_edge_vs_his_price": 0.0, "deployed": 0.0}})
    r = _row(out, "w")
    assert r["price_diff_per_dollar"] is None
    assert r["net_edge_ci95_at_his_selection"] is None


def test_his_numbers_are_carried_verbatim_not_recomputed(monkeypatch):
    # roi deliberately NOT the interval midpoint (0.0566): with the
    # midpoint, "carried verbatim" and "recomputed as the midpoint" are
    # indistinguishable, and a mutation doing exactly that passed.
    out = _call(monkeypatch,
                snap={"whales": {"w": _pub([0.0438, 0.0694], 0.0512, fills=822321)}})
    r = _row(out, "w")
    assert r["his_edge_ci95"] == [0.0438, 0.0694]
    assert r["his_edge_roi"] == 0.0512
    assert r["his_fills_total"] == 822321


def test_still_needed_counts_down_from_the_projection(monkeypatch):
    out = _call(monkeypatch,
                snap={"whales": {"w": _pub([0.04, 0.08], 0.06)}},
                real={"w": {"n": 100, "roi": 0.02, "ci95": [-0.02, 0.06],
                            "clusters": 90, "sigma_per_dollar": 1.0}})
    r = _row(out, "w")
    assert r["n_needed_at_observed"] is not None
    assert r["n_still_needed"] == max(0, r["n_needed_at_observed"] - 100)


def test_a_non_positive_realized_estimate_gets_no_projection(monkeypatch):
    """No sample size demonstrates profit from a negative estimate."""
    out = _call(monkeypatch,
                snap={"whales": {"w": _pub([0.04, 0.08], 0.06)}},
                real={"w": {"n": 100, "roi": -0.01, "ci95": [-0.05, 0.03],
                            "clusters": 90, "sigma_per_dollar": 1.0}})
    assert _row(out, "w")["n_needed_at_observed"] is None


def test_the_price_differential_is_labelled_for_the_population_it_reads():
    """cohort_fidelity reads every filled row, open ones included; the
    label used to say 'settled copies'."""
    import inspect

    from sportsassets.api import app as _app
    src = inspect.getsource(_app.admin_whale_screen)
    assert "open and closed" in src and "on our settled copies" not in src


# ------------------------------------------------------------- sources

def test_a_failing_source_is_reported_not_silently_emptied(monkeypatch):
    out = _call(monkeypatch, snap={"whales": {"w": _pub([0.04, 0.08], 0.06)}},
                fid_raises=True)
    assert out["errors"]["fidelity"] == "RuntimeError"
    assert _row(out, "w")["price_diff_per_dollar"] is None


def test_a_whale_known_only_to_the_ledger_still_appears(monkeypatch):
    """The union of sources, not the intersection: a whale we traded
    who has dropped out of the publish must not vanish from the screen."""
    out = _call(monkeypatch, snap={"whales": {}},
                real={"old": {"n": 20, "roi": 0.01, "ci95": [-0.1, 0.12]}})
    assert _row(out, "old")["his_edge_ci95"] is None
    assert _row(out, "old")["realized_settled"] == 20


# ------------------------------------------------------------ read only

def test_it_cannot_write():
    src = ast.unparse(_node()).lower()
    for verb in ("insert ", "update ", "delete ", "execute("):
        assert verb not in src


def test_it_is_admin_gated():
    for d in _node().decorator_list:
        if isinstance(d, ast.Call) and any(k.arg == "dependencies"
                                           for k in d.keywords):
            return
    raise AssertionError("whale-screen must require admin")
