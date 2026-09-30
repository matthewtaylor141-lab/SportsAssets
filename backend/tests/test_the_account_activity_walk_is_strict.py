"""THE ACCOUNT-WIDE ACTIVITY WALK: EVERY TYPE, STRICT ENDS, A TRUTHFUL FLAG.

`bettor_funded_account.read_account_activity_sync` is the historical read
the discrepancy report reconciles executions against. It asks for EVERY type
(no `types` filter), keeps a type it does not know instead of dropping it,
places every row by a strict time, and says `complete` only when a row older
than the window was read or the venue stated eof.

Pure: the venue client is a stand-in at the SDK boundary and `paced_read` is
the identity. SYNTHETIC venue pages; nothing reaches a network or database.
"""

import pytest

from sportsassets import bettor_funded_account as FACCT

SINCE = 1790000000.0      # 2026-09-21T14:13:20Z
T1, T2, T3 = "2026-09-21T18:00:00Z", "2026-09-21T17:00:00Z", \
    "2026-09-21T16:00:00Z"
BEFORE = "2026-09-21T14:00:00Z"


class _Acts:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def activities(self, params=None):
        self.calls.append(dict(params or {}))
        if not self.pages:
            raise AssertionError("read past the last page")
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


class _Client:
    def __init__(self, pages):
        self.portfolio = _Acts(pages)


def _trade(tid, ts, slug="aec-mlb-a-b", own="ord-1", qty="5", exec_id=None):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"id": tid, "marketSlug": slug, "createTime": ts,
                      "qty": qty, "price": {"value": "0.55",
                                            "currency": "USD"},
                      "isAggressor": True,
                      "aggressorExecution": {"id": exec_id or "x-" + tid,
                                             "order": {"id": own}}}}


def _resolution(slug, ts):
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
            "positionResolution": {"marketSlug": slug, "updateTime": ts,
                                   "tradeId": "r-" + slug}}


def _deposit(ts):
    return {"type": "ACTIVITY_TYPE_ACCOUNT_DEPOSIT",
            "accountBalanceChange": {"transactions": [
                {"transactionId": "tx-1", "status": "COMPLETE",
                 "amount": {"value": "100.00", "currency": "USD"},
                 "createTime": ts}]}}


def _walk(pages, **kw):
    c = _Client(pages)
    got = FACCT.read_account_activity_sync(
        c, since_ts=SINCE, paced_read=lambda f, **k: f(), **kw)
    return got, c


def test_every_type_is_requested_and_the_walk_pages_to_the_window():
    """PAGINATION: the walk follows cursors, asks for no type filter, and
    ends on the first row older than the window -- complete, and saying so."""
    got, c = _walk([
        {"activities": [_trade("t1", T1), _deposit(T2)], "nextCursor": "c1"},
        {"activities": [_resolution("aec-mlb-a-b", T3),
                        _trade("t0", BEFORE)], "nextCursor": "c2"},
    ])
    assert got["ok"] is True and got["complete"] is True, got
    assert got["complete_by"] == "A_ROW_OLDER_THAN_THE_WINDOW_WAS_READ"
    assert got["pages"] == 2
    assert [r["type"] for r in got["rows"]] == [
        "ACTIVITY_TYPE_TRADE", "ACTIVITY_TYPE_ACCOUNT_DEPOSIT",
        "ACTIVITY_TYPE_POSITION_RESOLUTION"]
    assert got["by_type"] == {"ACTIVITY_TYPE_TRADE": 1,
                              "ACTIVITY_TYPE_ACCOUNT_DEPOSIT": 1,
                              "ACTIVITY_TYPE_POSITION_RESOLUTION": 1}
    # NO TYPE FILTER: the venue returns every type it has
    assert all("types" not in p for p in c.portfolio.calls)
    assert c.portfolio.calls[1]["cursor"] == "c1"
    assert all(p["sortOrder"] == "SORT_ORDER_DESCENDING"
               for p in c.portfolio.calls)
    trade = got["rows"][0]
    assert trade["own_order_id"] == "ord-1"
    assert trade["own_execution_id"] == "x-t1"
    assert trade["qty"] == 5.0 and trade["price"] == pytest.approx(0.55)
    # A BALANCE CHANGE KEEPS ITS IDS, NOT ITS AMOUNT
    dep = got["rows"][1]
    assert dep["transactions"] == [{"transaction_id": "tx-1",
                                    "status": "COMPLETE"}]
    assert "100.00" not in repr(dep)
    # THE RESOLUTION WAS PLACED BY ITS updateTime (the SDK's only time)
    assert got["rows"][2]["us_market_slug"] == "aec-mlb-a-b"


def test_the_walk_also_ends_on_a_stated_eof():
    got, _ = _walk([{"activities": [_trade("t1", T1)], "eof": True}])
    assert got["ok"] is True and got["complete"] is True
    assert got["complete_by"] == "THE_VENUE_STATED_EOF"
    got, _ = _walk([{"activities": [], "eof": True}])
    assert got["ok"] is True and got["rows"] == []


