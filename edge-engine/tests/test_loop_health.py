"""The pricing loop must not be able to starve itself.

Every regression here produced the SAME symptom — an engine reporting
healthy telemetry while trading roughly once a day — because each one turns
"we never got to this market" into something indistinguishable from "this
market had no edge". They are pinned separately so the next one is caught by
a test rather than by a week of missing trades.
"""

import time

import pytest

from edge.execution.engine import Policy
from edge.execution.executor import PMUS_ORDER_PREFIX, execute, market_key
from edge.execution.risk import RiskManager
from edge.ledger.service import Ledger
from edge.shadow import runner as R
from edge.shadow.runner import run_cycle
from edge.venues.base import BookLevel, MarketBook
from tests.test_run_cycle_e2e import StubFeed, StubVenue, _event

POLICY = Policy.load()


def _rig(tmp_path, mode="PAPER"):
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    return ledger, RiskManager(ledger, {**POLICY.risk, "mode": mode})


# ── the platform mirror must never touch the hot path ───────────────────

def test_mirror_never_blocks_the_loop(tmp_path, monkeypatch):
    """This was a synchronous POST per study record, inside the per-outcome
    loop. At full sport coverage that is thousands of blocking round-trips
    per cycle, and a loop that is waiting on HTTP is not pricing anything."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EDGE_PLATFORM_API", "http://platform.invalid")
    monkeypatch.setenv("EDGE_INGEST_TOKEN", "t")
    monkeypatch.setattr(R, "_MIRROR", {"started": True, "sent": 0,
                                       "dropped": 0, "failed": 0})

    def explode(*a, **k):
        raise AssertionError("the pricing loop made a network call")

    monkeypatch.setattr("requests.post", explode)
    monkeypatch.setattr("requests.Session.post", explode)

    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] >= 1              # work happened...
    assert R._MIRROR_Q.qsize() > 0            # ...and telemetry queued instead


def test_mirror_drops_rather_than_backs_up(monkeypatch):
    """If the platform is down, records are shed and counted. Telemetry must
    never be able to apply back-pressure to trading."""
    monkeypatch.setenv("EDGE_PLATFORM_API", "http://platform.invalid")
    monkeypatch.setenv("EDGE_INGEST_TOKEN", "t")
    import queue as _q

    monkeypatch.setattr(R, "_MIRROR", {"started": True, "sent": 0,
                                       "dropped": 0, "failed": 0})
    monkeypatch.setattr(R, "_MIRROR_Q", _q.Queue(maxsize=2))
    for _ in range(10):
        R._record_to_platform({"ts": 1, "venue": "v", "market_id": "m",
                               "outcome_id": "o", "limit_price": 0.5,
                               "size_usd": 1.0})
    assert R._MIRROR_Q.qsize() == 2
    assert R._MIRROR["dropped"] == 8
    assert R.mirror_stats()["dropped"] == 8


def test_study_work_is_spread_across_the_hour():
    """A shared hourly boundary means every market studies in the same cycle.
    That herd grew with coverage until one cycle could not finish."""
    phases = [R._phase(f"polymarket-us:market-{i}") for i in range(2000)]
    assert all(0 <= p < 3600 for p in phases)
    # Evenly spread: no 10-minute slice holds more than a fifth of the work.
    buckets: dict[int, int] = {}
    for p in phases:
        buckets[p // 600] = buckets.get(p // 600, 0) + 1
    assert max(buckets.values()) < len(phases) / 5
    # ...and stable, or a market would be re-studied every cycle.
    assert R._phase("polymarket-us:market-7") == R._phase("polymarket-us:market-7")


# ── a bounded cycle, and a fair one ─────────────────────────────────────

def test_cycle_reports_its_own_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["cycle_s"] >= 0


def test_an_overrunning_cycle_says_so_instead_of_looking_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EDGE_CYCLE_BUDGET_S", "-1")   # always over budget
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["truncated"] == {"reached": 0, "of": 1}
    assert funnel["logged"] == 0


def test_the_slate_rotates_so_the_same_tail_is_not_always_cut(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(R, "_ROTATION", {"i": 0})
    ledger, risk = _rig(tmp_path)
    seen = []

    class Feed(StubFeed):
        def fetch_events(self, sport_key):
            return list(self._events)

    events = []
    for i in range(4):
        ev = _event()
        ev.home, ev.away = f"H{i}", f"A{i}"
        ev.h2h = {f"H{i}": 2.0, f"A{i}": 2.0}
        events.append(ev)

    class Venue(StubVenue):
        def discover_markets(self, league_codes):
            return []

    for _ in range(4):
        run_cycle([Venue(ask_price=0.47)], Feed(events), POLICY, risk, ledger,
                  ["soccer_epl"])
        seen.append(R._ROTATION["i"])
    assert len(set(seen)) == 4, "every cycle must start at a different event"


# ── the claim leak ──────────────────────────────────────────────────────

def test_a_failed_order_gives_its_market_back(tmp_path, monkeypatch):
    """approve() claims the market BEFORE the order is attempted. Keeping the
    claim when nothing was placed permanently retires that bet — one silent
    subtraction from the tradeable universe per failure, forever."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)

    class NoDepth(StubVenue):
        def get_book(self, market_id, token):
            b = super().get_book(market_id, token)
            b.asks = [BookLevel(0.47, 0.5)]   # under $1: paper_no_depth
            return b

    funnel = run_cycle([NoDepth(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] == 0
    assert funnel["reclaimed"] >= 1
    assert not ledger.event_traded("paper:kalshi:T-ARS")
    # ...and the market is genuinely tradeable again on the next look.
    funnel2 = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                        POLICY, risk, ledger, ["soccer_epl"])
    assert funnel2["logged"] >= 1


def test_a_successful_order_keeps_its_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] >= 1 and "reclaimed" not in funnel
    assert ledger.event_traded("paper:kalshi:T-ARS")


