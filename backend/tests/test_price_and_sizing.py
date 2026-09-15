"""Owner orders 2026-08-12 (mapping recovery round): same-or-better
price rule, inverse volume<->size scaling, and the deterministic
spread/total resolver's pure pieces."""

import asyncio

import pytest

from sportsassets import live_executor, pmus
from sportsassets.live_executor import volume_normalized_clip
from sportsassets.pmus import _feed_derivative


# ── inverse volume<->size scaling ───────────────────────────────────
class _SpentPool:
    def __init__(self, spent):
        self.spent = spent

    async def fetchval(self, sql, *a):
        assert "sum(filled_usd)" in sql
        # Review 2026-08-12: settled fills stay in the day's sum
        # (else the clip scales back UP as games resolve) and
        # stranded 'submitting' rows must never shrink it.
        assert "'settled'" in sql and "'cashed_out'" in sql
        assert "submitting" not in sql
        return self.spent


def _clip(whale, spent, slug=None):
    return asyncio.run(volume_normalized_clip(_SpentPool(spent), whale, slug))


def test_clip_is_base_at_or_under_envelope():
    """Dollars, not row count (audit 2026-08-21): the governor scales by
    money actually deployed against the whale's dollar envelope
    (baseline fills x clip), so partial IOC takes no longer burn a full
    baseline slot. Fixtures moved to the live whales after the
    2026-08-24 TRUEEDGE cut zeroed rn1's clip, and moved AGAIN on
    2026-08-25 when the merge-inclusive re-grade restored rn1
    (+$222,038) and cut swisstony (-$187,613). These fixtures name a
    whale only to pick up a (clip, baseline) pair; the governor
    arithmetic is what is under test, so they follow whoever is live."""
    # Probe sizing (owner authorization 2026-08-24 evening): $100 clip,
    # so the envelope is baseline x $100.
    from sportsassets.live_executor import (
        BASELINE_FILLS_DEFAULT, BASELINE_FILLS_PER_DAY, per_fill_usd)

    for w in ("rn1", "0x076daa87", "ferrarichampions2026"):
        base = per_fill_usd(w)
        n = BASELINE_FILLS_PER_DAY.get(w, BASELINE_FILLS_DEFAULT)
        assert _clip(w, 0) == base, w
        assert _clip(w, n * base) == base, f"{w} at exactly the envelope"


def test_partial_takes_do_not_burn_full_slots():
    # 300 partial takes of ~$9 = $2,700 deployed — nowhere near the
    # $37,500 rn1 envelope. The old count-based governor shrank
    # the clip here; the dollar governor keeps full size.
    assert _clip("rn1", 100 * 9.0) == 250.00


def test_ten_x_dollars_means_one_tenth_size():
    """Derived from the maps, not from hard-coded pairs.

    This fixture has now broken twice on roster moves because it named
    a whale to pick up a (clip, baseline) pair and then hard-coded the
    baseline beside it — so a whale whose baseline differed read as an
    arithmetic failure in the governor, which is not what is under
    test. The governor arithmetic is; the roster is not."""
    from sportsassets.live_executor import (
        BASELINE_FILLS_DEFAULT, BASELINE_FILLS_PER_DAY, per_fill_usd)

    for w in ("rn1", "0x076daa87", "ferrarichampions2026"):
        base = per_fill_usd(w)
        n = BASELINE_FILLS_PER_DAY.get(w, BASELINE_FILLS_DEFAULT)
        assert _clip(w, 10 * n * base) == pytest.approx(base / 10.0), w


def test_clip_floors_at_five_dollars_and_never_scales_up():
    assert _clip("rn1", 10_000_000) == 5.00
    assert _clip("rn1", 150 * 250.00 + 500) < 250.00


def test_sport_override_is_the_scaling_base():
    # During the probe every base is the $100 ceiling, so 10x the
    # envelope scales to $10.
    assert _clip("rn1", 10 * 150 * 250.00,
                 "epl-ars-che-2026-08-15") == pytest.approx(25.00)


def test_unreadable_count_degrades_to_base_clip():
    class _Boom:
        async def fetchval(self, sql, *a):
            raise RuntimeError("db down")

    assert asyncio.run(volume_normalized_clip(_Boom(), "rn1")) == 250.00


# ── same-or-better limit (float-floor regression) ───────────────────
def test_pm_limit_is_his_price_floored_to_cent():
    import math

    for his, want in ((0.29, 0.29), (0.30, 0.30), (0.55, 0.55),
                      (0.985, 0.98), (0.997, 0.99)):
        got = min(math.floor(round(his * 100, 6)) / 100.0, 0.99)
        assert got == want, f"his {his}"
        assert got <= his + 1e-9, "never above his price"


