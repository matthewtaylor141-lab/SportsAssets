"""Kalshi settlement: instrumented, lag-free, and paced.

The probe read settle_stats {"kalshi": null} for days while finished games
sat on the public LIVE BOOK card. Root causes, each pinned here:
- fetch_results kept no counters, so whether it even ran was unanswerable
  from telemetry (the null). It now reports the same stats contract as the
  PMUS adapter (checked/priced/no_price/errors/first_error).
- only status == "settled" priced. The venue holds a finished market at
  determined/finalized — result already yes/no — before financial
  settlement completes, so realized P&L lagged the game by however long
  the payout took. The result field is authoritative once present.
- non-200 pages were silently skipped (a rate-limited sweep read exactly
  like "no results"), and pages ran at full speed. Errors are now counted
  with their first cause and pages are paced 0.3s apart.
fetch_results also keeps last_market_status so kalshi_open_snapshot can
flag finished-but-unresolved rows instead of presenting them as LIVE.
"""

import types

import pytest

from edge.ledger.service import Ledger
from edge.shadow.runner import kalshi_open_snapshot, settle_cycle
from edge.venues.kalshi import KalshiAdapter


class _Resp:
    def __init__(self, status_code=200, markets=None, text=""):
        self.status_code = status_code
        self._markets = markets or []
        self.text = text

    def json(self):
        return {"markets": self._markets}


def _adapter(responses):
    """KalshiAdapter whose session replays `responses`, one per GET."""
    a = KalshiAdapter()
    calls: list[dict] = []

    def _get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    a._sess = types.SimpleNamespace(get=_get)
    a._calls = calls
    return a


def _market(ticker, status, result=""):
    return {"ticker": ticker, "status": status, "result": result}


def test_determined_and_finalized_price_without_waiting_for_settled():
    a = _adapter([_Resp(markets=[
        _market("T-SET", "settled", "yes"),
        _market("T-DET", "determined", "no"),
        _market("T-FIN", "finalized", "yes"),
        _market("T-LIVE", "active"),
        _market("T-VOID", "settled", "void"),   # pays cost back: never guess
    ])])
    out = a.fetch_results(["T-SET", "T-DET", "T-FIN", "T-LIVE", "T-VOID"])
    assert out == {"T-SET": 1.0, "T-DET": 0.0, "T-FIN": 1.0}
    assert a.last_settle_stats["checked"] == 5
    assert a.last_settle_stats["priced"] == 3
    assert a.last_settle_stats["no_price"] == 2
    assert a.last_settle_stats["errors"] == 0
    assert a.last_settle_stats["by_status"] == {
        "settled": 2, "determined": 1, "finalized": 1, "active": 1}
    # Status map kept for the kalshi_open card's finished-game flag.
    assert a.last_market_status["T-LIVE"] == "active"
    assert a.last_market_status["T-DET"] == "determined"


def test_stats_are_always_set_and_errors_keep_their_first_cause():
    a = _adapter([_Resp(status_code=429, text="rate limited")])
    out = a.fetch_results(["T1", "T2"])
    assert out == {}
    assert a.last_settle_stats["errors"] == 1
    assert a.last_settle_stats["priced"] == 0
    assert a.last_settle_stats["no_price"] == 2
    assert "HTTP 429" in a.last_settle_stats["first_error"]


def test_pages_are_paced_a_third_of_a_second_apart(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("edge.venues.kalshi.time.sleep", sleeps.append)
    a = _adapter([_Resp(markets=[])])
    a.fetch_results([f"T{i}" for i in range(250)])
    assert len(a._calls) == 3            # 100 + 100 + 50
    assert sleeps == [0.3, 0.3]          # between pages, never before page 1


def test_settle_cycle_closes_kalshi_positions_and_realizes_pnl(tmp_path):
    led = Ledger(db_path=str(tmp_path / "ledger.sqlite3"))
    for uid, mkey in (("f1", "kalshi:T-WON"), ("f2", "kalshi:T-OPEN")):
        led.record_fill(fill_uid=uid, venue="kalshi", market_key=mkey,
                        side="BUY", qty=10, price=0.40, mode="LIVE_BETA")

    class _Kalshi:
        name = "kalshi"

        def __init__(self):
            self.asked = None

        def fetch_results(self, tickers):
            self.asked = sorted(tickers)
            return {"T-WON": 1.0}

    a = _Kalshi()
    assert settle_cycle([a], led) == 1
    # The prefix is stripped before the venue is asked: 'kalshi:T' vs 'T'
    # was one of the suspects, so it is pinned.
    assert a.asked == ["T-OPEN", "T-WON"]
    open_keys = {p["market_key"] for p in led.open_positions()}
    assert open_keys == {"kalshi:T-OPEN"}
    assert led.summary()["net_realized"] == pytest.approx(6.0)  # 10*(1-0.4)


def test_kalshi_open_snapshot_flags_finished_but_unresolved_rows(tmp_path):
    led = Ledger(db_path=str(tmp_path / "ledger.sqlite3"))
    led.record_fill(fill_uid="f1", venue="kalshi", market_key="kalshi:T-DONE",
                    side="BUY", qty=5, price=0.30, mode="LIVE_BETA")
    led.record_fill(fill_uid="f2", venue="kalshi", market_key="kalshi:T-LIVE",
                    side="BUY", qty=4, price=0.50, mode="LIVE_BETA")
    led.record_fill(fill_uid="f3", venue="kalshi", market_key="kalshi:T-PAPER",
                    side="BUY", qty=3, price=0.20, mode="PAPER")

    kalshi = types.SimpleNamespace(
        name="kalshi",
        last_market_status={"T-DONE": "closed", "T-LIVE": "active"})
    snap = kalshi_open_snapshot(led, [kalshi])
    rows = {r["ticker"]: r for r in snap["rows"]}
    assert set(rows) == {"T-DONE", "T-LIVE"}        # paper never publishes
    assert rows["T-DONE"]["venue_status"] == "closed"
    assert "venue_status" not in rows["T-LIVE"]
    assert snap["n"] == 2
    assert snap["cost"] == pytest.approx(5 * 0.30 + 4 * 0.50)


def test_kalshi_open_snapshot_survives_an_adapter_with_no_status_map(tmp_path):
    led = Ledger(db_path=str(tmp_path / "ledger.sqlite3"))
    led.record_fill(fill_uid="f1", venue="kalshi", market_key="kalshi:T1",
                    side="BUY", qty=1, price=0.10, mode="LIVE_BETA")
    snap = kalshi_open_snapshot(led, [types.SimpleNamespace(name="kalshi")])
    assert snap["n"] == 1
    assert "venue_status" not in snap["rows"][0]
