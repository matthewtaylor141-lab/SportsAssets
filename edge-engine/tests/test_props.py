"""Player props: the mapper that must refuse rather than guess.

A mis-mapped team market usually produces an absurd edge the implausibility
guard catches. A mis-mapped PROP produces a confident, plausible fair value
for a DIFFERENT bet, and that is indistinguishable from edge until it
settles. Every test here is a way that could happen.
"""

import pytest

from edge.fairvalue.props import (PropBet, fair_for_prop, norm_player,
                                  parse_prop)


# ── "N+" is a half-point bet ────────────────────────────────────────────

def test_n_plus_is_the_half_point_below():
    """'5+ strikeouts' is P(X >= 5) = Over 4.5. Pricing it off Over 5.0 is a
    different, worse bet — on a whole line exactly 5 pushes."""
    bet, why = parse_prop("Shota Imanaga 5+ strikeouts")
    assert why is None
    assert bet == PropBet("shota imanaga", "pitcher_strikeouts", "Over", 4.5)


def test_a_whole_number_line_does_not_satisfy_an_n_plus_bet():
    bet, _ = parse_prop("Shota Imanaga 5+ strikeouts")
    quotes = {("pitcher_strikeouts", "shota imanaga", 5.0):
              {"Over": 1.95, "Under": 1.95}}
    fair, miss = fair_for_prop(bet, quotes)
    assert fair is None
    assert miss["reason"] == "no_prop_quote_at_point"
    assert miss["points_offered"] == [5.0]


def test_explicit_over_and_under_are_taken_literally():
    assert parse_prop("Aaron Judge over 1.5 total bases")[0] == \
        PropBet("aaron judge", "batter_total_bases", "Over", 1.5)
    assert parse_prop("Aaron Judge under 1.5 total bases")[0] == \
        PropBet("aaron judge", "batter_total_bases", "Under", 1.5)


# ── overlapping stat names ──────────────────────────────────────────────

def test_hits_allowed_is_a_pitcher_market_not_a_batter_one():
    """Substring matching maps one to the other silently. Longest phrase
    wins, so 'hits allowed' can never be swallowed by 'hits'."""
    assert parse_prop("Will Warren 4+ hits allowed")[0].market_key == \
        "pitcher_hits_allowed"
    assert parse_prop("Aaron Judge 2+ hits")[0].market_key == "batter_hits"


def test_earned_runs_allowed_is_not_runs_scored():
    assert parse_prop("Robbie Ray 1+ earned runs allowed")[0].market_key == \
        "pitcher_earned_runs"
    assert parse_prop("Mookie Betts 2+ runs")[0].market_key == \
        "batter_runs_scored"


def test_outs_recorded_beats_bare_outs():
    assert parse_prop("Will Warren 14+ outs recorded")[0] == \
        PropBet("will warren", "pitcher_outs", "Over", 13.5)


# ── refusals ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,reason", [
    ("Chicago Cubs to win", "unknown_stat"),
    ("Shota Imanaga strikeouts", "no_threshold"),
    ("5+ strikeouts", "no_player"),
    ("", "empty"),
    ("Erling Haaland anytime goal scorer", "unknown_stat"),
])
def test_unmappable_text_is_refused_by_name(text, reason):
    bet, why = parse_prop(text)
    assert bet is None and why == reason


def test_a_one_sided_quote_is_refused():
    """A lone Over still carries the book's margin. Treating it as a
    probability hands us a couple of cents of imaginary edge on every prop."""
    bet, _ = parse_prop("Shota Imanaga 5+ strikeouts")
    quotes = {("pitcher_strikeouts", "shota imanaga", 4.5): {"Over": 1.90}}
    fair, miss = fair_for_prop(bet, quotes)
    assert fair is None and miss["reason"] == "one_sided_prop_quote"


def test_a_binary_market_is_refused_rather_than_forced_into_over_under():
    from edge.fairvalue.props import BINARY_MARKETS
    assert "player_goal_scorer_anytime" in BINARY_MARKETS


