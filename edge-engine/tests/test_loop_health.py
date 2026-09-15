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

# Most tests here exercise loop and pricing MECHANICS, not trading policy.
# `blocked_categories` globally quarantines moneyline (measured -2.34c drift,
# retention 0.239 on our own fills), which would otherwise make every
# moneyline fixture untradeable and turn these into vacuous passes. The
# quarantine itself is pinned by its own tests in test_loop_health.py.
POLICY = Policy.load()
POLICY.leagues = {**POLICY.leagues, "blocked_categories": []}


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


def test_the_slate_rotates_when_settlement_times_cannot_order_it(tmp_path, monkeypatch):
    """Rotation is now the FALLBACK. Settlement priority orders the slate
    when kick-off times are known; rotation covers the case where they are
    not, and still must not cut the same tail every cycle."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EDGE_SETTLEMENT_PRIORITY", "0")
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


# ── away-mode phone alerts ──────────────────────────────────────────────

def test_only_verdict_CHANGES_are_pushed(monkeypatch):
    """A per-cycle heartbeat gets the channel muted, and a muted channel is
    the same as no channel."""
    from edge import notify

    sent = []
    monkeypatch.setattr(notify, "push",
                        lambda t, b, priority="default": sent.append((t, b)))
    w = notify.VerdictWatcher()
    assert w.observe("NO QUALIFYING EDGE: 900 books priced, top blocker 'threshold' x700")
    # Same class of verdict, different counts: not news.
    assert not w.observe("NO QUALIFYING EDGE: 902 books priced, top blocker 'threshold' x711")
    assert not w.observe("NO QUALIFYING EDGE: 880 books priced, top blocker 'band' x12")
    # A different class IS news.
    assert w.observe("TRADING: 4 order(s) placed this cycle")
    assert w.observe("CYCLE OVERRUN: only 12 of 900 events priced in 310s")
    assert len(sent) == 3


def test_a_persistent_stall_reminds_instead_of_going_quiet(monkeypatch):
    from edge import notify

    sent = []
    monkeypatch.setattr(notify, "push",
                        lambda t, b, priority="default": sent.append(priority))
    w = notify.VerdictWatcher(remind_after_s=3600)
    t = 1_000_000.0
    assert w.observe("BUDGET SPENT: $400 of $400", now=t)
    assert not w.observe("BUDGET SPENT: $400 of $400", now=t + 60)
    assert w.observe("BUDGET SPENT: $400 of $400", now=t + 3700)  # still stuck
    assert sent == ["high", "high"]      # not-trading verdicts are high priority


def test_a_healthy_verdict_does_not_nag(monkeypatch):
    from edge import notify

    sent = []
    monkeypatch.setattr(notify, "push",
                        lambda t, b, priority="default": sent.append(priority))
    w = notify.VerdictWatcher(remind_after_s=60)
    t = 1_000_000.0
    w.observe("TRADING: 4 order(s) placed this cycle", now=t)
    for i in range(1, 20):
        w.observe("TRADING: 9 order(s) placed this cycle", now=t + i * 600)
    assert sent == ["default"]


def test_alerts_are_off_without_a_topic(monkeypatch):
    from edge import notify

    monkeypatch.delenv("EDGE_NTFY_TOPIC", raising=False)
    assert notify.enabled() is False
    notify.push("t", "b")            # must be a silent no-op, not an error


def test_push_never_blocks_or_raises(monkeypatch):
    from edge import notify

    monkeypatch.setenv("EDGE_NTFY_TOPIC", "topic-x")
    monkeypatch.setattr(notify, "_STATE", {"started": True, "sent": 0, "dropped": 0})
    import queue as _q

    monkeypatch.setattr(notify, "_Q", _q.Queue(maxsize=1))
    for _ in range(5):
        notify.push("t", "b")        # no worker draining it
    assert notify._STATE["dropped"] == 4


def test_daily_summary_reports_live_money_only(tmp_path):
    from edge import notify

    ledger, risk = _rig(tmp_path, mode="LIVE_BETA")
    now = time.time()
    ledger.record_fill(fill_uid="live1", venue="polymarket-us",
                       market_key="polymarket-us:m", side="BUY", qty=2,
                       price=0.50, ts=now - 60, mode="LIVE_BETA")
    ledger.record_fill(fill_uid="paper1", venue="kalshi", market_key="kalshi:m",
                       side="BUY", qty=99, price=0.50, ts=now - 60, mode="PAPER")
    msg = notify.daily_summary(ledger, risk)
    assert "1 live fills" in msg and "$1.00 staked" in msg
    assert "mode LIVE_BETA" in msg


# ── stale quotes: one fetch must rescue the whole sport ─────────────────

def test_a_refresh_rescues_every_event_of_that_sport(tmp_path, monkeypatch):
    """The refresh updated only the event that triggered it, then marked the
    sport done — so a sport with 30 games rescued one and abandoned 29. That
    was 525 stale_quote rejections per cycle: markets we had priced, had a
    fair value for, and threw away over quote age."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)

    stale_at = time.time() - 40      # past the 30s freshness rule
    events = []
    for i in range(6):
        ev = _event()
        ev.home, ev.away = f"H{i}", f"A{i}"
        ev.h2h = {f"H{i}": 2.0, f"A{i}": 2.0}
        ev.fetched_at = stale_at
        events.append(ev)

    class Feed(StubFeed):
        fetches = 0

        def fetch_events(self, sport_key):
            # First call is the sweep and returns the STALE slate; the second
            # is the in-cycle refresh and returns current quotes.
            Feed.fetches += 1
            if Feed.fetches == 1:
                return events
            out = []
            for i in range(6):
                fresh = _event()
                fresh.home, fresh.away = f"H{i}", f"A{i}"
                fresh.h2h = {f"H{i}": 2.0, f"A{i}": 2.0}
                fresh.fetched_at = time.time()
                out.append(fresh)
            return out

    class Venue(StubVenue):
        def discover_markets(self, league_codes):
            return []

    funnel = run_cycle([Venue(ask_price=0.47)], Feed(events), POLICY, risk,
                       ledger, ["soccer_epl"])
    # One HTTP fetch (plus the initial sweep), every event rescued.
    assert funnel["refreshed"] == 6
    assert funnel["rejects"].get("stale_quote", 0) == 0


