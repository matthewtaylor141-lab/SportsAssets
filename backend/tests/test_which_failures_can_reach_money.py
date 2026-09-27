"""THE 177 STANDING FAILURES, CLASSIFIED BY WHETHER THEY CAN REACH MONEY.

THE ARGUMENT I ORIGINALLY GAVE WAS THE WRONG ARGUMENT, and these tests exist
because of it. I asserted that none of the standing failures affects the capital
path, and the only support was that they also fail in the baseline. That supports
a different claim entirely -- that the release did not cause them. A failure that
predates the release is still a capital-path failure if it sits on the capital
path.

So the support has to be structural, and `capital_path` computes it. These tests
hold the computation honest, including the two parser defects it produced on the
way -- each of which made the answer LOOK BETTER than it was, which is the
direction that matters.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import capital_path as CP

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(
    HERE, "..", "..", "research", "evidence", "gate",
    "FAILURES_BASELINE_6d75275_2026-09-27_confirming.txt")


def _baseline():
    with open(BASELINE, "r", encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


# ── 1 · THE TWO PARSER DEFECTS, EACH PINNED ──────────────────────────

def test_from_sportsassets_import_X_is_SEEN(tmp_path):
    """DEFECT ONE, and it silently emptied the answer.

    `from sportsassets import live_executor as le` is the form nearly every test
    in this repository uses. The first parser checked for a module starting with
    `"sportsassets."` or a relative level, and this statement has neither -- so it
    was dropped, and files that plainly import a capital module were classified
    as importing nothing.
    """
    p = tmp_path / "t.py"
    p.write_text("from sportsassets import live_executor as le\n"
                 "from sportsassets import bettor_funded_book\n")
    got = CP._module_imports(str(p))
    assert "live_executor" in got
    assert "bettor_funded_book" in got


def test_a_bare_package_is_not_a_capital_path_hit(tmp_path):
    """DEFECT TWO, and it inflated the answer in the other direction.

    Matching on the first dotted segment made `workers.mirror_live` count as
    capital-path because the bare package `workers` is reachable -- SOME worker
    is. That is a package being reachable, not that module.
    """
    p = tmp_path / "t.py"
    p.write_text("from sportsassets.workers import mirror_live\n")
    got = CP.classify_test_files([str(p)])
    row = got[str(p)]
    assert "workers.mirror_live" in row["sportsassets_imports"]
    assert "workers.mirror_live" not in row["capital_path_modules_reached"]


def test_a_symbol_is_not_counted_as_a_module(tmp_path):
    """`from sportsassets.analytics.mirror import Plan` yields a class name, not
    a module on disk, and only modules may enter the closure."""
    p = tmp_path / "t.py"
    p.write_text("from sportsassets.analytics.mirror import Plan\n")
    got = CP.classify_test_files([str(p)])
    assert "analytics.mirror.Plan" not in got[str(p)][
        "capital_path_modules_reached"]


def test_all_three_forms_of_relative_import_are_seen(tmp_path):
    p = tmp_path / "t.py"
    p.write_text("from . import bettor_funded_book\n"
                 "from .bettor_entry_gate import admit\n"
                 "import sportsassets.bettor_fee_schedule\n")
    got = CP._module_imports(str(p))
    assert {"bettor_funded_book", "bettor_entry_gate",
            "bettor_fee_schedule"} <= got


# ── 2 · THE THREE MEASURES ARE DIFFERENT, AND ORDERED ────────────────

def test_the_closure_contains_the_first_ring_contains_the_entry_points():
    """Outer bound, middle, inner. If any two collapsed the layering would be
    decoration rather than evidence."""
    entries = set(CP.CAPITAL_ENTRY_POINTS)
    ring = CP.first_ring()
    clo = set(CP.closure())
    assert entries <= ring <= clo
    assert len(ring) > len(entries)      # the ring adds real modules
    assert len(clo) > len(ring)          # the closure adds more


def test_the_funded_book_reaches_the_legacy_copier_and_that_is_recorded():
    """THE FINDING THAT MADE THE CLOSURE WIDE. `bettor_funded_book` imports one
    pure function from `live_executor`, whose own import tree is the legacy
    copier. So the copier is in the closure on the strength of `fill_cash`."""
    clo = CP.closure()
    assert "live_executor" in clo
    assert "analytics.mirror_live_rules" in clo
    assert "workers.mirror_shadow" in clo
    cs = CP.coupling_surface()
    assert "live_executor.fill_cash" in cs["the_whole_coupling_surface"]
    assert "live_executor" in cs["by_entry_point"]["bettor_funded_book"][
        "modules_imported_outside_the_capital_set"]


def test_the_coupling_surface_is_SMALL_and_named():
    """The inner bound is two symbols. Naming them is what makes 'the copier is
    in the closure' a statement about an import rather than about money."""
    surface = CP.coupling_surface()["the_whole_coupling_surface"]
    assert surface == ["bettor_market_stream._parse_ts",
                       "live_executor.fill_cash"]


