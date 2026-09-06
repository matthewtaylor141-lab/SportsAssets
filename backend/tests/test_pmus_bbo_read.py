"""pmus.bbo_read: the mirror's quote read names the venue's MARKET STATE
(2026-09-05). The venue was halted venue-wide for five hours -- HTTP 200
on every market read, `"state": "MARKET_STATE_HALTED"`, `bestBid: null`,
`bestAsk: null` -- and the mirror named every read `no_quote`, the word
it also uses for an unreadable book. Pinned here against the probe's
payloads VERBATIM (venue-probe.yml runs 1 and 2, 22:39-22:41Z), and the
2-tuple `_bbo_quotes` that slug_bid reads is untouched."""
import inspect
import json

import pytest

from sportsassets import pmus

# GET /v1/markets/tsc-cfb-okst-tulsa-2026-09-05-total-55pt5/bbo -> HTTP 200 (run 1)
HALTED_BBO = json.loads(
    '{"marketData":{"marketSlug":"tsc-cfb-okst-tulsa-2026-09-05-total-55pt5","currentPx":{"value":"0.5700","currency":"USD"},"lastTradePx":{"value":"0.5700","currency":"USD"},"settlementPx":{"value":"0.5800","currency":"USD"},"sharesTraded":"399.1000","openInterest":"876.1900","bestAsk":null,"bestBid":null,"askDepth":0,"bidDepth":0,"lastPriceSample":{"longPx":{"value":"0.5700","currency":"USD"},"shortPx":{"value":"0.43","currency":"USD"},"ts":"2026-09-05T22:27:28.880256160Z"},"state":"MARKET_STATE_HALTED"}}'  # noqa: E501
)
# GET /v1/markets/tsc-cfb-okst-tulsa-2026-09-05-total-55pt5/book -> HTTP 200 (run 1)
HALTED_BOOK = json.loads(
    '{"marketData":{"marketSlug":"tsc-cfb-okst-tulsa-2026-09-05-total-55pt5","bids":[],"offers":[],"state":"MARKET_STATE_HALTED","stats":{"openPx":{"value":"0.5800","currency":"USD"},"lowPx":{"value":"0.5700","currency":"USD"},"highPx":{"value":"0.5800","currency":"USD"},"lastTradePx":{"value":"0.5700","currency":"USD"},"settlementPx":{"value":"0.5800","currency":"USD"},"sharesTraded":"399.1000","notionalTraded":{"value":"22751.8000","currency":"USD"},"lastTradeQty":"396.0000","openInterest":"876.1900","openSetTime":"2026-09-05T15:18:50.292579536Z","highSetTime":"2026-09-05T15:18:50.292579536Z","lowSetTime":"2026-09-05T15:19:02.918119139Z","lastTradeSetTime":"2026-09-05T15:19:02.918119139Z","settlementSetTime":"2026-09-04T21:00:03.469777722Z","openInterestSetTime":"2026-09-05T15:19:02.992309359Z","notionalSetTime":"2026-09-05T15:19:02.918119139Z","settlementPriceCalculationMethod":"SETTLEMENT_PRICE_CALCULATION_METHOD_EVENT_TIER_3","currentPx":{"value":"0.5700","currency":"USD"},"lastPriceSample":{"longPx":{"value":"0.5700","currency":"USD"},"shortPx":{"value":"0.43","currency":"USD"},"ts":"2026-09-05T22:27:28.880256160Z"}},"transactTime":"2026-09-05T22:27:28.880256160Z"}}'  # noqa: E501
)
# GET /v1/markets/astatc-cfb-cita-charlt-2026-09-05-dstd-3pt5/bbo -> HTTP 200 (run 2):
# a settled (CLOSED) market with a stale resting book -- quotes present, state not OPEN
CLOSED_BBO = json.loads(
    '{"marketData":{"marketSlug":"astatc-cfb-cita-charlt-2026-09-05-dstd-3pt5","currentPx":{"value":"0.1000","currency":"USD"},"lastTradePx":null,"settlementPx":{"value":"0.0000","currency":"USD"},"sharesTraded":"","openInterest":"","bestAsk":{"value":"0.2000","currency":"USD"},"bestBid":{"value":"0.0100","currency":"USD"},"askDepth":7,"bidDepth":1,"lastPriceSample":{"longPx":{"value":"0.1000","currency":"USD"},"shortPx":{"value":"0.9","currency":"USD"},"ts":"2026-09-03T19:13:45.750219512Z"},"longQuote":{"value":"0.2000","currency":"USD"},"shortQuote":{"value":"0.99","currency":"USD"},"state":"MARKET_STATE_CLOSED"}}'  # noqa: E501
)
# GET /v1/markets/aec-nfl-lac-ten-2025-11-02/bbo -> HTTP 200 (run 2): EXPIRED, no quotes
EXPIRED_BBO = json.loads(
    '{"marketData":{"marketSlug":"aec-nfl-lac-ten-2025-11-02","currentPx":{"value":"0.9990","currency":"USD"},"lastTradePx":{"value":"0.9990","currency":"USD"},"settlementPx":{"value":"1.0000","currency":"USD"},"sharesTraded":"894.0000","openInterest":"","bestAsk":null,"bestBid":null,"askDepth":0,"bidDepth":0,"lastPriceSample":null,"state":"MARKET_STATE_EXPIRED"}}'  # noqa: E501
)