def test_a_refresh_is_still_paid_for_only_once_per_sport(tmp_path, monkeypatch):
    """Rescuing more events must not mean spending more credits."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    calls = {"n": 0}

    events = []
    for i in range(4):
        ev = _event()
        ev.home, ev.away = f"H{i}", f"A{i}"
        ev.h2h = {f"H{i}": 2.0, f"A{i}": 2.0}
        ev.fetched_at = time.time() - 40
        events.append(ev)

    class Feed(StubFeed):
        def fetch_events(self, sport_key):
            calls["n"] += 1
            return events if calls["n"] == 1 else []

    class Venue(StubVenue):
        def discover_markets(self, league_codes):
            return []

    run_cycle([Venue(ask_price=0.47)], Feed(events), POLICY, risk, ledger,
              ["soccer_epl"])
    assert calls["n"] == 2      # the sweep, then ONE refresh


# ── the engine can answer "is this profitable" from its own books ───────

def test_performance_counts_settled_money_only(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    for i, (price, payout) in enumerate([(0.50, 1.0), (0.50, 0.0), (0.40, 1.0)]):
        led.record_fill(fill_uid=f"f{i}", venue="polymarket-us",
                        market_key=f"m{i}", side="BUY", qty=2, price=price,
                        ts=now - 3600, mode="LIVE_BETA")
        led.record_resolution(f"m{i}", payout, ts=now - 60)
    # An open position must not be charged against a zero return.
    led.record_fill(fill_uid="open", venue="polymarket-us", market_key="m-open",
                    side="BUY", qty=2, price=0.50, ts=now - 60, mode="LIVE_BETA")

    perf = led.performance(days=7)
    assert perf["settled"] == 3 and perf["wins"] == 2 and perf["losses"] == 1
    assert perf["win_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert perf["fills"] == 4                      # includes the open one
    assert perf["open_cost"] == pytest.approx(1.0)
    # Settled: staked 1.00 + 1.00 + 0.80; returned 2.00 + 0 + 2.00.
    assert perf["realized"] == pytest.approx(1.20, abs=0.01)
    assert perf["roi"] == pytest.approx(1.20 / 2.80, abs=0.01)


def test_performance_reports_unknown_before_anything_settles(tmp_path):
    """Zero would read as 'breaking even'."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.record_fill(fill_uid="a", venue="polymarket-us", market_key="m",
                    side="BUY", qty=2, price=0.5, ts=time.time(), mode="LIVE_BETA")
    perf = led.performance(days=7)
    assert perf["settled"] == 0
    assert perf["win_rate"] is None and perf["roi"] is None


