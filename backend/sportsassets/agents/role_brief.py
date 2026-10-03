"""Account-scoped operational evidence for agent research, never trade authority.

Bounded recent samples are not population rates or counterfactual profit. ENTER
is a recommendation, not an accepted order/fill. Unknown freshness stays unknown.
"""
from __future__ import annotations
from collections import Counter
import json
import math

LIMIT = 100
WINDOW_S = 86400
SQL = {
 'DEREK': """SELECT decision_id,decided_at,strategy,policy_version,verdict,
 refusal,us_market_slug,holding_side,book_obs_id FROM paper_decisions
 WHERE account_id=$1 AND decided_at BETWEEN to_timestamp($2-86400) AND to_timestamp($2)
 ORDER BY decided_at DESC,decision_id LIMIT 101""",
 'XAVIER': """SELECT review_id,group_id,reviewed_at,recommendation,refusal,
 measure,selection FROM paper_xavier_reviews WHERE account_id=$1
 AND reviewed_at BETWEEN to_timestamp($2-86400) AND to_timestamp($2)
 ORDER BY reviewed_at DESC,review_id LIMIT 101""",
 'AUDREY': """SELECT finding_id,recorded_at,severity,kind,subject
 FROM paper_audrey_findings WHERE account_id=$1
 AND recorded_at BETWEEN to_timestamp($2-86400) AND to_timestamp($2)
 ORDER BY recorded_at DESC,finding_id LIMIT 101""",
 # Karen's own challenges: account-scoped when the challenged record has an
 # account, otherwise unscoped (a funded decision, an execution intent).
 'KAREN': """SELECT challenge_id,challenged_at,target_agent,target_kind,
 target_id,detector,severity,state FROM karen_challenges
 WHERE (account_id=$1 OR account_id IS NULL)
 AND challenged_at BETWEEN to_timestamp($2-86400) AND to_timestamp($2)
 ORDER BY challenged_at DESC,challenge_id LIMIT 101""",
}
FOCUS = {
 'DEREK': 'Find the largest recorded blocker by distinct contract/side within each strategy/version. Propose one measurable repair; do not infer missed profits or new fills from ENTER signals.',
 'XAVIER': 'Review held-position freshness and recorded alternatives. Compare HOLD, EXIT, REDUCE or a hedge only when current executable prices and settlement compatibility support them; never invent a cross-venue hedge.',
 'AUDREY': 'Prioritize recorded findings and testable causes. Separate peer claims from evidence; evaluate any proposed change on a frozen forward cohort before claiming improvement.',
 'KAREN': 'Challenge Derek, Xavier and Audrey only with records that exist: cite the decision, intent, review, reconciliation or audit id. Let the challenged agent answer; never resolve your own challenge. You hold no order, approval, activation, limit or promotion authority.',
}


def obj(v):
    if isinstance(v, str):
        try: v=json.loads(v)
        except (TypeError, ValueError): return {}
    return v if isinstance(v, dict) else {}