class _Markets:
    """The SDK's markets resource: `bbo` and `book` answer a payload,
    raise, or are absent (None)."""

    def __init__(self, bbo=None, book=None):
        self.calls = []
        if bbo is not None:
            self.bbo = self._make("bbo", bbo)
        if book is not None:
            self.book = self._make("book", book)

    def _make(self, name, ans):
        def _fn(slug):
            self.calls.append((name, slug))
            if isinstance(ans, BaseException):
                raise ans
            return ans
        return _fn


class _Client:
    def __init__(self, markets):
        self.markets = markets


def _read(bbo=None, book=None, slug="s"):
    m = _Markets(bbo, book)
    return pmus.bbo_read(_Client(m), slug), m.calls


def _triple(d):
    return d["bid"], d["ask"], d["state"]


def test_the_halted_payload_verbatim_is_no_quote_and_the_venues_own_state():
    out, calls = _read(HALTED_BBO, HALTED_BOOK, "tsc-cfb-okst-tulsa-2026-09-05-total-55pt5")
    assert _triple(out) == (None, None, "MARKET_STATE_HALTED")
    assert out["error"] is None, "HTTP 200 with an empty book is not an error"
    # the state is the string as the venue spells it: not lowered, the prefix kept
    assert out["state"] == "MARKET_STATE_HALTED" and out["state"] != "halted"
    # the book was tried second, as _bbo_quotes tries it, and carried no quote either
    assert [c[0] for c in calls] == ["bbo", "book"]
    assert set(out) == {"bid", "ask", "state", "error"}


def test_the_closed_payload_verbatim_carries_its_stale_quotes_and_its_state():
    out, calls = _read(CLOSED_BBO, slug="astatc-cfb-cita-charlt-2026-09-05-dstd-3pt5")
    assert _triple(out) == (pytest.approx(0.01), pytest.approx(0.20), "MARKET_STATE_CLOSED")
    assert out["error"] is None and calls == [("bbo", "astatc-cfb-cita-charlt-2026-09-05-dstd-3pt5")]
    # the same parse _bbo_quotes makes of the same payload
    assert pmus._bbo_quotes(_Client(_Markets(CLOSED_BBO)), "x") == (pytest.approx(0.01), pytest.approx(0.20))


def test_expired_and_the_sdk_shape_without_a_state():
    out, _ = _read(EXPIRED_BBO)
    assert _triple(out) == (None, None, "MARKET_STATE_EXPIRED") and out["error"] is None
    # the SDK's MarketBBO TypedDict lists no `state`: absent is None, never a guess
    out2, _ = _read({"marketData": {"bestBid": {"value": "0.30"}, "bestAsk": {"value": "0.32"}}})
    assert _triple(out2) == (0.30, 0.32, None) and out2["error"] is None
    out3, _ = _read({"marketData": {"bestBid": None, "bestAsk": None}})
    assert _triple(out3) == (None, None, None) and out3["error"] is None


