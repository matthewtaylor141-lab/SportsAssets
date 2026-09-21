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


# THE CORRECTED CONTRACT. Review reproduced three holes in the old one:
# an unverified venue, a negative price and a negative age all returned
# data_quality "OK". So a usable book must now state its venue_state and
# the provenance of its complement, and a pair action additionally needs
# a VERIFIED fee schedule and an account capability. These helpers supply
# them explicitly -- which is the point: nothing is usable by default.
from sportsassets import bettor_venue_contract as vc

VERIFIED_FEES = Fees(verified=True, source="TEST_VERIFIED_SCHEDULE")


def book(**kw):
    kw.setdefault("market_id", "m")
    kw.setdefault("age_s", 1.0)
    kw.setdefault("venue_state", "OPEN")
    kw.setdefault("complement_source", vc.OBSERVED)
    return Book(**kw)


def capable(monkeypatch):
    """An account that CAN hold both legs. Declared, never assumed."""
    vc.CAPABILITIES[("test-venue", "institutional")] = vc.VenueCapabilities(
        venue="test-venue", account_class="institutional",
        holds_both_legs_independently=vc.SUPPORTED,
        complement_quote_source=vc.OBSERVED, native_merge=vc.UNSUPPORTED,
        maker_orders=vc.SUPPORTED, cancel_replace=vc.SUPPORTED,
        verified_fee_schedule=True)
    return {"venue": "test-venue", "fees": VERIFIED_FEES}


def cand(record, action):
    return next(c for c in record["candidates"] if c["action"] == action)


# ── the model-free pair, which is the only identified EV ─────────────

class TestPairArithmeticIsForecastFree:
    """RENAMED IN SPIRIT: it is not forecast-free and never was.

    1 - yes_ask - no_ask - fees is a COMPLETED pair's conditional
    payoff. Calling it the EV assumed both legs fill, which is a
    conditional fill probability, which is P_FILL, which is
    NOT_IDENTIFIED. The corrected engine reports the conditional payoff
    and refuses to call it executable.
    """

    def test_the_conditional_payoff_is_computed_but_not_selected(self,
                                                                 monkeypatch):
        kw = capable(monkeypatch)
        r = de.decide(book(yes_ask=.47, no_ask=.50, yes_bid=.45, no_bid=.48,
                           yes_ask_size=300, no_ask_size=800),
                      max_contracts=1000, **kw)
        c = cand(r, de.PAIR_BUY)
        assert c["status"] == de.NOT_IDENTIFIED
        assert c["blocker"] == "P_BOTH_LEGS_FILL_NOT_IDENTIFIED"
        assert c["ev_net"] is None, "a conditional payoff is not an EV"
        assert c["conditional_payoff"] == pytest.approx(9.0)
        assert c["cash_now"] == pytest.approx(-291.0)
        assert c["cash_at_settlement"] == pytest.approx(300.0)
        assert r["selected"] == de.NO_TRADE

    def test_the_leg_risk_is_priced_not_narrated(self, monkeypatch):
        kw = capable(monkeypatch)
        r = de.decide(book(yes_ask=.47, no_ask=.50, yes_bid=.45, no_bid=.48,
                           yes_ask_size=100, no_ask_size=100),
                      max_contracts=100, **kw)
        c = cand(r, de.PAIR_BUY)
        assert c["leg_risk_cost"] == pytest.approx(2.0)  # 100 x 0.02 spread

    def test_a_derived_complement_is_blocked_as_an_identity(self,
                                                            monkeypatch):
        """Class C: 3,732 observations, 0 at or below par, min basis
        1.0050. ask + (1 - bid) = 1 + spread, by construction."""
        kw = capable(monkeypatch)
        r = de.decide(book(yes_ask=.47, no_ask=.50, yes_ask_size=100,
                           no_ask_size=100, complement_source=vc.DERIVED),
                      max_contracts=100, **kw)
        c = cand(r, de.PAIR_BUY)
        assert c["status"] == de.BLOCKED
        assert c["blocker"] == "COMPLEMENT_IS_DERIVED_NOT_OBSERVED"

    def test_an_absent_complement_is_not_identified(self, monkeypatch):
        kw = capable(monkeypatch)
        r = de.decide(book(yes_ask=.47, no_ask=.50, yes_ask_size=100,
                           no_ask_size=100, complement_source=vc.ABSENT),
                      max_contracts=100, **kw)
        assert cand(r, de.PAIR_BUY)["blocker"] == "COMPLEMENT_ABSENT"

    def test_an_unknown_account_capability_blocks(self):
        """UNVERIFIED_VENUE returned PAIR_BUY with data_quality OK."""
        r = de.decide(book(yes_ask=.47, no_ask=.50, yes_ask_size=100,
                           no_ask_size=100), max_contracts=100,
                      venue="UNVERIFIED_VENUE", fees=VERIFIED_FEES)
        c = cand(r, de.PAIR_BUY)
        assert c["status"] == de.BLOCKED
        assert "HOLDS_BOTH_LEGS_INDEPENDENTLY_UNKNOWN" in c["blocker"]

    def test_omitted_fees_are_an_unknown_cost_not_a_free_one(self,
                                                             monkeypatch):
        kw = capable(monkeypatch)
        r = de.decide(book(yes_ask=.47, no_ask=.50, yes_ask_size=100,
                           no_ask_size=100), max_contracts=100,
                      venue=kw["venue"])          # fees deliberately omitted
        c = cand(r, de.PAIR_BUY)
        assert c["blocker"] == "FEE_SCHEDULE_NOT_ESTABLISHED"

    def test_the_hypothetical_schedule_is_labelled(self):
        f = Fees.free_for_demonstration()
        assert f.hypothetical is True and f.verified is False
        assert "NOT_A_LIVE_SCHEDULE" in f.source