def summarize(agent, rows, now, account_id):
    if agent not in SQL: raise ValueError('UNKNOWN_AGENT')
    if not account_id: raise ValueError('ACCOUNT_REQUIRED')
    if isinstance(now, bool) or not math.isfinite(float(now)): raise ValueError('INVALID_CLOCK')
    selected=[dict(r) for r in rows[:LIMIT]]
    result={'agent':agent,'account_id':account_id,'read_at':now,
            'window':{'start':now-WINDOW_S,'end':now},
            'sample':{'rows':len(selected),'limit':LIMIT,'truncated':len(rows)>LIMIT,
                      'order':'MOST_RECENT_FIRST','population_totals_known':len(rows)<=LIMIT},
            'status':'RECORDED_EVIDENCE' if selected else 'NO_RECORDED_EVIDENCE',
            'authority':'RESEARCH_ONLY','focus':FOCUS[agent],
            'improvement_proven':False}
    if agent=='DEREK':
        cohorts={}
        for r in selected:
            k=(r['strategy'],r['policy_version'])
            c=cohorts.setdefault(k, {'strategy':k[0],'policy_version':k[1],
                                     'decisions':0,'enter_signals':0,'contracts':set(),'blockers':{}})
            c['decisions']+=1
            c['enter_signals']+=r.get('verdict')=='ENTER'
            contract=(r.get('us_market_slug'),r.get('holding_side'))
            if all(contract): c['contracts'].add(contract)
            if r.get('verdict')!='ENTER':
                reason=r.get('refusal') or 'REFUSAL_NOT_RECORDED'
                b=c['blockers'].setdefault(reason,{'reason':reason,'decisions':0,
                                                   'contracts':set(),'source_ids':[]})
                b['decisions']+=1
                if all(contract): b['contracts'].add(contract)
                if len(b['source_ids'])<3: b['source_ids'].append(r['decision_id'])
        output=[]
        for c in cohorts.values():
            c['distinct_contract_sides']=len(c.pop('contracts'))
            blockers=list(c['blockers'].values())
            for b in blockers: b['distinct_contract_sides']=len(b.pop('contracts'))
            c['blockers']=sorted(blockers,key=lambda b:(-b['distinct_contract_sides'],-b['decisions'],b['reason']))[:6]
            output.append(c)
        result.update(cohorts=output,source_ids=[r['decision_id'] for r in selected[:10]],
                      execution_results='NOT_READ; ENTER signals are not orders or fills',
                      limitation='A contract may appear under several reasons. Counts are sampled, not all available markets or counterfactual profit.')
    elif agent=='XAVIER':
        latest={}
        for r in selected:
            if r.get('group_id'): latest.setdefault(r['group_id'],r)
        buckets=Counter();attention=[]
        for group,r in latest.items():
            m=obj(r.get('measure'))
            state='STALE' if m.get('stale') is True else ('RECORDED_FRESH' if m.get('stale') is False else 'UNKNOWN')
            buckets[state]+=1
            selection=obj(r.get('selection'))
            if len(attention)<12:
                attention.append({'group_id':group,'review_id':r['review_id'],
                                  'reviewed_at':r['reviewed_at'],'freshness_at_review':state,
                                  'recommendation':r.get('recommendation'),'refusal':r.get('refusal'),
                                  'recorded_selection_present':bool(selection)})
        result.update(distinct_reviewed_groups=len(latest),freshness_at_review={k:buckets[k] for k in ('STALE','RECORDED_FRESH','UNKNOWN')},
                      recent_groups=attention,source_ids=[r['review_id'] for r in selected[:10]],
                      limitation='Groups are reviewed positions, not necessarily still open. Freshness is at the recorded review, not now. Read the position tool for actual orders, alternatives and settlements.')
    elif agent=='KAREN':
        result.update(by_state=dict(Counter(r.get('state') or 'UNKNOWN' for r in selected)),
                      by_target=dict(Counter(r.get('target_agent') or 'UNKNOWN' for r in selected)),
                      by_detector=dict(Counter(r.get('detector') or 'UNKNOWN' for r in selected)),
                      challenges=selected[:12],source_ids=[r['challenge_id'] for r in selected[:10]],
                      authority='NONE',
                      limitation='A challenge is a grounded question, not a proven defect: only an UPHELD outcome recorded by someone other than Karen counts as one.')
    else:
        result.update(by_severity=dict(Counter(r.get('severity') or 'UNKNOWN' for r in selected)),
                      by_kind=dict(Counter(r.get('kind') or 'UNKNOWN' for r in selected)),
                      findings=selected[:12],source_ids=[r['finding_id'] for r in selected[:10]],
                      limitation='Finding counts are not unique defects, verified remediations or performance gains; inspect linked records and forward evaluations.')
    return result


async def read(conn, agent, now, account_id):
    if agent not in SQL: raise ValueError('UNKNOWN_AGENT')
    rows=await conn.fetch(SQL[agent],account_id,now)
    return summarize(agent,rows,now,account_id)
