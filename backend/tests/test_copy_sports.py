"""Cell-level copy policy (owner directive 2026-08-06): whale x sport
x market-type x entry band, derived from four forensic reconstructions."""

from sportsassets.copy_sports import (copy_allowed, kalshi_min_ask,
                                     league_of, market_type_of)


def test_league_parsing_handles_both_slug_grammars():
    assert league_of("atc-nba-lal-bos-2026-11-01-lal") == "nba"
    assert league_of("aec-mlb-sf-tex-2026-08-05") == "mlb"
    assert league_of("uslc-tul-srp-2026-08-05-srp") == "uslc"
    assert league_of("astatc-atp-nunbor-tometc-2026-08-06-es-0-2") == "atp"


def test_market_type_parsing():
    assert market_type_of("atc-nba-lal-bos-2026-11-01-lal") == "moneyline"
    assert market_type_of("asc-nfl-kc-buf-2026-09-10-kc-3pt5") == "spread"
    assert market_type_of("tsc-mlb-tor-hou-2026-08-08-o8pt5") == "total"
    assert market_type_of("astatc-lgscup-tol-sea-2026-08-05-ftts-sea") == "btts"
    assert market_type_of("astatc-atp-a-b-2026-08-06-es-0-2") == "exact_score"
    assert market_type_of("uslc-tul-srp-2026-08-05-srp") == "moneyline"
    assert market_type_of("") == "unknown"


def test_allowed_cells_route_to_their_accounts():
    assert copy_allowed("kch123", "asc-nba-lal-bos-2026-11-01-lal-5pt5")
    assert copy_allowed("kch123", "tsc-cbb-duke-unc-2026-12-01-o150pt5")
    assert copy_allowed("kch123", "asc-nfl-kc-buf-2026-09-10-kc-3pt5")
    assert copy_allowed("kch123", "atc-nhl-tor-mtl-2026-10-12-tor")
    # HomeRunHazard's cells moved to test_paused_whale_copies_nothing —
    # he is PAUSED (owner 2026-08-11) and copies nothing anywhere.
    # swisstony's soccer positives live in
    # test_soccer_price_floor_governs_the_resume (halt lifted 2026-08-12;
    # his copies now require price >= the 40c floor).


def test_disallowed_cells_fail_closed():
    # kch123: moneyline basketball / football NOT allowed; baseball never.
    assert not copy_allowed("kch123", "atc-nba-lal-bos-2026-11-01-lal")
    assert not copy_allowed("kch123", "atc-nfl-kc-buf-2026-09-10-kc")
    assert not copy_allowed("kch123", "tsc-mlb-sf-tex-2026-08-05-o8pt5")
    # HomeRunHazard: MLB moneyline and ALL spreads rejected by the data.
    assert not copy_allowed("homerunhazard", "aec-mlb-sf-tex-2026-08-05")
    assert not copy_allowed("homerunhazard", "asc-mlb-sf-tex-2026-08-05-1pt5")
    assert not copy_allowed("homerunhazard", "asc-wnba-dal-wsh-2026-08-05-3")
    # swisstony: totals/exact-score fail his bar; US majors excluded.
    assert not copy_allowed("swisstony", "tsc-epl-ars-che-2026-08-15-o2pt5")
    assert not copy_allowed("swisstony", "astatc-cdb-cru-cha-2026-08-05-es-2-0")
    assert not copy_allowed("swisstony", "atc-nba-lal-bos-2026-11-01-lal")
    # RN1: UNRESTRICTED (owner decision 2026-08-06 evening) — the live
    # sleeve's own 285-settled record of the first-entry rule (+$170.95)
    # governs, not the donor-book residual. Any sport, any market type,
    # no band.
    assert copy_allowed("RN1", "aec-atp-rafjod-cormou-2026-08-06")
    assert copy_allowed("rn1", "aec-wta-vikgol-igaswi-2026-08-05")
    assert copy_allowed("rn1", "mlb-nyy-bos-2026-07-22-o8pt5")
    assert copy_allowed("rn1", "nhl-tor-mtl-2026-08-06-pos1pt5", price=None)
    # Unknown whale / empty slug: closed.
    assert not copy_allowed("somebody_new", "atc-epl-ars-che-2026-08-15-ars")
    assert not copy_allowed("swisstony", "")


