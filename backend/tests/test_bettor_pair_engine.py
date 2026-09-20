"""The pair engine: the margin it will report, and the EV it will not."""

from decimal import Decimal

import pytest

from sportsassets import bettor_inventory as binv
from sportsassets import bettor_pair_engine as pe

CONFIRMED = "EXACT_ONE_TO_COMPLEMENT_BASKET"


def _holding_yes(qty="100", px="0.48"):
    return binv.inventory([{"leg": "YES", "qty": qty, "price": px}],
                          identity_status=CONFIRMED)


def _book(ask="0.49", depth="200"):
    return {"ask": ask, "availableDepth": depth}


# ── the structural margin, which IS computable ───────────────────────

def test_the_gross_margin_is_computed_from_the_book():
    v = pe.pair_view(_holding_yes(), _book())
    assert v["heldLeg"] == "YES"
    assert v["complementLeg"] == "NO"
    assert Decimal(v["EXPECTED_PAIR_BASIS"]) == Decimal("0.97")
    assert Decimal(v["EXPECTED_PAIR_MARGIN_GROSS"]) == Decimal("0.03")


def test_the_margin_is_labelled_gross_and_structural():
    """PAIR_BASIS_ABOVE_PAR_IS GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES."""
    v = pe.pair_view(_holding_yes(), _book())
    assert v["marginIs"] == "GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES"
    assert v["marginIsNot"] == "ESTABLISHED_FINAL_NET_OUTCOME"


# ── the EV it refuses to return ──────────────────────────────────────

def test_pair_ev_is_never_one_minus_the_two_prices():
    """§5, and the Ferrari result behind it."""
    v = pe.pair_view(_holding_yes(), _book())
    assert "PAIR_EV" not in v
    assert "1 - YES_PRICE - NO_PRICE" in v["pairEvIsNot"]
    assert v["status"] == "STRUCTURE_IDENTIFIED_COMPLETION_NOT_IDENTIFIED"


@pytest.mark.parametrize("field", [
    "P_PAIR_COMPLETION",
    "EXPECTED_TIME_TO_COMPLETION",
    "EXPECTED_RESIDUAL_QTY",
    "EXPECTED_RESIDUAL_VALUE",
    "EXPECTED_RESIDUAL_LOSS",
    "RESIDUAL_UNCERTAINTY",
    "CAPITAL_HOURS_TO_COMPLETION",
    "INCENTIVES",
    "REBATES",
])
def test_the_unidentified_terms_are_named_not_defaulted(field):
    v = pe.pair_view(_holding_yes(), _book())
    assert v[field] == pe.NOT_IDENTIFIED


def test_the_residual_is_never_estimated_by_assumption():
    """It is the term that changed Ferrari's sign."""
    v = pe.pair_view(_holding_yes(), _book())
    assert "changed Ferrari's sign" in v["whyResidualNotIdentified"]


def test_capital_at_risk_on_the_held_leg_is_identified():
    v = pe.pair_view(_holding_yes("100", "0.48"), _book())
    assert Decimal(v["CAPITAL_AT_RISK_ON_HELD_LEG"]) == Decimal("48.00")


def test_a_missing_complement_book_yields_no_basis():
    v = pe.pair_view(_holding_yes(), None)
    assert v["EXPECTED_COMPLEMENT_PRICE"] == pe.NOT_IDENTIFIED
    assert v["EXPECTED_PAIR_BASIS"] == pe.NOT_IDENTIFIED
    assert v["EXPECTED_PAIR_MARGIN_GROSS"] == pe.NOT_IDENTIFIED


def test_a_pair_view_needs_exactly_one_leg_held():
    flat = binv.inventory([], identity_status=CONFIRMED)
    assert pe.pair_view(flat, _book())["status"] == pe.NOT_IDENTIFIED
    both = binv.inventory(
        [{"leg": "YES", "qty": "100", "price": "0.48"},
         {"leg": "NO", "qty": "100", "price": "0.49"}],
        identity_status=CONFIRMED)
    assert pe.pair_view(both, _book())["status"] == pe.NOT_IDENTIFIED


