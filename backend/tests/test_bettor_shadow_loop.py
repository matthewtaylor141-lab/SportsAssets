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
VERIFIED = de.Fees(verified=True, source="TEST_VERIFIED_SCHEDULE")


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


def loop(**kw):
    kw.setdefault("fees", VERIFIED)
    kw.setdefault("venue", "demo-venue")
    return sl.ShadowLoop(opening_cash=1000.0, **kw)


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
        assert r["inventory_after"] == {"yes": 20.0, "no": 0.0}

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
        lp.positions["m1"] = sl.Position("m1", yes=30, no=30, cash_spent=29.1)
        r = lp.step(bk("m1", yes_bid=.60, no_bid=.55, yes_bid_size=40,
                       no_bid_size=40), max_contracts=0)
        assert r["decision"] == de.PAIR_SELL
        assert r["risk"] == "NOT_APPLIED_ACTION_REDUCES_EXPOSURE"
        assert r["inventory_after"] == {"yes": 0.0, "no": 0.0}

    def test_settlement_of_a_pair_and_of_a_naked_leg(self):
        lp = loop()
        lp.positions["p"] = sl.Position("p", yes=10, no=10, cash_spent=9.7)
        lp.positions["n"] = sl.Position("n", yes=10, no=0, cash_spent=4.7)
        assert lp.settle("p", yes_wins=True)["payout"] == pytest.approx(10.0)
        assert lp.settle("n", yes_wins=False)["payout"] == pytest.approx(0.0)

    def test_settling_twice_is_refused(self):
        lp = loop()
        lp.positions["p"] = sl.Position("p", yes=1, no=1, cash_spent=1.0)
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
        lp.quote(bk("m2"), side="yes", price=.46, size=5, touched=True)
        lp.settle("m1", yes_wins=True)
        assert all("evidence_class" in t for t in lp.trace)
        assert sl.PROSPECTIVE not in lp.report()["by_evidence_class"], (
            "a synthetic run must not produce prospective evidence")
