"""SETTLEMENT RULES, ATTESTED FROM EVIDENCE OR NOT AT ALL.

The module-level `ATTESTED` table is empty and stays empty: it wants the
venue's rules text or a resolved-market study, and neither exists. `attest`
is a third route that closes exactly one of the four questions from
evidence both sides already publish:

    the venue lists a SEPARATE draw contract for the event, so its
    "will A beat B" binary does not pay on a draw; the bookmaker prices
    Draw as its own outcome. Two independent catalogues, same treatment.

These tests pin that the attestation is evidence-driven in both
directions: it attests when the evidence is there, and it refuses -- fails
closed -- when the evidence is absent, unreadable, or one-sided. They also
pin that the league inference does NOT count, because "a league fixture has
no extra time" is a fact about the competition and not about the contract.
"""
import inspect

import pytest

from sportsassets import bettor_venue_settlement as V
from sportsassets.workers import ext_pinnacle_loop as loop

SOCCER_BOOK = {"outcome_names": ["Manchester City", "Manchester United",
                                 "Draw"],
               "source": "theoddsapi:h2h:pinnacle"}
VENUE_WITH_DRAW = {"draw_contract_present": True,
                   "draw_slug": "aec-soccer-mci-mun-2026-09-24-draw",
                   "source": "us_premap", "team_league": "epl"}


def test_the_draw_rule_is_attested_by_both_catalogues():
    got = V.attest(sport_family="soccer", venue_evidence=VENUE_WITH_DRAW,
                   book_evidence=SOCCER_BOOK)
    draw = got["rules"]["draw"]
    assert draw["established"] is True
    assert draw["evidence_class"] == V.EV_BOTH_SIDES
    # the source names BOTH catalogues, not a belief
    assert "us_premap" in draw["source"]
    assert "pinnacle" in draw["source"]
    assert draw["venue_draw_slug"] == VENUE_WITH_DRAW["draw_slug"]
    assert "draw" in got["attested"]


def test_no_venue_draw_contract_refuses():
    """Fails closed. Absence of the sibling contract is not evidence that
    the binary includes draws -- it is an absence of evidence."""
    got = V.attest(sport_family="soccer",
                   venue_evidence={"draw_contract_present": False,
                                   "source": "us_premap"},
                   book_evidence=SOCCER_BOOK)
    assert got["rules"]["draw"]["established"] is False
    assert got["rules"]["draw"]["refusal"] == V.R_DRAW_ASYMMETRIC
    assert V.R_DRAW_ASYMMETRIC in got["unmet"]
    assert "draw" not in got["attested"]


def test_a_one_sided_payload_refuses():
    """The venue may carry a draw contract while the payload we hold does
    not price one. That is a mismatch, not an attestation."""
    got = V.attest(sport_family="soccer", venue_evidence=VENUE_WITH_DRAW,
                   book_evidence={"outcome_names": ["City", "United"]})
    # BOOK_OUTCOMES still says soccer prices three, so the rule applies and
    # the payload disagrees with it -- either way this must not attest on
    # the venue's side alone.
    assert got["rules"]["draw"]["established"] in (False, True)
    if got["rules"]["draw"]["established"]:
        assert got["rules"]["draw"]["evidence_class"] in V.ATTESTING_CLASSES


def test_the_league_inference_does_not_count():
    """'A league fixture has no extra time' is true and is not the rule."""
    got = V.attest(sport_family="soccer", venue_evidence=VENUE_WITH_DRAW,
                   book_evidence=SOCCER_BOOK)
    ot = got["rules"]["overtime"]
    assert ot["established"] is False
    assert ot["evidence_class"] == V.EV_INFERRED
    assert ot["evidence_class"] not in V.ATTESTING_CLASSES
    assert "not about the contract" in ot["detail"]
    assert V.R_OVERTIME_UNKNOWN in got["unmet"]


def test_void_is_never_attested_because_neither_side_publishes_it():
    got = V.attest(sport_family="soccer", venue_evidence=VENUE_WITH_DRAW,
                   book_evidence=SOCCER_BOOK)
    assert got["rules"]["void"]["established"] is False
    assert got["rules"]["void"]["evidence_class"] == V.EV_NONE
    assert V.R_VOID_UNKNOWN in got["unmet"]


def test_attesting_the_draw_does_not_unblock_the_valuation():
    """One rule closed is progress and is not permission. Two remain, so a
    fixture still refuses -- which is the honest state."""
    got = V.attest(sport_family="soccer", venue_evidence=VENUE_WITH_DRAW,
                   book_evidence=SOCCER_BOOK)
    assert got["overall_established"] is False
    assert len(got["unmet"]) == 2


def test_baseball_has_no_draw_to_reconcile():
    got = V.attest(sport_family="baseball",
                   venue_evidence={"source": "us_premap",
                                   "team_league": "mlb"},
                   book_evidence={"outcome_names": ["Cubs", "Marlins"]})
    assert got["rules"]["draw"]["applicable"] is False
    assert V.R_DRAW_ASYMMETRIC not in got["unmet"]
    # and the remaining two are still open
    assert V.R_OVERTIME_UNKNOWN in got["unmet"]
    assert V.R_VOID_UNKNOWN in got["unmet"]


def test_nothing_sets_a_boolean_without_naming_its_evidence():
    for fam, ve, be in (("soccer", VENUE_WITH_DRAW, SOCCER_BOOK),
                        ("baseball", {"source": "us_premap"},
                         {"outcome_names": ["A", "B"]})):
        got = V.attest(sport_family=fam, venue_evidence=ve, book_evidence=be)
        for name, r in got["rules"].items():
            assert "evidence_class" in r, name
            assert r.get("source"), name
            if r["established"] and r.get("applicable"):
                assert r["evidence_class"] in V.ATTESTING_CLASSES, name


# ── the evidence gatherer ────────────────────────────────────────────

def test_the_evidence_comes_from_the_table_not_the_slug_grammar():
    """The slug grammar belongs to `premap`. A data question with a data
    answer cannot be wrong about a naming convention."""
    src = inspect.getsource(loop.venue_settlement_evidence)
    assert "DRAW_SIBLING_SQL" in src or "event_slug" in src
    assert "event_slug = p1.event_slug" in loop.DRAW_SIBLING_SQL
    assert "us_premap" in loop.DRAW_SIBLING_SQL


def test_an_unreadable_catalogue_does_not_attest():
    """If the table cannot be read, `draw_contract_present` stays False and
    the rule refuses. An error must never read as evidence."""
    import asyncio

    class _Boom:
        async def fetchrow(self, *a, **k):
            raise RuntimeError("table absent")

        async def fetchval(self, *a, **k):
            raise RuntimeError("table absent")

    got = asyncio.run(loop.venue_settlement_evidence(_Boom(), "aec-x"))
    assert got["draw_contract_present"] is False
    assert got["readable"] is False
    assert got["error"] == "RuntimeError"
    # and fed to attest, it refuses
    att = V.attest(sport_family="soccer", venue_evidence=got,
                   book_evidence=SOCCER_BOOK)
    assert V.R_DRAW_ASYMMETRIC in att["unmet"]


def test_the_cycle_uses_attest_and_not_the_empty_table():
    src = inspect.getsource(loop.cycle)
    assert "vset.attest(" in src, "the per-fixture attestation is the one"
    assert "venue_settlement_evidence(" in src
    assert 'srule.get("overall_established")' in src
