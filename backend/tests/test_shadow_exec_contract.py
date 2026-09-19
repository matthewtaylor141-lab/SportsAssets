"""INSTITUTIONAL_L2_EXECUTION_CONTRACT_V1 AND THE LEG BINDING, PINNED.

Owner directive 2026-09-19 22:4xZ §2/§4.

THE TWO FAILURES THESE PREVENT:

  A SILENT SCALE. px and qty are scaled integers whose scale is per
  instrument. A default of 100 applied to an MLB or NFL row (both 1000)
  misprices the fill tenfold and nothing downstream looks wrong. The
  mutation test below moves the scale and REQUIRES the economics to
  move materially.

  A LEG WEARING THE OTHER LEG'S BOOK. The collector read one slug's BBO
  and stamped it on both the `yes` and the `no` row. An X1 series built
  on that would be scoring the YES book under the NO name.
"""

from __future__ import annotations

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_exec_contract as ec
from sportsassets import shadow_l2 as l2

BOOK_RESPONSE = {
    "symbol": "x", "state": "INSTRUMENT_STATE_OPEN",
    "transactTime": "2026-09-19T22:03:10Z",
    "bids": [{"px": "97", "qty": "1"}, {"px": "39", "qty": "407"}],
    "offers": [{"px": "40", "qty": "50000"}, {"px": "41", "qty": "25000"}],
}


def book(ps=100, qs=100):
    return l2.book_from(BOOK_RESPONSE, price_scale=ps, qty_scale=qs,
                        request_id="r1")


def econ(ps=100, qs=100, notional=1000):
    return ec.economics(book=book(ps, qs), side=sh.BUY,
                        intended_notional_usd=notional,
                        limit_price=0.42, decision_price=0.40)


# ── §4: the scale mutation ───────────────────────────────────────────


def test_a_scale_of_100_versus_1000_materially_alters_the_economics():
    """THE REQUIRED MUTATION. Same bytes from the venue, one scale
    changed, and the executed notional must not survive it."""
    at_100 = econ(100, 100)
    at_1000 = econ(1000, 1000)
    assert at_100["executedNotionalUsd"] != at_1000["executedNotionalUsd"]
    # two orders of magnitude, not a rounding difference
    assert at_100["executedNotionalUsd"] > at_1000["executedNotionalUsd"] * 10


def test_there_is_no_default_scale_anywhere():
    with pytest.raises(l2.ScalesRequired):
        l2.book_from(BOOK_RESPONSE, price_scale=None, qty_scale=100)
    with pytest.raises(ec.ContractViolation):
        ec.economics(book={"bids": [], "asks": []}, side=sh.BUY,
                     intended_notional_usd=1000, decision_price=0.4)


def test_the_contract_declaration_is_hashed_and_stable():
    assert ec.CONTRACT_SHA == ec.contract_sha()
    assert len(ec.CONTRACT_SHA) == 16
    assert ec.CONTRACT["scaleDefault"].startswith("NONE")


