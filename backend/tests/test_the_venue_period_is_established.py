"""The period a venue contract pays on is READ, never asserted.

THE DEFECT THESE PIN. Two places asserted it:

  * `bettor_venue_mapping.is_segment()` reads the TITLE only, while its
    sibling `is_line_market()` reads the title AND the slug -- and the
    venue puts the period in the slug.
  * `ext_pinnacle_loop` then set `contract["period"] = "FULL_GAME"` as a
    literal AND passed the same literal to the valuation, so both sides
    of the comparison agreed on a period neither had checked.

Run 71's board read is the evidence that this is reachable, not
theoretical: `atc-mlb-atl-mia-2026-09-25-i6-draw` (inning six) and
`atc-ebfcwc-bjo-paris-2026-09-26-dh1-bjo` both came back INSIDE the
money-line family, because `copy_sports.market_type_of` classifies the
venue grammar on the kind prefix alone and never inspects the suffix.

Every slug below is one the venue's own catalogue actually returned.
"""

import pytest

from sportsassets import bettor_venue_mapping as V


# ── 1 · THE FULL-MATCH SLUGS THE LANE ALREADY TRADES ─────────────────

@pytest.mark.parametrize("slug", [
    "aec-mlb-lad-sf-2026-09-25",
    "aec-mlb-laa-sea-2026-09-25",
    "aec-mlb-az-sd-2026-09-25",
    "aec-mlb-bal-nyy-2026-09-26",
    "aec-mlb-tb-phi-2026-09-25",
    "aec-mlb-cin-tor-2026-09-25",
    "aec-mlb-hou-ath-2026-09-25",
    "aec-cfb-clmsn-cah-2026-09-25",
    "aec-atp-danmed-valroy-2026-09-23",
    "aec-cs2-100t-ast-2026-09-26",
])
def test_nothing_after_the_date_is_the_whole_fixture(slug):
    got = V.period_of_venue_slug(slug, side="yes")
    assert got["period"] == V.FULL_MATCH, got
    assert got["residual"] == ""
    assert not got["refusals"]


def test_the_matched_side_may_follow_the_date():
    got = V.period_of_venue_slug("lmx-aft-cmf-2026-09-25-cmf", side="cmf")
    assert got["period"] == V.FULL_MATCH, got
    assert got["residual"] == "cmf"


def test_the_side_comparison_ignores_punctuation_and_case():
    got = V.period_of_venue_slug("lmx-aft-cmf-2026-09-25-cmf",
                                 side="CMF")
    assert got["period"] == V.FULL_MATCH, got


# ── 2 · THE SEGMENTS THAT USED TO PASS AS MONEY LINES ────────────────

def test_an_inning_segment_is_refused_not_priced_as_a_full_game():
    """The slug run 71 found inside the money-line family."""
    got = V.period_of_venue_slug("atc-mlb-atl-mia-2026-09-25-i6-draw",
                                 side="draw")
    assert got["period"] is None
    assert V.R_PERIOD_UNKNOWN in got["refusals"]
    assert got["residual"] == "i6-draw"
    assert "i6-draw" in got["why"]


@pytest.mark.parametrize("slug,side", [
    ("atc-ebfcwc-bjo-paris-2026-09-26-dh1-bjo", "bjo"),
    ("atc-ebfcwc-bjo-rpi-2026-09-26-dh1-draw", "draw"),
    ("atc-ebfsa-bol-juv-2026-09-26-dh1-bol", "bol"),
    ("aqc-nhl-eastconf-2027-05-19-finalq-bos", "bos"),
])
def test_an_extra_token_before_the_side_is_not_established(slug, side):
    got = V.period_of_venue_slug(slug, side=side)
    assert got["period"] is None, got
    assert V.R_PERIOD_UNKNOWN in got["refusals"]


def test_a_token_that_is_not_the_side_is_not_established():
    got = V.period_of_venue_slug("aec-mlb-lad-sf-2026-09-25-h1",
                                 side="sf")
    assert got["period"] is None
    assert V.R_PERIOD_UNKNOWN in got["refusals"]


def test_no_trailing_date_establishes_nothing():
    for slug in ("aec-mlb-lad-sf", "", None, "some-futures-market"):
        got = V.period_of_venue_slug(slug, side="x")
        assert got["period"] is None, slug
        assert V.R_PERIOD_UNKNOWN in got["refusals"], slug


def test_a_missing_side_still_admits_the_bare_dated_slug():
    """The `aec` family carries no side token; a null side must not refuse it."""
    got = V.period_of_venue_slug("aec-mlb-lad-sf-2026-09-25", side=None)
    assert got["period"] == V.FULL_MATCH, got


