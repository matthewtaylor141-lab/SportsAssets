"""account_snapshot (funnel["kalshi_account"], desk contract 2026-08-22):
balance + per-ticker holdings with live marks, and — the part that
matters — FAIL CLOSED: a venue error must produce nulls, never a raise
and never a zeroed account presented as real."""

import time
from types import SimpleNamespace

import requests

from edge.shadow.runner import kalshi_account_funnel
from edge.venues.kalshi import KalshiAdapter


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Sess:
    """Answers balance / positions / resting-orders with live-shaped rows."""

    def get(self, url, params=None, headers=None, timeout=None):
        if "balance" in url:
            return _Resp({"balance": 12345})            # cents -> $123.45
        if "positions" in url:
            return _Resp({"market_positions": [
                {"ticker": "KXNBA-A", "position_fp": "10.00",
                 "market_exposure_dollars": "4.500000"},
                {"ticker": "KXNBA-B", "position_fp": "3.00",
                 "market_exposure": 120},               # cents dialect
                {"ticker": "KXNBA-FLAT", "position_fp": "0.00"},
            ]})
        return _Resp({"orders": [{"order_id": "o1"}, {"order_id": "o2"}]})


class _RaisingSess:
    def get(self, url, params=None, headers=None, timeout=None):
        raise requests.ConnectionError("venue down")


def _adapter(sess):
    ad = KalshiAdapter.__new__(KalshiAdapter)
    ad._sess = sess
    ad._auth_headers = lambda *a, **k: {}
    return ad


def test_account_snapshot_shape():
    ad = _adapter(_Sess())
    # Mark HELD tickers only, tolerating a failed book read as a null
    # mark — never a raise, never a zero presented as a price.
    books = {"KXNBA-A": SimpleNamespace(
        bids=[SimpleNamespace(price=0.41, qty=100.0)])}
    ad.get_book = lambda mid, tk: books.get(tk)

    out = ad.account_snapshot()
    assert out["balance_usd"] == 123.45
    assert out["resting"] == 2
    assert out["exposure_usd"] == 5.70
    assert time.time() - out["at"] < 5
    rows = {r["ticker"]: r for r in out["positions"]}
    assert set(rows) == {"KXNBA-A", "KXNBA-B"}, \
        "zero-contract rows are not holdings"
    assert rows["KXNBA-A"] == {"ticker": "KXNBA-A", "qty": 10,
                               "cost_usd": 4.50, "mark_bid": 0.41,
                               "value_usd": 4.10}
    assert rows["KXNBA-B"]["cost_usd"] == 1.20
    assert rows["KXNBA-B"]["mark_bid"] is None, "no book = unknown mark"
    assert rows["KXNBA-B"]["value_usd"] is None, "unknown, never zero"


def test_account_snapshot_fails_closed_on_venue_error():
    ad = _adapter(_RaisingSess())
    out = ad.account_snapshot()          # must not raise
    assert out["balance_usd"] is None
    assert out["exposure_usd"] is None
    assert out["positions"] == []
    assert out["resting"] == 0
    assert out["at"] > 0


def test_account_snapshot_caps_book_reads():
    class _Wide(_Sess):
        def get(self, url, params=None, headers=None, timeout=None):
            if "positions" in url:
                return _Resp({"market_positions": [
                    {"ticker": f"KX-{i}", "position_fp": "1.00",
                     "market_exposure_dollars": "0.500000"}
                    for i in range(60)]})
            return super().get(url, params=params, headers=headers,
                               timeout=timeout)

    ad = _adapter(_Wide())
    reads = []
    ad.get_book = lambda mid, tk: reads.append(tk)
    out = ad.account_snapshot(max_marks=40)
    assert len(reads) == 40, "marks are capped, holdings are not"
    assert len(out["positions"]) == 60


def test_kalshi_account_funnel_ttl_and_carry():
    calls = []

    class _Ad:
        name = "kalshi"

        def has_credentials(self):
            return True

        def account_snapshot(self):
            calls.append(1)
            return {"balance_usd": 9.0, "at": time.time(), "resting": 0,
                    "exposure_usd": None, "positions": []}

    cache: dict = {}
    a = kalshi_account_funnel([_Ad()], cache, ttl_s=120)
    b = kalshi_account_funnel([_Ad()], cache, ttl_s=120)
    assert a["balance_usd"] == 9.0
    assert b == a, "inside the TTL the cached copy rides every heartbeat"
    assert len(calls) == 1, "TTL holds — one venue refresh, not two"
    cache["at"] = time.time() - 300
    kalshi_account_funnel([_Ad()], cache, ttl_s=120)
    assert len(calls) == 2, "a stale cache refreshes"

    class _NoCreds(_Ad):
        def has_credentials(self):
            return False

    assert kalshi_account_funnel([_NoCreds()], {}, ttl_s=120) is None
