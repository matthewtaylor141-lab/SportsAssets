"""THE FEE THE VENUE ACTUALLY STATES, AND THE LINE THE FEED ACTUALLY MEANS.

Two fixes, one file, because both are the same mistake in different
places: a reader that had the right answer available and never asked for
it.

(1) FEE CAPTURE. `_commission_fields` reads the per-execution commission
    keys and has never seen a value in one, and no change to it can
    invent one. The venue states the fee on the POSITION -- `cost` is
    all-in, `baseCost` is the same position before fees -- so
    `position_basis` subtracts them and gets the venue's own arithmetic.
    This is the number the owner's standing-order rule needs: a sell
    that never rests below basis + margin + fee.

(2) LINE GRAMMAR. `_us_slug_candidates` built the moneyline pair for
    every slug whose head was three tokens, and a spread's head IS three
    tokens -- the feed states the line AFTER the date. So a spread and a
    total each resolved onto the moneyline of the same game. That is not
    a missed copy, it is a position in the wrong market.

Neither fix is on an order path today: trading is paused and the basis
keys are recorded by the shadow and read by nothing.
"""
from __future__ import annotations

import math

import pytest

from sportsassets import pmus
from sportsassets.copy_sports import _us_slug_candidates, market_type_of


# ------------------------------------------------------------------ fee

def test_the_fee_is_cost_minus_base_cost_and_the_source_says_so():
    """The venue's own subtraction, not a fee model of ours."""
    b = pmus.position_basis({"netPosition": {"value": "100"},
                             "cost": {"value": "45.60"},
                             "baseCost": {"value": "45.00"}})
    assert b["fees"] == pytest.approx(0.60)
    assert b["source"] == "cost_minus_base"
    # all-in per share -- this is `c` in `c + q < 1.00`
    assert b["basis_px"] == pytest.approx(0.456)
    assert b["fee_px"] == pytest.approx(0.006)


def test_a_stated_fee_wins_over_the_subtraction_and_is_named_stated():
    b = pmus.position_basis({"netPosition": 10, "cost": 4.0,
                             "baseCost": 3.5, "fees": 0.4})
    assert b["fees"] == 0.4 and b["source"] == "stated"


def test_an_unread_fee_is_none_and_never_zero():
    """A zero fee and an unread fee price a sell differently, so the
    reader must not collapse them -- the same rule _commission_fields
    keeps."""
    b = pmus.position_basis({"netPosition": 10, "cost": 4.0})
    assert b["fees"] is None and b["source"] is None
    assert b["fee_px"] is None
    assert b["basis_px"] == pytest.approx(0.4)   # basis still readable


def test_a_short_reads_per_share_like_a_long():
    b = pmus.position_basis({"netPosition": -50, "cost": 20.0, "baseCost": 19.5})
    assert b["net"] == -50
    assert b["basis_px"] == pytest.approx(0.4)   # unsigned
    assert b["fees"] == pytest.approx(0.5)


def test_a_flat_position_has_no_per_share_anything():
    b = pmus.position_basis({"netPosition": 0, "cost": 5.0, "baseCost": 5.0})
    assert b["basis_px"] is None and b["fee_px"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "nan", "Infinity", "1e400"])
def test_a_non_finite_reading_is_refused_rather_than_written(bad):
    """These reach json.dumps and the jsonb column rejects the bare
    NaN / Infinity tokens they write -- one such value would fail the
    whole row."""
    assert pmus.position_basis({"netPosition": 10, "cost": bad})["cost"] is None
    assert pmus.position_basis({"netPosition": bad, "cost": 1})["net"] is None


def test_a_bool_is_not_a_number():
    assert pmus.position_basis({"netPosition": 10, "cost": True})["cost"] is None
    assert pmus.position_basis(
        {"netPosition": 10, "cost": {"value": True}})["cost"] is None


def test_a_non_dict_reads_as_all_unknown():
    b = pmus.position_basis("not a position")
    assert all(v is None for v in b.values())