# ── the ledger buckets stay apart ────────────────────────────────────

def test_the_pnl_buckets_are_declared_separately():
    for b in ("PAIR_PNL", "DIRECTIONAL_PNL", "RESIDUAL_INVENTORY_PNL",
              "EXIT_HEDGE_PNL", "REBATES", "INCENTIVES", "FEES",
              "SLIPPAGE"):
        assert b in pe.PNL_BUCKETS
    assert "3.84M" in pe.NEVER_BLENDED and "4.44M" in pe.NEVER_BLENDED


# ── §6: the whale hazard is a prior, not a trigger ───────────────────

def test_the_hazard_loads_from_the_frozen_priors():
    h = pe.completion_hazard(3.0)
    assert h["status"] == "IDENTIFIED"
    assert h["interval"] == "0s-5s"
    assert h["account"] == "rn1"
    assert 0 < h["WHALE_COMPLETION_HAZARD"] < 1


def test_the_hazard_carries_its_own_restrictions():
    """The number must not travel without the sentence limiting it."""
    h = pe.completion_hazard(3.0)
    assert h["IS_NOT"] == "BETTOR_P_FILL"
    assert h["MAY_SEED_BETTOR_P_FILL"] is False
    assert h["SELECTION_CONDITION"] == "OBSERVED_WHALE_ENTERED_POSITIONS_ONLY"
    assert h["CAUSE_SPECIFIC_HAZARD"] == "NOT_COMPUTED"


def test_the_confidence_interval_is_declared_a_lower_bound():
    """INDEPENDENT_EFFECTIVE_N is NOT_IDENTIFIED, so CI95 is too narrow."""
    h = pe.completion_hazard(3.0)
    assert len(h["HAZARD_LAMBDA_CI95"]) == 2
    assert "true interval is WIDER" in h["intervalWidthIsALowerBound"]


def test_the_hazard_never_becomes_p_pair_completion():
    v = pe.pair_view(_holding_yes(), _book(), seconds_unpaired=3.0)
    assert v["P_PAIR_COMPLETION"] == pe.NOT_IDENTIFIED
    assert v["WHALE_COMPLETION_HAZARD_PRIOR"]["status"] == "IDENTIFIED"
    assert "different fields on purpose" in v["priorIsNotPCompletion"]


def test_completion_is_front_loaded_and_decays(ic=None):
    """The mechanism lesson the prior legitimately supports."""
    fast = pe.completion_hazard(3.0)["WHALE_COMPLETION_HAZARD"]
    slow = pe.completion_hazard(2000.0)["WHALE_COMPLETION_HAZARD"]
    assert fast > slow * 50, "hazard should decay hard with time unpaired"


def test_swisstony_is_sensitivity_only():
    """Held out by an exclusion frozen before the result was known."""
    h = pe.completion_hazard(3.0, account="swisstony")
    assert h["status"] == "SENSITIVITY_ONLY"
    assert h["WHALE_COMPLETION_HAZARD"] == pe.NOT_IDENTIFIED
    assert "frozen before the result" in h["why"]


def test_the_forbidden_uses_are_declared():
    r = pe.WHALE_RESTRICTIONS
    assert r["WHALE_COMPLETION_AS_P_FILL"] == "FORBIDDEN"
    assert r["WHALE_ORDER_POLICY"] == pe.NOT_IDENTIFIED
    assert r["WHALE_EVIDENCE_ALONE_CAN_CREATE_A_TRADE"] is False
    assert "never becomes" in pe.WHALE_IS_A_TEACHER_NOT_A_TRIGGER


def test_missing_priors_fail_closed(monkeypatch):
    monkeypatch.setattr(pe, "priors", lambda root=None: None)
    h = pe.completion_hazard(3.0)
    assert h["status"] == pe.PRIORS_UNAVAILABLE
    assert h["WHALE_COMPLETION_HAZARD"] == pe.NOT_IDENTIFIED