# ── the arithmetic ──────────────────────────────────────────────────────

def test_the_pair_is_de_vigged_and_sums_to_one():
    bet, _ = parse_prop("Shota Imanaga 5+ strikeouts")
    quotes = {("pitcher_strikeouts", "shota imanaga", 4.5):
              {"Over": 1.80, "Under": 2.10}}
    over, miss = fair_for_prop(bet, quotes)
    assert miss is None
    under, _ = fair_for_prop(
        PropBet("shota imanaga", "pitcher_strikeouts", "Under", 4.5), quotes)
    assert over + under == pytest.approx(1.0)
    assert over > under          # the shorter price is the likelier side


def test_player_names_fold_accents_and_suffixes():
    assert norm_player("José Ramírez") == norm_player("Jose Ramirez")
    assert norm_player("Ronald Acuña Jr.") == norm_player("Ronald Acuna")


# ── end to end through the pricing loop ─────────────────────────────────

def test_a_prop_market_prices_off_its_own_line(tmp_path, monkeypatch):
    """The whole point: a Polymarket prop slug resolves to the exact
    provider line for that player, and nothing else."""
    from edge.execution.engine import Policy
    from edge.execution.risk import RiskManager
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from tests.test_run_cycle_e2e import StubFeed, StubVenue, _event

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    POLICY = Policy.load()
    POLICY.leagues = {**POLICY.leagues, "blocked_categories": []}
    ev = _event()
    ev.props = {("pitcher_strikeouts", "shota imanaga", 4.5):
                {"Over": 1.80, "Under": 2.10}}

    class Venue(StubVenue):
        def discover_markets(self, league_codes):
            m = super().discover_markets(league_codes)
            for mm in m:
                mm.outcome_tokens = {"Shota Imanaga 5+ strikeouts": "T-K"}
            return m

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**POLICY.risk, "mode": "PAPER"})
    # fair(Over 4.5) de-vigged from 1.80/2.10 is ~0.540. Ask 0.50 gives a
    # 4.0c edge: over the 3.5c prop bar, and under the 8c implausibility
    # ceiling. (An earlier draft of this test used 0.45 — a 9c "edge" — and
    # the guard correctly refused it, which is the guard working.)
    funnel = run_cycle([Venue(ask_price=0.50)], StubFeed([ev]), POLICY, risk,
                       led, ["soccer_epl"])
    assert funnel.get("by_category", {}).get("prop", 0) >= 1


def test_a_prop_with_no_matching_line_is_refused_not_guessed(tmp_path, monkeypatch):
    from edge.execution.engine import Policy
    from edge.execution.risk import RiskManager
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from tests.test_run_cycle_e2e import StubFeed, StubVenue, _event

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    POLICY = Policy.load()
    POLICY.leagues = {**POLICY.leagues, "blocked_categories": []}
    ev = _event()
    # The book quotes 5.5; the venue asks about 5+ (which is 4.5). Different
    # bets — this must refuse rather than pair them.
    ev.props = {("pitcher_strikeouts", "shota imanaga", 5.5):
                {"Over": 1.80, "Under": 2.10}}

    class Venue(StubVenue):
        def discover_markets(self, league_codes):
            m = super().discover_markets(league_codes)
            for mm in m:
                mm.outcome_tokens = {"Shota Imanaga 5+ strikeouts": "T-K"}
            return m

    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**POLICY.risk, "mode": "PAPER"})
    funnel = run_cycle([Venue(ask_price=0.50)], StubFeed([ev]), POLICY, risk,
                       led, ["soccer_epl"])
    assert funnel["logged"] == 0
    assert funnel["rejects"].get("no_prop_quote_at_point", 0) >= 1


# ── coverage: parseable and requested must stay the same set ────────────

