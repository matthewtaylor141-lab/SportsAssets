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

# Most tests here exercise loop and pricing MECHANICS, not trading policy.
# `blocked_categories` globally quarantines moneyline (measured -2.34c drift,
# retention 0.239 on our own fills), which would otherwise make every
# moneyline fixture untradeable and turn these into vacuous passes. The
# quarantine itself is pinned by its own tests in test_loop_health.py.
POLICY = Policy.load()
POLICY.leagues = {**POLICY.leagues, "blocked_categories": []}


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
    assert POLICY.band_threshold(0.42, "spread") == 0.025          # kch123 core
    # 0.52+ died on the NET-ROI FLOOR, not the dead zone: 2.5c minus the
    # 1.5c crossing at 52c pays 1.9%, under the benchmark. The measurement
    # stands; the trade doesn't pay. See test_category_bands.py.
    assert POLICY.band_threshold(0.52, "spread") is None
    assert POLICY.band_threshold(0.65, "spread") == 0.030


def test_totals_pricing_symmetry_ends_where_the_floor_begins():
    """Over and Under come from one de-vig — the expensive side is the same
    MEASUREMENT. It is no longer the same TRADE: the net-ROI floor closes
    prices where threshold minus crossing pays under the benchmark, and
    that is a function of price alone."""
    assert POLICY.band_threshold(0.45, "total") == 0.025
    assert POLICY.band_threshold(0.55, "total") is None            # floored
    assert POLICY.band_threshold(0.65, "total") == 0.030
    assert POLICY.band_threshold(0.80, "total") is None            # floored
    assert POLICY.band_threshold(0.95, "total") is None            # tail, excluded


def test_unknown_category_never_tradeable():
    assert POLICY.band_threshold(0.47, "props") is None