def test_the_owner_rule_closes_over_the_venue_basis():
    """`c + q < 1.00` with c from the venue: the arithmetic the standing
    sell will rest on, exercised end to end so the basis reader and the
    rule cannot drift apart."""
    b = pmus.position_basis({"netPosition": 200, "cost": 91.0, "baseCost": 90.0})
    c = b["basis_px"]
    assert c == pytest.approx(0.455)
    m = 0.02
    # q_max solves 0.06q^2 - 1.06q + (1 - m - c) = 0
    q_max = (1.06 - math.sqrt(1.1236 - 0.24 * (1 - m - c))) / 0.12
    assert c + q_max + 0.06 * q_max * (1 - q_max) == pytest.approx(1.0 - m, abs=1e-9)


# --------------------------------------------------------------- grammar

def test_a_moneyline_still_builds_the_moneyline_pair():
    out = _us_slug_candidates("epl-mun-che-2026-09-10", "Manchester United")
    assert out[0] == "atc-epl-mun-che-2026-09-10-che"
    assert "aec-epl-mun-che-2026-09-10" in out


@pytest.mark.parametrize("slug", [
    "epl-mun-che-2026-09-10-neg-1pt5",
    "epl-mun-che-2026-09-10-o2pt5",
    "epl-mun-che-2026-09-10-over-2pt5",
    "spl-sha-riy-2026-08-25-spread-away-1pt5",
    "nfl-ne-sea-2026-09-09-total-44pt5",
])
def test_a_spread_or_total_never_offers_the_moneyline_of_the_same_game(slug):
    """THE DEFECT. Each of these has a three-token head, so each used to
    produce the game's aec- moneyline as an exact candidate -- a real
    market, and the wrong one."""
    out = _us_slug_candidates(slug, "Over 2.5")
    assert not any(c.startswith("aec-") or c.startswith("atc-") for c in out), out


def test_a_total_builds_the_venue_total_grammar():
    out = _us_slug_candidates("epl-mun-che-2026-09-10-o2pt5", "Over 2.5")
    assert out[0] == "tsc-epl-mun-che-2026-09-10-tot-2pt5"


def test_a_spread_builds_the_venue_spread_prefix_with_the_line():
    out = _us_slug_candidates("epl-mun-che-2026-09-10-neg-1pt5", "Man Utd -1.5")
    assert out[0] == "asc-epl-mun-che-2026-09-10-1pt5"


def test_the_line_is_the_market_and_the_direction_is_the_outcome():
    """'o2pt5' and 'u2pt5' are the same market -- the venue names it by
    the line and you pick a side, exactly as a moneyline names the game."""
    over = _us_slug_candidates("epl-mun-che-2026-09-10-o2pt5", "Over 2.5")
    under = _us_slug_candidates("epl-mun-che-2026-09-10-u2pt5", "Under 2.5")
    assert over[0] == under[0] == "tsc-epl-mun-che-2026-09-10-tot-2pt5"


def test_a_line_the_suffix_does_not_state_builds_nothing():
    """A fabricated line is a live probe into a market that exists and is
    the wrong bet, so a spread with no readable line constructs nothing
    and leaves only the raw slug."""
    slug = "epl-mun-che-2026-09-10-spread"
    assert market_type_of(slug) == "spread"
    assert _us_slug_candidates(slug, "Man Utd") == [slug]


@pytest.mark.parametrize("slug", [
    "epl-mun-che-2026-09-10-btts",
    "epl-mun-che-2026-09-10-es-2-0",
    "epl-mun-che-2026-09-10-somethingunparsed",
])
def test_a_type_this_parser_cannot_name_constructs_nothing(slug):
    """Unknown has never been tradeable anywhere else in that file and
    it is not tradeable here."""
    assert _us_slug_candidates(slug, "Yes") == [slug]


def test_the_raw_slug_is_always_the_last_candidate():
    for slug in ("epl-mun-che-2026-09-10",
                 "epl-mun-che-2026-09-10-o2pt5",
                 "epl-mun-che-2026-09-10-btts"):
        assert _us_slug_candidates(slug, "x")[-1] == slug


def test_an_empty_slug_yields_no_candidates():
    assert _us_slug_candidates("", "x") == []