def test_paused_whale_copies_nothing(monkeypatch):
    """The PAUSED mechanism refuses every cell for a paused whale, at
    any price. HomeRunHazard came OFF this list 2026-08-21 (12W-8L,
    +$275.50, +104.6% ROI settled; prop bleed now blocked at the type
    level for everyone) — the mechanism is exercised with a synthetic
    entry, and HRH's strongest cell is asserted COPYABLE again."""
    from sportsassets import copy_sports as cs

    monkeypatch.setattr(cs, "PAUSED", frozenset({"homerunhazard"}))
    assert not copy_allowed("HomeRunHazard", "tsc-mlb-sf-tex-2026-08-05-o8pt5",
                            price=0.55)
    assert not copy_allowed("homerunhazard", "mlb-tor-hou-2026-08-08-o8pt5",
                            price=0.60)
    monkeypatch.setattr(cs, "PAUSED", frozenset())
    assert copy_allowed("HomeRunHazard", "tsc-mlb-sf-tex-2026-08-05-o8pt5",
                        price=0.55)
    # No band constraint for the unpaused (band-less) whales.
    assert copy_allowed("rn1", "aec-atp-rafjod-cormou-2026-08-06",
                        price=0.12)


def test_prop_markets_blocked_for_everyone():
    """Owner directive 2026-08-11: live prop copies graded 3W/16L
    (-64% ROI) — the type is blocked even for UNRESTRICTED whales."""
    prop = "astatc-mlb-kc-lad-2026-08-11-hr-tomedm-gte1"
    assert not copy_allowed("rn1", prop)
    assert not copy_allowed(
        "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465", prop)
    assert not copy_allowed("rn1", "astatc-atp-a-b-2026-08-11-hr-x-gte1")
    # Non-prop derivative types still governed by cells, not the blocklist:
    assert copy_allowed("rn1", "mlb-nyy-bos-2026-07-22-o8pt5")


def test_kalshi_fee_floor_for_thin_edge_cells():
    assert kalshi_min_ask("swisstony", "atc-epl-ars-che-2026-08-15-ars") == 0.70
    assert kalshi_min_ask("homerunhazard",
                          "tsc-mlb-sf-tex-2026-08-05-o8pt5") == 0.70
    assert kalshi_min_ask("homerunhazard",
                          "tsc-wnba-dal-wsh-2026-08-05-o160") == 0.0
    assert kalshi_min_ask("kch123", "atc-nhl-tor-mtl-2026-10-12-tor") == 0.0


def test_kindless_feed_grammar_market_types():
    """The whale FEED's slugs are kindless and league-led (audit
    2026-08-06) — the type lives in the post-date suffix. The fail-open
    scenarios from the audit are pinned here."""
    assert market_type_of("mlb-nyy-bos-2026-07-22") == "moneyline"
    assert market_type_of("uslc-tul-srp-2026-08-05-srp") == "moneyline"
    assert market_type_of("nhl-tor-mtl-2026-10-12-tor-1pt5") == "spread"
    assert market_type_of("nba-lal-bos-2026-11-01-lal-5pt5") == "spread"
    assert market_type_of("mlb-tor-hou-2026-08-08-o8pt5") == "total"
    assert market_type_of("epl-ars-che-2026-08-15-es-2-0") == "exact_score"
    assert market_type_of("lgscup-tol-sea-2026-08-05-ftts-sea") == "btts"
    assert market_type_of("mlb-tor-hou-2026-08-08-weird-99x") == "unknown"
    assert market_type_of("no-date-here-at-all") == "unknown"


