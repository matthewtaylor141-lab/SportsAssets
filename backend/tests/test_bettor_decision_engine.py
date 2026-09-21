"""The decision engine: what it decides, and what it refuses to decide.

The properties under test are the ones that separate an engine from a
random number generator with good manners:

  * NOT_IDENTIFIED never behaves as zero. An unscored action must not win
    a ranking by being the only thing left in it.
  * BLOCKED and NOT_IDENTIFIED are different states. Directional action
    is blocked by a MEASUREMENT (the challenger scored worse than the
    market on held-out events), not by a missing one.
  * every alternative is recorded, including on a NO_TRADE decision --
    an engine that only records its trades cannot be evaluated.
  * one reconciled cash-flow model: an action's EV equals the cash flows
    it causes, so spread, fair-value gain and pairing benefit cannot be
    added to each other.
  * a price with no depth is not an opportunity, and a stale book is not
    evidence about now.
"""

from __future__ import annotations

import pytest

from sportsassets import bettor_decision_engine as de
from sportsassets.bettor_decision_engine import Book, Fees, Inventory


def book(**kw):
    kw.setdefault("market_id", "m")
    kw.setdefault("age_s", 1.0)
    return Book(**kw)


def cand(record, action):
    return next(c for c in record["candidates"] if c["action"] == action)


# ── the model-free pair, which is the only identified EV ─────────────

class TestPairArithmeticIsForecastFree:

    def test_asks_under_par_are_a_positive_ev_buy(self):
        r = de.decide(book(yes_ask=.47, no_ask=.50,
                           yes_ask_size=300, no_ask_size=800),
                      max_contracts=1000)
        assert r["selected"] == de.PAIR_BUY
        assert r["size_contracts"] == 300
        c = cand(r, de.PAIR_BUY)
        # 300 x (1.00 - 0.97) = 9.00, and nothing else.
        assert c["ev_net"] == pytest.approx(9.0)
        assert c["cash_now"] == pytest.approx(-291.0)
        assert c["cash_at_settlement"] == pytest.approx(300.0)

    def test_the_ev_is_exactly_the_cash_flows_and_nothing_added(self):
        """ONE RECONCILED MODEL. If spread capture were added to the
        pairing benefit this would come out at roughly double."""
        r = de.decide(book(yes_bid=.40, yes_ask=.47, no_bid=.40, no_ask=.50,
                           yes_ask_size=100, no_ask_size=100),
                      max_contracts=100)
        c = cand(r, de.PAIR_BUY)
        assert c["ev_net"] == pytest.approx(c["cash_now"]
                                            + c["cash_at_settlement"])
        assert c["ev_net"] == pytest.approx(3.0)

    def test_asks_over_par_are_negative_and_lose_to_no_trade(self):
        r = de.decide(book(yes_ask=.47, no_ask=.56,
                           yes_ask_size=500, no_ask_size=500),
                      max_contracts=1000)
        assert r["selected"] == de.NO_TRADE
        assert cand(r, de.PAIR_BUY)["ev_net"] == pytest.approx(-15.0)

    def test_fees_can_turn_a_positive_pair_negative(self):
        cheap = de.decide(book(yes_ask=.47, no_ask=.50, yes_ask_size=300,
                               no_ask_size=300), max_contracts=300)
        dear = de.decide(book(yes_ask=.47, no_ask=.50, yes_ask_size=300,
                              no_ask_size=300), max_contracts=300,
                         fees=Fees(taker_per_contract=0.02))
        assert cheap["selected"] == de.PAIR_BUY
        assert dear["selected"] == de.NO_TRADE

    def test_an_unverified_rebate_contributes_nothing_by_default(self):
        """Fees() has rebate 0.0. An incentive nobody has seen on a
        settled statement must not rescue a losing trade."""
        assert Fees().rebate_verified_per_contract == 0.0
        r = de.decide(book(yes_ask=.50, no_ask=.51, yes_ask_size=100,
                           no_ask_size=100), max_contracts=100)
        assert r["selected"] == de.NO_TRADE

    def test_a_verified_rebate_does_count_when_supplied(self):
        r = de.decide(book(yes_ask=.50, no_ask=.51, yes_ask_size=100,
                           no_ask_size=100), max_contracts=100,
                      fees=Fees(rebate_verified_per_contract=0.02))
        assert r["selected"] == de.PAIR_BUY

    def test_size_is_the_lesser_depth_not_the_greater(self):
        r = de.decide(book(yes_ask=.47, no_ask=.50,
                           yes_ask_size=12, no_ask_size=9000),
                      max_contracts=9000)
        assert r["size_contracts"] == 12

    def test_max_contracts_binds(self):
        r = de.decide(book(yes_ask=.47, no_ask=.50,
                           yes_ask_size=9000, no_ask_size=9000),
                      max_contracts=25)
        assert r["size_contracts"] == 25