def test_every_stat_we_can_parse_is_a_stat_we_actually_request():
    """The silent-refusal bug class, pinned.

    `pitcher_walks` and `batter_stolen_bases` were in PROP_STATS — the
    engine knew exactly what those venue markets meant — but neither was in
    the provider request list, so every one of them was refused
    `no_prop_quote_at_point` with an empty `points_offered`. Not a bad
    trade, not a close call: a market we understood, priced by books we
    trust, thrown away every cycle because nobody asked for the quote.

    Nothing in the funnel distinguishes "no quote exists" from "we never
    asked", which is why this ran unnoticed. This test is the distinction.
    """
    from edge.fairvalue.feed import TheOddsAPIClient
    from edge.fairvalue.props import BINARY_MARKETS, PROP_STATS

    requested = {m for markets in TheOddsAPIClient.PROP_MARKETS.values()
                 for m in markets}
    parseable = set(PROP_STATS.values()) - BINARY_MARKETS
    missing = parseable - requested
    assert not missing, (
        f"parseable but never requested — guaranteed silent refusals: "
        f"{sorted(missing)}")


def test_alternate_lines_are_requested_and_fold_onto_the_base_market():
    """Alternate rungs are the SAME proposition at a different point.

    The provider quotes one standard point per player; the venue lists the
    whole ladder. Landing alternates under their own key would leave every
    non-standard rung unpriced — which was ~1,085 refusals a cycle, the
    largest single loss in the funnel.
    """
    from edge.fairvalue.feed import TheOddsAPIClient

    c = TheOddsAPIClient(api_key="k")
    markets = c._prop_markets("baseball_mlb")
    assert "pitcher_strikeouts" in markets
    assert "pitcher_strikeouts_alternate" in markets
    assert c.base_prop_market("pitcher_strikeouts_alternate") == \
        "pitcher_strikeouts"
    assert c.base_prop_market("pitcher_strikeouts") == "pitcher_strikeouts"
    # A market with no ladder must not gain a phantom alternate request.
    assert "pitcher_outs_alternate" not in markets


def test_alternate_outcomes_merge_into_the_base_markets_samples():
    from edge.fairvalue.feed import TheOddsAPIClient

    c = TheOddsAPIClient(api_key="k")
    raw = {"bookmakers": [{"key": "pinnacle", "markets": [
        {"key": "pitcher_strikeouts", "outcomes": [
            {"name": "Over", "description": "Shota Imanaga", "point": 5.5,
             "price": 1.9}]},
        {"key": "pitcher_strikeouts_alternate", "outcomes": [
            {"name": "Over", "description": "Shota Imanaga", "point": 3.5,
             "price": 1.4},
            {"name": "Over", "description": "Shota Imanaga", "point": 5.5,
             "price": 1.91}]},
    ]}]}
    got = c._absorb_props(
        raw, ["pitcher_strikeouts", "pitcher_strikeouts_alternate"], set())
    # every rung lands under the BASE key...
    assert all(k[0] == "pitcher_strikeouts" for k in got), sorted(got)
    # ...the ladder rung the standard market never offered is now priced...
    assert ("pitcher_strikeouts", "shota imanaga", 3.5) in got
    # ...and a rung quoted by both sources merges rather than splitting.
    assert len(got[("pitcher_strikeouts", "shota imanaga", 5.5)]["Over"]) == 2


def test_a_combo_stat_never_resolves_to_one_of_its_components():
    """'1+ hits + runs + RBIs' contains 'hits', 'runs' and 'rbis'. Pricing
    a three-way combo off a single-component line would look like a large,
    confident, entirely fictional edge."""
    from edge.fairvalue.props import parse_prop

    bet, why = parse_prop("Ronald Acuna Jr. 1+ hits + runs + RBIs")
    assert bet is not None, f"combo stat still unmapped: {why}"
    assert bet.market_key == "batter_hits_runs_rbis"
    assert bet.side == "Over" and bet.point == 0.5
    # And the components still resolve to themselves.
    assert parse_prop("Aaron Judge 2+ hits")[0].market_key == "batter_hits"
    assert parse_prop("Aaron Judge 2+ RBIs")[0].market_key == "batter_rbis"