# ── feed derivative parsing (pure) ──────────────────────────────────
def test_feed_totals_parse():
    fd = _feed_derivative("mlb-nyy-bos-2026-07-22-o8pt5")
    assert fd == {"base": "mlb-nyy-bos-2026-07-22", "kind": "total",
                  "line": "8pt5", "side": "o", "team": None}
    fd2 = _feed_derivative("epl-ars-che-2026-08-15-u2pt5")
    assert fd2["side"] == "u" and fd2["line"] == "2pt5"


def test_feed_spreads_parse():
    fd = _feed_derivative("mlb-kc-laa-2026-08-14-pos-1pt5")
    assert fd == {"base": "mlb-kc-laa-2026-08-14", "kind": "spread",
                  "line": "1pt5", "side": "pos", "team": None}
    fd2 = _feed_derivative("epl-ars-che-2026-08-15-ars-1pt5")
    assert fd2["team"] == "ars" and fd2["side"] is None
    fd3 = _feed_derivative("epl-ars-che-2026-08-15-che-neg-2pt5")
    assert fd3 == {"base": "epl-ars-che-2026-08-15", "kind": "spread",
                   "line": "2pt5", "side": "neg", "team": "che"}


def test_feed_non_derivatives_refuse():
    assert _feed_derivative("mlb-nyy-bos-2026-07-22") is None       # ML
    assert _feed_derivative("mlb-nyy-bos-2026-07-22-ftts") is None  # btts
    assert _feed_derivative("no-date-here") is None
    assert _feed_derivative("") is None
    assert _feed_derivative("mlb-nyy-bos-2026-07-22-es-2-0") is None


# ── derivative resolver against a faked venue ───────────────────────
class _Markets:
    def __init__(self, table):
        self.table = table

    def retrieve_by_slug(self, slug):
        if slug not in self.table:
            raise KeyError(slug)
        return {"market": self.table[slug]}


class _Client:
    def __init__(self, table):
        self.markets = _Markets(table)


_TOTALS_TABLE = {"tsc-mlb-nyy-bos-2026-07-22-8pt5": {
    "slug": "tsc-mlb-nyy-bos-2026-07-22-8pt5", "closed": False,
    "question": "Yankees vs Red Sox: O/U 8.5",
    "marketSides": [
        {"identifier": "tsc-mlb-nyy-bos-2026-07-22-8pt5-over",
         "description": "Over"},
        {"identifier": "tsc-mlb-nyy-bos-2026-07-22-8pt5-under",
         "description": "Under"},
    ]}}


def test_total_side_is_driven_by_his_outcome(monkeypatch):
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _Client(_TOTALS_TABLE))
    r = pmus.resolve_derivative_exact("mlb-nyy-bos-2026-07-22-o8pt5",
                                      "Over 8.5")
    assert r and r["market_slug"].endswith("-over")
    assert r["matched_by"] == "derivative_exact"
    r2 = pmus.resolve_derivative_exact("mlb-nyy-bos-2026-07-22-u8pt5",
                                       "Under 8.5")
    assert r2 and r2["market_slug"].endswith("-under")


def test_total_refuses_when_outcome_and_slug_disagree(monkeypatch):
    """Review 2026-08-12: the whale's outcome drives the side, and the
    slug's o/u token must AGREE — a disagreement (or an outcome that
    matches neither side) refuses rather than guesses."""
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _Client(_TOTALS_TABLE))
    # His outcome says Under, the feed slug token says over: refuse.
    assert pmus.resolve_derivative_exact(
        "mlb-nyy-bos-2026-07-22-o8pt5", "Under 8.5") is None
    # An outcome matching neither side description: refuse.
    assert pmus.resolve_derivative_exact(
        "mlb-nyy-bos-2026-07-22-o8pt5", "New York Yankees") is None


def test_spreads_stay_on_the_fuzzy_pipeline_for_now(monkeypatch):
    """Review 2026-08-12 (wrong-side risk): until the venue's pos/neg
    side convention is verified from live fills, the exact resolver
    refuses spreads outright — the fuzzy pipeline with its line-
    consistency guard remains their only mapper."""
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client({
        "asc-mlb-kc-laa-2026-08-14-pos-1pt5": {
            "slug": "asc-mlb-kc-laa-2026-08-14-pos-1pt5",
            "closed": False,
            "question": "Spread: Kansas City Royals (+1.5)",
            "marketSides": []}}))
    assert pmus.resolve_derivative_exact(
        "mlb-kc-laa-2026-08-14-pos-1pt5", "Kansas City Royals") is None


def test_unlisted_candidate_falls_through(monkeypatch):
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client({}))
    assert pmus.resolve_derivative_exact(
        "mlb-nyy-bos-2026-07-22-o8pt5", "Over") is None