# ── §7: the fields the directive names, each present by that name ────

def test_every_directive_field_is_on_the_view():
    """§7's list, checked by the directive's own names."""
    v = pe.pair_view(_holding_yes(), _book(), seconds_unpaired=120)
    for field in ("COMPLEMENT_IDENTITY", "CURRENT_COMPLEMENT_PRICE",
                  "P_PAIR_COMPLETION", "PAIR_COMPLETION_STATUS",
                  "WHALE_COMPLETION_HAZARD_PRIOR",
                  "EXPECTED_TIME_TO_COMPLETION", "EXPECTED_PAIR_BASIS",
                  "EXPECTED_PAIR_MARGIN_GROSS", "EXPECTED_RESIDUAL_QTY",
                  "EXPECTED_RESIDUAL_LOSS", "CAPITAL_HOURS_TO_COMPLETION",
                  "EXPECTED_CAPITAL_RELEASE"):
        assert field in v, field


def test_the_complement_identity_is_venue_native_or_not_identified():
    v = pe.pair_view(_holding_yes(), _book())
    assert v["COMPLEMENT_IDENTITY"] == pe.NOT_IDENTIFIED
    assert v["COMPLEMENT_IDENTITY_STATUS"] == pe.NOT_IDENTIFIED
    assert "no price matching" in v["complementIdentityBasis"]

    named = pe.pair_view(_holding_yes(),
                         dict(_book(), complementId="nfl-abc-NO",
                              identityStatus="VENUE_NATIVE_ID_MATCH"))
    assert named["COMPLEMENT_IDENTITY"] == "nfl-abc-NO"


def test_completion_probability_and_status_are_two_fields():
    """The probability is the number; the status is why there is none."""
    v = pe.pair_view(_holding_yes(), _book())
    assert v["P_PAIR_COMPLETION"] == pe.NOT_IDENTIFIED
    assert v["PAIR_COMPLETION_STATUS"] == \
        "NOT_IDENTIFIED_NO_BETTOR_NATIVE_COMPLETION_EVIDENCE"


def test_capital_release_is_not_identified_and_is_not_zero():
    """The RN1/Ferrari chain breaks at the merge step, and the field
    says so rather than reporting a zero release."""
    v = pe.pair_view(_holding_yes(), _book())
    assert v["EXPECTED_CAPITAL_RELEASE"] == pe.NOT_IDENTIFIED
    assert v["EXPECTED_CAPITAL_RELEASE"] != "0"
    assert "MERGE_MECHANISM" in v["capitalReleaseRequires"]
    assert "It is not zero" in v["whyCapitalReleaseNotIdentified"]
    assert "CAPITAL RETURNS TO ALLOCATOR" in \
        v["whyCapitalReleaseNotIdentified"]


# ── the rename, and the alias that keeps callers working ─────────────

def test_the_current_complement_price_is_not_called_expected():
    """The old key claimed a forecast while the value is the ask now.
    Both keys are served; only one of them is the right name."""
    v = pe.pair_view(_holding_yes(), _book(ask="0.49"))
    assert v["CURRENT_COMPLEMENT_PRICE"] == "0.49"
    assert v["EXPECTED_COMPLEMENT_PRICE"] == v["CURRENT_COMPLEMENT_PRICE"]
    assert v["CURRENT_COMPLEMENT_DEPTH"] == v["EXPECTED_COMPLEMENT_DEPTH"]
    assert "not a forecast" in v["complementPriceBasis"]


def test_the_rename_is_recorded_rather_than_done_silently():
    r = pe.RENAMED_FIELDS["EXPECTED_COMPLEMENT_PRICE"]
    assert r["now"] == "CURRENT_COMPLEMENT_PRICE"
    assert r["aliasKept"] is True
    assert "read as a forecast" in r["why"]
