"""Per-leg inventory: what it refuses to net, and what it refuses to claim."""

from decimal import Decimal

import pytest

from sportsassets import bettor_inventory as inv

CONFIRMED = "EXACT_ONE_TO_COMPLEMENT_BASKET"
PENDING = ("STRUCTURALLY_IDENTIFIED_COMPLEMENT_PENDING_"
           "INSTITUTIONAL_CONFIRMATION")


def _rows(yes_qty=None, no_qty=None, yes_px="0.48", no_px="0.49"):
    out = []
    if yes_qty:
        out.append({"position_id": "y", "leg": "YES",
                    "qty": yes_qty, "price": yes_px})
    if no_qty:
        out.append({"position_id": "n", "leg": "NO",
                    "qty": no_qty, "price": no_px})
    return out


# ── the netting failure, which is the whole point ────────────────────

def test_a_matched_pair_and_a_flat_book_are_not_the_same_thing():
    """Both net to zero. One carries locked P&L and occupies capital."""
    matched = inv.inventory(_rows("100", "100"), identity_status=CONFIRMED)
    flat = inv.inventory([], identity_status=CONFIRMED)

    assert matched["IS_MATCHED_NOT_FLAT"] is True
    assert matched["IS_GENUINELY_FLAT"] is False
    assert Decimal(matched["CAPITAL_OCCUPIED"]) > 0
    assert Decimal(matched["LOCKED_PNL"]) > 0

    assert flat["IS_GENUINELY_FLAT"] is True
    assert Decimal(flat["CAPITAL_OCCUPIED"]) == 0


def test_the_legs_are_stored_separately_and_never_summed():
    s = inv.inventory(_rows("100", "60"), identity_status=CONFIRMED)
    assert s["YES_QTY"] == "100"
    assert s["NO_QTY"] == "60"
    # The pair is DERIVED from them, never stored in their place.
    assert s["MATCHED_QTY"] == "60"
    assert s["RESIDUAL_YES_QTY"] == "40"
    assert s["RESIDUAL_NO_QTY"] == "0"


def test_a_complement_acquisition_is_not_a_reduction_of_the_other_leg():
    """§3: never let a hedge look like a sale because the venue nets."""
    holding = inv.inventory(_rows("100"), identity_status=CONFIRMED)
    hedged = inv.inventory(_rows("100", "100"), identity_status=CONFIRMED)
    # Buying NO did NOT reduce the YES leg.
    assert holding["YES_QTY"] == hedged["YES_QTY"] == "100"
    assert hedged["NO_QTY"] == "100"
    assert "never recorded as a reduction" in hedged["complementIsNotASell"]


# ── a pair is a claim, not arithmetic ────────────────────────────────

def test_an_unconfirmed_complement_yields_no_locked_pnl():
    """min(YES, NO) is only a pair if exactly one leg pays $1."""
    s = inv.inventory(_rows("100", "60"), identity_status=PENDING)
    assert s["pairStatus"] == inv.PAIR_UNCONFIRMED
    assert s["MATCHED_QTY"] == inv.NOT_IDENTIFIED
    assert s["LOCKED_PNL"] == inv.NOT_IDENTIFIED
    # Both legs stand alone as directional positions.
    assert s["RESIDUAL_YES_QTY"] == "100"
    assert s["RESIDUAL_NO_QTY"] == "60"


def test_different_contracts_are_refused_as_a_pair():
    s = inv.inventory(_rows("100", "100"),
                      identity_status="DIFFERENT_CONTRACT")
    assert s["pairStatus"] == inv.PAIR_REFUSED
    assert s["MATCHED_QTY"] == inv.NOT_IDENTIFIED


def test_a_missing_binding_does_not_default_to_confirmed():
    s = inv.inventory(_rows("100", "100"), identity_status=None)
    assert s["pairStatus"] == inv.PAIR_UNCONFIRMED
    assert s["identityStatus"] == inv.NOT_IDENTIFIED


def test_confirmed_statuses_are_listed_positively():
    """A verdict does not become a pair by resembling one."""
    assert PENDING not in inv.CONFIRMED_COMPLEMENT_STATUSES
    assert "DIFFERENT_CONTRACT" not in inv.CONFIRMED_COMPLEMENT_STATUSES
    for s in inv.CONFIRMED_COMPLEMENT_STATUSES:
        assert inv.pair_status(s)["pairStatus"] == inv.PAIR_CONFIRMED