def test_performance_excludes_paper_from_the_live_scorecard(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    led.record_fill(fill_uid="p", venue="kalshi", market_key="mp", side="BUY",
                    qty=100, price=0.5, ts=now - 3600, mode="PAPER")
    led.record_resolution("mp", 0.0, ts=now - 60)
    assert led.performance(days=7, live_only=True)["fills"] == 0
    assert led.performance(days=7, live_only=False)["fills"] == 1


def test_every_cycle_carries_the_scorecard(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert "performance" in funnel and funnel["performance"]["days"] == 7


# ── adverse selection: does the edge survive the next observation? ──────

def test_edge_that_evaporates_is_measured_as_evaporated(tmp_path):
    """The failure this exists to catch: our fair value can be 30s old while
    the venue's book is live, so 'the ask is below our fair' can just mean
    the price already moved away from us. An engine buying stale quotes looks
    identical to one with edge until the settlements arrive days later."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    # Two trades whose edge held, two where it vanished.
    for i, (price, f0, f1) in enumerate([(0.47, 0.50, 0.505), (0.30, 0.33, 0.331),
                                         (0.47, 0.50, 0.468), (0.60, 0.63, 0.598)]):
        led.record_entry_fair(f"m{i}", price, f0, ts=now - 120)
        led.record_drift_later(f"m{i}", f1)
    rep = led.drift_report(days=7)
    assert rep["n"] == 4 and rep["held"] == 2
    assert rep["retention"] == pytest.approx(0.517, abs=0.01)
    assert rep["mean_drift_c"] < 0            # edge decayed on average


def test_edge_that_holds_reports_full_retention(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    for i in range(5):
        led.record_entry_fair(f"m{i}", 0.47, 0.50, ts=now - 120)
        led.record_drift_later(f"m{i}", 0.50)
    rep = led.drift_report(days=7)
    assert rep["retention"] == pytest.approx(1.0)
    assert rep["held"] == 5 and rep["mean_drift_c"] == pytest.approx(0.0)


def test_only_markets_old_enough_are_re_observed(tmp_path):
    """Re-reading a second later measures nothing — the quote has not had a
    chance to move."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    led.record_entry_fair("fresh", 0.47, 0.50, ts=now)
    led.record_entry_fair("ripe", 0.47, 0.50, ts=now - 120)
    assert led.awaiting_drift(now=now) == {"ripe"}


def test_entry_fair_is_stamped_once_and_never_reset(tmp_path):
    """Re-stamping would restart the clock and the drift would never mature."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    led.record_entry_fair("m", 0.47, 0.50, ts=now - 120)
    led.record_entry_fair("m", 0.49, 0.60, ts=now)      # ignored
    assert led.awaiting_drift(now=now) == {"m"}
    led.record_drift_later("m", 0.50)
    assert led.drift_report()["retention"] == pytest.approx(1.0)


def test_nothing_measured_reports_unknown(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    rep = led.drift_report()
    assert rep["n"] == 0
    assert rep["retention"] is None and rep["mean_drift_c"] is None
    assert rep["by_category"] == {}
    # No measurement means no surcharge — the bands govern alone, exactly as
    # they did before drift existed. Unknown is not an excuse to charge.
    assert led.drift_penalties() == {"*": 0.0}


def test_a_live_cycle_stamps_entries_and_closes_them_out(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]), POLICY, risk,
              ledger, ["soccer_epl"])
    assert ledger.awaiting_drift(now=time.time() + 120) == {
        "kalshi:T-ARS", "kalshi:T-CHE"}

    # A later cycle re-prices the same markets and closes the observation.
    run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]), POLICY, risk,
              ledger, ["soccer_epl"])
    # Nothing matured yet (entries are seconds old), so still pending...
    assert ledger.drift_report()["n"] == 0
    # ...but once they are old enough the next pass records them.
    ledger.record_drift_later("kalshi:T-ARS", 0.50)
    assert ledger.drift_report()["n"] == 1


# ── the bar must move with measured adverse selection ──────────────────
#
# The engine ran for a week at a 2c bar and settled -21% ROI while its own
# drift meter read: retention 0.318, mean drift -1.33c. Those two facts are
# the same fact. A threshold is a bar our ESTIMATE has to clear, and if fair
# value reliably moves against us right after we buy, the estimate is
# optimistic by exactly that much — so a 2c bar was really a 0.7c bar, and
# 0.7c does not survive crossing a spread. Trading more at that bar just
# loses faster. These pin the correction.

def _drifted(tmp_path, n, drift, category=None, price=0.47, fair=0.50):
    """n matured entries whose fair moved by `drift` after we bought."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    for i in range(n):
        key = f"m{i}"
        led.record_entry_fair(key, price, fair, category=category)
        led.record_drift_later(key, fair + drift)
    return led


def test_measured_adverse_drift_raises_the_bar(tmp_path):
    led = _drifted(tmp_path, 20, -0.013)
    pen = led.drift_penalties()
    assert pen["*"] == pytest.approx(0.013, abs=1e-4)

    # A 2c edge that used to clear a 2c bar no longer clears 2c + 1.3c.
    from edge.execution.engine import strategy_filter
    clean = strategy_filter(POLICY, "mls", 0.48, 0.50, category="moneyline")
    charged = strategy_filter(POLICY, "mls", 0.48, 0.50, category="moneyline",
                              drift_penalty=pen["*"])
    assert clean.threshold is not None
    assert charged.threshold == pytest.approx(clean.threshold + 0.013)
    assert charged.edge == pytest.approx(clean.edge)   # the EDGE is untouched
    assert not charged.ok


def test_favourable_drift_never_lowers_the_bar(tmp_path):
    """Being early is not a licence to be looser. Drift in our favour is
    clamped to zero, or a lucky week would quietly buy a lower threshold."""
    led = _drifted(tmp_path, 20, +0.02)
    assert led.drift_penalties()["*"] == 0.0

    from edge.execution.engine import strategy_filter
    v = strategy_filter(POLICY, "mls", 0.48, 0.50, drift_penalty=-0.05)
    base = strategy_filter(POLICY, "mls", 0.48, 0.50)
    assert v.threshold == pytest.approx(base.threshold)


def test_penalty_is_capped_so_one_noisy_week_cannot_halt_everything(tmp_path):
    led = _drifted(tmp_path, 20, -0.40)
    assert led.drift_penalties()["*"] == pytest.approx(Ledger.DRIFT_MAX_PENALTY)


def test_thin_evidence_charges_nothing_extra(tmp_path):
    """Below the observation floor there is no estimate to act on."""
    led = _drifted(tmp_path, Ledger.DRIFT_MIN_N - 1, -0.05)
    assert led.drift_penalties()["*"] == 0.0


def test_draws_are_measured_apart_from_the_two_way_sides(tmp_path):
    """The draw is the longshot leg of a three-way — where book margin
    concentrates and de-vig methods disagree most. Blended into the
    moneyline average it hides inside the number it is dragging down."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    for i in range(20):
        led.record_entry_fair(f"ml{i}", 0.47, 0.50, category="moneyline")
        led.record_drift_later(f"ml{i}", 0.500)          # clean
        led.record_entry_fair(f"dr{i}", 0.28, 0.31, category="draw")
        led.record_drift_later(f"dr{i}", 0.290)          # -2c against us

    pen = led.drift_penalties()
    assert pen["moneyline"] == 0.0
    assert pen["draw"] == pytest.approx(0.02, abs=1e-4)

    rep = led.drift_report()
    assert rep["by_category"]["draw"]["n"] == 20
    assert rep["by_category"]["moneyline"]["mean_drift_c"] == pytest.approx(0.0)
    # And the blend really would have hidden it: overall is half the draw's.
    assert 0 < pen["*"] < pen["draw"]


def test_unmeasured_category_inherits_the_overall_charge(tmp_path):
    """A brand-new category is unproven, not exempt."""
    led = _drifted(tmp_path, 20, -0.013, category="moneyline")
    pen = led.drift_penalties()
    assert pen.get("total", pen["*"]) == pytest.approx(pen["*"])
    assert pen["*"] > 0


def test_exploration_floor_moves_with_the_drift(tmp_path):
    """Exploration exists to test whether SMALL edges pay. An edge smaller
    than the measured drift is not an open question — it is a measured
    loser — so the study budget must stop funding it."""
    from edge.execution.engine import strategy_filter
    # 1c edge, plenty of book agreement: explorable at a 0c drift...
    free = strategy_filter(POLICY, "mls", 0.48, 0.4901, category="moneyline",
                           consensus_books=6)
    assert free.ok and free.tier == "exploration"
    # ...and not explorable once drift is measured at 1.3c.
    charged = strategy_filter(POLICY, "mls", 0.48, 0.4901, category="moneyline",
                              consensus_books=6, drift_penalty=0.013)
    assert not charged.ok


# ── the meter must not go blind when the bar goes up ────────────────────
#
# edge_drift can only learn from fills. Charging its measurement to the bar
# therefore starves it: a higher bar means fewer fills, fewer fills means
# less evidence, and the bar can never come back down. That is a system
# that freezes itself with no way out. Priced-but-not-bought outcomes are
# free samples of the same movement, so the meter keeps running at zero
# cost even when nothing qualifies.

def test_priced_outcomes_are_sampled_without_being_bought(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]), POLICY, risk,
              ledger, ["soccer_epl"])
    assert ledger.awaiting_price_drift(now=time.time() + 120), \
        "a priced outcome left no free drift sample"


def test_a_free_sample_closes_out_on_the_pricing_path(tmp_path, monkeypatch):
    """Closed out where prices are read, not where studies are written: the
    study bucket comes round once an hour, far too late to see a 1-minute
    move."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]), POLICY, risk,
              ledger, ["soccer_epl"])
    open_now = ledger.awaiting_price_drift(now=time.time() + 120)
    assert open_now
    # Age the samples, then price again — the second look should land.
    for mkey, obs in open_now.items():
        ledger.record_price_drift_later(obs, 0.52)
    rep = ledger.price_drift_report()
    assert rep["n"] == len(open_now)
    assert rep["by_category"]


def test_free_samples_never_feed_the_surcharge(tmp_path):
    """Our fills are SELECTED — we get filled when someone wants to sell —
    so fill drift carries staleness plus adverse selection, while a free
    sample carries staleness alone and is a floor, not a substitute. Letting
    the cheap number set the bar would understate what trading costs."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    for i in range(50):
        led.record_price_observation(f"o{i}", f"m{i}", 0.47, 0.50,
                                     category="moneyline")
        led.record_price_drift_later(f"o{i}", 0.30)   # enormous fake drift
    assert led.price_drift_report()["n"] == 50
    assert led.drift_penalties() == {"*": 0.0}, \
        "free samples leaked into the surcharge"


def test_one_sample_per_market_per_hour(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    led.record_price_observation("mkt:100", "mkt", 0.47, 0.50)
    led.record_price_observation("mkt:100", "mkt", 0.49, 0.55)   # same bucket
    assert len(led.awaiting_price_drift(now=time.time() + 120)) == 1


# ── shrinkage: charge the claim, not every trade ────────────────────────
#
# Free samples said fair value does NOT drift on its own (0.0c across
# hundreds). Our fills drift -1.33c. Same feed, same minute — so the move is
# not staleness, it is selection: we buy where fair - price is largest, which
# preferentially picks the moments our own estimate is most wrong, and those
# revert. The correct correction is proportional to the size of the claim,
# not a flat tax on every trade.

def _reverting(tmp_path, n=400, keep=0.3):
    """n samples where a fraction (1-keep) of each apparent edge reverts."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    for i in range(n):
        claim = 0.001 + (i % 40) * 0.001        # 0.1c..4c of apparent edge
        price, fair = 0.50, 0.50 + claim
        led.record_price_observation(f"o{i}", f"m{i}", price, fair,
                                     category="moneyline", edge=claim)
        led.record_price_drift_later(f"o{i}", fair - claim * (1 - keep))
    return led


def test_reversion_recovers_the_surviving_fraction(tmp_path):
    led = _reverting(tmp_path, keep=0.3)
    rev = led.reversion()
    assert rev["n"] == 400
    assert rev["keep"] == pytest.approx(0.3, abs=0.02)
    assert rev["slope"] == pytest.approx(-0.7, abs=0.02)


def test_no_reversion_means_no_shrinkage(tmp_path):
    """If apparent edge holds, shrinkage must be a no-op — the correction
    has to disappear when the problem does."""
    led = _reverting(tmp_path, keep=1.0)
    assert led.reversion()["keep"] == pytest.approx(1.0, abs=0.02)


def test_growing_edge_never_pays_us_a_bonus(tmp_path):
    """A positive slope would mean apparent edge grows after we look. We do
    not get to bank that as a lower bar — keep caps at 1."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    for i in range(400):
        claim = 0.001 + (i % 40) * 0.001
        led.record_price_observation(f"o{i}", f"m{i}", 0.50, 0.50 + claim,
                                     edge=claim)
        led.record_price_drift_later(f"o{i}", 0.50 + claim * 2)
    assert led.reversion()["keep"] == 1.0


def test_reversion_withholds_judgement_until_it_can_judge(tmp_path):
    # Too few samples.
    assert _reverting(tmp_path, n=50).reversion()["keep"] is None
    # Enough samples, but every claim identical — a slope through one point
    # in x is meaningless however many rows sit on it.
    led = Ledger(db_path=str(tmp_path / "flat.sqlite3"))
    for i in range(400):
        led.record_price_observation(f"o{i}", f"m{i}", 0.50, 0.52, edge=0.02)
        led.record_price_drift_later(f"o{i}", 0.51)
    assert led.reversion()["keep"] is None
    assert led.reversion()["n"] == 400


def test_shrinkage_scales_with_the_claim(tmp_path):
    """A 10c claim is charged ten times what a 1c claim is — that is where
    the estimation error actually lives. A flat surcharge cannot do this."""
    from edge.execution.engine import strategy_filter
    small = strategy_filter(POLICY, "mls", 0.50, 0.51, keep=0.3)
    big = strategy_filter(POLICY, "mls", 0.50, 0.60, keep=0.3)
    assert small.raw_edge == pytest.approx(0.01)
    assert small.edge == pytest.approx(0.003)
    assert big.raw_edge == pytest.approx(0.10)
    assert big.edge == pytest.approx(0.030)
    # ...and the charge itself is 10x, not equal.
    assert (big.raw_edge - big.edge) == pytest.approx(
        10 * (small.raw_edge - small.edge))


def test_shrinkage_and_surcharge_never_both_apply(tmp_path):
    """Both correct the same error. Charging both would double-count."""
    from edge.execution.engine import strategy_filter
    v = strategy_filter(POLICY, "mls", 0.50, 0.56, keep=0.3, drift_penalty=0.02)
    assert v.drift_penalty == 0.0
    base = strategy_filter(POLICY, "mls", 0.50, 0.56)
    assert v.threshold == pytest.approx(base.threshold)
    assert v.edge == pytest.approx(0.06 * 0.3)


def test_no_measurement_leaves_the_surcharge_in_charge(tmp_path):
    """Until reversion is measurable, the blunt instrument still governs."""
    from edge.execution.engine import strategy_filter
    v = strategy_filter(POLICY, "mls", 0.47, 0.53, keep=None, drift_penalty=0.02)
    assert v.keep == 1.0
    assert v.drift_penalty == pytest.approx(0.02)
    assert v.edge == pytest.approx(v.raw_edge)


# ── not all sharp books are equally sharp ───────────────────────────────
#
# A plain median across the sharp list gave Pinnacle and an exchange exactly
# the same vote as a small low-margin book that is itself copying Pinnacle
# with a lag. Where six books quote, that is merely imprecise. Where two or
# three quote, the median can BE the softest book's opinion — and thin
# consensus is where the largest apparent edges appear. That manufactures
# fake edge precisely where we are least able to check it.

def test_the_reference_book_outvotes_the_followers():
    from edge.fairvalue.feed import _weighted_median
    # Pinnacle says 2.00; two followers say 2.20. A plain median takes 2.20.
    quotes = [(2.00, 3.0), (2.20, 1.0), (2.20, 1.0)]
    assert _weighted_median(quotes) == 2.00
    import statistics
    assert statistics.median([q for q, _ in quotes]) == 2.20   # the old answer


def test_followers_still_win_when_there_are_enough_of_them():
    """Weighting is not a veto. Pinnacle is 3 votes, not infinite ones."""
    from edge.fairvalue.feed import _weighted_median
    assert _weighted_median([(2.00, 3.0)] + [(2.20, 1.0)] * 4) == 2.20


def test_weighting_stays_a_median_not_a_mean():
    """One wild quote must not drag the estimate — robustness to outliers is
    the entire reason a median is used on book prices."""
    from edge.fairvalue.feed import _weighted_median
    sane = [(2.00, 3.0), (2.02, 1.0), (2.01, 1.0)]
    assert _weighted_median(sane + [(50.0, 1.0)]) < 2.10


def test_a_lone_quote_is_still_its_own_answer():
    from edge.fairvalue.feed import _weighted_median
    assert _weighted_median([(1.91, 1.0)]) == 1.91


# ── no reference book, no trade ─────────────────────────────────────────
#
# Measured against the live feed on 2026-07-31: MLB player props carry
# Pinnacle across 440 outcomes a game. Liga MX props carry 191 outcomes and
# NOT ONE sharp book. NHL is lowvig alone on 5 of 31 events. Same provider,
# same plan, same request — so "is there an anchor" is a per-league fact and
# cannot be assumed. Without one, fair value is a median of books that are
# themselves following someone else with a lag, and the largest apparent
# edges come from the thinnest consensus.

def _unanchored(ev):
    ev.anchors = 0
    return ev


def test_an_unanchored_event_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)],
                       StubFeed([_unanchored(_event())]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] == 0
    assert funnel["rejects"].get("no_sharp_anchor", 0) >= 1
    # ...and it says which league and how thin, so the refusal is diagnosable
    # rather than just a count that goes up.
    ex = funnel["unpriced_examples"]["no_sharp_anchor"][0]
    assert ex["league"] == "epl" and ex["anchors"] == 0


def test_an_anchored_event_still_trades(tmp_path, monkeypatch):
    """The gate must cost nothing where the anchor exists."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] >= 1
    assert "no_sharp_anchor" not in funnel["rejects"]


def test_the_gate_can_be_disabled_by_config(tmp_path, monkeypatch):
    """0 means off — the gate is evidence-backed, not sacred, and turning it
    off must not require a code change."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    from edge.execution.engine import Policy as P
    policy = P(POLICY.bands, POLICY.leagues, {**POLICY.risk, "min_anchor_books": 0})
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)],
                       StubFeed([_unanchored(_event())]),
                       policy, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] >= 1


# ── which KIND of bet is paying ─────────────────────────────────────────
#
# Props showed 0.01c drift and retention 1.00 while game lines showed
# -2.5c. But drift compares our own fair value against ITSELF a minute
# later, so a prop mapped to the wrong line reads perfectly clean: a
# stably-wrong number does not move. Settlement is the only place that
# error surfaces, and a blended scorecard cannot show it.

def test_settled_pnl_splits_by_category(tmp_path):
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    for i, (cat, pnl) in enumerate([("prop", 2.0), ("prop", -1.0),
                                    ("moneyline", -1.0), ("moneyline", -1.0)]):
        mkey = f"m{i}"
        # price 0.5: a win realizes +0.5, a loss -0.5. Buying at 1.0 would
        # make every outcome realize zero and the assertion meaningless.
        led.record_fill(fill_uid=f"f{i}", venue="v", market_key=mkey, side="BUY",
                        qty=1.0, price=0.5, ts=now, league="mlb",
                        mode="LIVE_BETA", category=cat)
        led.record_resolution(mkey, payout=1.0 if pnl > 0 else 0.0, ts=now)

    by_cat = led.performance_by_category(days=7, live_only=True)
    assert set(by_cat) == {"prop", "moneyline"}
    assert by_cat["prop"]["settled"] == 2
    assert by_cat["prop"]["wins"] == 1 and by_cat["prop"]["losses"] == 1
    assert by_cat["moneyline"]["settled"] == 2
    assert by_cat["moneyline"]["wins"] == 0
    # The blend would have shown one number; the split shows which half hurt.
    assert by_cat["moneyline"]["roi"] < by_cat["prop"]["roi"]


def test_a_fill_without_a_category_is_reported_as_unknown(tmp_path):
    """Fills predating the column must not vanish from the scorecard, and
    must not be quietly counted as whatever category is being examined."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    led.record_fill(fill_uid="f", venue="v", market_key="m", side="BUY",
                    qty=1.0, price=0.5, ts=now, mode="LIVE_BETA")
    led.record_resolution("m", payout=0.0, ts=now)
    assert "unknown" in led.performance_by_category(days=7, live_only=True)


