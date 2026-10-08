from decimal import Decimal
from bettor_kalshi_venue.models import CanonicalEvent, Book, PROVEN
from bettor_kalshi_venue.normalization import binary_proposition_instrument
from bettor_kalshi_venue.canonical import (
    are_equivalent, are_complements, equivalence_classes, claim_fingerprint
)
from bettor_kalshi_venue.quotes import (
    kalshi_taker_fee, zero_fee, best_split_route
)
from bettor_kalshi_venue.arbitrage import (
    scan_complement_arbitrage, best_acquisition_for_claim
)
from bettor_kalshi_venue.mapping_contract import (
    FixtureIdentity, VenueFixture, MarketIdentity, map_fixture_exact, map_market_exact
)

NOW=1000.0
EVENT=CanonicalEvent(
    event_key="MLB:2026-10-07:NYY@TB",
    sport="BASEBALL", league="MLB", start_time="2026-10-07T23:05:00Z",
    outcomes=("NYY","TB","VOID","POSTPONED"), exhaustive=True,
    basis="CANONICAL_FIXTURE_PLUS_RULES"
)
NONSTD={"VOID":Decimal(".5"),"POSTPONED":Decimal(".5")}

def inst(venue, iid, market, side, subject, *, status=PROVEN):
    return binary_proposition_instrument(
        venue=venue,instrument_id=iid,market_id=market,side=side,event=EVENT,
        family="MONEYLINE",period="FULL_GAME",subject_outcomes={subject},
        nonstandard_payoff=NONSTD,rules_status=status,mapping_status=status,
        settlement_status=status,orderable_id=iid,sport="BASEBALL"
    )

def book(i, asks, age=1):
    return Book(i.venue,i.instrument_id,i.side,tuple((Decimal(str(p)),q) for p,q in asks),
                NOW-age,5,True)

def test_yankees_yes_equals_rays_no_when_payoffs_prove_it():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    rn=inst("KALSHI","k-tb","m-tb","NO","TB")
    assert are_equivalent(EVENT,yy,rn)
    assert claim_fingerprint(EVENT,yy)==claim_fingerprint(EVENT,rn)

def test_yankees_yes_is_not_rays_yes():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    ry=inst("KALSHI","k-tb","m-tb","YES","TB")
    assert not are_equivalent(EVENT,yy,ry)
    assert are_complements(EVENT,yy,ry)

def test_best_price_can_be_opponents_no_on_same_venue():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    rn=inst("KALSHI","k-tb","m-tb","NO","TB")
    books={yy.key:book(yy,[(".49",50)]), rn.key:book(rn,[(".45",50)])}
    r=best_acquisition_for_claim(EVENT,[yy,rn],yy,books,qty=10,now=NOW,
                                 fee_by_venue={"KALSHI":kalshi_taker_fee})
    assert r is not None
    assert r.allocations[0]["instrument_id"]=="k-tb"
    assert r.allocations[0]["side"]=="NO"

def test_best_price_is_all_in_not_displayed_price():
    # Kalshi displayed ask is lower, but an exaggerated fee model makes PMUS cheaper all-in.
    ky=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    py=inst("POLYMARKET_US","p-nyy","p-nyy","YES","NYY")
    books={ky.key:book(ky,[(".45",50)]), py.key:book(py,[(".46",50)])}
    fees={"KALSHI":lambda c,p: Decimal("1.00"), "POLYMARKET_US":zero_fee}
    r=best_acquisition_for_claim(EVENT,[ky,py],ky,books,qty=10,now=NOW,fee_by_venue=fees)
    assert r.allocations[0]["venue"]=="POLYMARKET_US"

