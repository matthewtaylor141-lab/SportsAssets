from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal
from collections import defaultdict
from math import sqrt
from typing import Iterable

MECHANISMS=("DIRECTIONAL","SAME_VENUE_ARB","CROSS_VENUE_ARB",
            "ROUTING_SAVINGS","EXECUTION_ALPHA","ALLOCATION_ALPHA",
            "XAVIER_MANAGEMENT")

@dataclass(frozen=True)
class EventRow:
    event_key:str
    mechanism:str
    expected_pnl:Decimal
    realized_pnl:Decimal
    capital_hours:Decimal=Decimal("0")
    deployed_capital:Decimal=Decimal("0")
    settlement_proven:bool=True
    fees_complete:bool=True
    fresh:bool=True

@dataclass(frozen=True)
class RoutingRow:
    decision_id:str
    event_key:str
    chosen_route:str
    chosen_all_in:Decimal
    next_best_all_in:Decimal|None
    qty:Decimal
    claim_key:str
    fresh:bool=True

@dataclass(frozen=True)
class ArbRow:
    opportunity_id:str
    event_key:str
    topology:str
    theoretical_profit:Decimal
    matched_qty:Decimal
    target_qty:Decimal
    realized_or_shadow_pnl:Decimal
    payoff_floor_proven:bool
    settlement_proven:bool
    fees_complete:bool
    books_synchronized:bool
    execution_locked:bool

def _mean(xs):
    return sum(xs,Decimal("0"))/Decimal(len(xs)) if xs else None

def clustered_lcb(values:list[Decimal], z=Decimal("1.645")):
    """Simple event-level normal lower bound.

    Production may substitute a clustered bootstrap; this reference refuses to
    use decision-row pseudo-replication because input must already be one value
    per independent event.
    """
    n=len(values)
    if n<2:
        return None
    m=_mean(values)
    var=sum((x-m)*(x-m) for x in values)/Decimal(n-1)
    se=var.sqrt()/Decimal(n).sqrt()
    return m-Decimal(z)*se

def mechanism_summary(rows:list[EventRow])->dict:
    grouped=defaultdict(list)
    for r in rows:
        grouped[r.mechanism].append(r)
    out={}
    for mech,rs in grouped.items():
        by_event=defaultdict(lambda: [Decimal("0"),Decimal("0"),Decimal("0"),Decimal("0"),True,True,True])
        for r in rs:
            b=by_event[r.event_key]
            b[0]+=Decimal(r.expected_pnl); b[1]+=Decimal(r.realized_pnl)
            b[2]+=Decimal(r.capital_hours); b[3]+=Decimal(r.deployed_capital)
            b[4]=b[4] and r.settlement_proven
            b[5]=b[5] and r.fees_complete
            b[6]=b[6] and r.fresh
        event_realized=[v[1] for v in by_event.values()]
        event_residual=[v[1]-v[0] for v in by_event.values()]
        expected=sum((v[0] for v in by_event.values()),Decimal("0"))
        realized=sum(event_realized,Decimal("0"))
        capital_hours=sum((v[2] for v in by_event.values()),Decimal("0"))
        out[mech]={
          "independent_events":len(by_event),
          "expected_pnl":expected,
          "realized_pnl":realized,
          "residual":realized-expected,
          "mean_pnl_per_event":_mean(event_realized),
          "forward_pnl_lcb_per_event":clustered_lcb(event_realized),
          "residual_lcb_per_event":clustered_lcb(event_residual),
          "capital_hours":capital_hours,
          "pnl_per_capital_hour":None if capital_hours<=0 else realized/capital_hours,
          "settlement_complete":all(v[4] for v in by_event.values()),
          "fees_complete":all(v[5] for v in by_event.values()),
          "fresh_complete":all(v[6] for v in by_event.values()),
        }
    return out

