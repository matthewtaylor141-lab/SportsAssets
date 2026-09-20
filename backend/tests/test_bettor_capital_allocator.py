"""§17: which claim on the same dollar wins, and when none may be ranked."""

from decimal import Decimal

import pytest

from sportsassets import bettor_capital_allocator as ca
from sportsassets import bettor_risk_engine as risk


@pytest.fixture
def rails_open():
    """Predeclare every rail and clear every gate, so risk stops binding."""
    originals = {n: s["limit"] for n, s in risk.RAILS.items()}
    for s in risk.RAILS.values():
        s["limit"] = "1000000"
    yield ({n: "1" for n in risk.RAILS},
           {g: True for g in risk.STATE_GATES})
    for n, v in originals.items():
        risk.RAILS[n]["limit"] = v


FAST_PENNY = {"expectedNetDollars": "0.01", "capitalRequired": "50",
              "expectedOccupancySeconds": "1"}
SLOW_DOLLAR = {"expectedNetDollars": "5.00", "capitalRequired": "50",
               "expectedOccupancySeconds": "86400"}


# ── nothing is ranked today, and that is correct ─────────────────────

def test_no_ranking_is_produced_without_an_identified_ev():
    r = ca.allocate()
    assert r["allocation"] == ca.NOT_IDENTIFIED
    assert r["ranking"] == []
    assert "FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED" in r["why"]


def test_a_partial_ranking_is_refused_rather_than_approximated():
    r = ca.allocate()
    assert "present that order as an economic judgement" in \
        r["partialRankingIsWorseThanNone"]


def test_capital_alone_does_not_make_a_candidate_rankable():
    """Capital is identified from the book; EV is not. That is not enough."""
    r = ca.allocate({"NEW_MAKER_QUOTE": {"capitalRequired": "50",
                                         "expectedOccupancySeconds": "60"}})
    row = {c["candidate"]: c for c in r["candidates"]}["NEW_MAKER_QUOTE"]
    assert row["CAPITAL_REQUIRED"] == "50"
    assert row[ca.NORTH_STAR] == ca.NOT_IDENTIFIED
    assert r["ranking"] == []


# ── risk binds before economics ──────────────────────────────────────

def test_a_risk_blocked_candidate_is_not_ranked_however_attractive():
    r = ca.allocate({"NEW_MAKER_QUOTE": FAST_PENNY})
    row = {c["candidate"]: c for c in r["candidates"]}["NEW_MAKER_QUOTE"]
    assert row["status"] == "IDENTIFIED"      # the economics are known
    assert row["admissible"] is False         # and it is still not ranked
    assert r["ranking"] == []
    assert "argue with a limit" in r["riskBindsFirst"]


def test_neutral_candidates_remain_admissible():
    r = ca.allocate()
    admissible = [c["candidate"] for c in r["candidates"] if c["admissible"]]
    assert "NO_TRADE" in admissible
    assert "HOLD_HIGH_EV_RESIDUAL" in admissible


# ── the two traps, in both directions ────────────────────────────────

def test_the_rate_and_the_level_are_reported_together(rails_open):
    observed, state = rails_open
    r = ca.allocate({"NEW_MAKER_QUOTE": FAST_PENNY,
                     "COMPLETE_EXISTING_PAIR": SLOW_DOLLAR},
                    risk_observed=observed, risk_state=state)
    rows = {c["candidate"]: c for c in r["candidates"]}
    penny = rows["NEW_MAKER_QUOTE"]
    dollar = rows["COMPLETE_EXISTING_PAIR"]

    # The penny has the better RATE and the worse LEVEL. Both are shown.
    assert Decimal(penny[ca.NORTH_STAR]) > Decimal(dollar[ca.NORTH_STAR])
    assert Decimal(penny["EXPECTED_NET_DOLLARS"]) < \
        Decimal(dollar["EXPECTED_NET_DOLLARS"])
    for row in (penny, dollar):
        assert row["EXPECTED_NET_DOLLARS"] != ca.NOT_IDENTIFIED
        assert row[ca.NORTH_STAR] != ca.NOT_IDENTIFIED


def test_a_penny_in_a_second_still_earns_a_penny(rails_open):
    observed, state = rails_open
    r = ca.allocate({"NEW_MAKER_QUOTE": FAST_PENNY},
                    risk_observed=observed, risk_state=state)
    row = {c["candidate"]: c for c in r["candidates"]}["NEW_MAKER_QUOTE"]
    # Both warnings travel with the row: the rate one on the row itself,
    # the level one on the result.
    assert "operationally expensive" in row["rateWithoutLevelIsMisleading"]
    assert "best line in the book" in r["bothFiguresOrNeither"]


def test_percentage_ev_is_rejected_as_a_ranking():
    assert "thirty seconds" in ca.NOT_PERCENTAGE_EV
    assert "three days" in ca.NOT_PERCENTAGE_EV


def test_a_rate_without_a_positive_denominator_is_not_identified():
    r = ca.score("NEW_MAKER_QUOTE", expected_net_dollars="1.00",
                 capital_required="50", expected_occupancy_seconds="0")
    assert r[ca.NORTH_STAR] == ca.NOT_IDENTIFIED
    assert "guessed denominator" in r["why"]


# ── structure ────────────────────────────────────────────────────────

def test_the_named_candidates_are_all_present():
    for c in ("NEW_MAKER_QUOTE", "COMPLETE_EXISTING_PAIR",
              "PAY_HEDGE_TAX_TO_RELEASE_CAPITAL", "HOLD_HIGH_EV_RESIDUAL",
              "NO_TRADE"):
        assert c in ca.CANDIDATES


def test_every_candidate_maps_to_a_canonical_action():
    from sportsassets import bettor_ev_actions as acts
    for c in ca.CANDIDATES:
        assert ca.CANDIDATE_ACTION[c] in acts.CANONICAL_ACTIONS, c


def test_the_named_constraints_are_declared():
    for k in ("UNCERTAINTY", "RESIDUAL_INVENTORY", "EVENT_CONCENTRATION",
              "CORRELATED_EXPOSURE", "LIQUIDITY", "DRAWDOWN",
              "EXECUTION_CAPACITY", "RISK_RAILS"):
        assert k in ca.CONSTRAINTS


def test_the_arithmetic_is_borrowed_not_rewritten():
    r = ca.score("NEW_MAKER_QUOTE", expected_net_dollars="1.00",
                 capital_required="50", expected_occupancy_seconds="3600")
    assert r["derivedBy"].endswith("inventory_state.py")
    # $1 on $50 for one hour = 0.02 per capital-hour.
    assert Decimal(r[ca.NORTH_STAR]) == Decimal("0.02")