def test_router_can_split_depth_across_equivalent_claims():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    rn=inst("KALSHI","k-tb","m-tb","NO","TB")
    py=inst("POLYMARKET_US","p-nyy","p-nyy","YES","NYY")
    classes,_=equivalence_classes(EVENT,[yy,rn,py])
    cl=next(c for c in classes if yy in c.instruments)
    books={
      yy.key:book(yy,[(".44",3)]),
      rn.key:book(rn,[(".45",3)]),
      py.key:book(py,[(".46",20)]),
    }
    r=best_split_route(cl,books,8,now=NOW,
                       fee_by_venue={"KALSHI":zero_fee,"POLYMARKET_US":zero_fee})
    assert sum(a["qty"] for a in r.allocations)==8
    assert r.topology=="CROSS_VENUE_SPLIT"
    assert {a["instrument_id"] for a in r.allocations}=={"k-nyy","k-tb","p-nyy"}

def test_same_venue_kalshi_arbitrage_is_first_class():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    ry=inst("KALSHI","k-tb","m-tb","YES","TB")
    books={yy.key:book(yy,[(".45",50)]), ry.key:book(ry,[(".45",50)])}
    opps,census=scan_complement_arbitrage(
        EVENT,[yy,ry],books,now=NOW,fee_by_venue={"KALSHI":zero_fee},max_qty=10
    )
    assert opps and opps[0].topology=="SAME_VENUE"
    assert opps[0].guaranteed_profit==Decimal("1.0")

def test_same_venue_polymarket_arbitrage_is_first_class():
    yy=inst("POLYMARKET_US","p-nyy","p-nyy","YES","NYY")
    ry=inst("POLYMARKET_US","p-tb","p-tb","YES","TB")
    books={yy.key:book(yy,[(".47",50)]), ry.key:book(ry,[(".48",50)])}
    opps,_=scan_complement_arbitrage(
        EVENT,[yy,ry],books,now=NOW,fee_by_venue={"POLYMARKET_US":zero_fee},max_qty=10
    )
    assert opps and opps[0].topology=="SAME_VENUE"
    assert opps[0].guaranteed_profit==Decimal(".5")

def test_cross_venue_arbitrage_selects_best_leg_venue():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    py=inst("POLYMARKET_US","p-nyy","p-nyy","YES","NYY")
    ry=inst("KALSHI","k-tb","m-tb","YES","TB")
    pr=inst("POLYMARKET_US","p-tb","p-tb","YES","TB")
    books={
      yy.key:book(yy,[(".44",50)]), py.key:book(py,[(".48",50)]),
      ry.key:book(ry,[(".50",50)]), pr.key:book(pr,[(".45",50)]),
    }
    opps,_=scan_complement_arbitrage(
        EVENT,[yy,py,ry,pr],books,now=NOW,
        fee_by_venue={"KALSHI":zero_fee,"POLYMARKET_US":zero_fee},max_qty=10
    )
    assert opps and opps[0].topology=="CROSS_VENUE"
    legs=opps[0].leg_a_route["allocations"]+opps[0].leg_b_route["allocations"]
    assert {x["venue"] for x in legs}=={"KALSHI","POLYMARKET_US"}

def test_stale_cheapest_book_is_not_used():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    rn=inst("KALSHI","k-tb","m-tb","NO","TB")
    books={yy.key:book(yy,[(".48",50)]), rn.key:book(rn,[(".40",50)],age=20)}
    r=best_acquisition_for_claim(EVENT,[yy,rn],yy,books,qty=10,now=NOW,
                                 fee_by_venue={"KALSHI":zero_fee})
    assert r.allocations[0]["instrument_id"]=="k-nyy"

def test_unproven_rules_cannot_join_equivalence_class():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    rn=inst("KALSHI","k-tb","m-tb","NO","TB",status="NOT_PROVEN")
    classes,refused=equivalence_classes(EVENT,[yy,rn])
    assert len(classes)==1
    assert refused

