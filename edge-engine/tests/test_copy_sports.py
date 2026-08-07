"""Cell-level copy policy (owner directive 2026-08-06): whale x sport
x market-type x entry band, derived from four forensic reconstructions."""

from edge.shadow.copy_sports import (copy_allowed, kalshi_min_ask,
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
    assert copy_allowed("HomeRunHazard", "tsc-mlb-sf-tex-2026-08-05-o8pt5",
                        price=0.55)
    assert copy_allowed("homerunhazard", "tsc-wnba-dal-wsh-2026-08-05-o160",
                        price=0.62)
    assert copy_allowed("homerunhazard", "atc-wnba-dal-wsh-2026-08-05-wsh",
                        price=0.50)
    assert copy_allowed("swisstony", "atc-lgscup-mia-asl-2026-08-05-mia")
    assert copy_allowed("swisstony", "asc-epl-ars-che-2026-08-15-ars-1pt5")
    assert copy_allowed("swisstony", "uslc-tul-srp-2026-08-05-srp")


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


def test_homerunhazard_entry_band_50_to_95():
    slug = "tsc-mlb-sf-tex-2026-08-05-o8pt5"
    assert copy_allowed("homerunhazard", slug, price=0.50)
    assert copy_allowed("homerunhazard", slug, price=0.95)
    assert not copy_allowed("homerunhazard", slug, price=0.49), \
        "every sub-50c band in his book loses"
    assert not copy_allowed("homerunhazard", slug, price=0.97), \
        ">95c fails the $1M sample floor"
    # No band constraint for the others.
    assert copy_allowed("swisstony", "atc-epl-ars-che-2026-08-15-ars",
                        price=0.12)


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
    assert copy_allowed("homerunhazard", "mlb-tor-hou-2026-08-08-o8pt5",
                        price=0.60)
    # Audit fail-open scenarios refused:
    assert not copy_allowed("kch123", "nhl-tor-mtl-2026-10-12-tor-1pt5")
    assert not copy_allowed("homerunhazard",
                            "wnba-dal-wsh-2026-08-05-dal-3pt5", price=0.55)
    assert not copy_allowed("swisstony", "epl-ars-che-2026-08-15-es-2-0")
    # Banded whale with missing/garbage price: refused.
    assert not copy_allowed("homerunhazard", "mlb-tor-hou-2026-08-08-o8pt5")
    assert not copy_allowed("homerunhazard", "mlb-tor-hou-2026-08-08-o8pt5",
                            price="n/a")
