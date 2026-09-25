"""THE VENUE DID REPORT A SETTLEMENT. OUR PARSER READ THE WRONG FIELD.

MEASURED IN PRODUCTION, command-verify run 55, job 108082946649,
2026-09-25T13:08:43Z, slug `aec-mlb-az-col-2026-09-24`. The settlement
endpoint returned:

    {"slug": "aec-mlb-az-col-2026-09-24", "settlement": 1}

`read_settlement` looked only for `settlementPrice` as an `Amount` dict --
the SDK's declared type -- found nothing, and returned
NO_SETTLEMENT_PRICE_IN_RESPONSE. I then reported that to the owner as the
venue declining to report an outcome. It was not. The venue reported the
settlement under a different KEY (`settlement`) and a different TYPE (a bare
int), and the parser walked past it.

The listing corroborates it twice over, and both of those were unread too:

    "outcomes":      "[\\"Arizona Diamondbacks\\",\\"Colorado Rockies\\"]"
    "outcomePrices": "[\\"1\\",\\"0\\"]"          <- JSON-ENCODED STRINGS
    "marketSides":   [{"description": "Arizona Diamondbacks",
                       "price": "1", "long": true},
                      {"description": "Colorado Rockies",
                       "price": "0", "long": false}]
    "status":        "MARKET_STATUS_RESOLVED"

`_converged_winner` required a list and rejected the price vector on the
isinstance check before reading a number, which is how a market priced at
exactly 1 and 0 came back CLOSED_BUT_NO_REPORTED_OR_CONVERGED_OUTCOME.

THE PAYLOAD IS THE FIXTURE. Every field these tests read is verbatim from
that capture, so this cannot pass against an invented shape -- which is the
mistake that produced the original bug.

WHAT IS STILL NOT ASSUMED. The endpoint returns ONE number, and which side
it pays is the whole question. `marketSides` carries `long` and `price`, so
the claim "this is the LONG side's payout" is CHECKED, and a contradiction
refuses instead of picking a winner. A converged price vector remains
RESOLVED_DERIVED -- our inference -- and is never promoted to RESOLVED.
"""

from __future__ import annotations

import json
import os

import pytest

from sportsassets import bettor_live_read as LR
from sportsassets import bettor_venue_settlement_probe as P

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "pmus_settled_market_2026_09_24_az_col.json")
SLUG = "aec-mlb-az-col-2026-09-24"


def _real():
    with open(FIXTURE) as fh:
        return json.load(fh)


class _Markets:
    def __init__(self, market, settlement, raises=None):
        self._m, self._s, self._raises = market, settlement, raises

    def list(self, _q):
        return {"markets": ([self._m] if self._m else [])}

    def settlement(self, _slug):
        if self._raises:
            raise self._raises
        return self._s


class _Client:
    def __init__(self, market=None, settlement=None, raises=None):
        self.markets = _Markets(market, settlement, raises)


class _NotFound(Exception):
    pass


_NotFound.__name__ = "NotFoundError"


def _client_from_fixture(**over):
    d = _real()
    m = dict(d["listing_market"])
    m.update(over.pop("market", {}))
    st = over.pop("settlement", d["settlement_response"])
    return _Client(market=m, settlement=st, **over)


# ── the fixture is the real thing ────────────────────────────────────

def test_the_fixture_is_the_payload_production_returned():
    d = _real()
    assert d["settlement_response"] == {"slug": SLUG, "settlement": 1}
    m = d["listing_market"]
    assert m["outcomePrices"] == '["1","0"]'
    assert isinstance(m["outcomePrices"], str), "a STRING, which is the bug"
    assert m["status"] == "MARKET_STATUS_RESOLVED"
    assert "run 55" in d["_provenance"]["captured_from"]


# ── 1 · the settlement field is now read ─────────────────────────────

def test_the_bare_settlement_integer_is_read_as_the_price():
    got = LR.read_settlement(_client_from_fixture(), SLUG)
    assert got["status"] == LR.RESOLVED, got
    assert got["settlement_price"] == 1.0
    assert got["settlement_price_raw"] == "1"
    # WHICH FIELD AND SHAPE SUPPLIED IT, recorded rather than assumed.
    assert got["settlement_field"] == "settlement"
    assert got["settlement_shape"] == "int"
    assert got["error"] is None
    assert got["units_status"] == "DOLLARS_PER_CONTRACT_CONSISTENT"


def test_the_declared_amount_dict_still_works():
    """The SDK's declared type must keep working: this is an addition, not
    a replacement, and a venue that starts sending `Amount` must not break."""
    got = LR.read_settlement(
        _client_from_fixture(settlement={
            "marketSlug": SLUG,
            "settlementPrice": {"value": "1", "currency": "USD"},
            "settledAt": "2026-09-25T02:14:05Z"}), SLUG)
    assert got["status"] == LR.RESOLVED
    assert got["settlement_field"] == "settlementPrice"
    assert got["settlement_shape"] == "dict"
    assert got["currency"] == "USD"
    assert got["settled_at"] == "2026-09-25T02:14:05Z"


def test_a_genuinely_absent_price_still_refuses_by_name():
    got = LR.read_settlement(
        _client_from_fixture(settlement={"slug": SLUG}), SLUG)
    assert got["status"] == LR.UNREADABLE
    assert got["error"] == "NO_SETTLEMENT_PRICE_IN_RESPONSE"
    assert got["settlement_field"] is None


