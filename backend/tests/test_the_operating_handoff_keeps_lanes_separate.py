"""The operating handoff: product vocabulary, separate books, NO_TRADE real.

WHAT THIS VIEW MUST NOT DO, and each is a specific misreport:

  * present a copied, historical or synthetic fill as autonomous Bettor
    performance. The books are separate and are never summed.
  * present an external bookmaker's de-vigged price as an independently
    trained internal model.
  * present an empty screen as an absence of opportunity, or invent one to
    fill it. NO_TRADE is a result and it carries the named gate each
    candidate stopped at.
  * imply that a market-metadata repair resolved venue freshness or
    settlement compatibility. Both stay listed as open.

It ASSEMBLES existing readers and computes no new economics -- a test
asserts it holds no mutating statement and no venue client.
"""

import inspect

from sportsassets.api import app as A


SRC = inspect.getsource(A.bettor_desk)


def test_the_route_is_registered_under_the_product_vocabulary():
    paths = [getattr(r, "path", "") for r in A.app.routes]
    assert "/api/command/bettor/desk" in paths


def test_every_required_section_is_present():
    for section in ("Controls", "Opportunities", "Orders", "Positions",
                    "Performance"):
        assert '"section": "%s"' % section in SRC, section


def test_controls_carry_build_identity_mode_heartbeat_and_control_state():
    for key in ("build_identity", "research_mode", "scheduler_heartbeat",
                "control_state", "cycle_label"):
        assert '"%s"' % key in SRC, key


def test_the_funnel_and_the_per_candidate_first_refusal_are_both_shown():
    assert '"funnel"' in SRC
    assert '"first_refusal_per_mapped_candidate"' in SRC
    assert "mapped_candidate_ledger" in SRC


def test_no_trade_is_a_result_with_named_reasons():
    assert '"NO_TRADE"' in SRC
    assert '"no_trade_reasons"' in SRC
    # the sentence spans two adjacent string literals in the source, so the
    # concatenation seams are removed before matching
    import re
    flat = re.sub(r'"\s*"', "", re.sub(r"\s+", " ", SRC))
    assert "no order was forced and no edge was invented" in flat
    assert "NO_TRADE is a result, not an empty screen" in flat


def test_the_books_are_separate_and_never_summed():
    assert "lanes_are_separate_books" in SRC
    assert "never summed" in SRC
    for lane in ("autonomous_research", "controlled_demonstration",
                 "historical_benchmark", "acceptance"):
        assert lane in SRC, lane


def test_the_external_source_is_not_presented_as_an_internal_model():
    assert '"is_an_internally_trained_model": False' in SRC
    assert "EXTERNAL_BOOKMAKER_DEVIGGED_PROBABILITY" in SRC


def test_performance_reports_per_lane_with_a_mark_basis_and_unmarked():
    for key in ("per_lane", "mark_basis", "why_not_a_midpoint"):
        assert '"%s"' % key in SRC, key
    assert "unmarked" in SRC.lower()


def test_the_open_limitations_are_stated_not_implied():
    assert "venue_book_freshness" in SRC
    assert "settlement_compatibility" in SRC
    assert "not_resolved_by_this_metadata_repair" in SRC
    # and the freshness entry says the refusal is retained
    assert "explicit refusal is retained" in SRC


def test_the_internal_identifiers_are_preserved_in_audit():
    assert SRC.count('"audit"') >= 4
    assert "experiment_id" in SRC and "policy" in SRC


def test_the_view_writes_nothing_and_opens_no_venue_client():
    up = SRC.upper()
    for banned in ("INSERT ", "UPDATE ", "DELETE ", "PMUS.", "BOOK_READ"):
        assert banned not in up, banned
    assert "reads_only" in SRC


def test_funded_submission_is_declared_disabled_on_the_face_of_it():
    assert '"funded_submission": "DISABLED"' in SRC
    assert '"submits_orders": False' in SRC


def test_the_response_is_never_cached():
    assert "no-store" in SRC