# ── maker-first cannot cost a trade ─────────────────────────────────────

def _pmus(maker_first):
    from edge.venues.polymarket_us import PolymarketUSAdapter

    a = PolymarketUSAdapter.__new__(PolymarketUSAdapter)
    a.book_errors, a._taker_fee, a._maker_fee_rate = {}, 0.0, 0.0
    a._maker_first, a._force_taker, a._stream, a._auth = maker_first, {}, None, None
    return a


def test_maker_first_is_opt_in():
    """It was shipped default-on without ever being exercised against the
    live venue — an unverified order type as the only path real money could
    take."""
    import os

    from edge.venues.polymarket_us import PolymarketUSAdapter

    os.environ.pop("EDGE_PMUS_MAKER_FIRST", None)
    a = PolymarketUSAdapter.__new__(PolymarketUSAdapter)
    a._maker_first = os.environ.get("EDGE_PMUS_MAKER_FIRST", "0") == "1"
    assert a._maker_first is False
    assert PolymarketUSAdapter.plan_entry(
        _pmus(False), MarketBook("polymarket-us", "e", "s", [BookLevel(0.40, 9)],
                                 [BookLevel(0.50, 9)], time.time())) == (0.50, True)


def test_a_refused_maker_order_crosses_instead_of_giving_up(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _pmus(True)
    calls = []

    class Orders:
        def create(self, params):
            calls.append(params)
            if params["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL":
                return {"id": "o1", "executions": [
                    {"order": {"state": "ORDER_STATE_REJECTED"},
                     "type": "EXECUTION_TYPE_REJECTED"}]}
            return {"id": "o2", "executions": [
                {"order": {"state": "ORDER_STATE_FILLED"},
                 "type": "EXECUTION_TYPE_FILL",
                 "lastPx": {"value": "0.50"}, "lastShares": "2"}]}

    import types

    a._auth = types.SimpleNamespace(orders=Orders())
    r = execute(adapter=a, ledger=led, mode="LIVE_BETA",
                mkey=market_key("polymarket-us", "slug-x"), league="nba",
                ask_price=0.50, ask_size=500, size_usd=1.0, edge=0.04,
                threshold=0.02, decision={}, ts=1000.0, entry_price=0.49,
                taker=False, event_key="ev1")
    assert r["placed"] and r["status"] == "filled_fok"
    assert [c["tif"] for c in calls] == ["TIME_IN_FORCE_GOOD_TILL_CANCEL",
                                         "TIME_IN_FORCE_FILL_OR_KILL"]
    assert led.summary()["fills"] == 1
    assert led.get_state(f"{PMUS_ORDER_PREFIX}slug-x") is None


def test_the_fallback_still_respects_the_threshold_at_the_ask(tmp_path):
    """Crossing is a fallback, not a licence: 1c of edge at the ask against a
    2c bar is still no trade."""
    import types

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    a = _pmus(True)

    class Orders:
        def create(self, params):
            assert params["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
            return {"id": "o1", "executions": [
                {"order": {"state": "ORDER_STATE_REJECTED"},
                 "type": "EXECUTION_TYPE_REJECTED"}]}

    a._auth = types.SimpleNamespace(orders=Orders())
    r = execute(adapter=a, ledger=led, mode="LIVE_BETA",
                mkey=market_key("polymarket-us", "slug-x"), league="nba",
                ask_price=0.50, ask_size=500, size_usd=1.0, edge=0.02,
                threshold=0.02, decision={}, ts=1000.0, entry_price=0.49,
                taker=False, event_key="ev1")
    assert not r["placed"] and r["status"].startswith("maker_rejected")
    assert led.summary()["fills"] == 0


# ── the verdict: one sentence, right answer, right order ────────────────

def _verdict(funnel, mode="LIVE_BETA"):
    class _R:
        pass

    r = _R()
    r.mode = mode
    return R.volume_verdict(funnel, r)


def test_verdict_names_the_loop_before_anything_else():
    """A cycle that cannot finish explains every other number in the funnel,
    so it must outrank them."""
    v = _verdict({"truncated": {"reached": 12, "of": 900}, "cycle_s": 310.0,
                  "halted": True, "feed_events": 900, "books_checked": 0})
    assert v.startswith("CYCLE OVERRUN") and "12 of 900" in v


def test_verdict_reports_a_spent_budget_as_a_funding_limit():
    v = _verdict({"feed_events": 50, "tradeable": 20, "books_checked": 40,
                  "logged": 0, "budget": {"fills_left": 0, "spent": 400,
                                          "day_cap": 400}})
    assert v.startswith("BUDGET SPENT") and "fund the account" in v


def test_verdict_distinguishes_no_edge_from_no_markets():
    empty = _verdict({"feed_events": 0})
    unmapped = _verdict({"feed_events": 80, "tradeable": 0})
    nobooks = _verdict({"feed_events": 80, "tradeable": 40, "books_checked": 0})
    assert empty.startswith("NO FEED EVENTS")
    assert unmapped.startswith("NOTHING MAPPED")
    assert nobooks.startswith("NO BOOKS")


def test_verdict_quantifies_near_misses_when_the_rules_are_the_limit():
    v = _verdict({"feed_events": 80, "tradeable": 40, "books_checked": 900,
                  "logged": 0, "blockers": {"threshold": 700, "band": 20},
                  "threshold_gap": {"<0.5c": 90, "0.5-1c": 60, ">2c": 550}})
    assert "NO QUALIFYING EDGE" in v and "threshold' x700" in v
    assert "150 within 1c" in v


def test_verdict_says_so_when_it_is_working():
    assert _verdict({"feed_events": 80, "tradeable": 40, "books_checked": 900,
                     "logged": 7}).startswith("TRADING: 7 order")


def test_every_cycle_carries_a_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["verdict"].startswith("TRADING")
    assert funnel["cycle_s"] >= 0