def test_a_price_outside_zero_one_is_not_divided_by_a_hundred():
    got = LR.read_settlement(
        _client_from_fixture(settlement={"slug": SLUG,
                                         "settlement": 100}), SLUG)
    assert got["status"] != LR.RESOLVED
    assert got["units_status"] == "UNITS_UNVERIFIED_OUT_OF_0_1"
    assert got["settlement_price"] is None


# ── 2 · orientation is corroborated, not assumed ─────────────────────

def test_resolution_is_corroborated_against_the_long_sides_price():
    """Arizona is the side the venue marks `long`, priced 1; the settlement
    endpoint says 1. Two independent fields agree, so the orientation is a
    finding rather than a convention."""
    got = LR.read_resolution(_client_from_fixture(), SLUG)
    assert got["status"] == LR.RESOLVED, got
    assert got["outcome"] == "1"
    assert got["settlement_field"] == "settlement"
    assert got["long_side_price"] == 1.0
    assert got["corroboration"] == LR.CORROBORATED
    assert "LONG side's payout" in got["orientation"]
    assert "1 - settlement" in got["orientation"]


def test_the_long_side_is_arizona_and_the_short_side_is_colorado():
    """THE ORIENTATION ITSELF, off the real payload. Arizona's position is
    ORDER_INTENT_BUY_LONG, so it pays the settlement value; a short holding
    would pay 1 - settlement."""
    m = _real()["listing_market"]
    sides = {sd["description"]: sd for sd in m["marketSides"]}
    assert sides["Arizona Diamondbacks"]["long"] is True
    assert sides["Arizona Diamondbacks"]["price"] == "1"
    assert sides["Colorado Rockies"]["long"] is False
    assert sides["Colorado Rockies"]["price"] == "0"
    assert LR._long_side_price(m) == 1.0


def test_a_contradicting_long_price_refuses_rather_than_choosing():
    """The failure this guards is settling against the wrong team, which
    has happened on this book before."""
    m = dict(_real()["listing_market"])
    m["marketSides"] = [dict(m["marketSides"][0], price="0", long=True),
                        dict(m["marketSides"][1], price="1", long=False)]
    got = LR.read_resolution(_Client(market=m, settlement={"slug": SLUG,
                                                          "settlement": 1}),
                             SLUG)
    assert got["status"] == LR.UNREADABLE, got
    assert got["corroboration"] == LR.CONTRADICTED
    assert got["error"] == "LONG_SIDE_PRICE_CONTRADICTS_THE_SETTLEMENT"
    assert got["settlement_price"] is None


def test_an_absent_market_sides_is_uncorroborated_not_contradicted():
    m = dict(_real()["listing_market"])
    m.pop("marketSides")
    got = LR.read_resolution(_Client(market=m, settlement={"slug": SLUG,
                                                          "settlement": 1}),
                             SLUG)
    assert got["status"] == LR.RESOLVED
    assert got["corroboration"] == LR.UNCORROBORATED
    assert got["long_side_price"] is None


# ── 3 · the JSON-encoded price vector ────────────────────────────────

def test_a_json_encoded_price_vector_now_converges():
    """With the settlement endpoint unavailable, the same market's prices
    yield RESOLVED_DERIVED -- our inference from a price, which is what it
    has always been and is still not a reported settlement."""
    got = LR.read_resolution(
        _client_from_fixture(raises=_NotFound("no settlement")), SLUG)
    assert got["status"] == LR.RESOLVED_DERIVED, got
    assert got["outcome"] == "Arizona Diamondbacks"
    assert got["outcome_field"] == "outcomePrices"
    assert "inference from a price" in got["derivation"]


def test_a_converged_inference_is_never_promoted_to_reported():
    assert LR.RESOLVED_DERIVED != LR.RESOLVED
    got = LR.read_resolution(
        _client_from_fixture(raises=_NotFound("x")), SLUG)
    assert got["status"] != LR.RESOLVED


def test_an_unconverged_price_vector_still_refuses():
    got = LR.read_resolution(
        _client_from_fixture(market={"outcomePrices": '["0.55","0.45"]'},
                             raises=_NotFound("x")), SLUG)
    assert got["status"] == LR.UNREADABLE
    assert got["error"] == "CLOSED_BUT_NO_REPORTED_OR_CONVERGED_OUTCOME"


def test_a_malformed_price_field_refuses_rather_than_guessing():
    for bad in ('["1",', "not json at all", '{"a":1}', ""):
        got = LR.read_resolution(
            _client_from_fixture(market={"outcomePrices": bad},
                                 raises=_NotFound("x")), SLUG)
        assert got["status"] == LR.UNREADABLE, bad


# ── 4 · the probe now reports the reported settlement ────────────────

def test_the_probe_reports_an_authoritative_payout_on_this_payload():
    got = P.probe(_client_from_fixture(), SLUG)
    assert got["terminal_reading"] == P.R_REPORTED, got
    assert got["authoritative_payout_present"] is True
    assert got["settlement"]["top_level_keys"] == ["settlement", "slug"]
    assert got["settlement"]["field_shapes"]["settlement"]["type"] == "int"
    # AND THE LISTING'S SHAPES ARE STILL CAPTURED as the string they are.
    sh = got["listing"]["payout_candidates"]["outcomePrices"]["shape"]
    assert sh["type"] == "str"
    assert sh["looks_like_json_encoded"] is True
    assert sh["decoded_preview"] == ["1", "0"]


def test_a_resolved_status_is_not_read_as_a_void():
    """`MARKET_STATUS_RESOLVED` contains no void word; a status-field match
    must not fire on it."""
    got = P.probe(_client_from_fixture(), SLUG)
    assert got["listing"]["void_evidence"]["declared"] is False
