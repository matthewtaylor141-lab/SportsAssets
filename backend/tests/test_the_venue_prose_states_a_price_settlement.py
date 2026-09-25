"""WHAT THE VENUE ACTUALLY PUBLISHES, AND WHY IT BLOCKS HARDER THAN SILENCE.

THE MISTAKE THESE TESTS CLOSE WAS MINE, TWICE OVER.

I reported a per-condition settlement table for `aec-mlb-az-col-2026-09-24`
saying the venue states two conditions -- the final result including extra
innings, and VOID WITH STAKES RETURNED when a game is abandoned -- and that
nothing conflicts. Both halves were wrong:

  1 The measurement was taken from `pmus_settled_market_2026_09_24_az_col
    .json`, a TEST FIXTURE whose `description` is 183 hand-written
    characters. The live listing for the same slug carries 380.

  2 The venue's real rule for a game that is never completed is NOT a void
    with stakes returned. It is:

        "the market will settle to the last fair market price"

    which pays the holder whatever the contract last traded at. Entered at
    0.56 and last trading at 0.20, that returns 0.20 -- a loss. A stake
    return is whole. They are different cash.

The bookmaker reference returns the stake for the same condition. So this is
not a silence to be resolved: it is a STATED DISAGREEMENT, and reading it
correctly turns the verdict from UNKNOWN into INCOMPATIBLE. Nothing here
admits a candidate that was refused before; the refusal gets a truer name
and a stricter class.
"""

from __future__ import annotations

import json
import pathlib

from sportsassets import bettor_settlement_terms as ST

FIXTURE = (pathlib.Path(__file__).resolve().parent / "fixtures"
           / "pmus_live_description_mlb_h2h_2026_09_25.json")

#: The hand-written prose the earlier report was computed against. Kept here
#: as the thing being contradicted, never as venue evidence.
SYNTHETIC_PROSE = (
    "This market settles on the final result of the game, including any "
    "extra innings. If the game is abandoned or postponed and never "
    "completed the market is void and stakes are returned.")


def _live():
    return json.loads(FIXTURE.read_text())["markets"]


def test_the_fixture_is_the_venues_own_words_and_its_length_is_checked():
    for m in _live():
        assert m["chars"] == len(m["description"]), m["slug"]
        assert "last fair market price" in m["description"]
        # AND IT IS NOT THE SYNTHETIC PROSE.
        assert "stakes are returned" not in m["description"]
        assert m["description"] != SYNTHETIC_PROSE


def test_the_price_settlement_payout_is_its_own_class():
    """It is neither a stake return nor a score-based payout, so it cannot
    be folded into either. Folding it into PAY_STAKE_BACK would have
    manufactured agreement with the bookmaker out of a disagreement."""
    assert ST.PAY_LAST_FAIR_MARKET_PRICE in ST.PAYOUTS
    assert ST.PAY_LAST_FAIR_MARKET_PRICE != ST.PAY_STAKE_BACK
    assert ST.PAY_LAST_FAIR_MARKET_PRICE != ST.PAY_ON_FINAL
    assert ST.PAY_LAST_FAIR_MARKET_PRICE != ST.PAY_ON_PARTIAL
    # NEVER DECLARED EQUIVALENT TO ANYTHING, under any condition.
    for cond, groups in ST.SAME_PAYOUT_UNDER.items():
        for g in groups:
            assert ST.PAY_LAST_FAIR_MARKET_PRICE not in g, cond
    for cond in ST.CONDITIONS:
        assert not ST.same_payout(cond, ST.PAY_LAST_FAIR_MARKET_PRICE,
                                  ST.PAY_STAKE_BACK), cond