class TestPairSellFromInventory:

    def test_bids_over_par_sell_the_pair(self):
        r = de.decide(book(yes_bid=.55, no_bid=.48,
                           yes_bid_size=200, no_bid_size=200),
                      inventory=Inventory(yes_contracts=200, no_contracts=200))
        assert r["selected"] == de.PAIR_SELL
        assert cand(r, de.PAIR_SELL)["ev_net"] == pytest.approx(6.0)

    def test_bids_under_par_hold(self):
        r = de.decide(book(yes_bid=.45, no_bid=.48,
                           yes_bid_size=200, no_bid_size=200),
                      inventory=Inventory(yes_contracts=200, no_contracts=200))
        assert r["selected"] == de.HOLD

    def test_nothing_to_sell_is_unsupported_not_zero_ev(self):
        r = de.decide(book(yes_bid=.55, no_bid=.55,
                           yes_bid_size=100, no_bid_size=100))
        assert cand(r, de.PAIR_SELL)["status"] == de.UNSUPPORTED

    def test_the_baseline_switches_with_inventory(self):
        assert de.decide(book(yes_ask=.5, no_ask=.6))["baseline"] == de.NO_TRADE
        held = de.decide(book(yes_ask=.5, no_ask=.6),
                         inventory=Inventory(yes_contracts=10))
        assert held["baseline"] == de.HOLD

    def test_basis_does_not_enter_any_ev(self):
        """Sunk cost must not justify holding. Two identical books with
        wildly different bases must produce the same decision."""
        cheap = de.decide(book(yes_bid=.55, no_bid=.48, yes_bid_size=200,
                               no_bid_size=200),
                          inventory=Inventory(200, 200, 0.01, 0.01))
        dear = de.decide(book(yes_bid=.55, no_bid=.48, yes_bid_size=200,
                              no_bid_size=200),
                         inventory=Inventory(200, 200, 0.99, 0.99))
        assert cheap["selected"] == dear["selected"]
        assert (cand(cheap, de.PAIR_SELL)["ev_net"]
                == cand(dear, de.PAIR_SELL)["ev_net"])


# ── the refusals, and that they are refusals of different kinds ──────

class TestUnscoredNeverWins:

    def test_maker_is_not_identified_and_does_not_win(self):
        """The spread is 40 cents -- a huge notional prize -- and the
        engine still declines, because the probability of collecting it
        is unmeasured. NOT_IDENTIFIED is not zero and it is not a bet."""
        r = de.decide(book(yes_bid=.30, yes_ask=.70, no_bid=.30, no_ask=.70,
                           yes_ask_size=500, no_ask_size=500),
                      max_contracts=500)
        assert r["selected"] == de.NO_TRADE
        for a in (de.MAKE_YES, de.MAKE_NO):
            c = cand(r, a)
            assert c["status"] == de.NOT_IDENTIFIED
            assert c["ev_net"] is None
            assert c["blocker"] == "P_FILL_NOT_IDENTIFIED"

    def test_directional_is_blocked_not_merely_unscored(self):
        """The distinction is the point. BLOCKED means we measured and
        the answer told us not to; NOT_IDENTIFIED means we could not
        measure. Collapsing them would lose the reason."""
        r = de.decide(book(yes_ask=.10, no_ask=.10, yes_ask_size=999,
                           no_ask_size=999), max_contracts=999)
        for a in (de.TAKE_YES, de.TAKE_NO):
            c = cand(r, a)
            assert c["status"] == de.BLOCKED
            assert "FV_BETTOR_INDEPENDENT" in c["blocker"]
            assert "worse" in c["why"].lower()

    def test_not_detected_is_not_recorded_as_refuted(self):
        r = de.decide(book(yes_ask=.5, no_ask=.6))
        c = cand(r, de.TAKE_YES)
        assert "not a refutation" in c["uncertainty"]

    def test_merge_is_unsupported_on_both_venues(self):
        for venue in ("institutional", "retail"):
            r = de.decide(book(yes_ask=.5, no_ask=.6), venue=venue)
            c = cand(r, de.MERGE)
            assert c["status"] == de.UNSUPPORTED
            assert venue.upper() in c["blocker"]

    def test_a_reference_account_mechanism_is_not_inherited(self):
        r = de.decide(book(yes_ask=.5, no_ask=.6), venue="institutional")
        c = cand(r, de.MERGE)
        assert any("reference account" in a for a in c["assumptions"])


# ── data quality: a bad book produces no action ──────────────────────