def test_a_missing_side_cannot_admit_a_suffixed_slug():
    got = V.period_of_venue_slug("atc-mlb-atl-mia-2026-09-25-i6-draw",
                                 side=None)
    assert got["period"] is None
    assert V.R_PERIOD_UNKNOWN in got["refusals"]


# ── 3 · THE RULE IS VOCABULARY-FREE, WHICH IS THE POINT ──────────────

def test_a_period_token_nobody_listed_is_still_refused():
    """A whitelist only catches the segments someone thought of."""
    for invented in ("zz9", "leg2", "set3", "map1", "frame7", "overs20"):
        got = V.period_of_venue_slug(
            "aec-xyz-aaa-bbb-2026-09-25-%s-aaa" % invented, side="aaa")
        assert got["period"] is None, invented
        assert V.R_PERIOD_UNKNOWN in got["refusals"], invented


def test_the_refusal_code_is_declared_in_the_module_refusal_list():
    assert V.R_PERIOD_UNKNOWN in V.REFUSALS


def test_the_stage_map_attributes_the_period_refusal_to_identity():
    from sportsassets import bettor_external_shadow as ext
    assert ext.STAGE_OF[V.R_PERIOD_UNKNOWN] == "3_IDENTITY"


# ── 4 · THE LOOP'S OWN IDENTITY RESOLVER, END TO END ─────────────────
#
# `resolve_venue_identity` is where the refusal has to land, and it is
# also where the resolver's evidence was being thrown away.

import asyncio

import pytest as _pytest

from sportsassets.workers import ext_pinnacle_loop as loop
from sportsassets.workers import premap as _pm


def _identity(monkeypatch, *, market_slug, outcome,
              intent="ORDER_INTENT_BUY_LONG", title="Will A beat B?"):
    async def fake_resolve(conn_, t, et, oc, slug, **kw):
        # EXACTLY the keys premap.resolve returns -- no more, no less.
        return {"market_slug": market_slug, "outcome": outcome,
                "title": title, "matched_by": "premap_identity",
                "score": 1.0, "intent": intent}

    monkeypatch.setattr(_pm, "resolve", fake_resolve)
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        loop.resolve_venue_identity(
            None,
            market_row={"slug": "mlb-a-b-2026-09-25", "title": "A vs B",
                        "event_title": "A vs. B",
                        "condition_id": "cond-1"},
            priced_outcome="Team A"))


def test_the_resolvers_evidence_reaches_the_row_instead_of_three_nulls(
        monkeypatch):
    """`side_norm`/`identifier`/`question` were never resolve()'s keys."""
    got = _identity(monkeypatch, market_slug="aec-mlb-a-b-2026-09-25",
                    outcome="teama", title="Will Team A beat Team B?")
    assert got["ok"] is True, got
    assert got["matched_side_norm"] == "teama"
    assert got["matched_identifier"] == "aec-mlb-a-b-2026-09-25"
    assert got["matched_question"] == "Will Team A beat Team B?"
    assert got["matched_by"] == "premap_identity"
    # None of these may be None again without this test failing.
    for k in ("matched_side_norm", "matched_identifier", "matched_question"):
        assert got[k] is not None, k


def test_the_established_period_travels_with_the_identity(monkeypatch):
    got = _identity(monkeypatch, market_slug="aec-mlb-a-b-2026-09-25",
                    outcome="teama")
    assert got["ok"] is True, got
    assert got["period"] == "FULL_GAME"
    assert got["period_evidence"]["period"] == V.FULL_MATCH
    assert got["period_basis"] == got["period_evidence"]["basis"]


def test_a_segment_contract_is_refused_by_the_identity_resolver(monkeypatch):
    got = _identity(monkeypatch,
                    market_slug="atc-mlb-a-b-2026-09-25-i6-draw",
                    outcome="draw")
    assert got["ok"] is False
    assert got["refusal"] == V.R_PERIOD_UNKNOWN
    assert "i6-draw" in got["why"]
    # AND NO PERIOD IS PUBLISHED FOR IT. A refused identity must not leave
    # a FULL_GAME behind for a later reader to pick up.
    assert "period" not in got


def test_the_payout_orientation_is_unchanged_by_this_repair(monkeypatch):
    """A short intent still pays on the requested outcome, not its complement."""
    got = _identity(monkeypatch, market_slug="aec-mlb-a-b-2026-09-25",
                    outcome="teama", intent="ORDER_INTENT_BUY_SHORT")
    assert got["ok"] is True, got
    assert got["payout_event"] == "Team A"
    assert got["probability_event"] == "Team A"
    assert got["payout_is_complement"] is False
    assert got["ladder_side"] == "BID"