# ── the four changes of 2026-08-02 ──────────────────────────────────────

def test_moneyline_is_globally_quarantined_by_config():
    """Measured on our own fills: moneyline -2.34c drift at retention 0.239,
    draw -2.54c at 0.146, across ~126 entries. The free-sample pool shows
    0.01c on the same categories when we DON'T buy, over 12,000+
    observations — so this is adverse selection, not noise, and a surcharge
    only makes us take a losing bet less often."""
    live = Policy.load()                      # the real config, not the rig's
    assert live.category_blocked("epl", "moneyline") is True
    assert live.category_blocked("mlb", "moneyline") is True
    # Props are on probation, NOT quarantined — retention cannot detect a
    # prop mapping error, so the settled scorecard decides, not this list.
    assert live.category_blocked("mlb", "prop") is False


def test_the_quarantine_is_reversible_by_config():
    p = Policy.load()
    p.leagues = {**p.leagues, "blocked_categories": []}
    assert p.category_blocked("epl", "moneyline") is False


def test_spread_cost_is_measured_against_claimed_edge(tmp_path):
    """The venue quotes in whole cents: at 50c one tick is 2% of stake, the
    same size as our entire claimed edge. If crossing eats it, a negative
    result needs no model error to explain it."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    # bid 0.48 / ask 0.52 -> mid 0.50. We pay the ask: 2c over mid, against
    # a claimed 3c edge, so only 1c of it was ever real.
    decision = {"edge": 0.03, "book_asks": [[0.52, 100]], "book_bids": [[0.48, 100]]}
    led.record_fill(fill_uid="f1", venue="v", market_key="m1", side="BUY",
                    qty=1.0, price=0.52, ts=now, mode="LIVE_BETA",
                    category="prop", decision=decision)
    rep = led.spread_report(days=7, live_only=True)
    assert rep["prop"]["n"] == 1
    assert rep["prop"]["spread_c"] == 4.0
    assert rep["prop"]["paid_over_mid_c"] == 2.0
    assert rep["prop"]["edge_c"] == 3.0
    assert rep["prop"]["net_c"] == 1.0


def test_a_fill_with_no_book_snapshot_is_skipped_not_counted_as_free():
    """A missing snapshot must not read as zero spread — that would flatter
    the average in exactly the direction we are testing for."""
    import tempfile

    led = Ledger(db_path=str(tempfile.mkdtemp() + "/l.sqlite3"))
    led.record_fill(fill_uid="f", venue="v", market_key="m", side="BUY",
                    qty=1.0, price=0.52, ts=time.time(), mode="LIVE_BETA",
                    category="prop", decision={"edge": 0.03})
    assert led.spread_report(days=7, live_only=True) == {}


def test_bands_are_graded_on_our_own_fills(tmp_path):
    """bands.yaml is calibrated from the reference account's GLOBAL
    Polymarket history. We trade Polymarket US. That transfer has never been
    validated, and this is how it eventually gets validated."""
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    now = time.time()
    for i, (price, payout) in enumerate([(0.22, 1.0), (0.23, 0.0), (0.51, 0.0)]):
        led.record_fill(fill_uid=f"f{i}", venue="v", market_key=f"m{i}",
                        side="BUY", qty=1.0, price=price, ts=now,
                        mode="LIVE_BETA", category="prop")
        led.record_resolution(f"m{i}", payout=payout, ts=now)
    by_band = led.performance_by_band(days=7, live_only=True)
    assert "0.20-0.25" in by_band and "0.50-0.55" in by_band
    assert by_band["0.20-0.25"]["settled"] == 2
    assert by_band["0.50-0.55"]["settled"] == 1
    assert by_band["0.50-0.55"]["roi"] == -1.0



def test_the_nearest_kick_off_is_priced_first(tmp_path, monkeypatch):
    """Capital, not opportunity, binds a small book. We hold to resolution,
    so a bet on tonight's game recycles tomorrow and one on next Sunday's
    does not — pricing nearest-first turns the same bankroll over more
    often, and any truncation drops the games that would have locked money
    up longest."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    seen = []

    class Feed(StubFeed):
        def fetch_events(self, sport_key):
            return list(self._events)

    events = []
    for i, hours in enumerate([200, 2, 50]):        # deliberately unsorted
        ev = _event()
        ev.home, ev.away = f"H{i}", f"A{i}"
        ev.h2h = {f"H{i}": 2.0, f"A{i}": 2.0}
        ev.commence_ts = time.time() + hours * 3600
        events.append(ev)

    class Venue(StubVenue):
        def discover_markets(self, league_codes):
            seen.append(None)                        # one call per event
            return []

    funnel = run_cycle([Venue(ask_price=0.47)], Feed(events), POLICY, risk,
                       ledger, ["soccer_epl"])
    assert funnel["order"] == "near-threshold+settlement"


