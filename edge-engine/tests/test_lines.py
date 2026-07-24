"""Spreads/totals extension gates: line parsing, exact-point pairing,
canonicalization, per-category bands, league x category blocks, and the
e2e derivative paper cycle."""

import time

import pytest

from edge.execution.engine import Policy, strategy_filter
from edge.fairvalue.lines import (
    canonical_outcome,
    outcome_matches,
    pair_quotes,
    parse_outcome_line,
    title_point,
)

POLICY = Policy.load()


# ── parsing ────────────────────────────────────────────────────────────

def test_parse_total_and_spread_and_ml():
    p = parse_outcome_line("Over 8.5")
    assert (p.kind, p.side, p.point) == ("total", "over", 8.5)
    p = parse_outcome_line("Kansas City Chiefs -3.5")
    assert (p.kind, p.team, p.point) == ("spread", "Kansas City Chiefs", -3.5)
    p = parse_outcome_line("Eagles (+7.5)")
    assert (p.kind, p.point) == ("spread", 7.5)
    assert parse_outcome_line("Red Sox").kind == "moneyline"
    assert parse_outcome_line("Under").kind == "total"


def test_title_point_extraction():
    assert title_point("Mets vs. Tigers: O/U 8.5") == 8.5
    assert title_point("Spread: Eagles (-7.5)") == -7.5
    assert title_point("Red Sox vs. Angels") is None


def test_canonical_outcome_from_title_context():
    # kch123 statement format: line in the title, plain names as outcomes.
    assert canonical_outcome("Mets vs. Tigers: O/U 8.5", "Over") == "Over 8.5"
    assert canonical_outcome("Spread: Eagles (-7.5)", "Eagles") == "Eagles -7.5"
    # The OTHER team gets the mirrored line.
    assert canonical_outcome("Spread: Eagles (-7.5)", "Cowboys") == "Cowboys +7.5"
    # Plain moneyline stays untouched.
    assert canonical_outcome("Red Sox vs. Angels", "Red Sox") == "Red Sox"


# ── pairing: exact-point rule ──────────────────────────────────────────

def test_totals_pair_only_at_identical_points():
    quotes = {"Over 8.5": 1.91, "Under 8.5": 1.91, "Over 9.5": 2.1}
    pairs = pair_quotes(quotes, "total")
    assert len(pairs) == 1 and pairs[0].point == 8.5  # unpaired 9.5 dropped


def test_spreads_pair_mirrored_points_only():
    quotes = {"Chiefs -3.5": 1.95, "Eagles +3.5": 1.87, "Eagles +4.5": 1.7}
    pairs = pair_quotes(quotes, "spread")
    assert len(pairs) == 1
    assert pairs[0].a_name == "Chiefs -3.5" and pairs[0].b_name == "Eagles +3.5"


def test_outcome_matches_enforces_exact_point_and_team():
    [pair] = pair_quotes({"Chiefs -3.5": 1.95, "Eagles +3.5": 1.87}, "spread")
    assert outcome_matches("Kansas City Chiefs -3.5", pair.a_parsed)
    assert not outcome_matches("Kansas City Chiefs -2.5", pair.a_parsed)  # wrong line
    assert not outcome_matches("Eagles -3.5", pair.a_parsed)              # wrong team
    [tp] = pair_quotes({"Over 8.5": 1.9, "Under 8.5": 1.9}, "total")
    assert outcome_matches("Over 8.5", tp.a_parsed)
    assert not outcome_matches("Over 9.5", tp.a_parsed)
    assert not outcome_matches("Under 8.5", tp.a_parsed)


# ── per-category bands ─────────────────────────────────────────────────

def test_moneyline_dead_zone_does_not_apply_to_spreads():
    assert POLICY.band_threshold(0.42, "moneyline") is None       # dead zone
    assert POLICY.band_threshold(0.42, "spread") == 0.025          # kch123 window
    assert POLICY.band_threshold(0.52, "spread") == 0.025
    assert POLICY.band_threshold(0.57, "spread") is None           # outside window