def routing_summary(rows:list[RoutingRow])->dict:
    eligible=[r for r in rows if r.fresh and r.next_best_all_in is not None]
    savings=[]
    for r in eligible:
        diff=(Decimal(r.next_best_all_in)-Decimal(r.chosen_all_in))*Decimal(r.qty)
        savings.append(diff)
    by_route=defaultdict(int)
    for r in rows: by_route[r.chosen_route]+=1
    return {
      "decisions":len(rows),
      "comparable_decisions":len(eligible),
      "total_savings_vs_next_best":sum(savings,Decimal("0")),
      "mean_savings_per_comparable_decision":_mean(savings),
      "route_counts":dict(by_route),
    }

def arb_summary(rows:list[ArbRow])->dict:
    out={}
    for topo in ("SAME_VENUE","CROSS_VENUE"):
        rs=[r for r in rows if r.topology==topo]
        if not rs:
            out[topo]={"opportunities":0}
            continue
        fully=[r for r in rs if r.target_qty>0 and r.matched_qty>=r.target_qty]
        out[topo]={
          "opportunities":len(rs),
          "independent_events":len({r.event_key for r in rs}),
          "theoretical_profit":sum((r.theoretical_profit for r in rs),Decimal("0")),
          "realized_or_shadow_pnl":sum((r.realized_or_shadow_pnl for r in rs),Decimal("0")),
          "full_match_rate":Decimal(len(fully))/Decimal(len(rs)),
          "all_payoff_floors_proven":all(r.payoff_floor_proven for r in rs),
          "all_settlement_proven":all(r.settlement_proven for r in rs),
          "all_fees_complete":all(r.fees_complete for r in rs),
          "all_books_synchronized":all(r.books_synchronized for r in rs),
          "execution_locked_count":sum(1 for r in rs if r.execution_locked),
        }
    return out

def reconcile_total(*, mechanism_realized:dict[str,Decimal],
                    settlement_adjustment:Decimal=Decimal("0"),
                    outcome_variance:Decimal=Decimal("0"),
                    reported_total:Decimal,
                    tolerance:Decimal=Decimal("0.01"))->dict:
    subtotal=sum((Decimal(v) for v in mechanism_realized.values()),Decimal("0"))
    calc=subtotal+Decimal(settlement_adjustment)+Decimal(outcome_variance)
    residual=Decimal(reported_total)-calc
    return {
      "green":abs(residual)<=Decimal(tolerance),
      "mechanism_total":subtotal,
      "settlement_adjustment":Decimal(settlement_adjustment),
      "outcome_variance":Decimal(outcome_variance),
      "calculated_total":calc,
      "reported_total":Decimal(reported_total),
      "residual":residual,
    }

def classify_mechanism(name:str, summary:dict, thresholds:dict)->dict:
    blockers=[]
    n=int(summary.get("independent_events") or 0)
    lcb=summary.get("forward_pnl_lcb_per_event")
    if name=="DIRECTIONAL":
        if n<int(thresholds["directional"]["min_independent_events"]):
            blockers.append("INSUFFICIENT_INDEPENDENT_EVENTS")
        if lcb is None or lcb<=0:
            blockers.append("FORWARD_PNL_LOWER_BOUND_NOT_POSITIVE")
    if not summary.get("settlement_complete",False):
        blockers.append("SETTLEMENT_INCOMPLETE")
    if not summary.get("fees_complete",False):
        blockers.append("FEES_INCOMPLETE")
    if not summary.get("fresh_complete",False):
        blockers.append("FRESHNESS_INCOMPLETE")
    return {"status":"ELIGIBLE" if not blockers else "SHADOW_ONLY",
            "blockers":tuple(blockers)}

def headline_scoreboard(event_rows:list[EventRow], routing_rows:list[RoutingRow],
                        arb_rows:list[ArbRow], thresholds:dict)->dict:
    mechs=mechanism_summary(event_rows)
    states={k:classify_mechanism(k,v,thresholds) for k,v in mechs.items()}
    return {
      "mechanisms":mechs,
      "mechanism_states":states,
      "routing":routing_summary(routing_rows),
      "arbitrage":arb_summary(arb_rows),
    }
