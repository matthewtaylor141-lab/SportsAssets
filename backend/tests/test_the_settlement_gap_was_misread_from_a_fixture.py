"""THE SETTLEMENT GAP, REMEASURED AGAINST THE VENUE'S OWN WORDS.

RENAMED FROM `test_the_settlement_gap_is_five_silences.py`, because that name
asserted a conclusion this file now retracts. Every assertion here replaces
one that was computed against a `description` the venue does not publish.

WHAT THE OLD FILE CLAIMED, AND WHAT IS ACTUALLY TRUE.

  "the real description is 183 chars"     -> it is 380, and the 183 were
                                             hand-written into the fixture
  "nothing conflicts"                     -> one condition conflicts
  "two conditions already agree"          -> ZERO agree
  "exactly five conditions are silent"    -> SIX are
  "the book side is complete on all 7"    -> that one held, and still holds

The one thing the old file got right is the thing that mattered least.

THE TRIGGER, WHICH THE OLD FILE NEVER LOOKED AT. `CONDITIONS` are named for
what happened to the fixture. Neither side conditions its rule on that:

  book   "If a fixture isn't started 12 hours after its scheduled starting
          time all bets on that fixture will be voided."
  venue  "If the game is delayed, postponed, or suspended and not
          rescheduled to a date within two weeks of the originally
          scheduled date, the market will settle to the last fair market
          price."

Different variables, different windows. So the mismatch is real where both
triggers hold and unestablished where only one does, and the comparison says
so rather than implying the two sides described the same games.
"""

from __future__ import annotations

import json

from sportsassets import bettor_settlement_terms as ST

from .conftest import backend_path

FIXTURE = backend_path("tests", "fixtures",
                       "pmus_settled_market_2026_09_24_az_col.json")

#: Six now, not five: the venue states one rule, and it is the abandonment
#: case -- which is the one the old file thought was settled agreement.
SILENT = (
    ST.C_FULL,
    ST.C_OVERTIME,
    ST.C_CALLED_FINAL,
    ST.C_STOPPED_EARLY,
    ST.C_SUSPENDED_RESUMED,
    ST.C_SUSPENDED_BEYOND,
)


def _prose():
    with open(FIXTURE) as fh:
        return json.load(fh)["listing_market"]["description"]


def _compare():
    return ST.compare_prose(
        sport_family="baseball", market="h2h", venue_prose=_prose(),
        observed_at=1758800000.0, start_at=1758810000.0,
        quote_is_in_play=False, phase=ST.PHASE_REGULAR,
        game_format=ST.FMT_NINE)


def test_the_fixture_now_carries_the_venues_own_text():
    """RETRACTS `test_the_real_description_is_183_chars_not_380`. The fixture
    was corrected to the live field, and the correction is recorded in the
    file itself rather than only in a commit message."""
    p = _prose()
    assert len(p) == 380, len(p)
    assert "last fair market price" in p
    assert "void" not in p.lower()
    with open(FIXTURE) as fh:
        d = json.load(fh)
    c = d["_description_correction"]
    assert c["its_length"] == 183
    assert "VOID" in c["why_it_was_corrected"]
    # AND WHAT IS STILL UNKNOWN IS NAMED, not smoothed over.
    assert "either the capture was not verbatim" in c["what_is_NOT_established"]


def test_the_contract_carries_no_asset_price_terms():
    """Unchanged and still true: the field that would naturally hold
    per-contract terms is null."""
    with open(FIXTURE) as fh:
        m = json.load(fh)["listing_market"]
    assert "assetPriceTerms" in m
    assert m["assetPriceTerms"] is None


def test_one_condition_conflicts():
    """RETRACTS `test_nothing_conflicts`. The venue pays the contract's last
    price where the book returns the stake."""
    got = _compare()
    assert got["mismatched_conditions"] == [ST.C_NOT_PLAYED]
    assert got["verdict"] == ST.INCOMPATIBLE
    row = got["per_condition"][ST.C_NOT_PLAYED]
    assert row["venue_payout"] == ST.PAY_LAST_FAIR_MARKET_PRICE
    assert row["book_payout"] == ST.PAY_STAKE_BACK


