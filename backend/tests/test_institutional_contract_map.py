"""RETAIL SLUG -> INSTITUTIONAL SYMBOL: EXACT OR REFUSED BY NAME.

The moneyline record below is built to the venue's documented instrument
schema (/data-guide/asset-naming-conventions: `{product_code}-{event_id}`,
cftc_instrument_id = product_id = symbol for a moneyline, long/short
participant ids `{series}-{abbreviation}`). It is NOT a captured production
row -- none for an aec instrument is in the repository -- and the mapping does
not depend on any field beyond those the documentation names. The spread
case reuses the PRODUCTION row from test_contract_family.

Each mutation changes ONE venue field and must refuse, so loosening any
single condition fails here.
"""

from __future__ import annotations

import copy

import pytest

from sportsassets import institutional_contract_map as M
from tests.test_contract_family import (MLS, MLS_RETAIL, SPREAD,
                                        SPREAD_RETAIL, SPREAD_SLUG)

SLUG = "aec-mlb-sd-mil-2026-10-03"

AEC = {
    "symbol": SLUG,
    "productId": SLUG,
    "priceScale": "1000",
    "fractionalQtyScale": "100",
    "state": "INSTRUMENT_STATE_OPEN",
    "eventAttributes": {
        "eventId": SLUG,
        "payoutValue": "1000",
        "question": "Will San Diego win?",
    },
    "metadata": {
        "cftc_instrument_id": SLUG,
        "instrument_product": "aec",
        "product_id": SLUG,
        "event_id": "mlb-sd-mil-2026-10-03",
        "outcome_type": "moneyline",
        "outcome_strike": "0.0",
        "long_participant_id": "mlb-sd",
        "short_participant_id": "mlb-mil",
        "long_participant_name": "San Diego",
        "short_participant_name": "Milwaukee",
        "instrument_rules": "This market will settle to Yes if San Diego "
                            "wins the game scheduled for Oct 3, 2026.",
    },
}


def mutate(path, value, base=AEC):
    rec = copy.deepcopy(base)
    node = rec
    for k in path[:-1]:
        node = node[k]
    if value is None:
        node.pop(path[-1], None)
    else:
        node[path[-1]] = value
    return rec


def test_an_exact_moneyline_maps_to_its_own_symbol_long_side_identity_price():
    r = M.map_retail_to_institutional(SLUG, "yes", AEC)
    assert r["ok"] is True and r["refusal"] is None
    assert r["institutional_symbol"] == SLUG
    assert r["institutional_side"] == "LONG"
    assert r["price_transform"] == "IDENTITY"
    assert (r["price_scale"], r["qty_scale"], r["payout_value"]) == (
        1000, 100, "1000")
    assert len(r["basis"]) == 5
    assert r["book_equivalence"]["verdict"].startswith(
        "SAME_INSTRUMENT_SAME_CLOB_BY_DOCUMENTATION")
    assert r["book_equivalence"]["executable_price_authority"] == \
        "CONDITIONAL_ON_THE_DOCUMENTED_SINGLE_CLOB"
    assert M.map_retail_to_institutional(SLUG, "long", AEC)["ok"] is True


@pytest.mark.parametrize("leg", ["no", "short", "NO"])
def test_the_retail_no_leg_is_refused_by_name(leg):
    r = M.map_retail_to_institutional(SLUG, leg, AEC)
    assert r["ok"] is False and r["refusal"] == M.M_LEG_SHORT
    assert r["institutional_symbol"] is None


@pytest.mark.parametrize("slug,leg,rec,refusal", [
    ("", "yes", AEC, M.M_NO_SLUG),
    (SLUG, "", AEC, M.M_LEG_UNKNOWN),
    (SLUG, "over", AEC, M.M_LEG_UNKNOWN),
    (SLUG, "yes", None, M.M_NO_RECORD),
    (SLUG, "yes", {"symbol": ""}, M.M_NO_RECORD),
    # a different slug against this record: never mapped
    ("aec-mlb-mil-sd-2026-10-03", "yes", AEC, M.M_KEYS_DISAGREE),
])
def test_missing_or_mismatched_inputs_are_refused(slug, leg, rec, refusal):
    assert M.map_retail_to_institutional(slug, leg, rec)["refusal"] == refusal