def test_nfl_moneyline_blocked_but_spread_allowed():
    # Fixtures sit at 0.30-0.35 — the strongest measured band (+3.59c) and
    # inside the net-ROI floor, unlike the old 0.47 coin-flip fixtures.
    v = strategy_filter(POLICY, "nfl", 0.32, 0.40, category="moneyline")
    assert not v.ok and "blocked" in v.reason
    v2 = strategy_filter(POLICY, "nfl", 0.32, 0.40, category="spread")
    assert v2.ok
    v3 = strategy_filter(POLICY, "nhl", 0.32, 0.40, category="moneyline")
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

        def maker_fee(self, price):
            return 0.0

        def plan_entry(self, book):
            return book.asks[0].price, True

    class Feed:
        def fetch_events(self, sport_key):
            return [FeedEvent(
                sport_key="icehockey_nhl", league_code="nhl",
                home="Mets", away="Tigers", commence_ts=time.time() + 3600,
                h2h={"Mets": 2.0, "Tigers": 2.0},
                totals={"Over 8.5": 2.04, "Under 8.5": 1.96},  # fair Over ≈ 0.49
                books=5, anchors=1, fetched_at=time.time(),
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


def test_implausible_edge_never_trades():
    # A "45c edge" is a mapping error wearing a costume — reject in any mode.
    v = strategy_filter(POLICY, "epl", 0.09, 0.54, category="moneyline")
    assert not v.ok and "implausible" in v.reason
    # Healthy edges still clear (in a band the net-ROI floor keeps open).
    v2 = strategy_filter(POLICY, "epl", 0.31, 0.35, category="moneyline")
    assert v2.ok


# ── venue slugs carry the handicap (measured live 2026-07-24) ──────────

def test_slug_point_parses_real_venue_slugs():
    from edge.fairvalue.lines import slug_point

    assert slug_point("asc-lmx-san-atl-2026-07-25-neg-2pt5") == -2.5
    assert slug_point("asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5") == 1.5
    assert slug_point("asc-lpa-rac-gim-2026-07-24-pos-2pt5") == 2.5
    assert slug_point("nba-lal-bos-2026-07-24") is None


def test_apply_slug_line_prevents_moneyline_mispricing():
    from edge.fairvalue.lines import apply_slug_line

    # THE BUG: outcome field is just the team, line only in the slug ->
    # priced against the moneyline. Now it's line-explicit.
    key = apply_slug_line("Club Santos Laguna", "asc-lmx-san-atl-2026-07-25-neg-2pt5")
    assert key == "Club Santos Laguna -2.5"
    p = parse_outcome_line(key)
    assert p.kind == "spread" and p.point == -2.5
    # A moneyline sharp quote can no longer match this market.
    [pair] = pair_quotes({"Club Santos Laguna -2.5": 1.9,
                          "Atlas FC +2.5": 1.9}, "spread")
    assert outcome_matches(key, pair.a_parsed)
    # Already-explicit outcomes and line-free slugs are untouched.
    assert apply_slug_line("Eagles -7.5", "x-neg-2pt5") == "Eagles -7.5"
    assert apply_slug_line("Red Sox", "mlb-bos-nyy-2026-07-24") == "Red Sox"
    # Totals take the unsigned point.
    assert apply_slug_line("Over", "tot-x-pos-8pt5") == "Over 8.5"


# ── structural defence: bet identity must agree across signals ─────────

def test_bet_identity_agrees_across_slug_title_outcome():
    from edge.fairvalue.lines import bet_identity

    b = bet_identity("asc-lmx-san-atl-2026-07-25-neg-2pt5",
                     "Club Santos Laguna vs. Atlas FC", "Club Santos Laguna")
    assert b.category == "spread" and b.point == -2.5 and b.tradeable
    assert "slug" in b.sources


def test_bet_identity_conflict_is_untradeable():
    from edge.fairvalue.lines import bet_identity

    # Slug says -2.5, title says -7.5: we do NOT get to pick one.
    b = bet_identity("game-neg-2pt5", "Spread: Eagles (-7.5)", "Eagles")
    assert b.conflict and not b.tradeable


def test_spread_market_with_unreadable_line_never_trades_as_moneyline():
    from edge.fairvalue.lines import bet_identity

    # THE ORIGINAL BUG: a spread market whose handicap we can't read must
    # not silently become a moneyline.
    b = bet_identity("some-slug", "Spread: Eagles", "Eagles")
    assert b.category == "spread" and b.point is None and not b.tradeable


def test_plain_moneyline_still_tradeable():
    from edge.fairvalue.lines import bet_identity

    b = bet_identity("mlb-bos-nyy-2026-07-24", "Red Sox vs. Angels", "Red Sox")
    assert b.category == "moneyline" and b.tradeable


def test_totals_compare_unsigned_without_false_conflict():
    from edge.fairvalue.lines import bet_identity

    b = bet_identity("tot-x-pos-8pt5", "Mets vs. Tigers: O/U 8.5", "Over")
    assert b.category == "total" and b.point == 8.5 and b.tradeable


# ── band policy regenerated from measured calibration ─────────────────

def test_every_measured_positive_band_is_tradeable():
    """Volume regression guard: a band with measured positive edge must not
    be silently blocked (the transcription error that cost ~45% of the
    reference account's staked volume)."""
    import csv
    import re
    from pathlib import Path

    csv_path = Path(__file__).resolve().parents[1] / "data" / "calib_price.csv"
    for row in csv.DictReader(open(csv_path)):
        m = re.match(r"\[([\d.]+), ([\d.]+)\)", row["bin"])
        if not m:
            continue
        lo, edge = float(m.group(1)), float(row["edge_cents"])
        mid = lo + 0.025
        th = POLICY.band_threshold(mid, "moneyline")
        # Tradeable requires BOTH: measured-positive edge AND clearing the
        # net-ROI floor at our measured crossing cost. Measured-positive
        # bands above ~0.40 are real edges that lose to their own costs.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from gen_bands import clears_net_floor, threshold_for
        # The floor gates on EXPECTED CAPTURE — the band's measured edge
        # where one exists, never below the threshold — minus crossing.
        should = (edge > 0.2 and lo < 0.90
                  and clears_net_floor(lo, lo + 0.05, threshold_for(lo), edge))
        if should:
            assert th is not None, f"band at {lo:.2f} measures +{edge}c but is blocked"
        else:
            assert th is None, f"band at {lo:.2f}: measured {edge}c, floored or dead"


def test_surviving_thresholds_unchanged_by_regeneration():
    """Bands that clear the net-ROI floor keep their exact measured
    thresholds — the floor removes bands, it never re-prices them."""
    for price, expected in ((0.07, 0.030), (0.17, 0.025), (0.32, 0.025),
                            (0.37, 0.025), (0.47, 0.020)):
        assert POLICY.band_threshold(price, "moneyline") == expected
    # The floored bands: measured-positive, dead at our costs. 0.45-0.50
    # SURVIVES because its measured +2.94c nets 3.0% after the 1.5c
    # crossing; 0.75-0.90 measures +1.4-2.6c and cannot pay the benchmark.
    for price in (0.52, 0.77, 0.82, 0.87):
        assert POLICY.band_threshold(price, "moneyline") is None


def test_fat_middle_is_floored_out():
    # 0.50-0.65 measures +0.5 to +1.2c — real, and SMALLER than the 1.5c we
    # pay to cross. Trading it converts a measured edge into a measured
    # loss. The floor keeps it shut until the crossing cost falls (maker
    # pricing), at which point regeneration re-opens it.
    for price in (0.52, 0.57, 0.62):
        assert POLICY.band_threshold(price, "moneyline") is None
    # ...and the genuinely dead zones stay dead.
    for price in (0.42, 0.67, 0.92, 0.97):
        assert POLICY.band_threshold(price, "moneyline") is None


# ── game segments: a partial-game market is a DIFFERENT BET ─────────────

def test_first_five_innings_is_not_the_full_game():
    """'asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5' is a FIRST FIVE INNINGS run
    line. Read only for its handicap it is indistinguishable from the
    full-game +1.5, and pricing it against the full-game fair value compares
    two different propositions."""
    from edge.fairvalue.lines import bet_identity

    f5 = bet_identity("asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5", "", "Phillies")
    full = bet_identity("asc-mlb-nyy-phi-2026-07-24-pos-1pt5", "", "Phillies")
    assert f5.point == full.point == 1.5      # identical handicap...
    assert f5.segment == "f5" and full.segment is None   # ...different bets


def test_segments_are_read_from_slug_and_title():
    from edge.fairvalue.lines import slug_segment, title_segment

    assert slug_segment("asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5") == "f5"
    assert slug_segment("atc-epl-ars-che-2026-08-01-1h-ars") == "h1"
    assert slug_segment("atc-nhl-bos-mtl-2026-08-01-1p-bos") == "p1"
    assert slug_segment("atc-bra-vit-pal-2026-07-29-pal") is None
    assert title_segment("Yankees vs Phillies (First 5 Innings)") == "f5"
    assert title_segment("Arsenal vs Chelsea - 1st Half") == "h1"
    assert title_segment("Arsenal vs. Chelsea") is None


def test_disagreeing_segment_signals_make_a_market_untradeable():
    """Same rule as a handicap mismatch: when signals disagree we do not
    guess which bet this is."""
    from edge.fairvalue.lines import bet_identity

    i = bet_identity("asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5",
                     "Yankees vs Phillies - 1st Half", "Phillies")
    assert not i.tradeable and "segment mismatch" in i.conflict


def test_segment_tagging_round_trips_and_cannot_collide():
    from edge.fairvalue.lines import split_segment, tag_segment

    full, f5 = tag_segment("Phillies +1.5", None), tag_segment("Phillies +1.5", "f5")
    assert full != f5                       # never the same dict key
    assert split_segment(f5) == ("f5", "Phillies +1.5")
    assert split_segment(full) == (None, "Phillies +1.5")


# ── the draw, and per-inning markets: found live 2026-07-30 ─────────────

@pytest.mark.parametrize("text,want", [
    ("Tie (Reg. Time)", True),      # how the VENUE writes it
    ("Draw", True),                 # how the FEED writes it
    ("The Draw", True),
    ("Draw (Full Time)", True),
    ("FC Lahti", False),
    ("Sligo Rovers", False),
    ("Over 2.5", False),            # a different bet entirely
    ("Tie in first 5 innings", False),
    # A club merely CONTAINING the word must not be classified as the draw —
    # that would price a team against the draw's fair value.
    ("Tie Break FC United", False),
    ("Drawbridge City", False),
])
def test_draw_synonyms(text, want):
    from edge.fairvalue.lines import is_draw

    assert is_draw(text) is want


def test_per_inning_markets_are_segments_not_full_games():
    """'atc-mlb-tex-tb-2026-07-29-i9-tex' is INNING 9. Priced against the
    full-game line it produced a -96c 'edge' (ask 0.98 vs a 0.02 fair value)
    and, more dangerously, several in the 2-8c range that the implausibility
    guard would have passed through to a real order."""
    from edge.fairvalue.lines import bet_identity, slug_segment

    assert slug_segment("atc-mlb-tex-tb-2026-07-29-i9-tex") == "i9"
    assert slug_segment("atc-mlb-cle-cin-2026-07-29-i3-cin") == "i3"
    assert slug_segment("atc-bra-vit-pal-2026-07-29-pal") is None
    ident = bet_identity("atc-mlb-tex-tb-2026-07-29-i9-tex", "", "Rangers")
    assert ident.segment == "i9"


def test_an_inning_number_is_never_read_as_a_team_code():
    from edge.venues.pmus_slug import parse_slug

    p = parse_slug("atc-mlb-tex-tb-2026-07-29-i9-tex")
    assert p.side == "tex" and p.codes == ("tex", "tb")


def test_a_three_way_soccer_market_prices_all_three_outcomes(tmp_path, monkeypatch):
    """The venue's 'Tie (Reg. Time)' scored 0.24 against the feed's 'Draw',
    so every three-way soccer market silently lost its third outcome — 2,989
    rejections in one live cycle, the largest single loss in the funnel."""
    from edge.execution.risk import RiskManager
    from edge.fairvalue.feed import FeedEvent
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from edge.venues.base import BookLevel, MarketBook
    from edge.venues.mapper import VenueMarket

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))

    class Venue:
        name = "kalshi"
        book_errors: dict = {}

        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT", title="Dundalk vs. Sligo Rovers",
                league_code="epl",
                outcome_tokens={"Dundalk": "T-DUN", "Sligo Rovers": "T-SLR",
                                "Tie (Reg. Time)": "T-DRAW"})]

        def get_book(self, market_id, token):
            return MarketBook(venue=self.name, market_id=market_id,
                              outcome_id=token, bids=[BookLevel(0.26, 400)],
                              asks=[BookLevel(0.28, 400)], ts=time.time())

        def taker_fee(self, price):
            return 0.0

        def maker_fee(self, price):
            return 0.0

        def plan_entry(self, book):
            return book.asks[0].price, True

    class Feed:
        def fetch_events(self, sport_key):
            return [FeedEvent(
                sport_key="soccer_epl", league_code="epl", home="Dundalk",
                away="Sligo Rovers", commence_ts=time.time() + 3600,
                h2h={"Dundalk": 3.0, "Sligo Rovers": 3.0, "Draw": 3.0},
                books=6, anchors=1, fetched_at=time.time())]

        def server_clock_skew_s(self):
            return 0.0

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**POLICY.risk, "mode": "PAPER"})
    funnel = run_cycle([Venue()], Feed(), POLICY, risk, led, ["soccer_epl"])
    assert funnel["rejects"].get("no_side_match_moneyline", 0) == 0
    assert led.position("kalshi:T-DRAW") is not None   # the draw traded


