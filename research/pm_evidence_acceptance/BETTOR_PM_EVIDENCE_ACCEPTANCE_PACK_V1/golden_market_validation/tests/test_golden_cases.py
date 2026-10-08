import json
from pathlib import Path
from decimal import Decimal
from golden_validator.validator import event_match,market_match,payoff_equivalent,payoff_complements,best_route,arb_floor

DATA=json.loads((Path(__file__).parents[1]/"data"/"golden_cases.json").read_text())
C={x["id"]:x for x in DATA["cases"]}

def test_g01_nfl_structured_moneyline_fixture_is_frozen():
    x=C["G01_NFL_STRUCTURED_MONEYLINE"]
    assert x["event"]["league"]=="NFL" and x["market"]["family"]=="MONEYLINE"

def test_g02_wrong_start_refuses():
    x=C["G02_NFL_WRONG_START"]
    assert event_match(x["event"],x["candidate"])[1]=="START_TIME_MISMATCH"

def test_g03_wrong_team_refuses():
    x=C["G03_NFL_WRONG_TEAM"]
    assert event_match(x["event"],x["candidate"])[1]=="TEAM_MISMATCH"

def test_g04_spread_exact_line_maps():
    x=C["G04_SPREAD_LINE_MATCH"]
    assert market_match(x["market"],x["candidate"])[0]

def test_g05_spread_line_drift_refuses():
    x=C["G05_SPREAD_LINE_DRIFT"]
    assert market_match(x["market"],x["candidate"])[1]=="LINE_MISMATCH"

def test_g06_game_total_does_not_map_to_team_total():
    x=C["G06_TOTAL_VS_TEAM_TOTAL"]
    assert not market_match(x["market"],x["candidate"])[0]

def test_g07_settlement_window_difference_is_frozen_as_refusal():
    x=C["G07_NHL_SETTLEMENT_WINDOW_DIFFERS"]
    assert x["left"]["postponement_window_hours"] != x["right"]["postponement_window_hours"]

def test_g08_ebattles_never_maps_to_real_epl_on_names():
    x=C["G08_SOCCER_CROSS_COMPETITION_REFUSAL"]
    assert x["source_market"]["competition"]!=x["probability_source"]["competition"]
    assert x["expected"]["mapping"]=="NOT_ESTABLISHED"

def test_g09_soccer_sample_is_separate_binaries():
    assert C["G09_SOCCER_BINARY_CONTRACT_NOT_3WAY_LINE"]["expected"]["three_way_single_market"] is False

def test_g10_yankees_yes_equals_rays_no_only_by_payoff():
    x=C["G10_KALSHI_TWO_WAY_YES_NO_ALIAS"]
    ok,_=payoff_equivalent(x["event"]["outcomes"],x["claims"]["NYY_YES"],x["claims"]["TB_NO"])
    assert ok

def test_g11_opposite_yes_is_complement_not_alias():
    x=C["G11_KALSHI_TWO_WAY_OPPOSITE_YES"]
    outs=x["event"]["outcomes"]; a=x["claims"]["NYY_YES"]; b=x["claims"]["TB_YES"]
    assert not payoff_equivalent(outs,a,b)[0]
    assert payoff_complements(outs,a,b)

def test_g12_three_way_false_no_alias_is_rejected():
    x=C["G12_THREE_WAY_FALSE_NO_ALIAS"]
    ok,why=payoff_equivalent(x["event"]["outcomes"],x["claims"]["ARS_YES"],x["claims"]["CHE_NO"])
    assert not ok and "DRAW" in why

def test_g13_unknown_settlement_refuses():
    x=C["G13_UNKNOWN_SETTLEMENT_REFUSES"]
    ok,why=payoff_equivalent(x["event"]["outcomes"],x["left"],x["right"])
    assert not ok and why=="UNKNOWN_SETTLEMENT_STATE"

def test_g14_opponent_no_can_be_best_route():
    assert best_route(C["G14_BEST_PRICE_USES_OPPONENT_NO"]["routes"])=="KALSHI_TB_NO"

def test_g15_fee_can_reverse_screen_price():
    assert best_route(C["G15_FEE_REVERSES_SCREEN_PRICE"]["routes"])=="PMUS"

def test_g16_stale_cheap_route_loses():
    assert best_route(C["G16_STALE_CHEAP_ROUTE_LOSES"]["routes"])=="CURRENT_PMUS"

def test_g17_same_venue_arb_positive_floor():
    x=C["G17_SAME_VENUE_ARB"]
    assert arb_floor(x["legs"],x["guaranteed_payout"])==Decimal(".10")

def test_g18_cross_venue_arb_positive_floor():
    x=C["G18_CROSS_VENUE_ARB"]
    assert arb_floor(x["legs"],x["guaranteed_payout"])==Decimal(".11")