# ── 3 · THE ANSWER ON THE REAL BASELINE ──────────────────────────────

@pytest.mark.skipif(not os.path.isfile(BASELINE),
                    reason="the baseline identity list is not in this checkout")
def test_the_original_claim_that_none_reach_the_capital_path_is_FALSE():
    """THE CORRECTION, ASSERTED SO IT CANNOT BE QUIETLY RESTATED.

    A substantial share of the standing failures sit in the first ring. The claim
    that none of them affects the capital path was not supported and is
    withdrawn; each first-ring identity needs reading on its own.
    """
    rep = CP.report(_baseline())
    assert rep["identities"] == 177
    assert rep["C_first_ring"]["identity_count"] > 0, (
        "if this were zero the original claim would have been right, and the "
        "measurement would need re-checking rather than believing")
    assert rep["A_closure"]["identities"] >= rep["C_first_ring"][
        "identity_count"]


@pytest.mark.skipif(not os.path.isfile(BASELINE), reason="no baseline list")
def test_no_failing_file_imports_a_coupling_symbol_BY_NAME():
    """The two coupled symbols are covered by tests that PASS -- `fill_cash` by
    test_price_fidelity, test_mirror_live_ledger, test_mirror_live_le_consumers
    and test_mirror_live_rules, none of which has a standing failure. This is
    bounded: importing the module is not exercising the symbol, so it narrows
    the question rather than closing it."""
    rep = CP.report(_baseline())
    assert rep["B_coupling_surface"]["identities"] == 0


# ── 4 · THE LIMITS ARE PART OF THE OUTPUT ────────────────────────────

def test_outside_the_closure_is_reported_as_BOUNDED_not_as_a_clearance():
    got = CP.classify_test_files(["tests/test_workflow_size_guard.py"])
    row = got["tests/test_workflow_size_guard.py"]
    if row["verdict"] == "NO_STATIC_IMPORT_PATH":
        assert "not a clearance" in row["and_what_that_verdict_means"]


def test_the_report_names_what_static_imports_cannot_see():
    rep = CP.report(["tests/test_arena_cap.py::x"])
    blind = " ".join(rep["what_this_cannot_see"])
    assert "importlib" in blind
    assert "DATABASE" in blind          # the cross-lane case, named
    assert "subprocess" in blind
    assert "does not establish that they are off the capital path" in (
        rep["and_the_baseline_argument_is_NOT_this_argument"])


def test_the_closure_reports_that_it_OVER_approximates():
    rep = CP.report(["tests/test_arena_cap.py::x"])
    assert "OVER_approximates" in " ".join(rep["A_closure"].keys())
    assert "must be read individually" in (
        rep["A_closure"]["and_it_OVER_approximates"])


def test_an_unreadable_test_file_is_reported_rather_than_skipped():
    got = CP.classify_test_files(["tests/this_file_does_not_exist.py"])
    row = got["tests/this_file_does_not_exist.py"]
    assert row["readable"] is False
    assert row["sportsassets_imports"] == []