def test_totals_cheap_side_only():
    assert POLICY.band_threshold(0.45, "total") == 0.025
    assert POLICY.band_threshold(0.60, "total") is None            # negative zone
    assert POLICY.band_threshold(0.75, "total") is None


def test_unknown_category_never_tradeable():
    assert POLICY.band_threshold(0.47, "props") is None


def test_nfl_moneyline_blocked_but_spread_allowed():
    v = strategy_filter(POLICY, "nfl", 0.47, 0.55, category="moneyline")
    assert not v.ok and "blocked" in v.reason
    v2 = strategy_filter(POLICY, "nfl", 0.47, 0.55, category="spread")
    assert v2.ok
    v3 = strategy_filter(POLICY, "nhl", 0.47, 0.55, category="moneyline")
    assert v3.ok  # NHL ML: specialist-positive on 104k fills


# ── e2e: derivative paper cycle ────────────────────────────────────────

def test_paper_cycle_trades_a_total(tmp_path, monkeypatch):
    from edge.execution.risk import RiskManager
    from edge.fairvalue.feed import FeedEvent
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from edge.venues.base import BookLevel, MarketBook
    from edge.venues.mapper import VenueMarket

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))

    class Venue:
        name = "kalshi"

        def __init__(self):
            self.book_errors = {}

        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT-TOT", title="Mets at Tigers: O/U 8.5",
                league_code="nhl",  # allowlisted league for the test
                outcome_tokens={"Over 8.5": "T-OVER", "Under 8.5": "T-UNDER"},
            )]

        def get_book(self, market_id, token):
            return MarketBook(venue=self.name, market_id=market_id,
                              outcome_id=token, bids=[BookLevel(0.41, 400)],
                              asks=[BookLevel(0.43, 400)], ts=time.time())

        def taker_fee(self, price):
            return 0.0

    class Feed:
        def fetch_events(self, sport_key):
            return [FeedEvent(
                sport_key="icehockey_nhl", league_code="nhl",
                home="Mets", away="Tigers", commence_ts=time.time() + 3600,
                h2h={"Mets": 2.0, "Tigers": 2.0},
                totals={"Over 8.5": 2.04, "Under 8.5": 1.96},  # fair Over ≈ 0.49
                fetched_at=time.time(),
            )]

        def server_clock_skew_s(self):
            return 0.0

    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(ledger, {**POLICY.risk, "mode": "PAPER"})
    funnel = run_cycle([Venue()], Feed(), POLICY, risk, ledger, ["icehockey_nhl"])
    # Over fair ≈0.49 vs ask 0.43 -> ~6c edge in the totals 0.40-0.50 window.
    assert funnel["by_category"].get("total", 0) >= 1
    pos = ledger.position("kalshi:T-OVER")
    assert pos is not None and pos["shares"] > 0
    rec_decision = None
    import sqlite3

    with sqlite3.connect(ledger.db_path) as conn:
        row = conn.execute("SELECT decision FROM fills LIMIT 1").fetchone()
    import json

    rec_decision = json.loads(row[0])
    assert rec_decision["category"] == "total"
    assert rec_decision["outcome"] == "Over 8.5"


def test_ml_dead_zone_still_dead_in_same_cycle_as_spread(tmp_path, monkeypatch):
    # A 0.43 ask: blocked for moneyline (dead zone) but the SAME price on a
    # spread market is inside the kch123 window — both verdicts in one config.
    v_ml = strategy_filter(POLICY, "nba", 0.43, 0.50, category="moneyline")
    v_sp = strategy_filter(POLICY, "nba", 0.43, 0.50, category="spread")
    assert not v_ml.ok and "dead" in v_ml.reason
    assert v_sp.ok and v_sp.edge == pytest.approx(0.07)
