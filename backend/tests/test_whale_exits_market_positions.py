"""`market_positions`: the per-market venue read that is Phase 1's
position source (to-a-tee, owner order 2026-09-02: "I want us to match
everything ... mirror the whales to a tee").

The whole-book snapshot is partial on every probe, so the mirror could
never call a read fresh-and-complete and refused `snapshot_stale` on
every RN1 candidate. This read answers for ONE condition, both tokens,
and fails CLOSED to None on anything a reader could mistake for a size.
These tests pin: both tokens come back; a merged-out token is present
as 0.0, not absent; every unreadable shape is None and never a partial
dict; and the request is the one `_confirm_gone` already makes, pinned
by comparing the recorded params of the two calls.
"""
from __future__ import annotations

import math

import httpx
import pytest

from sportsassets import ratelimit
from sportsassets.workers import whale_exits as we

CID = "0xcond1"


class _Throttle:
    """A recording stand-in for the process-wide data-API throttle: the
    real one sleeps 1/data_api_max_rps per call, which turned this file
    into a ten-second wait for nothing. Counting waits pins the pacing
    behaviourally instead of by source text."""

    def __init__(self):
        self.waits = 0

    async def wait(self):
        self.waits += 1


@pytest.fixture(autouse=True)
def throttle(monkeypatch):
    t = _Throttle()
    monkeypatch.setattr(ratelimit, "_throttle", t)
    return t
LONG = "111"
OTHER = "222"
ADDR = "0xwhale"