def test_three_way_no_is_not_other_team_yes():
    soccer=CanonicalEvent("EPL:X","SOCCER","EPL","2026-10-07T19:00:00Z",
                          ("ARS","DRAW","CHE","VOID","POSTPONED"),True,"RULES")
    nonstd={"VOID":Decimal(".5"),"POSTPONED":Decimal(".5")}
    ars=binary_proposition_instrument(
        venue="KALSHI",instrument_id="ars",market_id="ars",side="YES",event=soccer,
        family="MONEYLINE",period="REGULATION",subject_outcomes={"ARS"},
        nonstandard_payoff=nonstd)
    che_no=binary_proposition_instrument(
        venue="KALSHI",instrument_id="che",market_id="che",side="NO",event=soccer,
        family="MONEYLINE",period="REGULATION",subject_outcomes={"CHE"},
        nonstandard_payoff=nonstd)
    assert che_no.payoff["DRAW"]==1
    assert ars.payoff["DRAW"]==0
    assert not are_equivalent(soccer,ars,che_no)

def test_fee_formula_matches_current_kalshi_shape():
    assert kalshi_taker_fee(4,Decimal(".5"))==Decimal(".07")

def test_exact_fixture_mapping_rejects_cross_league_city_collision():
    canonical=FixtureIdentity("BASKETBALL","WNBA","WNBA:NYL","WNBA:LVA",1000)
    bad=VenueFixture("KALSHI","BASKETBALL","NBA","NBA:NYK","NBA:LAL",1000,"VENUE_IDS")
    assert map_fixture_exact(canonical,bad).status=="NOT_ESTABLISHED"

def test_fixture_mapping_requires_structured_basis():
    canonical=FixtureIdentity("BASEBALL","MLB","MLB:NYY","MLB:TB",1000)
    c=VenueFixture("KALSHI","BASEBALL","MLB","MLB:NYY","MLB:TB",1000,None)
    assert "STRUCTURED_BASIS_MISSING" in map_fixture_exact(canonical,c).reasons

def test_market_mapping_rejects_period_and_line_drift():
    a=MarketIdentity("TOTAL","FULL_GAME",Decimal("8.5"),None)
    b=MarketIdentity("TOTAL","FIRST_5",Decimal("8.0"),None)
    d=map_market_exact(a,b)
    assert d.status=="NOT_ESTABLISHED"
    assert set(d.reasons)=={"PERIOD_MISMATCH","LINE_MISMATCH"}

def test_no_arbitrage_when_fees_consume_edge():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    ry=inst("KALSHI","k-tb","m-tb","YES","TB")
    books={yy.key:book(yy,[(".49",10)]), ry.key:book(ry,[(".49",10)])}
    huge=lambda c,p: Decimal(".20")
    opps,_=scan_complement_arbitrage(EVENT,[yy,ry],books,now=NOW,
                                     fee_by_venue={"KALSHI":huge},max_qty=10)
    assert opps==[]

def test_pairing_can_use_opponents_no_as_cheapest_alias_for_one_leg():
    yy=inst("KALSHI","k-nyy","m-nyy","YES","NYY")
    rn=inst("KALSHI","k-tb-no","m-tb","NO","TB")
    ry=inst("KALSHI","k-tb-yes","m-tb","YES","TB")
    ny_no=inst("KALSHI","k-nyy-no","m-nyy","NO","NYY")
    books={
      yy.key:book(yy,[(".49",20)]), rn.key:book(rn,[(".44",20)]),
      ry.key:book(ry,[(".48",20)]), ny_no.key:book(ny_no,[(".46",20)]),
    }
    opps,_=scan_complement_arbitrage(EVENT,[yy,rn,ry,ny_no],books,now=NOW,
                                     fee_by_venue={"KALSHI":zero_fee},max_qty=10)
    assert opps
    all_allocs=opps[0].leg_a_route["allocations"]+opps[0].leg_b_route["allocations"]
    ids={a["instrument_id"] for a in all_allocs}
    # Cheapest NYY claim is Rays NO (.44); cheapest TB claim is Yankees NO (.46).
    assert ids=={"k-tb-no","k-nyy-no"}
