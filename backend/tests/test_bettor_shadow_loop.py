"""The shadow loop: every branch, and the boundaries it must not cross.

Built after review reproduced three input-validation holes in the
decision engine and pointed out that the loop must not wait for a
profitable opportunity before integrating the rest of the system.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_decision_engine as de
from sportsassets import bettor_shadow_loop as sl
from sportsassets import bettor_venue_contract as vc

ASSUME = "DECLARED: p_both_legs_fill assumed, to exercise execution"
VERIFIED = de.Fees(taker_per_contract=0.02, maker_per_contract=0.01,
                   verified=True, source="TEST_VERIFIED_SCHEDULE")


@pytest.fixture(autouse=True)
def _demo_venue():
    vc.CAPABILITIES[("demo-venue", "institutional")] = vc.VenueCapabilities(
        venue="demo-venue", account_class="institutional",
        holds_both_legs_independently=vc.SUPPORTED,
        complement_quote_source=vc.OBSERVED, native_merge=vc.UNSUPPORTED,
        maker_orders=vc.SUPPORTED, cancel_replace=vc.SUPPORTED,
        verified_fee_schedule=True)
    yield
    vc.CAPABILITIES.pop(("demo-venue", "institutional"), None)


def loop(cash=1000.0, **kw):
    kw.setdefault("fees", VERIFIED)
    kw.setdefault("venue", "demo-venue")
    return sl.ShadowLoop(opening_cash=cash, **kw)


def bk(mid="m", **kw):
    d = dict(yes_bid=.45, yes_ask=.47, no_bid=.48, no_ask=.50,
             yes_bid_size=50, yes_ask_size=50, no_bid_size=50, no_ask_size=50,
             age_s=1.0, venue_state="OPEN", complement_source=vc.OBSERVED)
    d.update(kw)
    return de.Book(mid, **d)


class TestIsolation:
    def test_the_adapter_cannot_submit(self):
        assert sl.ShadowAdapter().submits_orders() is False

    def test_no_order_path_exists_in_the_module(self):
        import inspect
        src = inspect.getsource(sl)
        for bad in ("submit_fok", "_get_client", "pmus.", "authorize(",
                    "requests.", "httpx"):
            assert bad not in src, bad

    def test_a_touch_is_not_a_fill(self):
        r = sl.ShadowAdapter().rest(price=.46, want=10, touched=True,
                                    evidence_class=sl.SYNTHETIC)
        assert r["outcome"] == sl.NO_FILL
        assert r["filled"] == 0.0
        assert "touch is not a fill" in r["why"]
        assert r["queue_ahead"] == vc.UNKNOWN

    def test_a_forced_fill_is_stamped_synthetic(self):
        r = sl.ShadowAdapter().force_fill(price=.46, want=10, reason="test")
        assert r["synthetic_fill"] is True
        assert r["evidence_class"] == sl.SYNTHETIC


class TestTheEngineIsNotWeakened:
    def test_without_a_declared_assumption_nothing_executes(self):
        lp = loop()
        r = lp.step(bk(), max_contracts=40)
        assert r["decision"] == de.NO_TRADE
        assert r["execution"] is None
        assert lp.ledger.cash == 1000.0

    def test_an_assumption_records_what_the_engine_actually_said(self):
        r = loop().step(bk(), max_contracts=40,
                        assume_unidentified_terms=ASSUME)
        assert r["assumed"]["engine_status"] == de.NOT_IDENTIFIED
        assert r["assumed"]["engine_blocker"] == "P_BOTH_LEGS_FILL_NOT_IDENTIFIED"
        assert r["evidence_class"] == sl.SYNTHETIC
        assert "did NOT select this" in r["assumed"]["warning"]

    def test_an_assumption_cannot_override_bad_data(self):
        r = loop().step(bk(age_s=-20.0), max_contracts=40,
                        assume_unidentified_terms=ASSUME)
        assert r["data_quality"] == "REJECTED"
        assert r["execution"] is None

    def test_an_assumption_cannot_override_a_blocked_action(self):
        r = loop().step(bk(complement_source=vc.DERIVED), max_contracts=40,
                        assume_unidentified_terms=ASSUME)
        assert r["decision"] == de.NO_TRADE
        assert r["execution"] is None


class TestTheChain:
    def test_entry_partial_fill_and_inventory(self):
        lp = loop()
        r = lp.step(bk("m1", yes_ask_size=30), max_contracts=40,
                    assume_unidentified_terms=ASSUME)
        assert r["decision"] == de.PAIR_BUY
        assert r["size_requested"] == 30
        assert r["inventory_after"] == {"yes": 30.0, "no": 30.0}
        assert lp.ledger.cash < 1000.0

    def test_a_one_leg_fill_is_named_not_averaged(self):
        lp = loop()
        r = lp.step(bk("m2"), max_contracts=20,
                    assume_unidentified_terms=ASSUME, reject_legs=("no",))
        assert r["execution"]["one_leg_only"] is True
        assert r["execution"]["unwound_exposure_required"] is True
        # RECORDING IS NOT HANDLING. The loop now acts on the exposure,
        # so the assertion is that a recovery DECISION was taken and
        # named -- not that the naked leg was left sitting there.
        # No fresh recovery observation was supplied, so the policy
        # refuses to decide on a book it knows is stale.
        assert r["execution"]["recovery"]["action"] == "UNRESOLVED_EXPOSURE"
        assert (r["execution"]["recovery"]["blocker"]
                == "NO_FRESH_RECOVERY_OBSERVATION")

    def test_risk_refuses_before_execution_and_cash_is_untouched(self):
        lp = loop(limits=sl.RiskLimits(max_cash_per_market=2.0))
        r = lp.step(bk(), max_contracts=40, assume_unidentified_terms=ASSUME)
        assert r["risk"] == "RISK_MAX_CASH_PER_MARKET"
        assert r["execution"] is None
        assert r["cash_before"] == r["cash_after"] == 1000.0

    def test_a_limit_never_blocks_an_exit(self):
        """An entry limit that blocks a sell freezes the position at
        exactly the moment the limit says it is too large."""
        lp = loop(limits=sl.RiskLimits(max_cash_per_market=2.0))
        lp.positions["m1"] = sl.Position("m1", yes=30, no=30,
                                         yes_basis=14.1, no_basis=15.0)
        r = lp.step(bk("m1", yes_bid=.60, no_bid=.55, yes_bid_size=40,
                       no_bid_size=40), max_contracts=0)
        assert r["decision"] == de.PAIR_SELL
        assert r["risk"] == "REDUCTION_CHECKS_PASSED_ENTRY_LIMITS_EXEMPT"
        assert r["inventory_after"] == {"yes": 0.0, "no": 0.0}

    def test_settlement_of_a_pair_and_of_a_naked_leg(self):
        lp = loop()
        lp.positions["p"] = sl.Position("p", yes=10, no=10,
                                        yes_basis=4.7, no_basis=5.0)
        lp.positions["n"] = sl.Position("n", yes=10, no=0, yes_basis=4.7)
        assert lp.settle("p", yes_wins=True)["payout"] == pytest.approx(10.0)
        assert lp.settle("n", yes_wins=False)["payout"] == pytest.approx(0.0)

    def test_settling_twice_is_refused(self):
        lp = loop()
        lp.positions["p"] = sl.Position("p", yes=1, no=1,
                                        yes_basis=0.5, no_basis=0.5)
        lp.settle("p", yes_wins=True)
        assert lp.settle("p", yes_wins=True)["skipped"] == "ALREADY_SETTLED"


class TestAccounting:
    def test_the_ledger_reconciles_after_a_full_run(self):
        lp = loop()
        lp.step(bk("m1", yes_ask_size=30), max_contracts=40,
                assume_unidentified_terms=ASSUME)
        lp.step(bk("m2"), max_contracts=20,
                assume_unidentified_terms=ASSUME, reject_legs=("no",))
        lp.settle("m1", yes_wins=True)
        lp.settle("m2", yes_wins=False)
        rc = lp.ledger.reconciles()
        assert rc["reconciled"] is True
        assert rc["residual_must_be_0"] == 0

    def test_unsettled_positions_are_not_counted_as_cash(self):
        lp = loop()
        lp.step(bk("m1"), max_contracts=10, assume_unidentified_terms=ASSUME)
        assert lp.ledger.cash < 1000.0, "cash went out"
        assert lp.report()["unsettled_cost_basis"] > 0, "basis is carried"
        assert "profitability" in lp.report()["profitability_claim"].lower()

    def test_restart_recovers_cash_and_inventory_together(self):
        lp = loop()
        lp.step(bk("m1"), max_contracts=10, assume_unidentified_terms=ASSUME)
        back = sl.ShadowLoop.restore(lp.snapshot(), fees=VERIFIED,
                                     venue="demo-venue")
        assert back.ledger.cash == lp.ledger.cash
        assert back.positions["m1"].yes == lp.positions["m1"].yes
        assert back.ledger.reconciles()["reconciled"] is True

    def test_every_record_carries_an_evidence_class(self):
        lp = loop()
        lp.step(bk("m1"), max_contracts=10, assume_unidentified_terms=ASSUME)
        q = lp.quote(bk("m2"), side="yes", price=.46, size=5)
        lp.touch(q["quote_id"])
        lp.settle("m1", yes_wins=True)
        assert all("evidence_class" in t for t in lp.trace)
        assert sl.PROSPECTIVE not in lp.report()["by_evidence_class"], (
            "a synthetic run must not produce prospective evidence")


class TestAccountingDefectsFoundByReview:
    """Independent expected-cash and expected-inventory assertions.

    A ledger that reconciles to itself proves its arithmetic is
    self-consistent. It was self-consistent while fees were dropped
    entirely, so self-consistency is necessary and not sufficient.
    """

    def test_fees_are_applied_to_every_simulated_fill(self):
        lp = sl.ShadowLoop(opening_cash=100.0, venue="demo-venue",
                           fees=de.Fees(taker_per_contract=0.02,
                                        verified=True, source="TEST"))
        lp.step(bk("m"), max_contracts=10, assume_unidentified_terms=ASSUME)
        # 10 x 0.47 + 10 x 0.50 = 9.70, fees 0.20 + 0.20 = 0.40
        assert lp.ledger.cash == pytest.approx(89.90)
        assert lp.ledger.fees_paid == pytest.approx(0.40)

    def test_a_sale_never_creates_negative_deployed_capital(self):
        """Profit recorded as unspent capital EXPANDS the risk limits:
        a winning trade buys itself more room to trade."""
        lp = loop()
        p = lp.positions.setdefault("m", sl.Position("m"))
        p.buy("yes", 10, 0.47, 0.0)
        p.buy("no", 10, 0.50, 0.0)
        assert lp.deployed == pytest.approx(9.70)
        p.sell("yes", 10, 0.55, 0.0)
        p.sell("no", 10, 0.48, 0.0)
        assert p.flat
        assert lp.deployed == 0.0, "deployed went negative on a profit"
        assert p.realized_pnl == pytest.approx(0.60)

    def test_a_closed_position_has_exactly_zero_basis(self):
        p = sl.Position("m")
        p.buy("yes", 3, 0.3333333, 0.01)
        p.sell("yes", 3, 0.4, 0.01)
        assert p.yes == 0.0 and p.yes_basis == 0.0

    def test_partial_sale_removes_basis_proportionally(self):
        p = sl.Position("m")
        p.buy("yes", 10, 0.47, 0.20)      # basis 4.90
        p.sell("yes", 4, 0.60, 0.04)
        assert p.yes == pytest.approx(6.0)
        assert p.yes_basis == pytest.approx(2.94)   # 6/10 of 4.90

    def test_realized_profit_never_reduces_basis(self):
        p = sl.Position("m")
        p.buy("yes", 10, 0.40, 0.0)
        p.sell("yes", 5, 0.90, 0.0)
        assert p.yes_basis == pytest.approx(2.0)
        assert p.realized_pnl == pytest.approx(2.5)


class TestReductionChecksStillApply:

    def test_a_reduction_larger_than_the_holding_is_refused(self):
        lp = loop()
        pos = sl.Position("m", yes=5, no=5, yes_basis=2.0, no_basis=2.0)
        assert lp.limits.check_reduction(
            pos=pos, action="PAIR_SELL", qty=99,
            book=None) == "RISK_REDUCTION_EXCEEDS_PAIRED_HOLDING"

    def test_selling_one_side_of_a_pair_is_refused_as_directional(self):
        lp = loop()
        pos = sl.Position("m", yes=10, no=10, yes_basis=4.7, no_basis=5.0)
        assert lp.limits.check_reduction(
            pos=pos, action="SELL_YES", qty=4,
            book=None) == "RISK_REDUCTION_CREATES_DIRECTIONAL_EXPOSURE"

    def test_an_invalid_reduction_quantity_is_refused(self):
        lp = loop()
        pos = sl.Position("m", yes=10, no=10)
        for bad in (0, -1, float("inf")):
            assert lp.limits.check_reduction(
                pos=pos, action="PAIR_SELL", qty=bad, book=None) is not None


class TestOneLegRecoveryIsAutonomous:

    def test_it_completes_when_the_complement_is_executable(self):
        lp = loop()
        r = lp.step(bk("m"), max_contracts=10,
                    assume_unidentified_terms=ASSUME, reject_legs=("no",),
                    recovery_book=bk("m"))
        rec = r["execution"]["recovery"]
        assert rec["action"] == "COMPLETE"
        assert lp.positions["m"].directional == 0
        # The sunk basis is REPORTED and excluded from the comparison.
        assert rec["considered"]["sunk_basis_excluded"] > 0
        assert rec["considered"]["complete_net_incremental"] is not None

    def test_sunk_basis_does_not_change_the_recovery_choice(self):
        """An expensive entry used to make the engine LESS willing to
        complete, which is backwards: the basis is identical under
        every action available now."""
        picks = []
        for entry_ask in (0.10, 0.90):
            lp = loop()
            b = bk("m", yes_ask=entry_ask, no_ask=0.50)
            r = lp.step(b, max_contracts=10,
                        assume_unidentified_terms=ASSUME,
                        reject_legs=("no",), recovery_book=bk("m"))
            picks.append(r["execution"]["recovery"]["action"])
        assert picks[0] == picks[1], picks

    def test_completion_is_gated_on_the_account_capability(self):
        """Where the venue NETS the complement, buying it CLOSES rather
        than completes and the par model does not apply.

        Driven directly, because on a venue whose capability is UNKNOWN
        the ENTRY is blocked too -- so there is no way to reach recovery
        through step() on that venue, which is itself correct.
        """
        lp = loop(venue="polymarket-us")          # capability UNKNOWN
        pos = sl.Position("m", yes=10, yes_basis=4.9)
        rec = lp._recover_one_leg(
            bk("m"), pos, {"leg": "yes", "filled": 10.0},
            sl.SYNTHETIC, fresh=True)
        assert rec["action"] != "COMPLETE"
        assert rec["considered"]["pair_capability"] == vc.UNKNOWN

    def test_a_partial_exit_keeps_the_remainder_exposed(self):
        lp = loop()
        thin = bk("m", no_ask=None, no_ask_size=0, yes_bid=.46,
                  yes_bid_size=3)
        r = lp.step(bk("m"), max_contracts=10,
                    assume_unidentified_terms=ASSUME, reject_legs=("no",),
                    recovery_book=thin)
        rec = r["execution"]["recovery"]
        assert rec["action"] == "EXIT"
        assert rec["resolved"] is False
        assert rec["remaining_exposed"] == pytest.approx(7.0)
        assert rec["next"]["action"] == "UNRESOLVED_EXPOSURE"

    def test_it_exits_when_the_complement_is_gone(self):
        lp = loop()
        gone = bk("m", no_ask=None, no_ask_size=0, yes_bid=.46,
                  yes_bid_size=50)
        r = lp.step(bk("m"), max_contracts=10,
                    assume_unidentified_terms=ASSUME, reject_legs=("no",),
                    recovery_book=gone)
        assert r["execution"]["recovery"]["action"] == "EXIT"
        assert lp.positions["m"].flat

    def test_it_names_the_exposure_when_there_is_no_exit(self):
        lp = loop()
        stuck = bk("m", no_ask=None, no_ask_size=0, yes_bid=None,
                   yes_bid_size=0)
        r = lp.step(bk("m"), max_contracts=10,
                    assume_unidentified_terms=ASSUME, reject_legs=("no",),
                    recovery_book=stuck)
        rec = r["execution"]["recovery"]
        assert rec["action"] == "UNRESOLVED_EXPOSURE"
        assert rec["directional_contracts"] == 10

    def test_a_stale_recovery_observation_is_refused(self):
        lp = loop()
        stale = bk("m", age_s=600.0)
        r = lp.step(bk("m"), max_contracts=10,
                    assume_unidentified_terms=ASSUME, reject_legs=("no",),
                    recovery_book=stale)
        rec = r["execution"]["recovery"]
        assert rec["action"] == "UNRESOLVED_EXPOSURE"
        assert rec["blocker"] == "RECOVERY_OBSERVATION_UNUSABLE"


class TestQuoteLifecycle:

    def test_a_quote_rests_holds_exposure_and_releases_it(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        assert q["state"] == "RESTING"
        # 10 x 0.45 notional plus the maker fee the fill would incur
        assert lp.quoted_exposure == pytest.approx(4.5 + 0.10)
        lp.cancel_quote(q["quote_id"])
        assert lp.quoted_exposure == 0.0

    def test_a_touch_does_not_change_inventory_or_cash(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        before = lp.ledger.cash
        t = lp.touch(q["quote_id"])
        assert t["outcome"] == sl.NO_FILL
        assert lp.ledger.cash == before
        assert lp.positions["m"].flat

    def test_a_reprice_creates_a_new_quote_at_the_back(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        rp = lp.reprice_quote(q["quote_id"], new_price=0.455)
        assert rp["new_quote_id"] != q["quote_id"]
        assert lp.quotes[q["quote_id"]]["state"] == sl.REPRICED
        assert "BACK" in rp["why"] or "back" in rp["why"]

    def test_a_maker_fill_reaches_inventory_and_cash(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        before = lp.ledger.cash
        f = lp.fill_quote(q["quote_id"], qty=6, reason="test")
        assert lp.positions["m"].yes == 6
        assert lp.ledger.cash == pytest.approx(
            before - (6 * 0.45 + VERIFIED.fill_fee(6, maker=True, price=0.45)))
        assert f["synthetic_fill"] is True

    def test_a_simulated_fill_is_synthetic_even_on_a_real_book(self):
        """A fresh observation does not make its simulated fill
        observed."""
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10,
                     evidence_class=sl.PROSPECTIVE)
        f = lp.fill_quote(q["quote_id"], qty=5, reason="test")
        assert f["evidence_class"] == sl.SYNTHETIC
        assert f["book_evidence_class"] == sl.PROSPECTIVE

    def test_a_resting_quote_counts_against_limits(self):
        lp = loop(limits=sl.RiskLimits(max_total_deployed=3.0))
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        assert q["state"] == "REFUSED"
        assert q["risk"] is not None

    def test_quotes_survive_a_restart(self):
        lp = loop()
        lp.quote(bk("m"), side="yes", price=0.44, size=8)
        back = sl.ShadowLoop.restore(lp.snapshot(), fees=VERIFIED,
                                     venue="demo-venue")
        assert back.quoted_exposure == pytest.approx(lp.quoted_exposure)


class TestReservationsSurvivePartialFills:
    """Reproduced by review: filling 6 of 10 dropped the reservation on
    the remaining 4 to zero while that remainder was still working."""

    def test_a_partial_fill_keeps_the_remainder_reserved(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        assert lp.quoted_exposure == pytest.approx(4.5 + 0.1)
        lp.fill_quote(q["quote_id"], qty=6, reason="t")
        assert lp.quotes[q["quote_id"]]["state"] == "PARTIAL"
        assert lp.quoted_exposure == pytest.approx(1.8 + 0.04)

    def test_successive_partial_fills_complete_the_quote(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        for qty in (3, 3, 4):
            r = lp.fill_quote(q["quote_id"], qty=qty, reason="t")
            assert "refused" not in r
        assert lp.quotes[q["quote_id"]]["state"] == "FILLED"
        assert lp.positions["m"].yes == pytest.approx(10.0)
        assert lp.quoted_exposure == 0.0

    def test_a_partial_quote_can_still_be_cancelled(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        lp.fill_quote(q["quote_id"], qty=6, reason="t")
        c = lp.cancel_quote(q["quote_id"])
        assert c["state"] == sl.CANCELLED
        assert lp.quoted_exposure == 0.0

    def test_a_partial_quote_can_still_be_repriced(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        lp.fill_quote(q["quote_id"], qty=6, reason="t")
        rp = lp.reprice_quote(q["quote_id"], new_price=0.44)
        assert rp["size"] == pytest.approx(4.0)

    def test_filling_past_the_remainder_is_capped(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        lp.fill_quote(q["quote_id"], qty=6, reason="t")
        lp.fill_quote(q["quote_id"], qty=99, reason="t")
        assert lp.positions["m"].yes == pytest.approx(10.0)


class TestReplacementIsANewOrder:

    def test_a_reprice_beyond_cash_is_refused(self):
        """Reproduced: 10 shares 0.45 -> 0.99 with $5 cash was accepted
        and reserved 9.90."""
        lp = loop(cash=5.0)
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        rp = lp.reprice_quote(q["quote_id"], new_price=0.99)
        assert rp.get("refused") is not None
        assert lp.quoted_exposure == pytest.approx(4.6)
        assert lp.quotes[q["quote_id"]]["state"] == "RESTING"

    def test_an_invalid_replacement_price_is_refused(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        for bad in (0.0, 1.0, -0.5, float("inf")):
            assert lp.reprice_quote(q["quote_id"],
                                    new_price=bad).get("refused")

    def test_concurrent_quotes_compete_for_the_same_cash(self):
        lp = loop(cash=6.0)
        a = lp.quote(bk("m1"), side="yes", price=0.45, size=10)
        b = lp.quote(bk("m2"), side="yes", price=0.45, size=10)
        assert a["state"] == "RESTING"
        assert b["state"] == "REFUSED", "the second quote double-spent cash"

    def test_reservations_survive_a_restart(self):
        lp = loop()
        q = lp.quote(bk("m"), side="yes", price=0.45, size=10)
        lp.fill_quote(q["quote_id"], qty=6, reason="t")
        back = sl.ShadowLoop.restore(lp.snapshot(), fees=VERIFIED,
                                     venue="demo-venue")
        assert back.quoted_exposure == pytest.approx(lp.quoted_exposure)
        assert back.quotes[q["quote_id"]]["state"] == "PARTIAL"