def test_an_unknown_type_is_reported_never_dropped():
    odd = {"type": "ACTIVITY_TYPE_SOMETHING_NEW", "timestamp": T2,
           "somethingNew": {"marketSlug": "aec-x", "value": 1}}
    got, _ = _walk([{"activities": [_trade("t1", T1), odd], "eof": True}])
    assert got["ok"] is True, got
    assert got["unknown_types"] == {"ACTIVITY_TYPE_SOMETHING_NEW": 1}
    row = [r for r in got["rows"] if not r["known"]]
    assert len(row) == 1
    assert row[0]["type"] == "ACTIVITY_TYPE_SOMETHING_NEW"
    # NAMED BY ITS KEYS: an unknown shape is not interpreted
    assert row[0]["keys"] == ["somethingNew", "timestamp", "type"]
    # ITS TIME MAY SIT ON ITS OWN BODY, and is still read strictly
    nested = {"type": "ACTIVITY_TYPE_OTHER", "other": {"createTime": T2}}
    got, _ = _walk([{"activities": [nested], "eof": True}])
    assert got["ok"] is True and got["unknown_types"] == {
        "ACTIVITY_TYPE_OTHER": 1}
    # AND AN UNKNOWN ROW THAT STATES NO TIME AT ALL CANNOT BE PLACED
    got, _ = _walk([{"activities": [{"type": "ACTIVITY_TYPE_OTHER",
                                     "other": {"x": 1}}], "eof": True}])
    assert got["ok"] is False
    assert got["refusal"] == FACCT.R_ACTIVITY_TIME_UNREADABLE


@pytest.mark.parametrize("bad", [
    "yesterday", True, 12, "2026-13-45T00:00:00Z", {"value": T1}])
def test_a_malformed_timestamp_refuses_the_walk(bad):
    row = _trade("t1", T1)
    row["timestamp"] = bad
    got, _ = _walk([{"activities": [row], "eof": True}])
    assert got["ok"] is False and got["complete"] is False, got
    assert got["refusal"] == FACCT.R_ACTIVITY_TIME_UNREADABLE
    assert "rows" not in got


def test_truncation_at_max_pages_is_a_prefix_not_the_window():
    pages = [{"activities": [_trade("t%d" % i,
                                    "2026-09-21T18:%02d:00Z" % (50 - i))],
              "nextCursor": "c%d" % i} for i in range(4)]
    got, c = _walk(pages, max_pages=3)
    assert got["ok"] is False and got["complete"] is False, got
    assert got["refusal"] == FACCT.R_ACTIVITY_WALK_INCOMPLETE
    assert got["pages"] == 3 and len(c.portfolio.calls) == 3
    assert got["rows_are_a_prefix_of_the_window"] is True
    assert [r["trade_id"] for r in got["rows"]] == ["t0", "t1", "t2"]


@pytest.mark.parametrize("pages,refusal", [
    ([{}], FACCT.R_ACTIVITY_RESPONSE_INCOMPLETE),
    ([{"activities": None, "eof": True}],
     FACCT.R_ACTIVITY_RESPONSE_INCOMPLETE),
    ([{"activities": [], "eof": False}], FACCT.R_PAGE_END_INCONSISTENT),
    ([{"activities": [], "eof": "true"}], FACCT.R_PAGE_END_MALFORMED),
    ([{"activities": []}], FACCT.R_PAGE_END_NOT_STATED),
    ([{"activities": [], "nextCursor": "c"},
      {"activities": [], "nextCursor": "c"}], FACCT.R_PAGE_END_INCONSISTENT),
    ([RuntimeError("boom")], FACCT.R_ACTIVITY_READ_RAISED),
    # A ROW WITH NO TYPE cannot be told from an execution
    ([{"activities": [{k: v for k, v in _trade("t", T1).items()
                       if k != "type"}], "eof": True}],
     FACCT.R_ACTIVITY_FIELD_MALFORMED),
    ([{"activities": ["x"], "eof": True}], FACCT.R_ACTIVITY_FIELD_MALFORMED),
    # NEWEST FIRST, checked
    ([{"activities": [_trade("t0", T3), _trade("t1", T1)], "eof": True}],
     FACCT.R_ACTIVITY_NOT_NEWEST_FIRST),
    # a trade naming no market, stating no quantity, or a damaged price
    ([{"activities": [_trade("t", T1, slug="")], "eof": True}],
     FACCT.R_ACTIVITY_FIELD_MALFORMED),
    ([{"activities": [_trade("t", T1, qty=None)], "eof": True}],
     FACCT.R_ACTIVITY_FIELD_MALFORMED),
    ([{"activities": [_trade("t", T1, qty="abc")], "eof": True}],
     FACCT.R_ACTIVITY_FIELD_MALFORMED),
    ([{"activities": [dict(_trade("t", T1), trade="x")], "eof": True}],
     FACCT.R_ACTIVITY_FIELD_MALFORMED),
    # a resolution naming no market
    ([{"activities": [_resolution("", T1)], "eof": True}],
     FACCT.R_ACTIVITY_FIELD_MALFORMED),
    # a balance change whose transaction time does not parse
    ([{"activities": [_deposit("soon")], "eof": True}],
     FACCT.R_ACTIVITY_TIME_UNREADABLE),
])
def test_a_walk_that_does_not_establish_the_window_refuses(pages, refusal):
    got, _ = _walk(pages)
    assert got["ok"] is False and got["complete"] is False, got
    assert got["refusal"] == refusal, got
    assert "rows" not in got


def test_the_own_trades_walk_is_unchanged_by_the_shared_time_reader():
    """`strict_activity_ts` gained optional arguments; its defaults are the
    own-trades walk's and read exactly as before."""
    assert FACCT.strict_activity_ts(
        {"trade": {"createTime": "2026-09-21T15:00:00"}}) == \
        pytest.approx(1790002800.0)
    assert FACCT.strict_activity_ts({"time": 1790002800000}) == \
        pytest.approx(1790002800.0)
    # a present top-level key that does not parse decides; no fall-through
    assert FACCT.strict_activity_ts(
        {"timestamp": True, "trade": {"createTime": T1}}) is None
    # and the resolution body is not searched by default
    assert FACCT.strict_activity_ts(
        {"positionResolution": {"createTime": T1}}) is None
