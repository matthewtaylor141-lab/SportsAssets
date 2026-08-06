"""Sport-weighted copy portfolio assignments (owner directive 2026-08-06)."""

from sportsassets.copy_sports import copy_allowed, league_of


def test_league_parsing_handles_both_slug_grammars():
    assert league_of("atc-nba-lal-bos-2026-11-01-lal") == "nba"
    assert league_of("aec-mlb-sf-tex-2026-08-05") == "mlb"
    assert league_of("uslc-tul-srp-2026-08-05-srp") == "uslc"
    assert league_of("astatc-atp-nunbor-tometc-2026-08-06-es-0-2") == "atp"


def test_assignments_route_each_sport_to_its_account():
    assert copy_allowed("kch123", "atc-nba-lal-bos-2026-11-01-lal")
    assert copy_allowed("kch123", "asc-nfl-kc-buf-2026-09-10-kc-3pt5")
    assert copy_allowed("kch123", "atc-nhl-tor-mtl-2026-10-12-tor")
    assert copy_allowed("HomeRunHazard", "aec-mlb-sf-tex-2026-08-05")
    assert copy_allowed("homerunhazard", "atc-wnba-dal-wsh-2026-08-05-wsh")
    assert copy_allowed("RN1", "aec-atp-rafjod-cormou-2026-08-06")
    assert copy_allowed("rn1", "aec-wta-vikgol-igaswi-2026-08-05")
    assert copy_allowed("swisstony", "atc-lgscup-mia-asl-2026-08-05-mia")
    assert copy_allowed("swisstony", "atc-epl-ars-che-2026-08-15-ars")


def test_unassigned_pairs_fail_closed():
    assert not copy_allowed("RN1", "aec-mlb-sf-tex-2026-08-05")
    assert not copy_allowed("kch123", "aec-mlb-sf-tex-2026-08-05")
    assert not copy_allowed("swisstony", "atc-nba-lal-bos-2026-11-01-lal")
    assert not copy_allowed("swisstony", "aec-atp-rafjod-cormou-2026-08-06")
    assert not copy_allowed("HomeRunHazard", "atc-nba-lal-bos-2026-11-01-lal")
    assert not copy_allowed("somebody_new", "atc-epl-ars-che-2026-08-15-ars")
    assert not copy_allowed("", "atc-epl-ars-che-2026-08-15-ars")
    assert not copy_allowed("swisstony", "")


def test_market_type_parsing():
    from sportsassets.copy_sports import market_type_of
    assert market_type_of("atc-nba-lal-bos-2026-11-01-lal") == "moneyline"
    assert market_type_of("aec-mlb-sf-tex-2026-08-05") == "moneyline"
    assert market_type_of("asc-nfl-kc-buf-2026-09-10-kc-3pt5") == "spread"
    assert market_type_of("tsc-mlb-tor-hou-2026-08-08-o8pt5") == "total"
    assert market_type_of("astatc-lgscup-tol-sea-2026-08-05-ftts-sea") == "btts"
    assert market_type_of("astatc-atp-nunbor-tometc-2026-08-06-es-0-2") == "exact_score"
    assert market_type_of("astatc-mlb-nyy-bos-2026-08-08-hr-judge") == "prop"
    assert market_type_of("uslc-tul-srp-2026-08-05-srp") == "moneyline"
    assert market_type_of("") == "unknown"
