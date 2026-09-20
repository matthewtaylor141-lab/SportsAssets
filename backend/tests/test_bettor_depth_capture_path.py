"""§A. The depth actually reaches the market-state row, end to end."""

import pytest

from sportsassets import bettor_book_snapshot as bs
from sportsassets import pmus
from sportsassets.workers import shadow_bettor as w


MD = {
    "bids": [{"px": {"value": "0.52"}, "qty": "120"},
             {"px": {"value": "0.51"}, "qty": "80"}],
    "offers": [{"px": {"value": "0.55"}, "qty": "60"}],
    "stats": {"sharesTraded": "48210", "lastTradePx": {"value": "0.53"}},
    "state": "MARKET_STATE_OPEN",
    "transactTime": "2026-09-20T18:00:00Z",
    "bestBid": {"value": "0.52"}, "bestAsk": {"value": "0.55"},
}


class _Client:
    def __init__(self, md=MD, raises=None):
        outer = self

        class markets:
            @staticmethod
            def book(slug):
                if outer.raises:
                    raise outer.raises("boom")
                return {"marketData": outer.md}

            @staticmethod
            def bbo(slug):
                return {"marketData": {"bestBid": {"value": "0.40"},
                                       "bestAsk": {"value": "0.44"},
                                       "state": "MARKET_STATE_OPEN"}}
        self.md, self.raises, self.markets = md, raises, markets


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(w, "pace", lambda *a, **k: None)


def _state(monkeypatch, client):
    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    q = w._read_quote("aec-test")
    return q, w._market_state({"symbol": "aec-test", "outcomeLeg": "YES"},
                              q, "2026-09-20T18:00:00Z")


def test_the_ladder_reaches_available_depth(monkeypatch):
    q, st = _state(monkeypatch, _Client())
    assert st["readable"] is True
    d = st["availableDepth"]
    assert d["PARSE_STATUS"] == bs.PARSE_OK
    assert [lv["qty"] for lv in d["BID_LADDER"]] == ["120", "80"]
    assert [lv["price"] for lv in d["BID_LADDER"]] == ["0.52", "0.51"]
    assert d["ASK_LADDER"][0]["qty"] == "60"
    assert d["STATS_SHARES_TRADED"] == "48210"
    assert d["DISPLAYED_DEPTH_AT_T0"]["bid"] == "200"


def test_available_depth_is_no_longer_none(monkeypatch):
    """The whole point: 984 of 1,972 decisions fired INSUFFICIENT_DEPTH
    from a field this path set to None."""
    _q, st = _state(monkeypatch, _Client())
    assert st["availableDepth"] is not None


def test_the_raw_source_is_kept_for_audit(monkeypatch):
    _q, st = _state(monkeypatch, _Client())
    assert st["availableDepth"]["RAW_SOURCE"] == MD


def test_one_request_serves_both_readings(monkeypatch):
    """Reading the endpoint twice would double our rate against a venue
    we pace deliberately."""
    calls = []
    c = _Client()
    orig = c.markets.book
    c.markets.book = staticmethod(
        lambda s: (calls.append(s), orig(s))[1])
    q, _st = _state(monkeypatch, c)
    assert len(calls) == 1
    assert q["bid"] == 0.52 and q["ask"] == 0.55


def test_the_quote_still_comes_out_when_the_book_feed_raises(monkeypatch):
    """The bbo fallback stays, and the snapshot names the absence."""
    q, st = _state(monkeypatch, _Client(raises=TimeoutError))
    assert q["bid"] == 0.40 and q["ask"] == 0.44
    d = st["availableDepth"]
    assert d["PARSE_STATUS"] == bs.PARSE_NO_MARKET_DATA
    assert any(r.startswith("BOOK_FEED_ERROR") for r in
               d["MISSING_FIELD_REASONS"])


def test_an_unreadable_market_is_still_a_named_state(monkeypatch):
    class Dead:
        class markets:
            @staticmethod
            def book(slug):
                return {"marketData": {"state": "MARKET_STATE_HALTED"}}

            @staticmethod
            def bbo(slug):
                return {"marketData": {"state": "MARKET_STATE_HALTED"}}
    _q, st = _state(monkeypatch, Dead())
    assert st["readable"] is False
    assert "HALTED" in st["whyUnreadable"]


def test_the_l2_reference_records_the_feed_and_parse_status(monkeypatch):
    _q, st = _state(monkeypatch, _Client())
    ref = st["l2Reference"]
    assert ref["feed"] == "book"
    assert ref["depth"] == bs.PARSE_OK
    assert ref["snapshotVersion"] == bs.SNAPSHOT_VERSION


def test_queue_ahead_is_derived_from_the_captured_state(monkeypatch):
    _q, st = _state(monkeypatch, _Client())
    qa = bs.queue_ahead_at_t0(st["availableDepth"], side="BID",
                              price="0.52")
    assert qa["QUEUE_AHEAD_AT_T0"] == "120"
    assert qa["DERIVATION"] == bs.QUEUE_AHEAD_DERIVED_FROM_DISPLAYED
    assert len(qa["assumptions"]) == 4
