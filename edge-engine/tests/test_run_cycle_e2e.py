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

    def maker_fee(self, price):
        return 0.0

    def plan_entry(self, book):
        return (book.asks[0].price, True) if book.asks else (0.0, True)


def _event(fresh=True, home_odds=2.00):
    # Fair (2-way, equal-vig): Arsenal = 0.50; ask 0.47 -> edge > threshold.
    return FeedEvent(
        sport_key="soccer_epl", league_code="epl", home="Arsenal", away="Chelsea",
        commence_ts=time.time() + 3600,
        h2h={"Arsenal": home_odds, "Chelsea": home_odds},
        fetched_at=time.time() - (0 if fresh else 60),
        # NOTE: `books` is deliberately left at 0 here. Setting it makes the
        # event eligible for the exploration tier, which silently converts
        # "blocked by threshold" into "traded as exploration" and guts every
        # threshold test in this file. Only the anchor is set.
        # A reference-class book (Pinnacle / an exchange) stands behind this
        # number. Without one the runner refuses to price at all, so the
        # default fixture is an ANCHORED event; the unanchored case is its
        # own test rather than an accident of every other one.
        anchors=1,
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


def test_every_priced_side_of_a_game_is_tradeable(tmp_path, monkeypatch):
    """One position per BET, not per game. Both sides of this event carry
    edge; claiming the event took one and abandoned the other, which is the
    single biggest reason the fill count never resembled the reference
    account's ~36 fills per market."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    funnel = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["logged"] == 2                       # Arsenal AND Chelsea
    assert ledger.position("kalshi:T-ARS") and ledger.position("kalshi:T-CHE")


def test_never_adds_to_a_market_across_cycles(tmp_path, monkeypatch):
    """The rule that survives: a bet is entered once and never topped up."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
              POLICY, risk, ledger, ["soccer_epl"])
    fills_after_first = ledger.summary()["fills"]
    assert fills_after_first >= 1
    funnel2 = run_cycle([StubVenue(ask_price=0.47)], StubFeed([_event()]),
                        POLICY, risk, ledger, ["soccer_epl"])
    assert ledger.summary()["fills"] == fills_after_first  # never adds
    assert funnel2["rejects"], "re-entry must be refused with a named reason"


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


