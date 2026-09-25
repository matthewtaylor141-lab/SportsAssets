"""THE SOURCE/PAYOFF DEPENDENCY, HELD AS CHECKS RATHER THAN AS A PARAGRAPH.

The entry lane's binding blocker is not a threshold anyone can tune. It is
that the only probability source connected to it answers a different
question from the one the contract asks.

    book rule   "If a fixture isn't started 12 hours after its scheduled
                 starting time all bets on that fixture will be voided."
    venue rule  "If the game is delayed, postponed, or suspended and not
                 rescheduled to a date within two weeks of the originally
                 scheduled date, the market will settle to the last fair
                 market price."

A voided bet leaves the book's sample, so its de-vigged price is
P(win | the bet has action) and contains nothing about how often a fixture
is lost or what the contract would then be worth. The venue pays a number
its own order book prints. Value therefore needs four quantities and the
source supplies one.

These tests exist so the missing three cannot quietly become zero.
"""

from __future__ import annotations

import inspect

from sportsassets import bettor_pinnacle_devig as DEVIG
from sportsassets import bettor_settlement_terms as ST


def test_the_source_states_what_its_probability_is_conditional_on():
    d = DEVIG.describe()
    assert d["probability_is_conditional_on"] == (
        "THE_BOOKMAKERS_BET_HAVING_ACTION_UNDER_ITS_OWN_PUBLISHED_RULE")
    # AND IT SAYS SO ON EVERY describe(), which is what consumers read.
    assert "missing_terms" in d


def test_exactly_three_terms_are_missing_and_they_are_named():
    req = DEVIG.PRICING_A_PRICE_SETTLED_CONTRACT_REQUIRES
    held = sorted(k for k, v in req.items() if v["state"] == "HELD")
    missing = sorted(k for k, v in req.items() if v["state"] == "MISSING")
    assert held == ["P_win_given_action"]
    assert missing == [
        "E_last_fair_market_price_given_that_branch",
        "P_the_price_settlement_branch_fires",
        "the_two_windows_are_the_same_variable",
    ]
    assert DEVIG.describe()["missing_terms"] == missing
    # EVERY MISSING TERM SAYS WHY, so nobody has to guess whether it is
    # unimplemented or unmeasurable.
    for k in missing:
        assert req[k]["why_not_held"], k
        assert req[k]["what_it_is"], k


def test_no_module_estimates_the_missing_terms():
    """THE FAILURE MODE THIS BLOCKS. A "small adjustment for postponements"
    would be a fabricated number with a plausible shape. Nothing in the
    package may name such a factor."""
    import pathlib

    root = pathlib.Path(DEVIG.__file__).resolve().parent
    banned = ("postponement_rate", "POSTPONEMENT_RATE", "rainout_prob",
              "P_POSTPONED", "p_postponed", "abandon_rate",
              "expected_last_price", "EXPECTED_LAST_PRICE")
    hits = []
    for p in sorted(root.rglob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        for name in banned:
            if name in src:
                hits.append("%s: %s" % (p.name, name))
    assert hits == [], hits


def test_the_ways_forward_are_capabilities_not_adjustments():
    ways = DEVIG.WAYS_FORWARD
    assert len(ways) == 3
    joined = " ".join(ways)
    # Each names a CAPABILITY or a different source/market.
    assert "A_SUPPORTED_MODEL_OF_THE_ALTERNATIVE_OUTCOMES" in joined
    assert "A_REFERENCE_SOURCE_WHOSE_OWN_RULE_MATCHES_THE_VENUES" in joined
    assert "A_MARKET_OR_VENUE_WHOSE_PAYOFF_IS_SCORE_ONLY" in joined
    # AND NONE OF THEM IS A WAIVER. The research-shadow waiver covers
    # calibration only, and this is not calibration.
    from sportsassets import bettor_research_shadow as RSH

    assert "UNRESOLVED_SETTLEMENT_SEMANTICS" in RSH.NEVER_WAIVABLE
    assert RSH.WAIVABLE == frozenset({"MODEL_TRUST_DRIFT"})


def test_the_two_published_windows_are_recorded_from_their_own_quotes():
    """The book's window comes from its citation, the venue's from the
    sentence that states the rule. Neither is restated from memory."""
    bk = ST.BOOK_TRIGGER_WINDOWS[ST.C_NOT_PLAYED]
    assert bk["qualifier"] == "HOURS_FROM_THE_SCHEDULED_START"
    assert "12 hours" in bk["window_as_published"]
    q = ST.trigger_qualifiers(
        "If the game is delayed, postponed, or suspended and not rescheduled "
        "to a date within two weeks of the originally scheduled date, the "
        "market will settle to the last fair market price.")
    assert q == ["A_MAKE_UP_DATE_INSIDE_A_NAMED_WINDOW"]
    assert bk["qualifier"] not in q


def test_the_gate_still_refuses_on_its_own_terms():
    """The dependency is not enforced by a new rule -- the existing
    settlement gate already refuses, and this test pins that it is the
    reason, so nobody "fixes" the blocker by deleting the check."""
    from sportsassets import bettor_entry_execution as EX

    src = inspect.getsource(EX)
    assert "UNRESOLVED_SETTLEMENT_SEMANTICS" in src
    assert "MODEL_TRUST_DRIFT" in src