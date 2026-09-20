"""§11. The hedge tax, and the three routes it has to beat."""

from sportsassets import bettor_hedge_tax as ht



# ── §11: the five quantities of a complement acquisition ─────────────

def _acq(**over):
    kw = dict(own_leg="YES", own_leg_basis="0.48", complement_price="0.53",
              qty="10")
    kw.update(over)
    return ht.complement_acquisition(**kw)


def test_all_five_directive_quantities_are_present():
    a = _acq()
    for field in ("COMPLEMENT_COST_PER_CONTRACT", "PAIR_BASIS",
                  "PAIR_VALUE_AT_SETTLEMENT", "HEDGE_TAX_VS_PAR",
                  "CAPITAL_RELEASE_IF_COMPLETED"):
        assert field in a, field


def test_the_complement_is_the_other_leg():
    assert _acq(own_leg="YES")["COMPLEMENT_LEG"] == "NO"
    assert _acq(own_leg="NO")["COMPLEMENT_LEG"] == "YES"


def test_the_gross_cost_is_scaled_and_the_net_cost_is_not_identified():
    a = _acq(qty="10", complement_price="0.53")
    assert a["COMPLEMENT_COST_PER_CONTRACT"] == "0.53"
    assert a["COMPLEMENT_COST_GROSS"] == "5.30"
    assert a["COMPLEMENT_COST_NET"] == ht.NOT_IDENTIFIED
    assert a["FEE"] == ht.NOT_IDENTIFIED
    assert "not the gross cost" in a["whyCostNetNotIdentified"]


def test_the_hedge_tax_is_scaled_as_well_as_per_contract():
    a = _acq(qty="10")
    assert a["HEDGE_TAX_VS_PAR"] == "0.01"
    assert a["HEDGE_TAX_VS_PAR_TOTAL"] == "0.10"


def test_pair_value_at_settlement_is_structural_and_before_it_is_not():
    a = _acq()
    assert a["PAIR_VALUE_AT_SETTLEMENT"] == "1.00"
    assert a["PAIR_VALUE_BEFORE_SETTLEMENT"] == ht.NOT_IDENTIFIED
    assert "structural rather than estimated" in a["pairValueBasis"]


def test_capital_release_is_not_identified_and_is_not_zero():
    a = _acq()
    assert a["CAPITAL_RELEASE_IF_COMPLETED"] == ht.NOT_IDENTIFIED
    assert a["CAPITAL_RELEASE_IF_COMPLETED"] != "0"
    assert a["MERGE_MECHANISM"] == ht.NOT_IDENTIFIED
    assert "It is not zero" in a["whyCapitalReleaseNotIdentified"]


# ── §11: direct exit and hedge are different trades ──────────────────

def test_exit_sells_our_leg_and_hedge_buys_the_other():
    e = ht.EXIT_VS_HEDGE
    assert e["DIRECT_EXIT"]["book"] == "the BID on our own leg"
    assert e["HEDGE"]["book"] == "the ASK on the other leg"
    assert "returns capital" in e["whyItMatters"]


def test_hedge_leaves_the_capital_tied_up_and_exit_does_not():
    e = ht.EXIT_VS_HEDGE
    assert "nothing" in e["DIRECT_EXIT"]["leaves"]
    assert "still in it" in e["HEDGE"]["leaves"]


# ── §11: the rule is not always hedge ────────────────────────────────

def test_the_four_routes_are_the_directives_four():
    assert ht.ROUTES == ("EV_HOLD", "EV_DIRECT_EXIT", "EV_HEDGE",
                         "EV_COMPLETE_PAIR")


def test_no_route_identified_means_no_hedge():
    c = ht.compare_routes()
    assert c["DOMINANCE_STATUS"] == ht.NOT_IDENTIFIED
    assert c["HEDGE_PERMITTED"] is False
    assert len(c["routesMissing"]) == 4


def test_a_missing_alternative_is_not_treated_as_zero():
    """Otherwise HEDGE wins by default against routes nobody measured."""
    c = ht.compare_routes(ev_hedge="0.010")
    assert c["HEDGE_PERMITTED"] is False
    assert "EV_HOLD" in c["routesMissing"]
    assert "win by default" in c["whyNotZero"]


def test_hedge_must_beat_all_three_alternatives():
    c = ht.compare_routes(ev_hold="0.001", ev_direct_exit="-0.002",
                          ev_hedge="0.004", ev_complete_pair="0.003")
    assert c["HEDGE_DOMINATES"] is True
    assert c["BEST_ROUTE"] == "EV_HEDGE"


def test_beating_two_of_three_is_not_dominance():
    c = ht.compare_routes(ev_hold="0.001", ev_direct_exit="-0.002",
                          ev_hedge="0.004", ev_complete_pair="0.009")
    assert c["HEDGE_DOMINATES"] is False
    assert c["HEDGE_PERMITTED"] is False
    assert c["BEST_ROUTE"] == "EV_COMPLETE_PAIR"


def test_a_tie_is_not_dominance():
    c = ht.compare_routes(ev_hold="0.004", ev_direct_exit="0.001",
                          ev_hedge="0.004", ev_complete_pair="0.002")
    assert c["HEDGE_DOMINATES"] is False


def test_dominance_is_on_the_conservative_read_not_the_mean():
    assert "LOWER_CONFIDENCE_VALUE" in ht.CONSERVATIVE_MEANS
    assert "not beating them on the mean" in ht.THE_RULE_IS_NOT_ALWAYS_HEDGE
    assert ht.compare_routes()["conservative"] is True
