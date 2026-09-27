"""§8: the pairing and learning lines, four books, no total.

The failure this file guards against is a single dollar figure that adds
a simulated profit to a real loss. §8 says research, demonstrations,
shadow and funded stay "separate and unsummed", and on a page headed with
a dollar sign nobody reads the footnote -- so the test that matters is
`test_no_key_anywhere_in_the_panel_reads_like_a_total`, which walks the
whole returned structure.

The second load-bearing group is the NOT-IDENTIFIED-never-0 set,
especially for `INVENTORY_DOUBLE_ALLOCATED`: a 0 there would tell
management no share is hedged twice on a page that never checked.
"""

from fractions import Fraction

import pytest

from sportsassets import bettor_completion_policy as CP
from sportsassets import bettor_indirect_structures as IS
from sportsassets import bettor_learning_authority as LA
from sportsassets import bettor_pairing_panel as PP


def _rows(p, book="SHADOW"):
    return {r["line"]: r for r in p["books"][book]["lines"]}


# ═════════════════════════════════════════════════════════════════════
# THE FOUR BOOKS ARE NEVER SUMMED
# ═════════════════════════════════════════════════════════════════════

def test_all_four_books_are_present_and_separate():
    p = PP.panel()
    assert set(p["books"]) == set(PP.BOOKS)
    assert p["bookOrder"] == list(PP.BOOKS)


def test_no_key_anywhere_in_the_panel_reads_like_a_total():
    """Walk the whole structure, not just the top level."""
    p = PP.panel()
    banned = ("total", "combined", "aggregate", "all_books", "overall",
              "grand")
    seen = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k).lower()
                if any(b in key for b in banned):
                    seen.append("%s.%s" % (path, k))
                walk(v, "%s.%s" % (path, k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))

    walk(p)
    assert seen == [], seen


def test_there_is_no_function_to_sum_the_books():
    """Not a guarded total -- no total to call."""
    for name in ("total_across_books", "total", "combined", "sum_books",
                 "aggregate"):
        assert not hasattr(PP, name), name


def test_a_fifth_book_is_refused():
    with pytest.raises(ValueError) as e:
        PP.book_panel("PAPER")
    assert "fifth book would be summed" in str(e.value)


def test_the_net_line_says_this_book_only():
    p = PP.panel(shadow={"NET_PORTFOLIO_PNL": 1.3})
    row = _rows(p)["NET_PORTFOLIO_PNL"]
    assert "this book only" in row["note"]
    assert "no figure on this page that adds the four books" in row["note"]


def test_each_book_states_what_it_means():
    p = PP.panel()
    for b in PP.BOOKS:
        assert p["books"][b]["means"]
    assert "not submitted" in PP.BOOK_MEANING["SHADOW"]
    assert "substituted transport" in PP.BOOK_MEANING["DEMONSTRATION"]


# ═════════════════════════════════════════════════════════════════════
# NOT IDENTIFIED, NEVER 0
# ═════════════════════════════════════════════════════════════════════

def test_an_unsupplied_line_reads_not_identified_not_zero():
    p = PP.panel()
    for name, row in _rows(p).items():
        assert row["value"] == PP.NOT_IDENTIFIED, name
        assert row["display"] == PP.DISPLAY_NOT_IDENTIFIED, name
        assert row["value"] != 0, name


def test_every_declared_line_is_present_even_when_empty():
    p = PP.panel()
    for b in PP.BOOKS:
        rows = _rows(p, b)
        assert set(rows) == set(PP.LINES), b
        assert p["books"][b]["declared"] == len(PP.LINES)


def test_a_supplied_zero_is_shown_as_zero_and_marked_identified():
    """A measured zero is a finding and must not read NOT IDENTIFIED."""
    p = PP.panel(shadow={"PAIRING_LOSSES": 0})
    row = _rows(p)["PAIRING_LOSSES"]
    assert row["value"] == 0
    assert row["status"] == "IDENTIFIED"
    assert row["display"] == "0"


def test_the_never_zero_reason_travels_with_every_absent_line():
    p = PP.panel()
    for name, row in _rows(p).items():
        assert row["neverZero"] == PP.NEVER_ZERO, name


def test_a_status_line_and_a_quantity_line_are_distinguished():
    p = PP.panel()
    rows = _rows(p)
    assert rows["MODEL_VERSION"]["kind"] == PP.STATUS
    assert rows["GROSS_PAIRING_GAINS"]["kind"] == PP.QUANTITY
    assert rows["MODEL_VERSION"]["whyKindMatters"]
    assert rows["GROSS_PAIRING_GAINS"]["whyKindMatters"] is None


# ═════════════════════════════════════════════════════════════════════
# A MIDDLE IS NOT A HEDGE
# ═════════════════════════════════════════════════════════════════════

def test_middles_above_par_is_its_own_line_beside_middles_held():
    assert "MIDDLES_HELD" in PP.LINES
    assert "MIDDLES_ABOVE_PAR" in PP.LINES
    p = PP.panel()
    assert _rows(p)["MIDDLES_HELD"]["note"] == PP.MIDDLE_IS_NOT_A_HEDGE


