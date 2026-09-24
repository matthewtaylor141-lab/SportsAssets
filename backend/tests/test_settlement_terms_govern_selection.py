"""SETTLEMENT COMPATIBILITY, COMPARED ON PAYOUTS AND ENFORCED AT SELECTION.

Two claims are under test here and they are separate claims.

  1. COMPATIBILITY IS A COMPARISON OF CONDITIONS AND PAYOUTS, not of
     vocabulary. Two documents that both say "void" and "stakes are
     returned" agree only if they say it about the SAME terminal
     condition, and a term captured for the bookmaker is admissible only
     with a citation.

  2. AN ESTABLISHED INCOMPATIBILITY CHANGES THE SELECTED ACTION. It is not
     a label beside an otherwise unchanged ranking: the probability leaves
     the comparison entirely, the hold is NOT valued at zero, and the
     position is NOT liquidated -- it falls to the declared missing-input
     fallback. An UNKNOWN rule does none of that and keeps its labelled
     conditional value.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_hold_value as HV
from sportsassets import bettor_mgmt_select as SEL
from sportsassets import bettor_settlement_terms as ST


# ── 1 · the comparison ───────────────────────────────────────────────

def test_the_same_words_about_different_conditions_are_not_a_match():
    """THE DEFECT THIS EXISTS FOR. The book voids when FEWER THAN FIVE
    INNINGS are complete; the venue voids when the game is NEVER COMPLETED.
    Both texts say "void" and "stakes returned", and the old class-to-class
    test called that established. They are rules about different states of
    the world, and the case between them -- a game called in the sixth --
    is exactly where the money is."""
    book = {ST.C_STOPPED_EARLY: ST.PAY_STAKE_BACK}
    venue = {ST.C_NOT_PLAYED: ST.PAY_STAKE_BACK}
    got = ST.compare(book=book, venue=venue,
                     conditions=(ST.C_STOPPED_EARLY, ST.C_NOT_PLAYED))
    assert got["verdict"] != ST.COMPATIBLE, got
    assert got["verdict"] == ST.UNKNOWN, got
    assert got["per_condition"][ST.C_STOPPED_EARLY]["verdict"] == \
        ST.V_VENUE_SILENT
    assert got["per_condition"][ST.C_NOT_PLAYED]["verdict"] == \
        ST.V_BOOK_SILENT


def test_a_payout_difference_under_one_condition_is_incompatible():
    """Both sides speak to the same condition and pay differently. That is
    not an unknown and no further reading reconciles it."""
    got = ST.compare(book={ST.C_NOT_PLAYED: ST.PAY_STAKE_BACK},
                     venue={ST.C_NOT_PLAYED: ST.PAY_NO},
                     conditions=(ST.C_NOT_PLAYED,))
    assert got["verdict"] == ST.INCOMPATIBLE, got
    assert got["mismatched_conditions"] == [ST.C_NOT_PLAYED]
    why = got["per_condition"][ST.C_NOT_PLAYED]["why"]
    assert "different cash outcomes" in why, why


def test_two_names_for_the_same_cash_match_only_where_they_coincide():
    """"final score" and "last completed period" are the same number for a
    game that finished, and different numbers for one that did not."""
    assert ST.same_payout(ST.C_FULL, ST.PAY_ON_FINAL, ST.PAY_ON_PARTIAL)
    assert ST.same_payout(ST.C_SUSPENDED_RESUMED,
                          ST.PAY_ON_FINAL, ST.PAY_ON_PARTIAL)
    # AND NOT where the game STOPPED. The blanket equivalence under the
    # stopped-game condition erased the one distinction the two sides could
    # disagree on, so it is gone.
    assert not ST.same_payout(ST.C_CALLED_FINAL,
                              ST.PAY_ON_FINAL, ST.PAY_ON_PARTIAL)
    assert not ST.same_payout(ST.C_SUSPENDED_BEYOND,
                              ST.PAY_ON_FINAL, ST.PAY_ON_PARTIAL)
    assert not ST.same_payout(ST.C_STOPPED_EARLY,
                              ST.PAY_ON_FINAL, ST.PAY_ON_PARTIAL)
    # the SCORING EXCEPTION is never equal to the plain rule
    assert not ST.same_payout(ST.C_CALLED_FINAL,
                              ST.PAY_ON_PARTIAL_WALKOFF, ST.PAY_ON_PARTIAL)


def test_a_payout_word_outside_any_condition_sentence_establishes_nothing():
    """The scoping rule, tested directly: "void" in a sentence that names no
    terminal condition is a word in a document, not a rule."""
    read = ST.read_terms("All markets are subject to the house rules. "
                         "Void bets are not included in turnover.")
    assert read["terms"] == {}, read


def test_one_sentence_naming_two_payouts_states_no_rule():
    read = ST.read_terms("If the game is abandoned the market is void and "
                         "stakes are returned, or it resolves to No.")
    assert ST.C_NOT_PLAYED not in read["terms"], read


def test_a_book_term_without_a_citation_is_refused():
    """The discipline is mechanical, not a comment asking for care."""
    adm = ST.admit_book_terms(
        {ST.C_NOT_PLAYED: {"payout": ST.PAY_STAKE_BACK}})
    assert adm["ok"] is False
    assert adm["admitted"] == {}
    assert adm["rejected"][0]["refusal"] == ST.R_NO_CITATION
    part = ST.admit_book_terms({ST.C_NOT_PLAYED: {
        "payout": ST.PAY_STAKE_BACK,
        "cite": {"source": "x", "source_url": "", "retrieved_at": "t",
                 "quote": "q"}}})
    assert part["ok"] is False, "a partial citation is not a citation"
    assert "missing source_url" in part["rejected"][0]["why"]


def test_every_captured_book_term_carries_a_complete_citation():
    """The table is no longer empty -- the page was retrieved -- so the
    discipline moves from "it ships empty" to "every entry is admissible".
    A term without source, URL, retrieval time and verbatim quote must not be
    in here, and `admit_book_terms` is the mechanism that keeps it out."""
    assert ST.BOOK_TERMS, "the capture landed, so this should not be empty"
    for key, terms in ST.BOOK_TERMS.items():
        assert len(key) == 3, (
            "a term set must be keyed by context too, since pre-game and "
            "In-Play differ: %r" % (key,))
        adm = ST.admit_book_terms(terms)
        assert adm["ok"], (key, adm["rejected"])
        for cond, rec in terms.items():
            assert ST.check_citation(rec["cite"]) == [], (key, cond)
            assert rec["cite"]["source_url"].startswith("https://"), rec
            assert len(rec["cite"]["quote"].split()) >= 8, (
                "a citation's quote must be the operative sentence, not a "
                "fragment: %r" % (rec["cite"]["quote"],))


def test_the_capture_records_its_own_provenance_and_its_limits():
    d = ST.describe()
    assert d["capture_run"]["http"] == 200
    assert d["capture_run"]["url"].startswith("https://www.pinnacle.com/")
    assert d["capture_run"]["retrieved_at"]
    assert d["capture_run"]["job"].startswith("https://github.com/")
    # THE SECOND SOURCE WAS REFUSED, and that is recorded rather than
    # quietly dropped -- nothing below depends on it.
    assert d["capture_run"]["also_attempted"]["http"] == 403
    assert d["capture_run"]["also_attempted"]["captured"] is False
    # AND WHAT THE CAPTURE DOES NOT COVER, so its scope is not overread
    lim = " ".join(d["capture_limits"])
    assert "PLAYOFF" in lim and "SEVEN-INNING" in lim and "MARKET RULES" in lim
    # the publisher's own precedence statement is recorded, because a market
    # rule would outrank the sport rules these terms come from
    assert "Market Rules take precedence over Sport Rules" in \
        d["rule_hierarchy"]["quote"]


def test_the_applicable_rule_comes_from_the_quotes_timing_not_the_market_name():
    """PRE-GAME AND IN-PLAY DISAGREE ON A CALLED GAME, so "h2h" cannot pick
    the rule. Pre-game grades the last completed inning; In-Play voids."""
    kw = dict(sport_family="baseball", market="h2h",
              phase=ST.PHASE_REGULAR, game_format=ST.FMT_NINE)
    pre = ST.book_terms(context=ST.CTX_PRE_GAME, **kw)
    live = ST.book_terms(context=ST.CTX_LIVE, **kw)
    assert pre[ST.C_CALLED_FINAL]["payout"] == ST.PAY_ON_PARTIAL_WALKOFF
    assert live[ST.C_CALLED_FINAL]["payout"] == ST.PAY_STAKE_BACK
    assert pre[ST.C_CALLED_FINAL]["payout"] != \
        live[ST.C_CALLED_FINAL]["payout"], (
        "if these were equal the distinction would not matter and the "
        "context could be dropped")
    # the context itself is established from the two stamps
    # A SCHEDULED STAMP CLASSIFIES NOTHING, in either direction
    assert ST.book_context_for(observed_at=10.0,
                               start_at=100.0)["context"] is None
    # only ACTUAL evidence does
    assert ST.book_context_for(
        observed_at=10.0, start_at=100.0,
        start_evidence=ST.SE_ACTUAL_REPORTED)["context"] == ST.CTX_PRE_GAME
    # AND AN UNKNOWN START IS NOT DEFAULTED TO PRE-GAME
    none = ST.book_context_for(observed_at=10.0, start_at=None)
    assert none["context"] is None
    assert none["refusal"] == ST.R_CONTEXT_UNKNOWN
    assert ST.book_terms(context=None, **kw) == {}


def test_an_unknown_quote_context_withholds_the_book_side_entirely():
    got = ST.compare_prose(sport_family="baseball", market="h2h",
                           venue_prose="If the game is abandoned and never "
                                       "completed the market is void and "
                                       "stakes are returned.",
                           observed_at=10.0, start_at=None,
                           phase=ST.PHASE_REGULAR,
                           game_format=ST.FMT_NINE)
    assert got["book_terms_held"] is False
    assert got["verdict"] == ST.UNKNOWN
    assert got["quote_context"]["refusal"] == ST.R_CONTEXT_UNKNOWN
    assert "NOT defaulted to pre-game" in got["why_book_side_absent"]


def test_an_undeclared_market_type_is_unknown_not_compatible():
    got = ST.compare_prose(sport_family="curling", market="h2h",
                           venue_prose="anything at all")
    assert got["verdict"] == ST.UNKNOWN
    assert got["refusal"] == ST.R_NO_APPLICABLE_SET


# ── 2 · the consequence at action selection ──────────────────────────

_P = 0.70          # probability of the payout event
_Q = 100.0         # contracts held
_BASIS = 0.57      # per contract
_BID = 0.72        # an exit that WOULD beat the hold if it were compared


def _row(**kw):
    r = {"probability": _P, "payout_event": "TEAM_A",
         "probability_event": "TEAM_A", "eligibility": "ELIGIBLE",
         "observed_at": 1000.0, "received_at": 1000.0,
         "provider": "t", "book": "b", "devig_method": "power"}
    r.update(kw)
    return r


def _settlement(*, unmet=(), established=False, mismatched=()):
    return {"sport_family": "baseball", "market": "h2h",
            "overall_established": established, "unmet": list(unmet),
            "attested": [], "mismatched_conditions": list(mismatched),
            "book_rule": "FULL_GAME_INCLUDING_EXTRA_INNINGS"}


def _hold(settlement):
    return HV.ev_hold(qty=_Q, basis_per_contract=_BASIS,
                      probability_row=_row(), now=1010.0,
                      payout_event_held="TEAM_A", event_state="PRE_MATCH",
                      max_age_s=30.0, settlement=settlement)


def _rank(hv, *, fired=False):
    return SEL.rank_with_hold(
        _Q, _BASIS, ev_hold=hv, bid=_BID, bid_size=_Q,
        fee_fn=lambda qty, price: 0.0, venue="PMUS",
        us_market_slug="s", held_is_long=True,
        fallback_trigger={"fired": fired, "reason": "no condition met",
                          "conditions": [{"status": "EVALUATED"}]})


def test_unknown_compatibility_keeps_a_labelled_conditional_value():
    hv = _hold(_settlement(unmet=["VOID_ABANDONMENT_BOOK_RULE_NOT_HELD"]))
    assert hv["status"] == "IDENTIFIED"
    assert hv["value_is_conditional"] is True
    assert hv["terminal_rule"]["compatibility"] == "UNKNOWN"
    assert hv["selection_eligible"] is True, (
        "an unstated rule leaves the probability the best available "
        "estimate; it is labelled, not withdrawn")
    out = _rank(hv)
    held = [c for c in out["ranked"] if c["action"] == "HOLD"]
    assert held and held[0]["value_is_conditional"] is True
    assert out["hold_input"]["excluded_from_selection"] is False
    # the comparison really was made: the better exit wins it
    assert out["selected"] == "DIRECT_EXIT", out["selected"]


def test_an_incompatible_rule_disqualifies_the_probability_from_selection():
    """THE SAME INPUTS, one changed fact, a DIFFERENT SELECTED ACTION.

    Paired with the test above deliberately: identical quantity, basis,
    probability and bid. The only difference is that the two sides are known
    to pay differently. If the incompatibility were merely a warning the
    selection would be DIRECT_EXIT in both."""
    hv = _hold(_settlement(
        unmet=["OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE"],
        mismatched=[ST.C_NOT_PLAYED]))
    assert hv["status"] == "IDENTIFIED", "the number is still computed"
    assert hv["ev_hold_usd"] is not None and hv["ev_hold_usd"] != 0.0, (
        "an unknown hold value is NOT zero")
    assert hv["selection_eligible"] is False
    assert hv["selection_refusal"] == HV.R_SETTLEMENT_INCOMPATIBLE
    assert hv["terminal_rule"]["compatibility"] == "INCOMPATIBLE"
    assert hv["terminal_rule"]["transform_available"] is False

    out = _rank(hv)
    assert out["selected"] == "HOLD", (
        "the position must NOT be liquidated for want of a comparable "
        "hold value: %r" % (out["selected"],))
    assert out["selected_qty"] == _Q
    assert out["operating_state"] == "HOLD_BY_FALLBACK_RULE"
    assert out["is_a_deliberate_hold"] is True
    assert not [c for c in out["ranked"] if c["action"] == "HOLD"]
    hi = out["hold_input"]
    assert hi["available"] is False
    assert hi["excluded_from_selection"] is True
    assert hi["ev_hold_usd"] is None, (
        "the selector used no hold value, so the row must not carry one "
        "in the field a reader takes as the comparison input")
    assert hi["ev_hold_usd_shadow_only"] == pytest.approx(_P * _Q
                                                          - _BASIS * _Q)
    blk = next(r for r in out["not_rankable"] if r["action"] == "HOLD")
    assert blk["blocker"] == HV.R_SETTLEMENT_INCOMPATIBLE
    assert blk["mismatched_conditions"] == [ST.C_NOT_PLAYED]


def test_a_disqualified_hold_with_no_fallback_selects_nothing_not_an_exit():
    """The strongest form of "do not automatically liquidate": with no
    fallback rule supplied, the answer is NOTHING SELECTED, never a sale."""
    hv = _hold(_settlement(unmet=["X_CONFLICTS_WITH_BOOK_RULE"],
                           mismatched=[ST.C_NOT_PLAYED]))
    out = SEL.rank_with_hold(
        _Q, _BASIS, ev_hold=hv, bid=_BID, bid_size=_Q,
        fee_fn=lambda qty, price: 0.0, venue="PMUS",
        us_market_slug="s", held_is_long=True, fallback_trigger=None)
    assert out["selected"] is None, out["selected"]
    assert out["operating_state"] == "HOLD_FOR_MISSING_INPUT"
    assert out["is_a_deliberate_hold"] is False


def test_a_fired_fallback_may_still_exit_a_disqualified_position():
    """Disqualification removes the FORECAST from the comparison. It does
    not freeze the position: the declared trigger, which uses no forecast,
    can still decide to close."""
    hv = _hold(_settlement(unmet=["X_CONFLICTS_WITH_BOOK_RULE"],
                           mismatched=[ST.C_NOT_PLAYED]))
    out = _rank(hv, fired=True)
    assert out["fallback_fired"] is True
    assert out["selected"] == "DIRECT_EXIT"
    assert not [c for c in out["ranked"] if c["action"] == "HOLD"], (
        "even then the disqualified hold is not a ranked alternative")


def test_no_transformation_is_established_so_none_can_excuse_a_conflict():
    assert HV.SETTLEMENT_TRANSFORMS == {}
    assert HV.settlement_transform(
        _settlement(mismatched=[ST.C_NOT_PLAYED])) is None


# ── 3 · the three repairs: start provenance, the exception, the scope ──

def test_a_scheduled_start_passing_does_not_make_a_quote_in_play():
    """THE DELAYED-START REGRESSION.

    `market_starts.game_start` is written from the CLOB catalogue's
    `game_start_time` -- a SCHEDULED first pitch.
    `bettor_progress_providers` already refuses to derive a period from it,
    and the first version of `book_context_for` reintroduced exactly that
    derivation. A rain delay leaves the scheduled time an hour in the past
    with no pitch thrown, and calling that IN_PLAY would apply rule 4, which
    VOIDS a called game, to a bet rule 3 would have PAID.
    """
    got = ST.book_context_for(observed_at=100.0 + 3600.0, start_at=100.0,
                              start_evidence=ST.SE_SCHEDULED_CATALOGUE)
    assert got["context"] != ST.CTX_LIVE, got
    assert got["context"] is None
    assert got["refusal"] == ST.R_SCHEDULED_ONLY
    assert "a delay means play may not have begun" in got["why"]
    # AND THE STALE-SCHEDULE CASE, the other direction: a snapshot taken
    # before the start was ADVANCED reads as pre-game while the game is
    # already under way, so observing before the stamp establishes nothing
    # either.
    stale = ST.book_context_for(observed_at=100.0 - 600.0, start_at=100.0,
                                start_evidence=ST.SE_SCHEDULED_CATALOGUE)
    assert stale["context"] is None, stale
    assert stale["refusal"] == ST.R_SCHEDULED_ONLY
    assert "may ALREADY have begun" in stale["why"]
    # AND THE BOOK SIDE IS THEREFORE WITHHELD, not guessed
    cmp_ = ST.compare_prose(
        sport_family="baseball", market="h2h",
        venue_prose="If the game is abandoned the market is void.",
        observed_at=100.0 + 3600.0, start_at=100.0,
        start_evidence=ST.SE_SCHEDULED_CATALOGUE,
        phase=ST.PHASE_REGULAR, game_format=ST.FMT_NINE)
    assert cmp_["book_terms_held"] is False
    assert cmp_["verdict"] != ST.COMPATIBLE
    assert ST.R_SCHEDULED_ONLY in cmp_["book_side_absent_refusals"]


def test_only_actual_start_evidence_or_a_provider_label_gives_in_play():
    after = dict(observed_at=200.0, start_at=100.0)
    assert ST.book_context_for(
        start_evidence=ST.SE_ACTUAL_REPORTED, **after)["context"] == ST.CTX_LIVE
    assert ST.book_context_for(
        quote_is_in_play=True, observed_at=None,
        start_at=None)["context"] == ST.CTX_LIVE
    assert ST.book_context_for(
        quote_is_in_play=False, observed_at=None,
        start_at=None)["context"] == ST.CTX_PRE_GAME
    # and a SCHEDULED stamp supports NEITHER direction
    for ob in (50.0, 200.0):
        assert ST.book_context_for(
            observed_at=ob, start_at=100.0,
            start_evidence=ST.SE_SCHEDULED_CATALOGUE)["context"] is None


def test_a_called_game_and_a_suspended_one_are_not_the_same_condition():
    """Rules 3, 7 and 8 give three different answers, and collapsing them
    would hide the one the two sides can disagree on."""
    kw = dict(sport_family="baseball", market="h2h",
              phase=ST.PHASE_REGULAR, game_format=ST.FMT_NINE)
    pre = ST.book_terms(context=ST.CTX_PRE_GAME, **kw)
    live = ST.book_terms(context=ST.CTX_LIVE, **kw)
    # PRE-GAME: called -> the exception formula; suspended-beyond -> the
    # plain last-completed-inning formula; resumed -> the final score.
    assert pre[ST.C_CALLED_FINAL]["payout"] == ST.PAY_ON_PARTIAL_WALKOFF
    assert pre[ST.C_SUSPENDED_BEYOND]["payout"] == ST.PAY_ON_PARTIAL
    assert pre[ST.C_SUSPENDED_RESUMED]["payout"] == ST.PAY_ON_FINAL
    assert pre[ST.C_CALLED_FINAL]["payout"] != pre[ST.C_SUSPENDED_BEYOND][
        "payout"], "the called and suspended formulas differ"
    # IN-PLAY: resumption inside the window keeps action; beyond it voids.
    assert live[ST.C_SUSPENDED_RESUMED]["payout"] == ST.PAY_ON_FINAL
    assert live[ST.C_SUSPENDED_BEYOND]["payout"] == ST.PAY_STAKE_BACK
    # THE WINDOWS DIFFER BY CONTEXT, so one suspension can be inside the
    # window for a pre-game bet and beyond it for a live one.
    assert ST.RESUMPTION_WINDOW_S[ST.CTX_PRE_GAME]["window_s"] == 12 * 3600
    assert ST.RESUMPTION_WINDOW_S[ST.CTX_LIVE]["window_s"] == 30 * 3600
    for w in ST.RESUMPTION_WINDOW_S.values():
        assert ST.check_citation(w["cite"]) == []


def test_two_sides_differing_only_on_the_scoring_exception_are_incompatible():
    """A venue that grades a called game on the last completed inning with NO
    walk-off exception pays differently from the book in exactly one case, and
    that is a MISMATCH rather than a rounding detail."""
    got = ST.compare(book={ST.C_CALLED_FINAL: ST.PAY_ON_PARTIAL_WALKOFF},
                     venue={ST.C_CALLED_FINAL: ST.PAY_ON_PARTIAL},
                     conditions=(ST.C_CALLED_FINAL,))
    assert got["verdict"] == ST.INCOMPATIBLE, got
    assert got["mismatched_conditions"] == [ST.C_CALLED_FINAL]


def test_an_unestablished_phase_or_format_admits_no_terms():
    """CAPTURE_LIMITS is prose for a reader; this is the gate."""
    base = dict(sport_family="baseball", market="h2h",
                context=ST.CTX_PRE_GAME)
    assert ST.book_terms(**base) == {}, "no phase, no format, no terms"
    assert ST.book_terms(phase=ST.PHASE_REGULAR, **base) == {}, "format missing"
    assert ST.book_terms(game_format=ST.FMT_NINE, **base) == {}, "phase missing"
    assert ST.book_terms(phase=ST.PHASE_REGULAR,
                         game_format=ST.FMT_NINE, **base)
    # AND AN EXCLUDED SCOPE IS REFUSED BY NAME, not merely unknown
    pl = ST.admit_scope(sport_family="baseball", phase=ST.PHASE_PLAYOFF,
                        game_format=ST.FMT_NINE)
    assert pl["ok"] is False and ST.R_PHASE_EXCLUDED in pl["refusals"]
    assert "whenever the game is completed" in pl["why"]
    dh = ST.admit_scope(sport_family="baseball", phase=ST.PHASE_REGULAR,
                        game_format=ST.FMT_SEVEN)
    assert dh["ok"] is False and ST.R_FORMAT_EXCLUDED in dh["refusals"]
    assert "7-inning threshold" in dh["why"]
    assert ST.book_terms(phase=ST.PHASE_PLAYOFF,
                         game_format=ST.FMT_SEVEN, **base) == {}


def test_an_excluded_scope_can_never_read_as_compatible():
    """The strongest form: prose that would otherwise agree on every
    condition still cannot produce COMPATIBLE outside the captured scope."""
    prose = ("This market settles on the final result of the game, including "
             "any extra innings. A game completed in regulation settles on "
             "the final score. If the game is called (ended) after at least "
             "five innings the market settles on the score at the end of the "
             "last completed inning, unless it is called in the bottom half "
             "and the home team has taken the lead, in which case the actual "
             "score is used. If the game is stopped before five innings the "
             "market is void and stakes are returned. If the game is "
             "suspended and resumed within the window it settles on the "
             "final score. If the game is suspended more than the window it "
             "settles on the score at the end of the last completed inning. "
             "If the game is abandoned or postponed and never completed the "
             "market is void and stakes are returned.")
    for phase, fmt in ((ST.PHASE_PLAYOFF, ST.FMT_NINE),
                       (ST.PHASE_REGULAR, ST.FMT_SEVEN),
                       (None, None)):
        got = ST.compare_prose(sport_family="baseball", market="h2h",
                               venue_prose=prose, observed_at=50.0,
                               start_at=100.0, phase=phase, game_format=fmt)
        assert got["verdict"] != ST.COMPATIBLE, (phase, fmt, got["verdict"])
        assert got["book_terms_held"] is False, (phase, fmt)
