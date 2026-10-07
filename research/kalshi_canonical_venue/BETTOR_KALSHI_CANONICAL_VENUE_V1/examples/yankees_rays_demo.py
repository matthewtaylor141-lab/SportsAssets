from decimal import Decimal
import json
from bettor_kalshi_venue.models import CanonicalEvent, Book
from bettor_kalshi_venue.normalization import binary_proposition_instrument
from bettor_kalshi_venue.quotes import kalshi_taker_fee, zero_fee
from bettor_kalshi_venue.arbitrage import best_acquisition_for_claim, scan_complement_arbitrage

NOW=1000.0
event=CanonicalEvent(
    "MLB:2026-10-07:NYY@TB","BASEBALL","MLB","2026-10-07T23:05:00Z",
    ("NYY","TB","VOID","POSTPONED"),True,"DEMO_STRUCTURED_FIXTURE"
)
nonstd={"VOID":Decimal(".5"),"POSTPONED":Decimal(".5")}

def I(v,i,m,s,subject):
    return binary_proposition_instrument(
        venue=v,instrument_id=i,market_id=m,side=s,event=event,
        family="MONEYLINE",period="FULL_GAME",subject_outcomes={subject},
        nonstandard_payoff=nonstd,sport="BASEBALL")

nyy_yes=I("KALSHI","K-NYY-YES","K-NYY","YES","NYY")
rays_no=I("KALSHI","K-RAYS-NO","K-RAYS","NO","TB")
rays_yes=I("KALSHI","K-RAYS-YES","K-RAYS","YES","TB")
pm_nyy=I("POLYMARKET_US","P-NYY","P-NYY","YES","NYY")
pm_rays=I("POLYMARKET_US","P-RAYS","P-RAYS","YES","TB")

def B(i,p,q=100):
    return Book(i.venue,i.instrument_id,i.side,((Decimal(p),q),),NOW-1,5,True)

books={
    nyy_yes.key:B(nyy_yes,".49"),
    rays_no.key:B(rays_no,".45"),
    rays_yes.key:B(rays_yes,".50"),
    pm_nyy.key:B(pm_nyy,".47"),
    pm_rays.key:B(pm_rays,".44"),
}
fees={"KALSHI":kalshi_taker_fee,"POLYMARKET_US":zero_fee}
instruments=[nyy_yes,rays_no,rays_yes,pm_nyy,pm_rays]

route=best_acquisition_for_claim(
    event,instruments,nyy_yes,books,qty=10,now=NOW,fee_by_venue=fees
)
opps,census=scan_complement_arbitrage(
    event,instruments,books,now=NOW,fee_by_venue=fees,max_qty=10
)
print(json.dumps({
    "best_nyy_claim_route":{
        "all_in":str(route.all_in),
        "effective_per_contract":str(route.effective_per_contract),
        "allocations":route.allocations,
    },
    "arb":[{
        "topology":o.topology,
        "qty":o.qty,
        "all_in_cost":str(o.all_in_cost),
        "guaranteed_profit":str(o.guaranteed_profit),
        "leg_a_route":o.leg_a_route,
        "leg_b_route":o.leg_b_route,
    } for o in opps],
    "census":census,
},indent=2,default=str))