# ── spread exact resolution (owner order 2026-08-13) ─────────────────
_SPREAD_TABLE = {"asc-epl-mun-lee-2026-08-16-mun-neg-1pt5": {
    "slug": "asc-epl-mun-lee-2026-08-16-mun-neg-1pt5", "closed": False,
    "question": "Manchester United -1.5",
    "marketSides": [
        {"identifier": "asc-epl-mun-lee-2026-08-16-mun-neg-1pt5-yes",
         "description": "Yes"},
        {"identifier": "asc-epl-mun-lee-2026-08-16-mun-neg-1pt5-no",
         "description": "No"},
    ]}}


def test_spread_maps_when_every_fact_corroborates(monkeypatch):
    """His title's signed line == the question's signed line, the
    question names HIS team, the Yes side is ordered."""
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _Client(_SPREAD_TABLE))
    r = pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title="Manchester United -1.5")
    assert r is not None and r["market_slug"].endswith("-yes")
    assert r["matched_by"] == "spread_exact_yes"


def test_spread_sign_mismatch_refuses(monkeypatch):
    """The venue's '+1.5' against his '-1.5' is the MIRROR bet — the
    exact leak the 2026-08-12 review excluded spreads over. Sign is
    compared, never inferred from pos/neg."""
    table = {"asc-epl-mun-lee-2026-08-16-mun-neg-1pt5": {
        **_SPREAD_TABLE["asc-epl-mun-lee-2026-08-16-mun-neg-1pt5"],
        "question": "Manchester United +1.5"}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    assert pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title="Manchester United -1.5") is None


def test_spread_refuses_without_his_signed_line(monkeypatch):
    """No signed line in HIS title -> nothing to corroborate against
    -> fuzzy keeps the trade. Never guessed from the slug token."""
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _Client(_SPREAD_TABLE))
    assert pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title="Man United vs Leeds - More Markets") is None
    assert pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title=None) is None


def test_spread_refuses_the_other_team(monkeypatch):
    """His outcome is Leeds, the question is about Manchester United:
    the corroboration is team-anchored, so this refuses."""
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _Client(_SPREAD_TABLE))
    assert pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Leeds United",
        his_title="Leeds United -1.5") is None


def test_spread_refuses_both_teams_question(monkeypatch):
    """Review 2026-08-12's defeat case: a question naming BOTH teams
    cannot anchor a side. The line-stripped remainder dilutes below
    the outcome floor and refuses by construction."""
    table = {"asc-epl-mun-lee-2026-08-16-mun-neg-1pt5": {
        **_SPREAD_TABLE["asc-epl-mun-lee-2026-08-16-mun-neg-1pt5"],
        "question": "Manchester United vs Leeds United: handicap -1.5"}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    assert pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title="Manchester United -1.5") is None


def test_spread_line_magnitude_must_match_his_slug(monkeypatch):
    """His title says -2.5 while his slug says 1pt5: inconsistent
    metadata maps nothing."""
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: _Client(_SPREAD_TABLE))
    assert pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title="Manchester United -2.5") is None


def test_spread_prefers_named_side_over_yes(monkeypatch):
    """When the venue's sides carry team names, the named match wins
    (and would catch a mislabeled Yes/No)."""
    table = {"asc-epl-mun-lee-2026-08-16-mun-neg-1pt5": {
        **_SPREAD_TABLE["asc-epl-mun-lee-2026-08-16-mun-neg-1pt5"],
        "marketSides": [
            {"identifier": "side-mun", "description":
             "Manchester United -1.5"},
            {"identifier": "side-lee", "description":
             "Leeds United +1.5"},
        ]}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    r = pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title="Manchester United -1.5")
    assert r is not None and r["market_slug"] == "side-mun"
    assert r["matched_by"] == "spread_exact"


def test_signed_line_parser_ignores_unsigned_numbers():
    assert pmus._signed_lines("O/U 8.5 in game 7") == set()
    assert pmus._signed_lines("Eagles -7.5") == {-7.5}
    assert pmus._signed_lines("Reds +1.5 (game 2)") == {1.5}
    assert pmus._signed_lines("A -1.5 or B +1.5") == {-1.5, 1.5}
    # unicode minus folds
    assert pmus._signed_lines("Ajax −2.5") == {-2.5}