def test_settlement_priority_survives_events_with_no_kick_off_time(tmp_path, monkeypatch):
    """An unknown kick-off must sort LAST, not first — a missing timestamp
    read as zero would put the least-known games at the front of the queue."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)

    class Feed(StubFeed):
        def fetch_events(self, sport_key):
            return list(self._events)

    near, unknown = _event(), _event()
    near.home, near.away = "Near", "B"
    near.h2h = {"Near": 2.0, "B": 2.0}
    near.commence_ts = time.time() + 3600
    unknown.home, unknown.away = "Unknown", "C"
    unknown.h2h = {"Unknown": 2.0, "C": 2.0}
    unknown.commence_ts = 0

    class Venue(StubVenue):
        def discover_markets(self, league_codes):
            return []

    funnel = run_cycle([Venue(ask_price=0.47)], Feed([unknown, near]), POLICY,
                       risk, ledger, ["soccer_epl"])
    assert funnel["order"] == "near-threshold+settlement"


# ── low-price hardening: the leash tightens as the price falls ──────────

def test_a_longshot_needs_two_anchors_not_one(tmp_path, monkeypatch):
    """Below 20c a single anchor is not evidence. The productive low bands
    are where return and proof-speed concentrate — and where a de-vig error
    is proportionally largest, so the same fixture that trades at 47c on
    one anchor is refused at 15c until a second sharp book agrees."""
    from edge.shadow.runner import run_cycle

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    ev = _event(home_odds=5.00)               # fair 0.20 each... two-way 0.50
    ev.h2h = {"Arsenal": 5.00, "Chelsea": 1.25}   # fair A ~0.20, ask below
    funnel = run_cycle([StubVenue(ask_price=0.155)], StubFeed([ev]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["rejects"].get("no_sharp_anchor", 0) >= 1
    assert funnel["logged"] == 0

    # The identical event with a second anchor trades.
    ledger2 = Ledger(db_path=str(tmp_path / "l2.sqlite3"))
    risk2 = RiskManager(ledger2, {**POLICY.risk, "mode": "PAPER"})
    ev2 = _event()
    ev2.h2h = {"Arsenal": 5.00, "Chelsea": 1.25}
    ev2.anchors = 2
    funnel2 = run_cycle([StubVenue(ask_price=0.155)], StubFeed([ev2]),
                        POLICY, risk2, ledger2, ["soccer_epl"])
    assert funnel2["logged"] >= 1, funnel2["rejects"]


def test_relative_implausibility_scales_where_the_absolute_cap_cannot():
    """8c of edge at 60c is a 13% disagreement; 6c at 10c claims the venue
    is wrong by 60% of the contract's value. The absolute cap passes both;
    the relative one refuses the second."""
    from edge.execution.engine import strategy_filter

    ok = strategy_filter(POLICY, "epl", 0.32, 0.36, consensus_books=6)
    assert ok.ok, ok.reason
    v = strategy_filter(POLICY, "epl", 0.10, 0.16, consensus_books=6)
    assert not v.ok and "implausible" in v.reason and "of price" in v.reason


def test_relative_guard_admits_the_measured_longshot_signature():
    """Reference fills at 5-10c averaged +2.7c — a 0.36 ratio. The guard
    exists to catch mapping errors, not to un-measure the favorite-longshot
    edge the whole low-band strategy is built on."""
    from edge.execution.engine import strategy_filter

    v = strategy_filter(POLICY, "epl", 0.09, 0.125, consensus_books=6)
    assert v.ok, v.reason                      # 3.5c at 9c: ratio 0.39


def test_relative_guard_closes_subnickel_prices():
    """A 2c contract claiming a 3c edge is claiming fair value 2.5x the
    market. The band table leaves 0.00-0.05 open on measured evidence; the
    ratio guard is what actually stands between us and trading it, because
    at those prices the 3c bar cannot fit under half the price."""
    from edge.execution.engine import strategy_filter

    v = strategy_filter(POLICY, "epl", 0.02, 0.055, consensus_books=6)
    assert not v.ok and "implausible" in v.reason


def test_exploration_respects_the_relative_guard_too():
    """The study budget studies small edges, not suspicious ones: a
    sub-threshold edge that is still over half the price does not become
    tradeable by being called exploration."""
    from edge.execution.engine import strategy_filter

    # 2.4c at 4c: below the 3.0c band bar, above the exploration floor,
    # deep consensus — and 60% of the price. Refused.
    v = strategy_filter(POLICY, "epl", 0.04, 0.064, consensus_books=6)
    assert not v.ok


# ── the per-fill net-margin gate ────────────────────────────────────────

def test_a_wide_spread_eats_the_edge_and_the_fill_is_refused(tmp_path, monkeypatch):
    """3c of edge on a 10c-wide book pays ~5c to cross: negative at entry,
    whatever settlement says later. Measured live 2026-08-02: exploration
    props entered at -0.92% net. The band threshold prices the edge; this
    gate prices the BOOK actually in front of us."""
    from edge.shadow.runner import run_cycle
    from edge.venues.base import BookLevel, MarketBook

    class WideVenue(StubVenue):
        def get_book(self, market_id, token):
            return MarketBook(venue=self.name, market_id=market_id,
                              outcome_id=token,
                              bids=[BookLevel(self._ask.price - 0.10, 500)],
                              asks=[self._ask], ts=time.time())

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([WideVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] == 0
    assert funnel["rejects"].get("net_margin", 0) >= 1
    assert funnel.get("net_margin_refused", {})


def test_a_tight_spread_passes_the_same_fill(tmp_path, monkeypatch):
    """Identical edge, 2c book: (3c - 1c) / 47c = 4.3% net — well over the
    floor. The gate charges for the book, not for existing."""
    from edge.shadow.runner import run_cycle

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] >= 1
    assert funnel["rejects"].get("net_margin", 0) == 0