def test_kindless_cells_route_correctly():
    # Strongest cells now reachable on production-shaped slugs:
    assert copy_allowed("kch123", "nba-lal-bos-2026-11-01-lal-5pt5")
    # (HomeRunHazard kindless-grammar cell now refused — he is paused;
    # covered in test_paused_whale_copies_nothing.)
    # Audit fail-open scenarios refused:
    assert not copy_allowed("kch123", "nhl-tor-mtl-2026-10-12-tor-1pt5")
    assert not copy_allowed("homerunhazard",
                            "wnba-dal-wsh-2026-08-05-dal-3pt5", price=0.55)
    assert not copy_allowed("swisstony", "epl-ars-che-2026-08-15-es-2-0")
    # Paused whale with missing/garbage price: refused (pause precedes
    # the band check, so these stay closed for two reasons).
    assert not copy_allowed("homerunhazard", "mlb-tor-hou-2026-08-08-o8pt5")
    assert not copy_allowed("homerunhazard", "mlb-tor-hou-2026-08-08-o8pt5",
                            price="n/a")


def test_promoted_whale_0x2c33_is_unrestricted():
    """Owner approval 2026-08-10: the vetted wallet copies UNRESTRICTED
    like RN1 (the vetting graded his WHOLE book, +0.76%/$1k over 1,712
    probes) — any sport, any market type, no band. His own settled
    record governs from here."""
    w = "0x2c335066FE58fe9237c3d3Dc7b275C2a034a0563-1759935795465"
    assert copy_allowed(w, "aec-atp-rafjod-cormou-2026-08-06")
    assert copy_allowed(w.lower(), "mlb-nyy-bos-2026-07-22-o8pt5")
    assert copy_allowed(w, "atc-nba-lal-bos-2026-11-01-lal", price=0.12)
    # The gate stays fail-closed for anyone NOT promoted.
    assert not copy_allowed("somebody_new", "aec-atp-rafjod-cormou-2026-08-06")


def test_copy_sports_twins_are_byte_identical():
    """backend/sportsassets/copy_sports.py and edge-engine/src/edge/
    shadow/copy_sports.py are ONE policy deployed to two services; a
    one-sided edit desynchronizes venue routing with no runtime error
    anywhere. Until 2026-08-10 only convention guarded this."""
    import pathlib

    import pytest

    here = pathlib.Path(__file__).resolve()
    root = next(
        (p for p in here.parents
         if (p / "backend" / "sportsassets" / "copy_sports.py").exists()
         and (p / "edge-engine" / "src" / "edge" / "shadow"
              / "copy_sports.py").exists()),
        None)
    if root is None:
        pytest.skip("sibling tree not present in this checkout")
    a = (root / "backend" / "sportsassets" / "copy_sports.py").read_bytes()
    b = (root / "edge-engine" / "src" / "edge" / "shadow"
         / "copy_sports.py").read_bytes()
    assert a == b, "edit both copies together — they are one policy"


def test_soccer_price_floor_governs_the_resume():
    """Soccer RESUMED 2026-08-12 (owner order, stacking fixes verified
    live) behind a 40c price floor: only his >= 40% lines copy, so a
    ladder's improbable rung (O3.5 at 22c) is skipped — and because a
    skip writes no row, the one-position-per-game slot stays free for
    his safest qualifying line. Binds EVERY whale incl. UNRESTRICTED;
    an unreadable price refuses; the misc bucket (table tennis,
    cricket) rides the same rule; US majors and tennis untouched."""
    # swisstony's measured cells, back on above the floor:
    assert copy_allowed("swisstony", "atc-epl-ars-che-2026-08-15-ars",
                        price=0.55)
    assert copy_allowed("swisstony", "asc-epl-ars-che-2026-08-15-ars-1pt5",
                        price=0.44)
    # The improbable rung refuses for every identity:
    assert not copy_allowed("swisstony", "atc-epl-ars-che-2026-08-15-ars",
                            price=0.22)
    assert not copy_allowed("rn1", "epl-ars-che-2026-08-15", price=0.39)
    assert copy_allowed("rn1", "epl-ars-che-2026-08-15", price=0.40)
    assert not copy_allowed(
        "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465",
        "atc-epl-ars-che-2026-08-15-ars", price=0.30)
    # No readable price: the floor is the protection — refuse.
    assert not copy_allowed("swisstony", "atc-epl-ars-che-2026-08-15-ars")
    assert not copy_allowed("rn1", "epl-ars-che-2026-08-15", price="junk")
    # Misc bucket rides the same floor:
    assert not copy_allowed("rn1", "setkameua-pakser-sydand-2026-08-11",
                            price=0.30)
    assert copy_allowed("rn1", "setkameua-pakser-sydand-2026-08-11",
                        price=0.55)
    # Non-soccer flow needs no price:
    assert copy_allowed("rn1", "aec-atp-rafjod-cormou-2026-08-06")
    assert copy_allowed("rn1", "mlb-nyy-bos-2026-07-22-o8pt5")