# ── unit-aware totals/spreads: tennis and friends ───────────────────────
#
# "Over 23.5 games" IS the tennis totals market the feed already carries;
# "Over 2.5 sets" merely looks like it. The old regex anchored at the
# number, so BOTH fell through to the team matcher and died as
# no_side_match_moneyline — 858 refusals in one measured cycle, none of
# them about edge.

def test_unit_suffixed_totals_parse_as_totals():
    from edge.fairvalue.lines import parse_outcome_line

    p = parse_outcome_line("Over 23.5 games")
    assert (p.kind, p.side, p.point, p.unit) == ("total", "over", 23.5, "games")
    q = parse_outcome_line("Under 21.5 games")
    assert (q.kind, q.side, q.point) == ("total", "under", 21.5)
    # The unit-less form stays exactly as it was.
    r = parse_outcome_line("Over 8.5")
    assert (r.kind, r.point, r.unit) == ("total", 8.5, None)


def test_unit_suffixed_spreads_parse_as_spreads():
    from edge.fairvalue.lines import parse_outcome_line

    p = parse_outcome_line("Jaume Munar +3.5 games")
    assert (p.kind, p.team, p.point, p.unit) == ("spread", "Jaume Munar", 3.5,
                                                 "games")


def test_a_stated_unit_must_match_what_the_sharp_quote_counts():
    """Pairing 'Over 2.5 sets' against a games total at the same number
    would manufacture a fair value for a different proposition. Stated
    units must equal the sport's primary unit; unknown families fail
    closed."""
    from edge.fairvalue.lines import unit_conflicts

    assert not unit_conflicts("games", "tennis_atp")
    assert not unit_conflicts(None, "tennis_atp")        # common venue form
    assert unit_conflicts("sets", "tennis_atp")
    assert not unit_conflicts("goals", "soccer_epl")
    assert unit_conflicts("corners", "soccer_epl")
    assert unit_conflicts("games", "dartsport_x")        # unknown family


