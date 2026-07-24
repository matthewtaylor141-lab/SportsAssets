"""End-to-end PAPER cycle: stub feed + stub venue books through the real
run_cycle — proves the operational loop records ledger fills, match stats,
funnel telemetry, and enforces the hard rules (freshness, 0.95 mapping,
dead zones, one-per-event) in one pass."""

import time

import pytest

from edge.execution.engine import Policy
from edge.execution.risk import RiskManager
from edge.fairvalue.feed import FeedEvent
from edge.ledger.service import Ledger
from edge.shadow.runner import run_cycle
from edge.venues.base import BookLevel, MarketBook
from edge.venues.mapper import VenueMarket

POLICY = Policy.load()


class StubFeed:
    def __init__(self, events):
        self._events = events

    def fetch_events(self, sport_key):
        return [e for e in self._events if e.sport_key == sport_key]

    def server_clock_skew_s(self):
        return 0.0


class StubVenue:
    name = "kalshi"

    def __init__(self, ask_price, ask_size=1000):
        self._ask = BookLevel(ask_price, ask_size)
        self.book_errors = {}

    def discover_markets(self, league_codes):
        return [VenueMarket(
            market_id="EVT-ARS-CHE", title="Arsenal vs. Chelsea", league_code="epl",
            outcome_tokens={"Arsenal": "T-ARS", "Chelsea": "T-CHE"},
        )]

    def get_book(self, market_id, token):
        return MarketBook(venue=self.name, market_id=market_id, outcome_id=token,
                          bids=[BookLevel(self._ask.price - 0.02, 500)],
                          asks=[self._ask], ts=time.time())

    def taker_fee(self, price):
        return 0.0  # keep the arithmetic transparent in the assertions


def _event(fresh=True, home_odds=2.00):
    # Fair (2-way, equal-vig): Arsenal = 0.50; ask 0.47 -> edge > threshold.
    return FeedEvent(
        sport_key="soccer_epl", league_code="epl", home="Arsenal", away="Chelsea",
        commence_ts=time.time() + 3600,
        h2h={"Arsenal": home_odds, "Chelsea": home_odds},
        fetched_at=time.time() - (0 if fresh else 60),
    )


def _rig(tmp_path):
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    return ledger, RiskManager(ledger, {**POLICY.risk, "mode": "PAPER"})


def test_paper_cycle_records_fill_and_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["mode"] == "PAPER"
    assert funnel["matched"] == 1 and funnel["tradeable"] == 1
    assert funnel["logged"] >= 1
    s = ledger.summary()
    assert s["fills"] >= 1 and s["staked"] > 0
    stats = ledger.match_rate_report(days=1)
    assert stats and stats[0]["tradeable_rate"] == 1.0
    pos = ledger.position("kalshi:T-ARS") or ledger.position("kalshi:T-CHE")
    assert pos is not None and pos["shares"] > 0


def test_stale_quotes_never_trade(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event(fresh=False)]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] == 0
    assert funnel["rejects"].get("stale_quote", 0) >= 1
    assert ledger.summary()["fills"] == 0


def test_dead_zone_ask_never_trades(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.43)], StubFeed(
        [_event(home_odds=1.60)]), POLICY, risk, ledger, ["soccer_epl"])
    # 0.43 ask sits in the 0.40-0.45 dead zone: unconditionally untradeable.
    assert ledger.summary()["fills"] == 0
    assert funnel["rejects"].get("band", 0) >= 1


def test_one_per_event_across_cycles(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
              POLICY, risk, ledger, ["soccer_epl"])
    fills_after_first = ledger.summary()["fills"]
    assert fills_after_first >= 1
    funnel2 = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                        POLICY, risk, ledger, ["soccer_epl"])
    assert ledger.summary()["fills"] == fills_after_first  # never adds
    assert funnel2["rejects"].get("one-per-event", 0) >= 1


def test_edge_telemetry_and_exploration_logging(tmp_path, monkeypatch):
    """A near-threshold edge (1.5c vs 2.0c needed) must NOT trade, but must
    be counted (evaluated/near-miss) and logged tagged 'exploration'."""
    import json as _json
    from pathlib import Path

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    # fair 0.50 (2.0/2.0 odds), ask 0.485 -> edge 1.5c < 2.0c threshold
    funnel = run_cycle([StubVenue(ask_price=0.485)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert ledger.summary()["fills"] == 0            # discipline held
    edges = funnel["edges"]
    assert edges["evaluated"] >= 1
    assert edges["best_cents"] == pytest.approx(1.5, abs=0.1)
    assert edges["near_miss_1c"] >= 1
    assert edges["explored"] >= 1
    log = Path(str(tmp_path)) / "shadow_fills.jsonl"
    recs = [_json.loads(line) for line in log.read_text().splitlines()]
    assert any(r.get("tag") == "exploration" for r in recs)
