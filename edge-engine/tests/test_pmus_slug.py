"""Reading the side of the bet structurally, from the slug.

The venue frequently sets a market's `title` to its own slug. The old path
fuzzy-matched that slug STRING against team names, which failed two ways at
once in the live funnel:

  * 208 no_side_match_moneyline per cycle — nothing in
    "aec-mlb-sea-lad-2026-07-30" resembles "Seattle Mariners";
  * occasionally it matched the WRONG side, e.g.
    "aec-wta-xinwan-liusam-2026-07-29" priced at 1c against a 96c fair
    value. A 95-cent "edge" is not an opportunity, it is an inverted bet.

Every case below is a real slug taken from the live probe.
"""

import pytest

from edge.venues.pmus_slug import (
    CODE_PREFIX,
    code_score,
    looks_like_a_slug,
    parse_slug,
    resolve_side,
)


# ── grammar ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug,kind,league,codes,side", [
    ("atc-lmx-pue-gua-2026-07-31-gua", "atc", "lmx", ("pue", "gua"), "gua"),
    ("atc-irlp-dun-slr-2026-07-31-slr", "atc", "irlp", ("dun", "slr"), "slr"),
    ("aec-mlb-sea-lad-2026-07-30", "aec", "mlb", ("sea", "lad"), None),
    ("asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5", "asc", "mlb", ("nyy", "phi"), None),
])
def test_slug_grammar(slug, kind, league, codes, side):
    p = parse_slug(slug)
    assert (p.kind, p.league, p.codes, p.side) == (kind, league, codes, side)


def test_line_and_segment_decorations_are_never_read_as_a_team():
    """'-pos-1pt5' and '-f5-' must not be mistaken for a side code — that is
    how a spread market ends up claiming to be a team."""
    assert parse_slug("asc-lmx-san-atl-2026-07-25-neg-2pt5").side is None
    assert parse_slug("asc-mlb-nyy-phi-2026-07-24-f5-pos-1pt5").side is None


def test_a_slug_masquerading_as_an_outcome_name_is_recognised():
    assert looks_like_a_slug("aec-mlb-sea-lad-2026-07-30")
    assert not looks_like_a_slug("New York Yankees")
    assert not looks_like_a_slug("Over 8.5")


# ── the side, from two candidates ───────────────────────────────────────

@pytest.mark.parametrize("code,home,away,want", [
    # Club prefixes must not defeat the code.
    ("gua", "CD Guadalajara", "Club Puebla", "CD Guadalajara"),
    ("pue", "CD Guadalajara", "Club Puebla", "Club Puebla"),
    # Abbreviations that skip letters: SLigo Rovers.
    ("slr", "Sligo Rovers", "Dundalk", "Sligo Rovers"),
    ("dun", "Sligo Rovers", "Dundalk", "Dundalk"),
    # Initials.
    ("nyy", "Philadelphia Phillies", "New York Yankees", "New York Yankees"),
    ("phi", "Philadelphia Phillies", "New York Yankees", "Philadelphia Phillies"),
    ("sea", "Seattle Mariners", "Los Angeles Dodgers", "Seattle Mariners"),
    ("lad", "Seattle Mariners", "Los Angeles Dodgers", "Los Angeles Dodgers"),
    ("hel", "IFK Norrkoping", "Helsingborgs IF", "Helsingborgs IF"),
    ("nkp", "IFK Norrkoping", "Helsingborgs IF", "IFK Norrkoping"),
])
def test_real_slug_codes_resolve_to_the_right_team(code, home, away, want):
    assert resolve_side(code, home, away) == want


def test_an_unrecognisable_code_resolves_to_nothing():
    assert resolve_side("xyz", "Sligo Rovers", "Dundalk") is None
    assert resolve_side("", "Sligo Rovers", "Dundalk") is None


def test_an_ambiguous_code_refuses_rather_than_picking():
    """Two teams a code fits equally well is the inversion case. Getting it
    wrong does not produce a small error — it produces a bet on the opposite
    outcome, priced with the other side's number."""
    assert resolve_side("man", "Manchester United", "Manchester City") is None
    assert resolve_side("nor", "Norrby IF", "Norrkoping") is None


def test_scores_are_ordered_by_how_much_they_actually_identify():
    assert code_score("gua", "Guadalajara") > code_score("slr", "Sligo Rovers")
    assert code_score("xyz", "Sligo Rovers") == 0.0


# ── end to end: the inversion cannot happen any more ────────────────────

def test_an_unresolvable_side_is_refused_by_name(tmp_path, monkeypatch):
    """The aec- family carries no side code. Rather than fuzzy-matching a
    slug and sometimes landing on the wrong player, the outcome is refused
    with a reason that names the code we could not place."""
    from edge.execution.engine import Policy
    from edge.execution.risk import RiskManager
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from edge.venues.mapper import VenueMarket
    from tests.test_run_cycle_e2e import StubFeed, StubVenue, _event

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    policy = Policy.load()
    # mechanics test: lift the measured moneyline quarantine
    policy.leagues = {**policy.leagues, "blocked_categories": []}
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**policy.risk, "mode": "PAPER"})

    class CodeVenue(StubVenue):
        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT", title="Arsenal vs. Chelsea", league_code="epl",
                outcome_tokens={f"{CODE_PREFIX}zzz": "T-?"})]

    funnel = run_cycle([CodeVenue(ask_price=0.30)], StubFeed([_event()]),
                       policy, risk, led, ["soccer_epl"])
    assert led.summary()["fills"] == 0
    assert funnel["rejects"].get("unresolved_slug_code", 0) >= 1
    ex = funnel["unpriced_examples"]["unresolved_slug_code"][0]
    assert ex["code"] == "zzz" and ex["home"] == "Arsenal"


def test_a_resolvable_side_prices_and_trades(tmp_path, monkeypatch):
    """And the 208-a-cycle loss becomes a trade: a slug code that names a
    side is now priced like any other outcome."""
    from edge.execution.engine import Policy
    from edge.execution.risk import RiskManager
    from edge.ledger.service import Ledger
    from edge.shadow.runner import run_cycle
    from edge.venues.mapper import VenueMarket
    from tests.test_run_cycle_e2e import StubFeed, StubVenue, _event

    monkeypatch.setenv("EDGE_DATA_DIR", str(tmp_path))
    policy = Policy.load()
    # mechanics test: lift the measured moneyline quarantine
    policy.leagues = {**policy.leagues, "blocked_categories": []}
    led = Ledger(db_path=str(tmp_path / "l.sqlite3"))
    risk = RiskManager(led, {**policy.risk, "mode": "PAPER"})

    class CodeVenue(StubVenue):
        def discover_markets(self, league_codes):
            return [VenueMarket(
                market_id="EVT", title="Arsenal vs. Chelsea", league_code="epl",
                outcome_tokens={f"{CODE_PREFIX}ars": "T-ARS",
                                f"{CODE_PREFIX}che": "T-CHE"})]

    funnel = run_cycle([CodeVenue(ask_price=0.47)], StubFeed([_event()]),
                       policy, risk, led, ["soccer_epl"])
    assert funnel["logged"] == 2          # both sides resolved and priced
    assert led.position("kalshi:T-ARS")["shares"] > 0