def test_the_live_prose_states_exactly_one_condition():
    """Sentence-scoped, as the reader requires. The first sentence names a
    payout with no condition; the second names a condition with no payout.
    Only the third states both, and it is the abandonment case."""
    for m in _live():
        r = ST.read_terms(m["description"])
        assert r["terms"] == {
            ST.C_NOT_PLAYED: ST.PAY_LAST_FAIR_MARKET_PRICE}, m["slug"]
        assert r["contradicted"] == []
        # THE TWO SENTENCES THAT ESTABLISH NOTHING ARE STILL RECORDED, so a
        # reader can tell venue silence from a reader that cannot read.
        unused = {c: [e["sentence"] for e in evs if not e["used"]]
                  for c, evs in r["evidence"].items()
                  if any(not e["used"] for e in evs)}
        assert ST.C_OVERTIME in unused, unused


def test_the_comparison_is_incompatible_on_a_stated_mismatch():
    """NOT `UNKNOWN`, and that is the whole point. Under both published
    contexts the venue's stated payout differs from the bookmaker's for the
    never-completed condition, so the verdict is a conflict."""
    for ctx in (ST.CTX_PRE_GAME, ST.CTX_LIVE):
        for m in _live():
            c = ST.compare_prose(sport_family="baseball", market="h2h",
                                 venue_prose=m["description"], context=ctx,
                                 phase=ST.PHASE_REGULAR,
                                 game_format=ST.FMT_NINE)
            assert c["verdict"] == ST.INCOMPATIBLE, (ctx, m["slug"], c)
            assert c["mismatched_conditions"] == [ST.C_NOT_PLAYED]
            pc = c["per_condition"][ST.C_NOT_PLAYED]
            assert pc["venue_payout"] == ST.PAY_LAST_FAIR_MARKET_PRICE
            assert pc["book_payout"] == ST.PAY_STAKE_BACK


def test_the_other_six_conditions_are_still_venue_silent():
    """The fix reads ONE sentence. It does not fill the other six, and a
    reader of this file must not come away thinking it did."""
    m = _live()[0]
    c = ST.compare_prose(sport_family="baseball", market="h2h",
                         venue_prose=m["description"], context=ST.CTX_PRE_GAME,
                         phase=ST.PHASE_REGULAR, game_format=ST.FMT_NINE)
    silent = [k for k, v in c["per_condition"].items()
              if v["verdict"] == "VENUE_STATES_NO_RULE_FOR_THIS_CONDITION"]
    assert len(silent) == 6, silent
    assert ST.C_NOT_PLAYED not in silent


def test_the_synthetic_prose_would_have_agreed_and_that_is_the_defect():
    """Run the hand-written fixture through the same reader: it produces a
    stake return, which MATCHES the bookmaker. That is how a fabricated
    fixture turned a real conflict into an agreement in my report."""
    r = ST.read_terms(SYNTHETIC_PROSE)
    assert r["terms"].get(ST.C_NOT_PLAYED) == ST.PAY_STAKE_BACK
    c = ST.compare_prose(sport_family="baseball", market="h2h",
                         venue_prose=SYNTHETIC_PROSE, context=ST.CTX_PRE_GAME,
                         phase=ST.PHASE_REGULAR, game_format=ST.FMT_NINE)
    assert c["per_condition"][ST.C_NOT_PLAYED]["verdict"] == "MATCH"
    # AND THE LIVE TEXT DOES NOT. Same reader, same scope, opposite answer.
    live = ST.compare_prose(sport_family="baseball", market="h2h",
                            venue_prose=_live()[0]["description"],
                            context=ST.CTX_PRE_GAME,
                            phase=ST.PHASE_REGULAR, game_format=ST.FMT_NINE)
    assert live["per_condition"][ST.C_NOT_PLAYED]["verdict"] == "MISMATCH"


def test_a_two_week_window_is_not_the_bookmakers_window():
    """Recorded, not acted on. The venue conditions its price settlement on
    a TWO WEEK rescheduling window; the bookmaker reference uses 12 hours
    pre-game and 30 hours in-play. Even if the payouts agreed, the trigger
    boundaries would not, and no code here treats one as the other."""
    for m in _live():
        assert "within two weeks" in m["description"]