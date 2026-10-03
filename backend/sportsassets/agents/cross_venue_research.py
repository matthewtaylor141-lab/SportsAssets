"""Compare recorded executable alternatives; never route or authorize an order.

Adapters must supply verified normalized settlement scenarios and fee bounds.
A model may discuss this evidence, but cannot certify its own market mapping.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import json

VENUES={'POLYMARKET_US','POLYMARKET','KALSHI'}
MAX_AGE=30


def dec(x):
    if isinstance(x,bool): raise ValueError('INVALID_NUMBER')
    try: d=Decimal(str(x))
    except (InvalidOperation,TypeError): raise ValueError('INVALID_NUMBER') from None
    if not d.is_finite() or abs(d)>Decimal('1e18'): raise ValueError('NONFINITE_OR_UNREPRESENTABLE_NUMBER')
    return d


def text(x):
    return isinstance(x,str) and 0<len(x)<=200


def recent(stamp,now):
    age=dec(now)-dec(stamp)
    if not 0<=age<=MAX_AGE: raise ValueError('STALE_OR_FUTURE_EVIDENCE')
    return float(age)


def compare(snapshot,now):
    """All payoffs USD, executable prices USD/contract. Fees conservative bounds.

    Ranking uses fillable quantity, so show target/fill fraction beside it.
    Partial-fill bounds compare every depth boundary, including zero fill.
    """
    base={'execution_authority':False,'basis':'RECORDED_BOOK_RESEARCH_NOT_AN_ORDER',
          'read_at':now,'partial_fill_probability':None}
    try:
        if not isinstance(snapshot,dict): raise ValueError('INVALID_SNAPSHOT')
        if len(json.dumps(snapshot,default=str))>64000: raise ValueError('SNAPSHOT_TOO_LARGE')
        if snapshot.get('schema')!='CROSS_VENUE_RESEARCH_V1': raise ValueError('UNKNOWN_SCHEMA')
        if not text(snapshot.get('snapshot_id')): raise ValueError('MISSING_SNAPSHOT_ID')
        scenarios=snapshot['scenarios']
        if not 2<=len(scenarios)<=16: raise ValueError('SCENARIO_LIMIT')
        ids=[x['id'] for x in scenarios]
        if len(set(ids))!=len(ids) or not all(text(x) for x in ids): raise ValueError('SCENARIO_IDENTITIES')
        if snapshot.get('exhaustive_mutually_exclusive') is not True or not text(snapshot.get('scenario_evidence_id')):
            raise ValueError('UNVERIFIED_SCENARIO_SPACE')
        probs=[dec(x['probability']) for x in scenarios]
        if any(not 0<=p<=1 for p in probs) or abs(sum(probs)-1)>Decimal('0.00000001'): raise ValueError('INVALID_PROBABILITIES')
        if not text(snapshot.get('forecast_version')) or not text(snapshot.get('forecast_id')): raise ValueError('MISSING_FORECAST_PROVENANCE')
        recent(snapshot['forecast_at'],now)
        held=[dec(snapshot['held_payoff'][s]) for s in ids]
        target=dec(snapshot['target_quantity'])
        if target<=0: raise ValueError('INVALID_TARGET')
        actions=snapshot['actions']
        if not isinstance(actions,list) or not 1<=len(actions)<=32 or not all(isinstance(a,dict) for a in actions): raise ValueError('ACTION_LIMIT')
        if len({a.get('action_id') for a in actions})!=len(actions): raise ValueError('DUPLICATE_ACTION_ID')
    except (ValueError,KeyError,TypeError,AttributeError,InvalidOperation) as exc:
        return dict(base,status='UNAVAILABLE',reason=str(exc)[:150])
    result=[]
    for a in actions:
        out={'action_id':a.get('action_id'),'venue':a.get('venue'),'side':a.get('side'),
             'contract_id':a.get('contract_id'),'book_id':a.get('book_id')}
        try:
            if a.get('venue') not in VENUES or not all(text(a.get(k)) for k in ('action_id','contract_id','book_id','rules_id','mapping_evidence_id','fee_evidence_id')):
                raise ValueError('MISSING_VENUE_OR_PROVENANCE')
            if a.get('mapping_verified') is not True or a.get('rules_verified') is not True: raise ValueError('UNVERIFIED_MAPPING_OR_RULES')
            # Require explicit state payoffs even for supposed exact counterparts.
            if a.get('scenario_evidence_id')!=snapshot['scenario_evidence_id']: raise ValueError('SETTLEMENT_SCENARIO_MISMATCH')
            if a.get('market_state')!='OPEN': raise ValueError('MARKET_NOT_OPEN')
            out['book_age_s']=recent(a['book_at'],now)
            payoff=[dec(a['payoff_per_contract'][s]) for s in ids]
            if any(not 0<=p<=1 for p in payoff): raise ValueError('INVALID_CONTRACT_PAYOFF')
            side=a['side']
            if side not in ('BUY','SELL'): raise ValueError('UNSUPPORTED_ACTION')
            if side=='SELL':
                if a.get('inventory_venue')!=a['venue'] or a.get('inventory_contract_id')!=a['contract_id'] or not text(a.get('inventory_evidence_id')):
                    raise ValueError('SELL_REQUIRES_SAME_VENUE_INVENTORY')
                recent(a['inventory_at'],now)
                available=dec(a['available_quantity'])
                if available<target: raise ValueError('INSUFFICIENT_SELL_INVENTORY')
            if not text(a.get('size_rules_id')): raise ValueError('MISSING_SIZE_RULES')
            step=dec(a['quantity_step']); minimum=dec(a['minimum_quantity']); min_notional=dec(a['minimum_notional'])
            if step<=0 or minimum<0 or min_notional<0: raise ValueError('INVALID_SIZE_RULES')
            rounded=(target//step)*step
            if rounded<minimum or rounded<=0: raise ValueError('BELOW_MINIMUM_SIZE')
            if side=='BUY':
                if not text(a.get('balance_evidence_id')): raise ValueError('MISSING_BALANCE_EVIDENCE')
                recent(a['balance_at'],now)
                balance=dec(a['available_cash'])
                if balance<0: raise ValueError('INVALID_BALANCE')
            levels=a['levels']
            if not isinstance(levels,list) or not 1<=len(levels)<=100: raise ValueError('DEPTH_LIMIT')
            parsed=[]
            for l in levels:
                price,qty,fee=map(dec,(l['price'],l['quantity'],l['fee_upper_bound_per_contract']))
                if not 0<=price<=1 or qty<=0 or fee<0 or qty%step!=0: raise ValueError('INVALID_DEPTH_OR_FEES')
                parsed.append((price,qty,fee))
            # The adapter must give a monotonic native executable side ladder.
            prices=[p for p,_,_ in parsed]
            if prices!=sorted(prices,reverse=side=='SELL'): raise ValueError('UNSORTED_EXECUTABLE_BOOK')
            qty=Decimal(0);cash=Decimal(0);fees=Decimal(0);boundaries=[held]
            for price,depth,fee in parsed:
                take=min(rounded-qty,depth)
                if take<=0: break
                qty+=take; fees+=take*fee
                cash+=take*((-price if side=='BUY' else price)-fee)
                boundaries.append([h+cash+(qty*p if side=='BUY' else -qty*p) for h,p in zip(held,payoff)])
            if qty<minimum or abs(cash+fees)<min_notional: raise ValueError('DEPTH_BELOW_MINIMUM_SIZE')
            if side=='BUY' and -cash>balance: raise ValueError('INSUFFICIENT_VENUE_CASH')
            final=boundaries[-1]
            out.update(status='COMPARABLE',target_quantity=float(target),fillable_quantity=float(qty),
                       rounded_quantity=float(rounded),fill_fraction=float(qty/target),unfilled_quantity=float(target-qty),
                       cash_delta_lower_bound=float(cash),fee_upper_bound=float(fees),
                       scenario_payoff_lower_bounds=dict(zip(ids,map(float,final))),
                       expected_payoff_lower_bound=float(sum(p*v for p,v in zip(probs,final))),
                       expected_increment_lower_bound=float(sum(p*(v-h) for p,v,h in zip(probs,final,held))),
                       worst_case_if_filled=float(min(final)),
                       worst_case_over_partial_fills=float(min(min(x) for x in boundaries)),
                       rules_id=a['rules_id'],mapping_evidence_id=a['mapping_evidence_id'],
                       settlement_differences=a.get('settlement_differences',[]),
                       relationship=a.get('relationship','UNCLASSIFIED'))
        except (ValueError,KeyError,TypeError,AttributeError,InvalidOperation) as exc:
            out.update(status='NOT_COMPARABLE',reason=str(exc)[:150])
        result.append(out)
    good=[r for r in result if r['status']=='COMPARABLE']
    # Explicitly keep HOLD; a partial hedge may lose to doing nothing.
    hold_ev=float(sum(p*h for p,h in zip(probs,held)))
    return dict(base,status='OK' if good else 'NO_COMPARABLE_ACTIONS',snapshot_id=snapshot['snapshot_id'],
                forecast_version=snapshot['forecast_version'],forecast_id=snapshot['forecast_id'],
                hold={'expected_payoff':hold_ev,'worst_case_payoff':float(min(held))},actions=result,
                expected_payoff_ranking=[r['action_id'] for r in sorted(good,key=lambda r:r['expected_payoff_lower_bound'],reverse=True)],
                best_recorded_action_or_hold=(max(good,key=lambda r:r['expected_payoff_lower_bound'])['action_id'] if good and max(r['expected_payoff_lower_bound'] for r in good)>hold_ev else 'HOLD'),
                limitation='Conditional on verified scenario probabilities and recorded depth; no fill guarantee. Fee bounds are not actual charged fees. Another-venue purchase is a hedge, not a sale of the existing holding. No atomic cross-venue execution is assumed.')


async def read(conn,account,group,now):
    if not group: return {'status':'UNAVAILABLE','reason':'POSITION_ID_REQUIRED','execution_authority':False}
    exists=await conn.fetchval('SELECT 1 FROM paper_orders WHERE account_id=$1 AND group_id=$2 LIMIT 1',account,group)
    if not exists: raise ValueError('POSITION_NOT_IN_ACCOUNT')
    raw=await conn.fetchval('SELECT value FROM ingestion_state WHERE key=$1','agent.cross_venue_evidence:'+account+':'+group)
    if not raw: return {'status':'UNAVAILABLE','reason':'NO_VERIFIED_CROSS_VENUE_SNAPSHOT','group_id':group,'execution_authority':False}
    try: snapshot=json.loads(raw) if isinstance(raw,str) else raw
    except (TypeError,ValueError): return {'status':'UNAVAILABLE','reason':'INVALID_SNAPSHOT'}
    if not isinstance(snapshot,dict) or snapshot.get('account_id')!=account or snapshot.get('group_id')!=group:
        return {'status':'UNAVAILABLE','reason':'SNAPSHOT_SCOPE_MISMATCH','execution_authority':False}
    return compare(snapshot,now)