def test_set_winners_are_segments_not_moneylines():
    """'Set 1 Winner' is a partial-match bet — the tennis analogue of a
    first-half line. It must refuse as a missing SEGMENT quote, never be
    scored against the players' names as a moneyline."""
    from edge.fairvalue.lines import split_segment

    assert split_segment("Set 1 Winner")[0] == "s1"
    assert split_segment("2nd Set Winner")[0] == "s2"
    assert split_segment("Arsenal")[0] is None


def test_tennis_games_total_prices_end_to_end(tmp_path, monkeypatch):
    """The whole point of the unit work: a venue 'Over 22.5 games' pairs
    against the feed's tennis totals and TRADES, while 'Over 2.5 sets'
    at the same event refuses on the unit."""
    from edge.execution.risk import RiskManager
    from edge.fairvalue.feed import FeedEvent
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from edge.venues.base import BookLevel, MarketBook
    from edge.venues.mapper import VenueMarket

    class TennisVenue:
        name = "kalshi"
        book_errors = {}

        def __init__(self):
            self.asks = {"T-OVER": 0.30, "T-SETS": 0.30}

        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT-TEN", title="Munar vs. Hijikata",
                league_code="wta",
                outcome_tokens={"Over 22.5 games": "T-OVER",
                                "Over 2.5 sets": "T-SETS"})]

        def get_book(self, market_id, token):
            ask = self.asks[token]
            return MarketBook(venue=self.name, market_id=market_id,
                              outcome_id=token,
                              bids=[BookLevel(ask - 0.02, 500)],
                              asks=[BookLevel(ask, 1000)], ts=time.time())

        def taker_fee(self, price):
            return 0.0

        def maker_fee(self, price):
            return 0.0

        def plan_entry(self, book):
            return (book.asks[0].price, True) if book.asks else (0.0, True)

    class Feed:
        def sport_keys(self):
            return ["tennis_wta_x"]

        def server_clock_skew_s(self):
            return 0.0

        def fetch_events(self, sport_key):
            ev = FeedEvent(
                sport_key="tennis_wta_x", league_code="wta",
                home="Jaume Munar", away="Rinky Hijikata",
                commence_ts=time.time() + 3600,
                h2h={"Jaume Munar": 2.0, "Rinky Hijikata": 2.0},
                # Sharp games total: Over/Under 22.5 at odds implying ~0.35
                # for the Over — 5c of edge over a 0.30 ask.
                totals={"Over 22.5": 2.857, "Under 22.5": 1.538},
                fetched_at=time.time(), anchors=1)
            return [ev]

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    ledger = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(ledger, {**POLICY.risk, "mode": "PAPER"})
    funnel = run_cycle([TennisVenue()], Feed(), POLICY, risk, ledger,
                       ["tennis_wta_x"])
    assert funnel["logged"] == 1, funnel["rejects"]
    assert funnel["rejects"].get("total_unit_mismatch", 0) >= 1
    assert funnel["rejects"].get("no_side_match_moneyline", 0) == 0