def test_no_condition_agrees():
    """RETRACTS `test_two_conditions_already_agree`. Nothing matches: the
    sentences that would establish the ordinary cases state a payout with no
    condition, or a condition with no payout."""
    got = _compare()["per_condition"]
    assert [c for c, r in got.items() if r["verdict"] == ST.V_MATCH] == []


def test_exactly_six_conditions_are_venue_silent():
    """RETRACTS `test_exactly_five_conditions_are_venue_silent`."""
    got = _compare()
    assert sorted(got["unstated_conditions"]) == sorted(SILENT), \
        got["unstated_conditions"]
    for cond in SILENT:
        row = got["per_condition"][cond]
        assert row["verdict"] == ST.V_VENUE_SILENT
        assert row["book_payout"], cond
        assert row["venue_payout"] is None, cond


def test_the_book_side_is_complete_on_all_seven():
    """The one claim that survived remeasurement."""
    got = _compare()["per_condition"]
    assert len(got) == 7
    assert all(r["book_payout"] for r in got.values())


def test_the_mismatch_is_scoped_to_the_intersection_of_two_triggers():
    """THE CHECK THE OLD FILE HAD NO CONCEPT OF. A payout comparison on a
    condition whose trigger each side defines differently must say which
    games it was established on."""
    row = _compare()["per_condition"][ST.C_NOT_PLAYED]
    t = row["trigger"]
    assert t["alignment"] == ST.V_TRIGGERS_DIFFER
    assert t["book_qualifier"] == "HOURS_FROM_THE_SCHEDULED_START"
    assert "12 hours" in t["book_window_as_published"]
    assert t["venue_qualifiers"] == ["A_MAKE_UP_DATE_INSIDE_A_NAMED_WINDOW"]
    assert "within two weeks" in t["venue_from_sentence"]
    assert row["mismatch_scope"] == ST.M_ON_THE_INTERSECTION
    assert "only ITS OWN trigger holds" in row["what_is_not_established"]


def test_the_two_unrecognised_sentences_are_recorded_with_their_reason():
    """WHY THE WINNER AND EXTRA-INNINGS SENTENCES ESTABLISH NOTHING, held as
    a check rather than as prose in a report.

    Sentence 1 states a payout ("settle to the winner of <away> vs <home>
    MLB game") and names NO condition -- no regulation, no innings count.
    Sentence 2 names a condition ("extra innings") and states NO payout --
    "are included if played" is not a payout phrase.

    Reading them together would attribute one sentence's payout to another
    sentence's condition. That is the cross-sentence inference this module
    forbids by design, and it is NOT applied here. It also would not
    unblock anything: the abandonment mismatch stands either way.
    """
    r = ST.read_terms(_prose())
    ev = r["evidence"]
    unused = {c: [e["sentence"] for e in evs if not e["used"]]
              for c, evs in ev.items() if any(not e["used"] for e in evs)}
    # The extra-innings sentence: condition seen, no payout.
    assert ST.C_OVERTIME in unused
    assert any("extra innings are included" in s for s in unused[ST.C_OVERTIME])
    # The winner sentence: no condition at all, so it is not even evidence
    # FOR a condition -- it appears under no key.
    assert not any("settle to the winner" in s
                   for ss in unused.values() for s in ss)
    # AND THE PAYOUT PHRASE ITSELF IS NOT MATCHED EITHER, because the team
    # names sit between "winner of the" and "game".
    assert "winner of the game" not in _prose()
    assert r["terms"] == {ST.C_NOT_PLAYED: ST.PAY_LAST_FAIR_MARKET_PRICE}


def test_silence_is_still_not_agreement():
    assert _compare()["silence_is_not_agreement"] is True