# ── to-a-tee program Phase 2 (owner order 2026-09-02) ────────────────

def test_segment_markets_are_props_not_the_game_market():
    """A first-half total typed as the GAME total (coverage lens §4(d):
    'itc-udi-ven-2026-09-02-first-half-total-0pt5' -> total) is a
    wrong-market hazard, not a refusal. A suffix naming a segment is a
    derivative of the game market: prop class, blocked for everyone,
    no premap prefix — it refuses instead of mis-routing."""
    assert market_type_of(
        "itc-udi-ven-2026-09-02-first-half-total-0pt5") == "prop"
    assert market_type_of("nba-lal-bos-2026-11-01-1h-total-110pt5") == "prop"
    assert market_type_of("epl-ars-che-2026-08-15-2h-over-0pt5") == "prop"
    assert market_type_of("nba-a-b-2026-11-01-q1-spread-neg-2pt5") == "prop"
    assert market_type_of("nhl-a-b-2026-10-01-period-1-moneyline") == "prop"
    assert market_type_of("atp-a-b-2026-09-03-set-1-winner") == "prop"
    assert market_type_of("cfb-a-b-2026-09-06-quarter-1-total-o7pt5") == "prop"
    # the feed's own attested spellings (coverage lens capture,
    # 2026-09-02, elc-qpr-car): 'halftime' outright, and the sibling
    # total shape that typed as the GAME total until the mapping unit's
    # review (2026-09-03) put the word in the set
    assert market_type_of("elc-qpr-car-2026-09-02-halftime-result") == "prop"
    assert market_type_of(
        "elc-qpr-car-2026-09-02-halftime-result-home") == "prop"
    assert market_type_of(
        "elc-qpr-car-2026-09-02-halftime-total-o0pt5") == "prop"
    assert market_type_of("epl-x-2026-08-24-ht-over-1pt5") == "prop"
    # and prop is refused for everyone, UNRESTRICTED whales included
    from sportsassets.copy_sports import copy_verdict

    assert copy_verdict(
        "rn1", "itc-udi-ven-2026-09-02-first-half-total-0pt5") \
        == "market_type_blocked"


def test_natural_sibling_spellings_are_segments_too():
    """The mapping unit's re-review (2026-09-03, minor): the set held
    the attested spellings only, and each natural sibling typed as the
    GAME market one spelling over ('h1' -> total, 'p1' -> moneyline,
    'sets-2-0' -> spread). The re-review deferred them to attestation;
    on a money path the missing token is the expensive error, so they
    are in the set now, and a team code that spells one refuses."""
    for s in ("nba-lal-bos-2026-11-01-1sthalf-total-110pt5",
              "nba-lal-bos-2026-11-01-h1-total-110pt5",
              "nba-lal-bos-2026-11-01-h2-total-110pt5",
              "nba-lal-bos-2026-11-01-1q-total-30pt5",
              "nba-lal-bos-2026-11-01-4q-spread-neg-2pt5",
              "epl-ars-che-2026-08-15-firsthalf-over-0pt5",
              "epl-ars-che-2026-08-15-secondhalf-over-0pt5",
              "epl-ars-che-2026-08-15-2ndhalf-result",
              "mlb-a-b-2026-09-06-f5-total-4pt5",
              "mlb-a-b-2026-09-06-1st-5-innings-total-4pt5",
              "mlb-a-b-2026-09-06-innings-1-5-total-4pt5",
              "mlb-a-b-2026-09-06-3rd-inning-total-0pt5",
              "nhl-a-b-2026-10-01-p1-moneyline",
              "nhl-a-b-2026-10-01-p3-total-1pt5",
              "atp-a-b-2026-09-03-total-sets-o2pt5",
              "atp-a-b-2026-09-03-sets-2-0",
              "lmx-ame-san-2026-08-29-h1"):
        assert market_type_of(s) == "prop", s
    # and the game-market readings beside them are untouched
    assert market_type_of("nba-lal-bos-2026-11-01-total-220pt5") == "total"
    assert market_type_of("nhl-a-b-2026-10-01-h2h") == "moneyline"
    assert market_type_of("atp-a-b-2026-09-03-o22pt5") == "total"
    assert market_type_of("mlb-a-b-2026-09-06-spread-neg-1pt5") == "spread"
    assert market_type_of("mlb-a-b-2026-09-06-o8pt5") == "total"