# ── margin prose is a run-line spread, priced or refused as one ─────────

def test_margin_prose_becomes_the_spread_it_is():
    from edge.fairvalue.lines import margin_to_spread, parse_outcome_line

    assert margin_to_spread("Baltimore Orioles wins by over 1.5 runs in first 5 innings") \
        == "Baltimore Orioles -1.5"
    # "N+ runs" is margin >= N: the -(N - 0.5) line, same half-point logic
    # as props.
    assert margin_to_spread("Atlanta Braves wins by 2+ runs") == "Atlanta Braves -1.5"
    assert margin_to_spread("Arsenal wins by over 2.5 goals") == "Arsenal -2.5"
    assert margin_to_spread("Atlanta Braves") is None
    assert margin_to_spread("Over 8.5") is None
    p = parse_outcome_line(margin_to_spread("Atlanta Braves wins by 2+ runs"))
    assert (p.kind, p.team, p.point) == ("spread", "Atlanta Braves", -1.5)


def test_margin_prose_never_parses_as_a_player_prop():
    """'wins by over 2.5 runs' contains 'runs' and a number, so the prop
    parser would read it as batter_runs_scored for a player named
    'atlanta braves wins by' — observed live. It refuses by name."""
    from edge.fairvalue.props import parse_prop

    bet, why = parse_prop("Atlanta Braves wins by over 2.5 runs")
    assert bet is None and why == "team_margin_not_prop"