def test_both_feeds_raising_names_the_last_exception_and_never_a_silent_none_pair():
    out, calls = _read(RuntimeError("429"), KeyError("book"))
    assert _triple(out) == (None, None, None) and out["error"] == "KeyError"
    assert [c[0] for c in calls] == ["bbo", "book"]
    # one feed raising and the other answering EMPTY is an answer, not an error
    out2, _ = _read(RuntimeError("429"), HALTED_BOOK)
    assert _triple(out2) == (None, None, "MARKET_STATE_HALTED") and out2["error"] is None
    out3, _ = _read(HALTED_BBO, RuntimeError("429"))
    assert _triple(out3) == (None, None, "MARKET_STATE_HALTED") and out3["error"] is None
    # a client with neither feed: nothing raised, nothing named
    out4, _ = _read()
    assert out4 == {"bid": None, "ask": None, "state": None, "error": None}
    # a marketData that is not an object (a list, on both feeds) is no
    # answer, and the read says so by the payload's shape
    out5, calls5 = _read({"marketData": [{"bestBid": {"value": "0.30"}}]},
                         {"marketData": ["bids", "offers"]})
    assert _triple(out5) == (None, None, None) and out5["error"] == "marketData:list"
    assert [c[0] for c in calls5] == ["bbo", "book"]
    # a list on one feed and an answer on the other: the answer stands
    out6, _ = _read({"marketData": [1, 2]}, HALTED_BOOK)
    assert _triple(out6) == (None, None, "MARKET_STATE_HALTED") and out6["error"] is None
    # _bbo_quotes still swallows the pair into (None, None): that is the
    # contract slug_bid was proven against, and it is untouched
    assert pmus._bbo_quotes(_Client(_Markets(RuntimeError("429"), KeyError("b"))), "x") == (None, None)


def test_bbo_empty_and_book_quoted_is_the_books_quotes_with_the_books_state():
    book = {"marketData": {"bestBid": {"value": "0.4100", "currency": "USD"},
                           "bestAsk": {"value": "0.4300", "currency": "USD"},
                           "state": "MARKET_STATE_OPEN"}}
    out, calls = _read(HALTED_BBO, book)
    assert _triple(out) == (0.41, 0.43, "MARKET_STATE_OPEN") and out["error"] is None
    assert [c[0] for c in calls] == ["bbo", "book"]
    # a quoted bbo returns at once: the book is never asked
    out2, calls2 = _read(CLOSED_BBO, book)
    assert _triple(out2) == (pytest.approx(0.01), pytest.approx(0.20), "MARKET_STATE_CLOSED")
    assert calls2 == [("bbo", "s")]
    # both empty: the first feed's state stands
    out3, _ = _read(EXPIRED_BBO, HALTED_BOOK)
    assert out3["state"] == "MARKET_STATE_EXPIRED"
    # a quote is a quote only inside (0, 1), as _quote_px reads it
    out4, _ = _read({"marketData": {"bestBid": {"value": "0"}, "bestAsk": {"value": "1.0"},
                                    "state": "MARKET_STATE_OPEN"}})
    assert _triple(out4) == (None, None, "MARKET_STATE_OPEN")


def test_bbo_quotes_is_byte_identical_to_its_proven_shape_and_calls_nothing_new():
    src = inspect.getsource(pmus._bbo_quotes)
    assert src.count("fn(us_slug)") == 1
    assert "bbo_read" not in src and "state" not in src
    assert 'for meth in ("bbo", "book"):' in src
    assert inspect.signature(pmus.bbo_read).parameters.keys() == {"client", "us_slug"}
    assert "_quote_px(d, \"bestBid\", \"best_bid\", \"bid\")" in inspect.getsource(pmus.bbo_read)