def test_the_middle_note_carries_both_case_study_averages():
    note = PP.MIDDLE_IS_NOT_A_HEDGE
    assert "1.2024" in note and "1.1711" in note
    assert "guaranteed shortfall" in note


def test_the_above_par_label_says_a_bet_not_a_hedge():
    assert "a bet, not a hedge" in PP.BUSINESS_LABEL["MIDDLES_ABOVE_PAR"]


def test_the_pairing_and_residual_results_are_separate_lines():
    assert "GROSS_PAIRING_GAINS" in PP.LINES
    assert "RESIDUAL_RESULTS" in PP.LINES
    assert "10.8M" in PP.WHY_THE_SPLIT
    assert "9.5M" in PP.WHY_THE_SPLIT
    assert "dominates the account's total" in PP.WHY_THE_SPLIT


# ═════════════════════════════════════════════════════════════════════
# NO CAPACITY LINE
# ═════════════════════════════════════════════════════════════════════

def test_the_panel_carries_no_capacity_or_turnover_line():
    joined = " ".join(PP.LINES).lower()
    for word in ("capacity", "throughput", "turnover", "orders_per",
                 "benchmark"):
        assert word not in joined, word


def test_the_absence_of_a_capacity_line_is_deliberate_and_stated():
    assert "forbids extrapolating" in PP.NO_CAPACITY_LINE
    assert "cannot be filled in with the wrong number" in PP.NO_CAPACITY_LINE


# ═════════════════════════════════════════════════════════════════════
# BUSINESS VOCABULARY
# ═════════════════════════════════════════════════════════════════════

def test_every_line_has_a_business_label():
    for name in PP.LINES:
        assert name in PP.BUSINESS_LABEL, name


def test_no_business_label_is_just_the_constant_name():
    for name, label in PP.BUSINESS_LABEL.items():
        assert label != name
        assert "_" not in label or label.startswith("..."), label


def test_the_labels_avoid_the_internal_vocabulary():
    assert PP.BUSINESS_LABEL["UNPAIRED_INVENTORY"] == "One-sided inventory"
    assert PP.BUSINESS_LABEL["MIDDLES_HELD"] == \
        "Structures that cannot lose both legs"


# ═════════════════════════════════════════════════════════════════════
# ASSEMBLED FROM THE OWNING MODULES
# ═════════════════════════════════════════════════════════════════════

def _legs():
    return [
        IS.Leg(condition_id="ml-A", fixture_id="fx",
               kind=IS.KIND_MONEYLINE, period=IS.PERIOD_FULL,
               overtime=IS.OT_INCLUDED, backs="A", quantity=100,
               cost_cents_per_unit=60, settlement_text_captured=True,
               tie_rule="tie resolves 50-50", void_rule="void: 50-50"),
        IS.Leg(condition_id="sp-B", fixture_id="fx",
               kind=IS.KIND_SPREAD, period=IS.PERIOD_FULL,
               overtime=IS.OT_INCLUDED, backs="B", line=Fraction(-13, 2),
               quantity=100, cost_cents_per_unit=57,
               settlement_text_captured=True,
               tie_rule="none", void_rule="void: 50-50"),
        IS.Leg(condition_id="sp-H1", fixture_id="fx",
               kind=IS.KIND_SPREAD, period=IS.PERIOD_H1,
               overtime=IS.OT_INCLUDED, backs="B", line=Fraction(-9, 2),
               quantity=50, cost_cents_per_unit=50,
               settlement_text_captured=True,
               tie_rule="none", void_rule="void: 50-50"),
    ]


def test_the_panel_assembles_an_allocation_without_rederiving_it():
    legs = _legs()
    alloc = IS.allocate(legs, sport_permits_tie=False,
                        fixture_can_void=False, fixture_can_postpone=False)
    supplied = PP.from_allocation(alloc, legs)
    p = PP.panel(shadow=supplied)
    rows = _rows(p)
    assert rows["MIDDLES_HELD"]["status"] == "IDENTIFIED"
    assert rows["MIDDLES_HELD"]["value"] == 1
    assert "bettor_indirect_structures" in rows["MIDDLES_HELD"]["source"]


def test_an_above_par_middle_is_counted_as_above_par():
    """60 + 57 = 117 cents against a $1 minimum."""
    legs = _legs()
    alloc = IS.allocate(legs, sport_permits_tie=False,
                        fixture_can_void=False, fixture_can_postpone=False)
    supplied = PP.from_allocation(alloc, legs)
    assert supplied["MIDDLES_ABOVE_PAR"]["value"] == 1


def test_a_refused_pairing_is_reported_not_dropped():
    legs = _legs()
    alloc = IS.allocate(legs, sport_permits_tie=False,
                        fixture_can_void=False, fixture_can_postpone=False)
    supplied = PP.from_allocation(alloc, legs)
    assert supplied["STRUCTURES_REFUSED_UNESTABLISHABLE"]["value"] >= 1
    assert "not a structure that scored badly" in \
        supplied["STRUCTURES_REFUSED_UNESTABLISHABLE"]["note"]


