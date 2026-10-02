"""Use existing authenticated controls; no credentials in arguments or output.
Default is read-only. --apply enables research and assigns three peer-reviewed goals.
Run from the backend environment where httpx and ADMIN_TOKEN already exist.
"""
import argparse
import json
import os
import time
import httpx

BASE='https://sportsassets-api.onrender.com'
PATH='/api/command/agents/capabilities'
# 2026-10-02 (b): the owner's second set (Slack activation brief). Each goal
# names an owner, the evidence to use, a concrete question, the peer review
# and a measurable next action; the flow is owner investigates -> the next
# agent reviews (and may disagree) -> Audrey audits.
GOALS=[
 ('coverage-freshness-matching-calibration','DEREK','Derek: from recorded decisions, refusals and the PinnAPI census, answer: which markets did we evaluate vs miss today and why (unmapped, no Pinnacle, stale, SETTLEMENT_NOT_SUPPORTED); how fresh were probabilities at decision; how many matches failed and why; is the probability model calibrated on settled outcomes? Quote recorded figures only; state the largest fixable blocker and one measurable next action with an owner.'),
 ('execution-standing-exits-paper-vs-live','XAVIER','Xavier: from recorded positions, standing orders, fills and marks (and the live 1:1,000 mirror records when present), answer: how good was execution (fill rate, slippage vs quoted price, fees); are standing orders sized to held inventory; which exit alternative (hold, resting exit, marketable exit) the records favour per open position; what differs between paper and live execution. Recommendation only; one measurable next action with an owner.'),
 ('reconciliation-review-defects-proposals','AUDREY','Audrey: reconcile cash, reserved, exposure, realized and unrealized P&L to the ledger; independently test Derek\'s and Xavier\'s latest findings against their cited records and say where you disagree; list operational defects seen today; propose measurable improvements, each with the forward measurement that would show it worked. Keep training apart from investment.')]

def request(client,method,suffix='',payload=None):
    r=client.request(method,PATH+suffix,json=payload)
    if r.status_code in (401,403):raise RuntimeError('COMMAND_CONTROL_AUTH_REQUIRED; use existing ADMIN_TOKEN in the release environment')
    if not 200<=r.status_code<300:raise RuntimeError('CONTROL_REQUEST_FAILED_HTTP_'+str(r.status_code))
    return r.json()

def run(client,actor,apply=False,now=None):
    now=time.time() if now is None else now
    before=request(client,'GET')
    if not apply:return {'changed':False,'control':before.get('control'),'heartbeat':before.get('heartbeat'),'work_count':len(before.get('work',[]))}
    request(client,'POST','/control',{'actor':actor,'enabled':True,'hourly_limit':24})
    flows=[]
    for key,first,title in GOALS:
        r=request(client,'POST','/goals',{'actor':actor,'request_id':'owner-20261002b-'+key+'-v1','title':title,'first':first,'priority':4,'due_at':now+86400})
        flows.append({'goal':key,**r})
    after=request(client,'GET')
    if after.get('control',{}).get('enabled') is not True:raise RuntimeError('RESEARCH_ENABLE_READBACK_FAILED')
    return {'changed':True,'control':after['control'],'flows':flows,'heartbeat':after.get('heartbeat'),
            'completed_learning':False,'next_check':'Verify genuine reviewed outcomes and peer dependencies; enablement alone is not improvement.'}

# A re-issue opens a NEW flow for one goal under a new request id (the
# rejected flow and its records stay as they are). v3 of Xavier's goal adds
# the grounding rule its v2 answer broke: the persona guard replaced that
# answer because it stated a computed figure no record holds. v4 is the same
# goal re-opened once after 9a1d422, whose retries ask the model afresh
# instead of replaying the stored attempt-1 reply. v5 is the same goal
# re-opened after 102572f: v4's answer quoted -8.42 and 2.91 from Xavier's
# recorded lesson, and the guard then read facts with their JSON arrays
# stripped, so it called those recorded figures invented.
REISSUE={'management-execution-exits':('v5','Xavier: position management, execution quality and exit alternatives for the open paper positions. From recorded reviews, resting orders, fills and marks, assess execution quality and compare hold-to-settlement, resting exit and marketable exit. Quote only figures in the cited records; give recorded inputs and describe comparisons rather than computing new totals. Label it a recommendation, not an activated change; one measurable next action with an owner.')}

def reissue(client,actor,key,now=None):
    now=time.time() if now is None else now
    first=next((f for k,f,_ in GOALS if k==key),'XAVIER')   # v5 goal predates set (b)
    suffix,title=REISSUE[key]
    if len(title)>500:raise ValueError('GOAL_TITLE_TOO_LONG')
    r=request(client,'POST','/goals',{'actor':actor,'request_id':'owner-20261002-'+key+'-'+suffix,'title':title,'first':first,'priority':4,'due_at':now+86400})
    after=request(client,'GET')
    return {'changed':True,'reissued':key,'request_id':'owner-20261002-'+key+'-'+suffix,**r,'control':after.get('control')}

def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--actor',required=True)
    p.add_argument('--reissue',choices=sorted(REISSUE));a=p.parse_args()
    if not 2<=len(a.actor.strip())<=100 or a.actor.upper() in ('DEREK','XAVIER','AUDREY','SYSTEM'):p.error('a named management actor is required')
    token=os.environ.get('ADMIN_TOKEN','')
    if not token:raise SystemExit('ADMIN_TOKEN is absent; keep it in the deployment environment, never paste it into chat.')
    try:
        with httpx.Client(base_url=BASE,headers={'x-admin-token':token},timeout=20,follow_redirects=False) as c:print(json.dumps(reissue(c,a.actor,a.reissue) if a.reissue else run(c,a.actor,a.apply),default=str,indent=2))
    except Exception as exc:raise SystemExit(type(exc).__name__+': '+str(exc))
if __name__=='__main__':main()