def test_exploration_excludes_implausible_and_dedupes(tmp_path, monkeypatch):
    """Implausible 'edges' are mapping errors, not study data; and a market
    is studied once per discovery window, not once per 10s cycle."""
    import json as _json
    from pathlib import Path

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    log = Path(str(tmp_path)) / "shadow_fills.jsonl"

    # Implausible: fair 0.50 vs ask 0.09 -> 41c "edge". Never studied.
    run_cycle([StubVenue(ask_price=0.09)], StubFeed([_event()]),
              POLICY, risk, ledger, ["soccer_epl"])
    recs = [_json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    assert not any(r.get("tag") == "exploration" for r in recs)

    # Near-miss (1.5c vs 2.0c needed): each of the event's two outcome
    # markets is studied ONCE across repeated cycles (2 records, not 6).
    log.write_text("")
    seen: set = set()
    for _ in range(3):
        run_cycle([StubVenue(ask_price=0.485)], StubFeed([_event()]),
                  POLICY, risk, ledger, ["soccer_epl"], explored_seen=seen)
    recs = [_json.loads(x) for x in log.read_text().splitlines()]
    assert sum(1 for r in recs if r.get("tag") == "exploration") == 2
    assert len(seen) == 2  # one entry per outcome market


def test_unpriced_outcomes_are_counted_not_silently_dropped(tmp_path, monkeypatch):
    """A venue line the sharp book doesn't quote must produce a NAMED reject
    with the mismatch visible — the blind spot behind '0 trades, no reason'."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)

    class SpreadVenue(StubVenue):
        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT", title="Arsenal vs. Chelsea", league_code="epl",
                # Venue lists a -2.5 handicap...
                outcome_tokens={"Arsenal -2.5": "T-A", "Chelsea +2.5": "T-C"},
            )]

    class Feed(StubFeed):
        def fetch_events(self, sport_key):
            ev = _event()
            # ...but the sharp book only quotes -0.5 and -1.5.
            ev.spreads = {"Arsenal -0.5": 1.9, "Chelsea +0.5": 1.9,
                          "Arsenal -1.5": 2.6, "Chelsea +1.5": 1.5}
            return [ev]

    funnel = run_cycle([SpreadVenue(ask_price=0.30)], Feed([]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert ledger.summary()["fills"] == 0
    assert funnel["rejects"].get("no_sharp_quote_spread", 0) >= 1
    ex = funnel["unpriced_examples"]["no_sharp_quote_spread"][0]
    assert ex["venue_line"] == -2.5
    assert -1.5 in ex["sharp_lines"] and -0.5 in ex["sharp_lines"]


def test_a_moneyline_mapping_miss_names_what_it_nearly_matched(tmp_path, monkeypatch):
    """Mapping is now the funnel's biggest single loss. A count is not
    actionable; the venue string, the closest feed team and the score are."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)

    class OddNames(StubVenue):
        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT", title="Arsenal vs. Chelsea", league_code="epl",
                outcome_tokens={"Arsenal": "T-A", "Draw No Bet": "T-D"})]

    funnel = run_cycle([OddNames(ask_price=0.47)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    [ex] = funnel["unpriced_examples"]["no_side_match_moneyline"]
    assert ex["venue_outcome"] == "Draw No Bet"
    assert ex["closest_feed_team"] in ("Arsenal", "Chelsea")
    assert 0.0 <= ex["score"] < 0.95
    assert "Arsenal" in ex["feed_teams"]


def test_study_records_every_priced_outcome_even_when_nothing_trades(tmp_path, monkeypatch):
    """The evidence stream must NOT be gated by the trading rules: a slate
    that trades nothing must still produce study data and name the blocker."""
    import json as _json
    from pathlib import Path

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    # 0.43 ask in a dead zone: zero trades by design.
    funnel = run_cycle([StubVenue(ask_price=0.43)], StubFeed([_event(home_odds=1.60)]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert ledger.summary()["fills"] == 0          # nothing traded, as intended
    assert funnel["studied"] >= 1                  # ...but we still learned
    assert funnel["blockers"].get("band", 0) >= 1  # and we know why

    recs = [_json.loads(x) for x in
            (Path(str(tmp_path)) / "shadow_fills.jsonl").read_text().splitlines()]
    study = [r for r in recs if r.get("tag") == "study"]
    assert study, "priced outcomes must be recorded for calibration"
    assert study[0]["feed"]["would_clear"] is False
    assert "dead" in study[0]["feed"]["blocked_by"]
    assert study[0]["fair_value"] > 0 and study[0]["limit_price"] == 0.43


def test_threshold_gap_distribution_is_reported(tmp_path, monkeypatch):
    """Near-misses are bucketed so we can see how close the market is to
    clearing — the number that says whether thresholds need tuning."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)
    # fair 0.50 vs ask 0.485 -> 1.5c edge, 2.0c needed: a 0.5-1c gap.
    funnel = run_cycle([StubVenue(ask_price=0.485)], StubFeed([_event()]),
                       POLICY, risk, ledger, ["soccer_epl"])
    assert funnel["blockers"]["threshold"] >= 1
    gaps = funnel["threshold_gap"]
    # 1.5c edge against a 2.0c bar: a sub-cent miss, and every threshold
    # rejection lands in exactly one bucket.
    assert gaps.get("<0.5c", 0) + gaps.get("0.5-1c", 0) >= 1
    assert sum(gaps.values()) == funnel["blockers"]["threshold"]


def test_a_segment_market_is_never_priced_off_the_full_game_line(tmp_path, monkeypatch):
    """The money-losing version of this bug: the venue lists a first-five-
    innings run line, we have no first-five quote, and we price it against
    the FULL GAME line — manufacturing a large phantom edge on a bet nobody
    evaluated. It must be refused, by name."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)

    class F5Venue(StubVenue):
        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT", title="Arsenal vs. Chelsea", league_code="epl",
                outcome_tokens={"[f5] Arsenal -1.5": "T-A"})]

    class Feed(StubFeed):
        def fetch_events(self, sport_key):
            ev = _event()
            # Full-game -1.5 IS quoted. The first-five line is not.
            ev.spreads = {"Arsenal -1.5": 2.6, "Chelsea +1.5": 1.5}
            return [ev]

    funnel = run_cycle([F5Venue(ask_price=0.30)], Feed([]), POLICY, risk,
                       ledger, ["soccer_epl"])
    assert ledger.summary()["fills"] == 0
    assert funnel["rejects"].get("no_sharp_quote_segment_f5", 0) >= 1


def test_a_segment_market_trades_off_its_OWN_quote(tmp_path, monkeypatch):
    """And when the sharp book does quote the segment, it prices normally —
    the point of pulling first-5-innings markets from the feed at all."""
    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger, risk = _rig(tmp_path)

    class F5Venue(StubVenue):
        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT", title="Arsenal vs. Chelsea", league_code="epl",
                outcome_tokens={"[f5] Arsenal": "T-A"})]

    class Feed(StubFeed):
        def fetch_events(self, sport_key):
            ev = _event()
            ev.segments = {"f5": {"h2h": {"Arsenal": 2.0, "Chelsea": 2.0}}}
            return [ev]

    funnel = run_cycle([F5Venue(ask_price=0.47)], Feed([]), POLICY, risk,
                       ledger, ["soccer_epl"])
    assert funnel["logged"] == 1          # fair 0.50 vs 0.47: 3c, clears
    assert ledger.position("kalshi:T-A")["shares"] > 0