class TestBadDataRefusesRatherThanGuesses:

    def test_a_stale_book_is_rejected_outright(self):
        r = de.decide(Book("m", yes_ask=.01, no_ask=.01, yes_ask_size=9999,
                           no_ask_size=9999, age_s=45.0), max_contracts=9999)
        assert r["selected"] == de.NO_TRADE
        assert r["data_quality"] == "REJECTED"
        assert "45.0s old" in r["reason"]

    def test_an_undated_book_is_rejected(self):
        r = de.decide(Book("m", yes_ask=.01, no_ask=.01, yes_ask_size=999,
                           no_ask_size=999, age_s=None), max_contracts=999)
        assert r["data_quality"] == "REJECTED"
        assert "cannot be dated" in r["reason"]

    def test_a_closed_venue_is_rejected(self):
        r = de.decide(Book("m", yes_ask=.01, no_ask=.01, yes_ask_size=999,
                           no_ask_size=999, age_s=1.0, venue_state="CLOSED"),
                      max_contracts=999)
        assert r["data_quality"] == "REJECTED"

    @pytest.mark.parametrize("bad", [None, "", "None", "n/a", float("nan")])
    def test_an_unparsable_price_is_not_a_price_of_zero(self, bad):
        """A price of 0.0 would make every broken book look like free
        money, which is the single most dangerous parse bug available."""
        r = de.decide(book(yes_ask=bad, no_ask=.50,
                           yes_ask_size=999, no_ask_size=999),
                      max_contracts=999)
        assert r["selected"] == de.NO_TRADE
        assert cand(r, de.PAIR_BUY)["blocker"] == "ASK_UNREADABLE"

    def test_a_price_with_no_depth_is_not_an_opportunity(self):
        r = de.decide(book(yes_ask=.10, no_ask=.10,
                           yes_ask_size=0, no_ask_size=9999),
                      max_contracts=9999)
        assert r["selected"] == de.NO_TRADE
        assert cand(r, de.PAIR_BUY)["blocker"] == "NO_EXECUTABLE_DEPTH"

    def test_zero_max_contracts_cannot_trade(self):
        r = de.decide(book(yes_ask=.10, no_ask=.10, yes_ask_size=999,
                           no_ask_size=999), max_contracts=0)
        assert r["selected"] == de.NO_TRADE


# ── the record itself ────────────────────────────────────────────────

class TestEveryDecisionIsAuditable:

    def test_alternatives_are_recorded_even_on_no_trade(self):
        r = de.decide(book(yes_ask=.60, no_ask=.60, yes_ask_size=100,
                           no_ask_size=100), max_contracts=100)
        assert r["selected"] == de.NO_TRADE
        actions = {c["action"] for c in r["candidates"]}
        assert {de.NO_TRADE, de.PAIR_BUY, de.TAKE_YES, de.TAKE_NO,
                de.MAKE_YES, de.MAKE_NO, de.MERGE} <= actions

    def test_the_record_carries_assumptions_and_uncertainty(self):
        r = de.decide(book(yes_ask=.47, no_ask=.50, yes_ask_size=100,
                           no_ask_size=100), max_contracts=100)
        assert r["assumptions"], "a decision with no stated assumptions"
        assert r["uncertainty"]
        assert r["reason"]

    def test_the_threshold_is_respected(self):
        cheap = de.decide(book(yes_ask=.499, no_ask=.50, yes_ask_size=100,
                               no_ask_size=100), max_contracts=100)
        assert cheap["selected"] == de.PAIR_BUY
        strict = de.decide(book(yes_ask=.499, no_ask=.50, yes_ask_size=100,
                                no_ask_size=100), max_contracts=100,
                           min_ev_to_act=5.0)
        assert strict["selected"] == de.NO_TRADE
        assert "threshold" in strict["reason"]

    def test_fair_value_basis_is_named_and_is_never_a_belief(self):
        r = de.decide(book(yes_ask=.47, no_ask=.50))
        assert r["fair_value_basis"] == "FV_VENUE_IMPLIED"
        assert r["directional_permitted"] is False

    def test_describe_states_what_it_cannot_do(self):
        d = de.describe()
        assert d["submits_orders"] is False
        assert d["unverified_incentives"] == "zero in the base case"
        assert set(d["blocked"]) == {de.TAKE_YES, de.TAKE_NO,
                                     de.SELL_YES, de.SELL_NO}
        assert set(d["unscored"]) == {de.MAKE_YES, de.MAKE_NO}

    def test_it_never_raises_on_any_input(self):
        """A decision engine that throws stops the loop. Every refusal
        must come back as a record."""
        for b in (Book("m"), Book("m", age_s=0.0),
                  Book("m", yes_ask="x", no_ask="y", age_s=1.0),
                  Book("m", yes_ask=-1.0, no_ask=99.0, age_s=1.0,
                       yes_ask_size=-5, no_ask_size=1e9)):
            r = de.decide(b, max_contracts=100)
            assert "selected" in r and "reason" in r


class TestNoOrderPathExists:

    def test_the_module_holds_no_adapter_credentials_or_pool(self):
        import inspect

        src = inspect.getsource(de)
        for forbidden in ("submit_fok", "pmus", "_get_client", "get_pool",
                          "authorize", "INSERT", "UPDATE "):
            assert forbidden not in src, (
                "%s appears; the decision engine computes and returns, it "
                "does not execute" % forbidden)