def test_editing_any_clause_moves_the_contract_hash():
    import copy
    for clause in ("priceField", "quantityField", "askSide", "ordering",
                   "priceConversion", "quantityConversion", "emptyBook"):
        edited = copy.deepcopy(ec.CONTRACT)
        edited[clause] = "EDITED"
        import hashlib, json
        raw = json.dumps(edited, sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(raw.encode()).hexdigest()[:16] \
            != ec.CONTRACT_SHA, clause


# ── §10: the $1,000 walk, partial and honest ─────────────────────────


def test_the_intended_notional_becomes_contracts_at_the_decision_price():
    """$1,000 intended at 0.40 is 2,500 contracts, not 1,000. Sizing in
    dollars and walking in contracts is where an order of magnitude
    hides."""
    out = econ()
    assert out["intendedContracts"] == pytest.approx(2500.0)


def test_a_thin_book_fills_what_was_there_and_no_more():
    thin = dict(BOOK_RESPONSE, offers=[{"px": "40", "qty": "5000"}])
    out = ec.economics(
        book=l2.book_from(thin, price_scale=100, qty_scale=100),
        side=sh.BUY, intended_notional_usd=1000,
        limit_price=0.42, decision_price=0.40)
    assert out["status"] == sh.PARTIAL
    assert out["executedNotionalUsd"] == pytest.approx(20.0)
    assert out["unfilledNotionalUsd"] == pytest.approx(980.0)
    # "Do not manufacture the remaining."
    assert out["executedNotionalUsd"] + out["unfilledNotionalUsd"] \
        == pytest.approx(1000.0)


def test_an_empty_book_yields_no_fill_and_no_notional():
    empty = l2.book_from(
        {"symbol": "x", "state": l2.STATE_OPEN, "bids": [], "offers": []},
        price_scale=100, qty_scale=100)
    out = ec.economics(book=empty, side=sh.BUY, intended_notional_usd=1000,
                       limit_price=0.42, decision_price=0.40)
    assert out["status"] == sh.NOT_IDENTIFIED
    assert out["executedNotionalUsd"] == 0.0
    assert out["unfilledNotionalUsd"] == pytest.approx(1000.0)


def test_a_notional_cannot_become_a_quantity_without_a_price():
    with pytest.raises(ec.ContractViolation):
        ec.economics(book=book(), side=sh.BUY, intended_notional_usd=1000)


def test_the_book_sha_identifies_the_exact_levels_walked():
    """The venue supplies no sequence number, so the levels themselves
    are the identity of the arrival book."""
    a = econ()["l2BookSha"]
    moved = dict(BOOK_RESPONSE, offers=[{"px": "41", "qty": "50000"}])
    b = ec.economics(
        book=l2.book_from(moved, price_scale=100, qty_scale=100),
        side=sh.BUY, intended_notional_usd=1000,
        limit_price=0.42, decision_price=0.40)["l2BookSha"]
    assert a != b


def test_every_execution_carries_the_shadow_facts():
    out = econ()
    assert out["realOrderSubmissionEnabled"] is False
    assert out["capitalAtRisk"] == 0
    assert out["executionClass"] == sh.MARKETABLE_RECONSTRUCTED
    assert out["contractSha"] == ec.CONTRACT_SHA


# ── §2: the leg binding ──────────────────────────────────────────────


RETAIL_BBO = {"readable": True, "bid": 0.59, "ask": 0.60, "mid": 0.595,
              "spread": 0.01, "spreadRelative": 0.0168}


def test_the_yes_leg_keeps_the_book_because_it_is_the_yes_book():
    """One long contract per slug; BUY_SHORT is SIDE_SELL at the same
    price. So the slug's BBO is the YES contract's book."""
    out = l2.bind_leg(RETAIL_BBO, "yes")
    assert out["bboBinding"] == l2.BIND_YES
    assert out["bid"] == 0.59 and out["ask"] == 0.60
    assert l2.leg_is_execution_bound(out) is True


def test_the_no_leg_does_not_inherit_the_yes_book():
    """THE DEFECT. Both legs previously carried this bid and ask."""
    out = l2.bind_leg(RETAIL_BBO, "no")
    assert out["bboBinding"] == l2.BIND_NOT_IDENTIFIED
    assert out["bid"] is None and out["ask"] is None
    assert out["mid"] is None and out["spread"] is None
    assert l2.leg_is_execution_bound(out) is False
    assert out["whyLegBookAbsent"]


def test_the_no_leg_is_not_derived_as_one_minus_yes():
    """The NO *probability* is 1 - P(yes) on an exhaustive set, but the
    NO *book* is the siblings' depth. §2 permits a derivation only
    where the transformation is exact; for depth it is not."""
    out = l2.bind_leg(RETAIL_BBO, "no")
    for field in ("bid", "ask", "mid"):
        assert out[field] != pytest.approx(1 - RETAIL_BBO[field])
        assert out[field] is None


def test_an_unnamed_leg_is_market_level_not_guessed_into_a_side():
    for leg in (None, "", "draw", "whatever"):
        out = l2.bind_leg(RETAIL_BBO, leg)
        assert out["bboBinding"] == l2.BIND_MARKET_LEVEL
        assert l2.leg_is_execution_bound(out) is False


def test_an_unreadable_book_is_market_level_whatever_the_leg():
    out = l2.bind_leg({"readable": False, "whyUnreadable": "halted"}, "yes")
    assert out["bboBinding"] == l2.BIND_MARKET_LEVEL
    assert l2.leg_is_execution_bound(out) is False


def test_the_feature_source_version_distinguishes_the_two_collectors():
    """§3: the duplicated rows are historical evidence and are NOT
    repaired. An experiment requires the corrected version instead."""
    assert l2.FEATURE_SOURCE_VERSION != l2.FEATURE_SOURCE_VERSION_DUPLICATED
    assert l2.bind_leg(RETAIL_BBO, "yes")["featureSourceVersion"] \
        == l2.FEATURE_SOURCE_VERSION
