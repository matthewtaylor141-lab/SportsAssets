"""Pins the arithmetic research/ef_cluster_bounds.py reports in
ECONOMIC_FUNNEL_2026-10-08.md: the Student-t quantile, the clustered
moments and the one-sided 98% bounds on the exact cluster values production
returned (research-sql run 37790252179). If the quantile or the moments drift,
the report's bounds are no longer what the file says they are."""
import importlib.util
import math
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "ef_cluster_bounds", HERE / "ef_cluster_bounds.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)


def test_t_quantile_matches_published_tables():
    # one-sided 0.975 at df=10 is 2.2281 and df=30 is 2.0423 (standard
    # tables); the normal limit at 0.98 is 2.0537
    assert abs(B.t_quantile(0.975, 10) - 2.2281) < 1e-3
    assert abs(B.t_quantile(0.975, 30) - 2.0423) < 1e-3
    assert abs(B.t_quantile(0.98, 10 ** 6) - 2.0537) < 1e-3
    # heavier tails at small df: the t bound is never tighter than normal
    assert B.t_quantile(0.98, 36) > B.t_quantile(0.98, 140) > 2.0537


def test_summarize_on_a_known_sample():
    s = B.summarize([1.0, 2.0, 3.0, 4.0, 5.0])
    assert s["n"] == 5 and s["mean"] == 3.0
    assert abs(s["sd"] - math.sqrt(2.5)) < 1e-2
    assert s["lb98_t"] < s["mean"] < s["ub98_t"]
    assert s["worst"] == 1.0 and s["units_positive"] == 5


def test_the_reported_derek_and_portfolio_bounds():
    import csv
    rows = list(csv.DictReader(open(
        HERE / "data" / "ef_paper_clusters_run37790252179.csv")))
    derek = [float(r["realized_usd"]) for r in rows
             if r["strategy"] == "DEREK_ENTRY_POLICY_V2"]
    s = B.summarize(derek)
    assert s["n"] == 51 and s["sum"] == -13860.58
    assert s["lb98_t"] == -654.74 and s["ub98_t"] == 111.19
    # the whole record reconciles to the ledger's realized total
    assert round(sum(float(r["realized_usd"]) for r in rows), 4) == -44374.0962
    by_fx = {}
    for r in rows:
        by_fx[r["fixture"]] = by_fx.get(r["fixture"], 0.0) + float(
            r["realized_usd"])
    p = B.summarize(list(by_fx.values()))
    # 141 independent fixtures across strategies; the 98% upper bound is
    # below zero: the portfolio is significantly negative
    assert p["n"] == 141 and p["ub98_t"] == -134.34 and p["ub98_t"] < 0