def test_double_allocation_is_measured_as_zero_when_the_legs_are_supplied():
    legs = _legs()
    alloc = IS.allocate(legs, sport_permits_tie=False,
                        fixture_can_void=False, fixture_can_postpone=False)
    supplied = PP.from_allocation(alloc, legs)
    assert supplied["INVENTORY_DOUBLE_ALLOCATED"]["value"] == 0
    assert supplied["INVENTORY_DOUBLE_ALLOCATED"]["source"]


def test_double_allocation_reads_not_identified_without_the_legs():
    """The worst possible 0 on this page. It must not be produced."""
    legs = _legs()
    alloc = IS.allocate(legs, sport_permits_tie=False,
                        fixture_can_void=False, fixture_can_postpone=False)
    supplied = PP.from_allocation(alloc)          # no legs
    p = PP.panel(shadow=supplied)
    row = _rows(p)["INVENTORY_DOUBLE_ALLOCATED"]
    assert row["value"] == PP.NOT_IDENTIFIED
    assert row["display"] == PP.DISPLAY_NOT_IDENTIFIED
    assert "never checked" in row["note"]


def test_the_panel_assembles_a_cohort_report():
    c = CP.Cohort()
    c.initiate(100)
    c.close(CP.D_COMPLETED, 10.8, n=59)
    c.close(CP.D_CARRIED_UNPAIRED, -9.5, n=41)
    supplied = PP.from_cohort(c.report())
    p = PP.panel(shadow=supplied)
    rows = _rows(p)
    assert rows["GROSS_PAIRING_GAINS"]["value"] == pytest.approx(10.8)
    assert rows["RESIDUAL_RESULTS"]["value"] == pytest.approx(-9.5)
    assert rows["NET_PORTFOLIO_PNL"]["value"] == pytest.approx(1.3)


def test_the_cohort_net_line_covers_every_initiation():
    c = CP.Cohort()
    c.initiate(10)
    c.close(CP.D_COMPLETED, 5.0, n=10)
    supplied = PP.from_cohort(c.report())
    assert "every initiation, not only the completed pairs" in \
        supplied["NET_PORTFOLIO_PNL"]["note"]


def test_a_pairing_loss_is_not_reported_as_a_gain():
    c = CP.Cohort()
    c.initiate(10)
    c.close(CP.D_COMPLETED, -2.0, n=10)
    supplied = PP.from_cohort(c.report())
    assert supplied["GROSS_PAIRING_GAINS"]["value"] is None
    assert supplied["PAIRING_LOSSES"]["value"] == pytest.approx(-2.0)


# ═════════════════════════════════════════════════════════════════════
# THE LEARNING GUARD REACHES THE PAGE
# ═════════════════════════════════════════════════════════════════════

def test_without_a_guard_result_the_line_does_not_claim_the_surface_held():
    p = PP.panel()
    row = _rows(p)["PROTECTED_SURFACE_MOVED"]
    assert row["value"] == PP.NOT_IDENTIFIED


def test_a_clean_guard_result_reaches_every_book():
    with LA.guard() as g:
        pass
    p = PP.panel(learning_guard=g.to_dict())
    for b in PP.BOOKS:
        row = _rows(p, b)["PROTECTED_SURFACE_MOVED"]
        assert row["status"] == "IDENTIFIED"
        assert "no, over the probed surface" in row["value"]
        assert "another process" in row["note"]


def test_a_violation_shows_as_a_void_promotion_on_the_page(monkeypatch):
    from sportsassets import bettor_funded_execution as fe
    result = None
    with pytest.raises(LA.AuthorityViolation):
        with LA.guard() as g:
            monkeypatch.setattr(fe, "FUNDED_SUBMISSION_ENABLED", True)
        result = g
    # The guard raises after fingerprinting, so read the result object.
    p = PP.panel(learning_guard=g.to_dict())
    row = _rows(p)["PROTECTED_SURFACE_MOVED"]
    assert "PROMOTION VOID" in row["value"]
    assert result is None          # the with-body did not complete normally


def test_the_guard_line_carries_what_the_guard_cannot_establish():
    with LA.guard() as g:
        pass
    p = PP.panel(learning_guard=g.to_dict())
    note = _rows(p)["PROTECTED_SURFACE_MOVED"]["note"]
    assert "learning changed nothing" in note


# ═════════════════════════════════════════════════════════════════════
# IT IS A READ
# ═════════════════════════════════════════════════════════════════════

def test_the_module_places_no_order():
    import inspect
    src = inspect.getsource(PP)
    for name in ("submit_fok", "cancel_order", "close_position",
                 "post_order", "create_order"):
        assert name not in src, name


def test_the_module_does_not_import_the_venue():
    import inspect
    src = inspect.getsource(PP)
    assert "import pmus" not in src
    assert "from . import pmus" not in src


def test_describe_states_that_it_places_nothing():
    assert "places, sizes and funds" in PP.describe()["placesNoOrder"]