@pytest.mark.parametrize("path,value,refusal", [
    (("metadata", "cftc_instrument_id"), None, M.M_NO_CFTC),
    (("metadata", "cftc_instrument_id"), "aec-mlb-sd-mil-2026-10-04",
     M.M_KEYS_DISAGREE),
    (("symbol",), "aec-mlb-sd-mil-2026-10-04", M.M_KEYS_DISAGREE),
    (("metadata", "event_id"), "mlb-sd-mil-2026-10-04", M.M_NOT_COMPOSED),
    (("metadata", "event_id"), None, M.M_NOT_COMPOSED),
    # the long side is NOT the slug's first team: opposite outcome side
    (("metadata", "long_participant_id"), "mlb-mil", M.M_SIDE),
    (("metadata", "short_participant_id"), "mlb-sd", M.M_SIDE),
    (("metadata", "long_participant_id"), None, M.M_SIDE),
    (("metadata", "instrument_rules"), "Resolves per the rulebook.",
     M.M_NOT_BINARY_YES),
    (("priceScale",), "0", M.M_SCALE),
    (("priceScale",), None, M.M_SCALE),
    (("fractionalQtyScale",), "x", M.M_SCALE),
    (("eventAttributes", "payoutValue"), "1.00", M.M_PAYOUT),
    (("eventAttributes", "payoutValue"), "100", M.M_PAYOUT),
    (("eventAttributes", "payoutValue"), None, M.M_PAYOUT),
    (("eventAttributes", "eventOutcome"), "EVENT_OUTCOME_MUTUALLY_EXCLUSIVE",
     M.M_FAMILY_CONTRADICTS),
])
def test_each_single_field_mutation_refuses(path, value, refusal):
    r = M.map_retail_to_institutional(SLUG, "yes", mutate(path, value))
    assert r["ok"] is False and r["refusal"] == refusal, r["why"]
    assert r["institutional_symbol"] is None and r["why"]


def test_the_production_spread_maps_through_the_existing_proof():
    r = M.map_retail_to_institutional(SPREAD_SLUG, "yes", SPREAD,
                                      retail_row=SPREAD_RETAIL)
    assert r["ok"] is True and r["institutional_symbol"] == SPREAD_SLUG
    assert r["price_scale"] == 100 and r["payout_value"] == "100"
    assert any("registered contract" in b for b in r["basis"])


def test_a_spread_whose_strike_disagrees_is_not_exact():
    rec = mutate(("metadata", "outcome_strike"), "-13.5", base=SPREAD)
    r = M.map_retail_to_institutional(SPREAD_SLUG, "yes", rec,
                                      retail_row=SPREAD_RETAIL)
    assert r["refusal"] == M.M_NOT_EXACT


def test_the_mls_outcome_set_is_refused_on_its_payout_scale():
    # yes_leg_binding finds the outcome instrument, but its payoutValue
    # "1.00" against priceScale 100 does not show px/priceScale to be the
    # retail dollar price, so it is refused rather than assumed.
    r = M.map_retail_to_institutional(MLS["symbol"], "yes", MLS,
                                      retail_row=MLS_RETAIL)
    assert r["ok"] is False
    assert r["refusal"] in (M.M_PAYOUT, M.M_NOT_EXACT, M.M_SCALE)


def test_the_mapping_never_raises_on_junk():
    for rec in ([], "x", {"symbol": 3}, {"symbol": SLUG, "metadata": None,
                                         "eventAttributes": None}):
        r = M.map_retail_to_institutional(SLUG, "yes", rec)
        assert r["ok"] is False and r["refusal"]