class TestPairSellFromInventory:

    def test_bids_over_par_sell_the_pair(self):
        r = de.decide(book(yes_bid=.55, no_bid=.48,
                           yes_bid_size=200, no_bid_size=200),
                      fees=VERIFIED_FEES,
                      inventory=Inventory(yes_contracts=200, no_contracts=200))
        assert r["selected"] == de.PAIR_SELL
        assert cand(r, de.PAIR_SELL)["ev_net"] == pytest.approx(6.0)

    def test_selling_without_an_established_schedule_is_not_identified(self):
        """The buy path grew a fee gate after review; the sell path had
        none, so all-zero unverified fees priced execution at zero and
        returned IDENTIFIED. Bids over par would then have been SELECTED
        on a schedule nobody supplied."""
        r = de.decide(book(yes_bid=.55, no_bid=.48,
                           yes_bid_size=200, no_bid_size=200),
                      inventory=Inventory(yes_contracts=200, no_contracts=200))
        c = cand(r, de.PAIR_SELL)
        assert c["status"] == de.NOT_IDENTIFIED
        assert c["blocker"] == "FEE_SCHEDULE_NOT_ESTABLISHED"
        assert c["ev_net"] is None
        assert r["selected"] == de.HOLD

    def test_a_published_schedule_computes_but_does_not_select(self):
        """PUBLISHED is a documented cost, not an observed one."""
        pub = Fees.published_pmus("2026-09-21")
        r = de.decide(book(yes_bid=.55, no_bid=.48,
                           yes_bid_size=200, no_bid_size=200),
                      fees=pub,
                      inventory=Inventory(yes_contracts=200, no_contracts=200))
        c = cand(r, de.PAIR_SELL)
        assert c["blocker"] == "FEE_APPLICATION_NOT_VERIFIED"
        assert c["status"] == de.NOT_IDENTIFIED
        assert c["ev_net"] is None
        # The number IS reported -- a documented cost is worth showing.
        assert c["conditional_payoff"] is not None
        assert r["selected"] == de.HOLD

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

    def test_merge_is_unsupported_on_both_account_classes(self):
        for klass in ("institutional", "retail"):
            r = de.decide(book(yes_ask=.5, no_ask=.6), account_class=klass)
            c = cand(r, de.MERGE)
            assert c["status"] == de.UNSUPPORTED
            assert klass.upper() in c["blocker"]

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
    def test_an_unparsable_price_is_not_a_price_of_zero(self, bad,
                                                        monkeypatch):
        """A price of 0.0 would make every broken book look like free
        money, which is the single most dangerous parse bug available."""
        kw = capable(monkeypatch)
        r = de.decide(book(yes_ask=bad, no_ask=.50, yes_bid=.45, no_bid=.48,
                           yes_ask_size=999, no_ask_size=999),
                      max_contracts=999, **kw)
        assert r["selected"] == de.NO_TRADE
        assert cand(r, de.PAIR_BUY)["conditional_payoff"] is None, (
            "an unreadable price produced a cash figure")

    def test_a_price_with_no_depth_is_not_an_opportunity(self, monkeypatch):
        kw = capable(monkeypatch)
        r = de.decide(book(yes_ask=.10, no_ask=.10, yes_bid=.09, no_bid=.09,
                           yes_ask_size=0, no_ask_size=9999),
                      max_contracts=9999, **kw)
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

    def test_the_threshold_is_respected_on_a_scorable_action(self):
        """PAIR_SELL from inventory is still IDENTIFIED -- it unwinds a
        position at known prices with no second-leg entry risk -- so it
        is what exercises the threshold now."""
        inv = Inventory(yes_contracts=200, no_contracts=200)
        cheap = de.decide(book(yes_bid=.55, no_bid=.48, yes_bid_size=200,
                               no_bid_size=200), inventory=inv,
                          fees=VERIFIED_FEES)
        assert cheap["selected"] == de.PAIR_SELL
        strict = de.decide(book(yes_bid=.55, no_bid=.48, yes_bid_size=200,
                                no_bid_size=200), inventory=inv,
                           fees=VERIFIED_FEES, min_ev_to_act=100.0)
        assert strict["selected"] == de.HOLD
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
        """AST scan of CALLS and IMPORTS, not a substring scan.

        The substring version failed on `Fees.published_pmus`, a method
        that reads a dated fee table and touches no venue. That is the
        third time a self-check has flagged its own vocabulary: a
        forbidden-name list that matches identifiers rather than call
        sites measures spelling, not behaviour.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(de))
        called, imported = set(), set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
            elif isinstance(n, ast.Import):
                imported.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
                imported.update(a.name for a in n.names)

        for forbidden in ("submit_fok", "submit", "cancel", "_get_client",
                          "get_pool", "execute", "executemany", "authorize"):
            assert forbidden not in called, (
                "%s() is CALLED; the decision engine computes and returns, "
                "it does not execute" % forbidden)
        for forbidden in ("polymarket_us", "psycopg", "asyncpg", "httpx",
                          "requests", "aiohttp"):
            assert forbidden not in imported, (
                "%s is imported; the decision engine holds no adapter, "
                "credentials or pool" % forbidden)