def test_a_lone_segment_token_is_a_segment_too():
    """'lmx-ame-san-2026-08-29-fh' typed as the team-code pick side, so
    premap.resolve mapped a first-half pick onto the game's moneyline
    with source 'premap' (reproduced 2026-09-03; the premap round-2
    tests pin the resolve side). Fail closed: a team whose code happens
    to be one of these literal tokens becomes an honest refusal."""
    assert market_type_of("lmx-ame-san-2026-08-29-fh") == "prop"
    assert market_type_of("lmx-ame-san-2026-08-29-sh") == "prop"
    assert market_type_of("itf-koyama-castelnuovo-2026-08-27-set") == "prop"


def test_the_family_table_is_otherwise_unchanged():
    """Every existing reading that is not a segment stays exactly as
    it was. ('ht' WAS pinned here as the game total on the first cut;
    the mapping unit's review, 2026-09-03, read it as the segment it
    is — see test_segment_markets_are_props_not_the_game_market.)"""
    assert market_type_of("mlb-sd-cin-2026-09-02-total-9pt5") == "total"
    assert market_type_of("epl-x-2026-08-24-over-1pt5") == "total"
    assert market_type_of("epl-x-2026-08-24-home-over-3pt5") == "prop"
    assert market_type_of("mlb-nyy-bos-2026-07-22") == "moneyline"
    assert market_type_of("csl-shs-bjg-2026-09-03-shs") == "moneyline"
    assert market_type_of("spl-sha-riy-2026-08-25-spread-away-1pt5") == "spread"
    assert market_type_of("nba-bos-mia-2026-08-24-bos-neg-10") == "spread"
    assert market_type_of("epl-ars-che-2026-08-15-es-2-0") == "exact_score"
    assert market_type_of("lgscup-tol-sea-2026-08-05-ftts-sea") == "btts"
    assert market_type_of("atc-nba-lal-bos-2026-11-01-lal") == "moneyline"
    assert market_type_of("tsc-mlb-tor-hou-2026-08-08-o8pt5") == "total"
    assert market_type_of("mlb-tor-hou-2026-08-08-weird-99x") == "unknown"


def test_the_candidate_grammar_lives_here_and_is_pure():
    """One feed-slug -> US-slug grammar for the copy lane, the underdog
    sleeve, the mirror and the runner: it lives in this module, which
    imports nothing from the backend, and live_executor re-exports the
    same objects so every existing import keeps working."""
    import inspect

    from sportsassets import copy_sports as cs
    from sportsassets import live_executor as le

    for name in ("_abbrev_player", "_tennis_candidates",
                 "_us_slug_candidates", "_TENNIS_LEAGUES",
                 "_TENNIS_US_CODES"):
        assert getattr(le, name) is getattr(cs, name), name
    assert inspect.getmodule(le._tennis_candidates) is cs
    for line in inspect.getsource(cs).splitlines():
        s = line.strip()
        assert not s.startswith(("from .", "from sportsassets",
                                 "import sportsassets", "import asyncpg")), s
    # the grammar still answers the shapes the copy lane relies on
    assert cs._tennis_candidates(
        "US Open ATP: Brandon Nakashima vs. Alex Michelsen",
        "atp-nakashi-michels-2026-09-02") == [
            "aec-atp-branak-alemic-2026-09-02",
            "aec-atp-alemic-branak-2026-09-02"]
    assert cs._us_slug_candidates("elc-qpr-car-2026-09-02-car", "Yes") == [
        "aec-elc-qpr-car-2026-09-02", "elc-qpr-car-2026-09-02-car"]
    assert cs._abbrev_player("João Sousa") == "joasou"