class _Resp:
    def __init__(self, status: int = 200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _Http:
    """Records every GET; answers with a queued response or raises."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a


class _Pool:
    """Just enough pool for `_confirm_gone` to look up a condition id."""

    async def fetchval(self, sql, *args):
        return CID


def _rows(long_size="10", other_size="4", **overrides):
    rows = [
        {"asset": LONG, "conditionId": CID, "size": long_size,
         "avgPrice": "0.55"},
        {"asset": OTHER, "conditionId": CID, "size": other_size,
         "avgPrice": 0.47},
    ]
    for r in rows:
        r.update(overrides)
    return rows


class TestBothTokensComeBack:
    async def test_both_tokens_with_sizes_and_the_long_named(self):
        http = _Http(_Resp(200, _rows()))
        out = await we.market_positions(http, ADDR, CID, long_asset=LONG)
        assert out is not None
        assert out["by_asset"] == {LONG: 10.0, OTHER: 4.0}
        assert out["long"] == 10.0
        assert out["complete"] is True
        assert isinstance(out["ts"], float) and out["ts"] > 0
        assert set(out) == {"by_asset", "avg_price", "long", "complete",
                            "ts"}

    async def test_the_venue_avg_price_rides_along_for_m21(self):
        http = _Http(_Resp(200, _rows()))
        out = await we.market_positions(http, ADDR, CID)
        assert out["avg_price"] == {LONG: 0.55, OTHER: 0.47}

    async def test_an_unreadable_avg_price_is_none_not_a_refusal(self):
        # avgPrice feeds a fidelity metric only, never a money-path
        # input, so a bad one must not cost the size read
        rows = _rows()
        rows[0]["avgPrice"] = "n/a"
        del rows[1]["avgPrice"]
        out = await we.market_positions(http=_Http(_Resp(200, rows)),
                                        address=ADDR, condition_id=CID)
        assert out["by_asset"] == {LONG: 10.0, OTHER: 4.0}
        assert out["avg_price"] == {LONG: None, OTHER: None}

    async def test_no_long_asset_means_long_is_none(self):
        http = _Http(_Resp(200, _rows()))
        out = await we.market_positions(http, ADDR, CID)
        assert out["long"] is None
        assert out["by_asset"] == {LONG: 10.0, OTHER: 4.0}

    async def test_a_size_zero_token_is_present_as_zero_point_zero(self):
        # sizeThreshold=0 is the whole point: a token he merged down to
        # nothing arrives as a row of size 0, and the caller must see
        # 0.0 rather than an absence it would have to guess about
        http = _Http(_Resp(200, _rows(long_size=0, other_size="7.5")))
        out = await we.market_positions(http, ADDR, CID, long_asset=LONG)
        assert out["by_asset"] == {LONG: 0.0, OTHER: 7.5}
        assert out["long"] == 0.0
        assert LONG in out["by_asset"]

    async def test_a_long_asset_absent_from_a_complete_answer_reads_zero(self):
        # the same reading _confirm_gone makes: the venue answered for
        # this market and the leg is not among its rows
        rows = [_rows()[1]]
        out = await we.market_positions(_Http(_Resp(200, rows)), ADDR, CID,
                                        long_asset=LONG)
        assert out["long"] == 0.0
        assert out["by_asset"] == {OTHER: 4.0}

    async def test_the_data_wrapper_confirm_gone_accepts_is_accepted(self):
        http = _Http(_Resp(200, {"data": _rows()}))
        out = await we.market_positions(http, ADDR, CID)
        assert out["by_asset"] == {LONG: 10.0, OTHER: 4.0}

    async def test_the_default_keyword_is_fail_closed_none(self):
        import inspect
        sig = inspect.signature(we.market_positions)
        assert sig.parameters["long_asset"].default is None
        assert sig.parameters["long_asset"].kind is \
            inspect.Parameter.KEYWORD_ONLY


class TestEveryUnreadableShapeIsNone:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
    async def test_a_non_200_is_none(self, status):
        http = _Http(_Resp(status, _rows()))
        assert await we.market_positions(http, ADDR, CID,
                                         long_asset=LONG) is None

    async def test_a_timeout_is_none(self):
        http = _Http(httpx.ReadTimeout("slow"))
        assert await we.market_positions(http, ADDR, CID) is None

    async def test_a_transport_error_is_none(self):
        http = _Http(httpx.ConnectError("refused"))
        assert await we.market_positions(http, ADDR, CID) is None

    @pytest.mark.parametrize("body", [
        None, "oops", 42, {"error": "nope"}, {"data": "notalist"},
        {"data": {"asset": LONG, "size": "1"}},
    ])
    async def test_a_body_that_is_not_a_list_is_none(self, body):
        http = _Http(_Resp(200, body))
        assert await we.market_positions(http, ADDR, CID) is None

    async def test_a_list_holding_a_non_dict_is_none(self):
        http = _Http(_Resp(200, [_rows()[0], "junk"]))
        assert await we.market_positions(http, ADDR, CID) is None

    async def test_an_empty_list_is_none_not_an_empty_book(self):
        # the transient empty 200 EmptyPositions documents at the top of
        # the module; under sizeThreshold=0 a merged-out market still
        # carries size-0 rows, so empty is a failure mode here too
        assert await we.market_positions(_Http(_Resp(200, [])),
                                         ADDR, CID) is None
        assert await we.market_positions(_Http(_Resp(200, {"data": []})),
                                         ADDR, CID) is None

    @pytest.mark.parametrize("bad", [
        "-1", -0.5, float("nan"), "nan", float("inf"), "inf", "-inf",
        None, "n/a", True, False, [1],
    ])
    async def test_a_size_that_is_not_a_finite_non_negative_number_is_none(
            self, bad):
        rows = _rows()
        rows[1]["size"] = bad
        assert await we.market_positions(_Http(_Resp(200, rows)),
                                         ADDR, CID, long_asset=LONG) is None

    async def test_a_missing_size_key_is_none(self):
        rows = _rows()
        del rows[0]["size"]
        assert await we.market_positions(_Http(_Resp(200, rows)),
                                         ADDR, CID) is None

    async def test_a_row_from_another_condition_is_refused_as_unfiltered(self):
        # _confirm_gone's rule: if the venue ever ignored the `market`
        # parameter it would hand back the unfiltered first page
        rows = _rows()
        rows[1]["conditionId"] = "0xsomewhere_else"
        assert await we.market_positions(_Http(_Resp(200, rows)),
                                         ADDR, CID) is None

    async def test_a_row_without_an_asset_id_is_none(self):
        rows = _rows()
        rows[0]["asset"] = ""
        assert await we.market_positions(_Http(_Resp(200, rows)),
                                         ADDR, CID) is None

    async def test_two_rows_for_one_asset_is_none(self):
        rows = _rows()
        rows[1]["asset"] = LONG
        assert await we.market_positions(_Http(_Resp(200, rows)),
                                         ADDR, CID) is None

    async def test_never_a_partial_dict(self):
        # the first row is fine and the second is poison: the answer is
        # None outright, not a dict carrying the good row
        rows = _rows(other_size=float("nan"))
        out = await we.market_positions(_Http(_Resp(200, rows)), ADDR, CID,
                                        long_asset=LONG)
        assert out is None
        assert not isinstance(out, dict)

    async def test_one_read_per_call(self):
        http = _Http(_Resp(200, _rows()))
        await we.market_positions(http, ADDR, CID)
        assert len(http.calls) == 1
        http = _Http(_Resp(503, None))
        await we.market_positions(http, ADDR, CID)
        assert len(http.calls) == 1, "a refusal does not retry"


class TestItIsConfirmGonesRequest:
    async def test_the_request_params_match_confirm_gone_exactly(self):
        # pinned by RECORDING both calls, not by reading the source:
        # user / market / sizeThreshold / limit, same url, same values
        mine = _Http(_Resp(200, _rows()))
        await we.market_positions(mine, ADDR, CID, long_asset=LONG)
        theirs = _Http(_Resp(200, _rows()))
        await we._confirm_gone(theirs, _Pool(), ADDR, LONG)
        assert mine.calls == theirs.calls
        url, params = mine.calls[0]
        assert url == "/positions"
        assert params["user"] == ADDR
        assert params["market"] == CID
        assert params["sizeThreshold"] == 0
        assert set(params) == {"user", "market", "limit", "sizeThreshold"}

    def test_confirm_gone_is_byte_identical_to_its_pinned_shape(self):
        # the existing caller's behaviour must not move: its own params
        # line still reads exactly as the day it was written
        import inspect
        src = inspect.getsource(we._confirm_gone)
        assert '"user": address, "market": cid, "limit": 100,' in src
        assert '"sizeThreshold": 0})' in src
        assert "venue_pace" not in src

    def test_it_waits_on_the_data_api_budget_not_the_venue_pacer(self):
        # the data API has its own process-wide rps ceiling
        # (config.data_api_max_rps); venue_pace bounds the trading
        # venue's client, which this read never touches
        import inspect
        src = inspect.getsource(we.market_positions)
        assert "data_api_throttle().wait()" in src
        assert "venue_pace" not in src.split('"""')[2]

    async def test_exactly_one_throttled_wait_precedes_the_one_get(
            self, throttle):
        # one paced read per call: the budget is charged once, before
        # the GET, and a refusal charges it once too (no retry loop)
        http = _Http(_Resp(200, _rows()))
        await we.market_positions(http, ADDR, CID)
        assert throttle.waits == 1 and len(http.calls) == 1
        http = _Http(_Resp(503, None))
        await we.market_positions(http, ADDR, CID)
        assert throttle.waits == 2 and len(http.calls) == 1

    async def test_a_throttle_that_cannot_be_built_is_none(self, monkeypatch):
        # settings unreadable -> data_api_throttle() raises -> fail closed
        def boom():
            raise RuntimeError("no settings")
        monkeypatch.setattr(ratelimit, "data_api_throttle", boom)
        http = _Http(_Resp(200, _rows()))
        assert await we.market_positions(http, ADDR, CID) is None
        assert http.calls == [], "no read without a budget slot"

    async def test_a_size_read_is_never_a_guess(self):
        # a NaN the venue could hand back survives float(); the helper
        # underneath refuses it and every other non-holding value
        assert we._finite_size(float("nan")) is None
        assert we._finite_size("1e3") == 1000.0
        assert we._finite_size(0) == 0.0
        assert we._finite_size(True) is None
        assert we._finite_size(-0.0) == 0.0
        assert math.isfinite(we._finite_size("2.5"))