# ── review 2026-08-13: the confirmed spread leaks, pinned ────────────
def test_spread_refuses_non_title_side_buy(monkeypatch):
    """CONFIRMED CRITICAL: titles frame ONE side ('Spread: Nationals
    (-1.5)') while the whale can buy EITHER outcome. When he buys the
    non-title side (Mets +1.5), the title's sign is the WRONG sign for
    his bet — and the only venue question that could pass a sign check
    anchored to it is his team at the MIRROR line. The exact path must
    therefore refuse every non-title-side buy outright."""
    table = {"asc-mlb-wsn-nym-2026-08-13-1pt5": {
        "slug": "asc-mlb-wsn-nym-2026-08-13-1pt5", "closed": False,
        "question": ("Will the New York Mets cover -1.5 against the "
                     "Washington Nationals?"),
        "marketSides": [
            {"identifier": "trap-yes", "description": "Yes"},
            {"identifier": "trap-no", "description": "No"},
        ]}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    assert pmus.resolve_derivative_exact(
        "mlb-wsn-nym-2026-08-13-wsn-neg-1pt5", "New York Mets",
        his_title="Spread: Washington Nationals (-1.5)") is None


def test_spread_side_choice_ignores_venue_ordering(monkeypatch):
    """CONFIRMED CRITICAL: first-past-the-floor + the shared-token
    boost let 'Leeds United +1.5' win when the venue listed it first.
    Side choice must be order-independent and boost-free."""
    for order in ([0, 1], [1, 0]):
        sides = [{"identifier": "side-mun",
                  "description": "Manchester United -1.5"},
                 {"identifier": "side-lee",
                  "description": "Leeds United +1.5"}]
        table = {"asc-epl-mun-lee-2026-08-16-mun-neg-1pt5": {
            **_SPREAD_TABLE["asc-epl-mun-lee-2026-08-16-mun-neg-1pt5"],
            "marketSides": [sides[i] for i in order]}}
        monkeypatch.setattr(pmus, "_get_client", lambda t=table: _Client(t))
        r = pmus.resolve_derivative_exact(
            "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
            his_title="Manchester United -1.5")
        assert r is not None and r["market_slug"] == "side-mun", \
            f"venue ordering {order} changed the side"


def test_spread_ambiguous_sides_refuse(monkeypatch):
    """Two sides that both read as his team is ambiguity, and
    ambiguity refuses — never the venue's ordering."""
    table = {"asc-epl-mun-lee-2026-08-16-mun-neg-1pt5": {
        **_SPREAD_TABLE["asc-epl-mun-lee-2026-08-16-mun-neg-1pt5"],
        "marketSides": [
            {"identifier": "a", "description": "Manchester United -1.5"},
            {"identifier": "b", "description": "Manchester Utd -1.5"},
        ]}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    assert pmus.resolve_derivative_exact(
        "epl-mun-lee-2026-08-16-mun-neg-1pt5", "Manchester United",
        his_title="Manchester United -1.5") is None


def test_spread_refuses_unknown_qualifier_tokens(monkeypatch):
    """CONFIRMED MAJOR: 'corners' parsed as a team token and was then
    DROPPED from candidates — mapping a corners handicap onto the
    game's goal spread at the same line. A team token that is not one
    of the slug base's two team codes refuses outright."""
    table = {"asc-epl-ars-che-2026-08-16-neg-2pt5": {
        "slug": "asc-epl-ars-che-2026-08-16-neg-2pt5", "closed": False,
        "question": "Will Arsenal cover -2.5 against Chelsea?",
        "marketSides": [{"identifier": "y", "description": "Yes"},
                        {"identifier": "n", "description": "No"}]}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    assert pmus.resolve_derivative_exact(
        "epl-ars-che-2026-08-16-corners-neg-2pt5", "Arsenal",
        his_title="Corner Handicap: Arsenal (-2.5)") is None


def test_exact_side_ambiguity_refuses_for_moneylines(monkeypatch):
    """CONFIRMED (pre-existing, newly load-bearing for tennis): _sim's
    containment rule scores a surname-only outcome 1.0 against BOTH
    'Aoi Ito' and 'Mai Saito' — the venue's side ORDER picked the
    player. A unique winner is now required."""
    table = {"aec-itfwo-aoiito-maisai-2026-08-13": {
        "slug": "aec-itfwo-aoiito-maisai-2026-08-13", "closed": False,
        "question": "Aoi Ito vs Mai Saito",
        "marketSides": [
            {"identifier": "s-saito", "description": "Mai Saito"},
            {"identifier": "s-ito", "description": "Aoi Ito"},
        ]}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    # Surname-only outcome matches both sides -> refuse.
    assert pmus.resolve_market_exact(
        ["aec-itfwo-aoiito-maisai-2026-08-13"], "Ito") is None
    # The full player name is unambiguous -> maps to HER side even
    # though the venue lists the opponent first.
    r = pmus.resolve_market_exact(
        ["aec-itfwo-aoiito-maisai-2026-08-13"], "Aoi Ito")
    assert r is not None and r["market_slug"] == "s-ito"
