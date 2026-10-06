from sportsassets import settlement_rule_registry as R

def test_polymarket_nfl():
    t=("Overtime is included if played. If the game ends in a tie, the market will settle to $0.50. "
       "If delayed, postponed, or suspended and not rescheduled to a date within two weeks, "
       "the market will settle to the last fair market price. Outcome sourced from NFL.")
    e=R.polymarket_us_rule_evidence(t,sport_family="football"); s=e["settlement"]
    assert s["overtime_included"] is True and s["draw_rule"]==R.SCALAR_0_50
    assert s["postponement_window_hours"]==336.0 and s["void_rule"]==R.LAST_FAIR_PRICE
    assert e["verification_sources"]==["NFL"]

def test_polymarket_nba_48h():
    t=("Overtime is included if played. If delayed, postponed, suspended, or otherwise rescheduled and "
       "is not rescheduled to start within two calendar days, the market will settle to the last fair market price.")
    s=R.polymarket_us_rule_evidence(t)["settlement"]
    assert s["overtime_included"] is True and s["postponement_window_hours"]==48.0

def test_polymarket_nhl_shootout():
    t=("Overtime and any shootout are included if played. If postponed and not rescheduled to start within "
       "two calendar days, the market will settle to the last fair market price.")
    s=R.polymarket_us_rule_evidence(t)["settlement"]
    assert s["overtime_included"] is True and s["shootout_included"] is True

def test_polymarket_mlb_extra_innings():
    t=("Extra innings are included if played. If postponed and not rescheduled to a date within two weeks, "
       "the market will settle to the last fair market price.")
    s=R.polymarket_us_rule_evidence(t)["settlement"]
    assert s["extra_innings_included"] is True and s["overtime_included"] is True

def test_period_specific_ot_exclusion_wins():
    assert R.polymarket_us_rule_evidence("Important: Overtime does not count for 2H and 4Q markets.")["settlement"]["overtime_included"] is False

def test_kalshi_tie_and_48h():
    m={"rules_primary":("In the event of a tie, all markets will resolve at 50c. If postponed but begins within 48 hours, "
                         "the market remains open. If cancelled or not started within 48 hours, all markets resolve to a fair market price.")}
    s=R.kalshi_rule_evidence(m)["settlement"]
    assert s["draw_rule"]==R.SCALAR_0_50 and s["postponement_window_hours"]==48.0
    assert s["void_rule"]==R.LAST_FAIR_PRICE and s["postponement_payout"]==R.LAST_FAIR_PRICE

def test_kalshi_dnp_special_not_generic_void():
    e=R.kalshi_rule_evidence({"rules_primary":"If a player is active but never takes a snap, the market resolves to a fair price."})
    assert "DNP_OR_NONSTARTER_FAIR_PRICE" in e["special_conditions"]
    assert "void_rule" not in e["settlement"]

def test_kalshi_tennis_retirement():
    e=R.kalshi_rule_evidence({"rules_secondary":"If a retirement occurs, markets that cannot be unconditionally settled resolve to a Fair Market Price."})
    assert "RETIREMENT_UNSETTLED_COMPONENTS_FAIR_PRICE" in e["special_conditions"]

def test_plain_winner_rule_does_not_invent_ot():
    e=R.kalshi_rule_evidence({"rules_primary":"Resolves Yes if Team A wins the professional game. Outcome verified from League X."})
    assert e["settlement"]=={} and e["status"]==R.PARTIAL and e["verification_sources"]==["League X"]

def test_explicit_structured_rule_wins_and_conflict_is_named():
    c={"rules_text":"Overtime is included if played.","settlement":{"overtime_included":False}}
    got=R.enrich_contract(c,venue=R.POLYMARKET_US)
    assert got["settlement"]["overtime_included"] is False
    assert got["settlement_rule_evidence"]["status"]==R.CONFLICT

def test_fill_only_missing():
    c={"rules_text":"Overtime is included if played. If not rescheduled within two weeks, settle to the last fair market price.",
       "settlement":{"draw_rule":"IMPOSSIBLE"}}
    got=R.enrich_contract(c,venue=R.POLYMARKET_US)
    assert got["settlement"]["draw_rule"]=="IMPOSSIBLE" and got["settlement"]["overtime_included"] is True

def test_no_text_no_guess():
    e=R.polymarket_us_rule_evidence("",sport_family="football")
    assert e["status"]==R.ABSENT and e["settlement"]=={}

def test_fingerprint_normalizes_whitespace():
    assert R.fingerprint(" a   b ")==R.fingerprint("a b")