# ── bases, and the convention that produces them ─────────────────────

def test_the_residual_basis_names_its_convention():
    s = inv.inventory(_rows("100", "60"), identity_status=CONFIRMED)
    assert s["basisConvention"] == "AVERAGE_COST"
    assert s["RESIDUAL_YES_BASIS"] == "0.48"
    assert "FIFO would give a different" in s["basisConventionRule"]


def test_average_basis_is_weighted_across_fills():
    rows = [{"leg": "YES", "qty": "100", "price": "0.40"},
            {"leg": "YES", "qty": "100", "price": "0.60"}]
    s = inv.inventory(rows, identity_status=CONFIRMED)
    assert s["YES_QTY"] == "200"
    assert Decimal(s["YES_AVG_BASIS"]) == Decimal("0.50")


def test_a_leg_with_no_quantity_has_no_basis():
    s = inv.inventory(_rows("100"), identity_status=CONFIRMED)
    assert s["NO_QTY"] == "0"
    assert s["NO_AVG_BASIS"] == inv.NOT_IDENTIFIED
    assert s["RESIDUAL_NO_BASIS"] == inv.NOT_IDENTIFIED


# ── rows that cannot be counted are named, never guessed ─────────────

def test_an_unrecognised_leg_is_refused_not_bucketed():
    rows = _rows("100") + [{"position_id": "x", "leg": "MAYBE",
                            "qty": "5", "price": "0.5"}]
    s = inv.inventory(rows, identity_status=CONFIRMED)
    assert s["unrecognisedLegs"] == ["x"]
    assert s["YES_QTY"] == "100", "an unknown leg leaked into YES"


def test_a_fill_without_a_price_does_not_enter_the_average_at_zero():
    rows = _rows("100") + [{"position_id": "z", "leg": "YES", "qty": "100"}]
    s = inv.inventory(rows, identity_status=CONFIRMED)
    assert s["unpricedRows"] == ["z"]
    # The average is still 0.48, not 0.24.
    assert Decimal(s["YES_AVG_BASIS"]) == Decimal("0.48")


@pytest.mark.parametrize("leg,expected", [
    ("yes", "YES"), ("YES", "YES"), (" no ", "NO"), ("NO", "NO"),
    ("BOTH", None), (None, None), ("", None),
])
def test_leg_normalisation_is_strict(leg, expected):
    assert inv._norm_leg(leg) == expected


# ── the arithmetic is borrowed, not rewritten ────────────────────────

def test_the_pair_arithmetic_comes_from_the_research_module():
    s = inv.inventory(_rows("100", "60"), identity_status=CONFIRMED)
    assert s["derivedBy"].endswith("inventory_state.py")
    # 0.48 + 0.49 = 0.97; 60 matched locks (1 - 0.97) * 60 = 1.80
    assert Decimal(s["MATCHED_PAIR_BASIS"]) == Decimal("0.97")
    assert Decimal(s["LOCKED_PNL"]) == Decimal("1.80")


def test_capital_splits_between_matched_and_residual():
    s = inv.inventory(_rows("100", "60"), identity_status=CONFIRMED)
    total = Decimal(s["MATCHED_CAPITAL"]) + Decimal(s["RESIDUAL_CAPITAL"])
    assert total == Decimal(s["CAPITAL_OCCUPIED"])


# ── lane separation (§22) ────────────────────────────────────────────

class _Pool:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []

    async def fetchrow(self, sql, *args):
        return None


@pytest.mark.asyncio
async def test_inventory_is_never_read_across_lanes():
    """RN1's frozen book must not become BETTOR's inventory."""
    with pytest.raises(ValueError, match="not a lane"):
        await inv.load(_Pool(), "mkt-1", lane="NOT_A_LANE")


@pytest.mark.asyncio
async def test_the_lane_predicate_actually_reaches_the_query():
    pool = _Pool()
    await inv.load(pool, "mkt-1")
    sql, args = pool.calls[0]
    assert "lane = $2" in sql
    assert args == ("mkt-1", inv.BETTOR_LANE)
    # The X-series table is not the source: §22 forbids attributing the
    # experiments' positions to BETTOR EV.
    assert "bettor_experimental_positions" not in sql
    assert "shadow_positions" in sql


def test_the_bettor_lane_is_a_declared_lane():
    from sportsassets import shadow_lanes as lanes
    assert inv.BETTOR_LANE in lanes.LANES
