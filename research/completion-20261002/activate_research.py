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
GOALS=[
 ('coverage','DEREK','Investigate recorded PinnAPI coverage and quote freshness. Distinguish cached, matched, grading-supported, price-eligible and actually evaluated markets. Cite the feed evidence timestamp and source IDs; name missing evidence, an engineering owner and a measurable acceptance check. Do not claim the feed changes decisions unless recorded evidence proves it.'),
 ('modeling','XAVIER','Review how fresh reference probabilities, fees, execution depth and exceptional settlement assumptions affect position management. Propose a falsifiable modeling improvement and a prospective evaluation protocol. Cite recorded evidence, separate peer opinion from facts, and do not activate policy or claim profitability improvement.'),
 ('audit','AUDREY','Audit the team research chain and reconcile decisions, fills, management reviews and outcomes. Identify missing links and proposed improvements with traceable evidence. Separate training costs from investment performance and automated acknowledgments from genuine reviews. Define a forward measurement before any improvement claim.')]

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
        r=request(client,'POST','/goals',{'actor':actor,'request_id':'owner-20261002-'+key+'-v1','title':title,'first':first,'priority':4,'due_at':now+86400})
        flows.append({'goal':key,**r})
    after=request(client,'GET')
    if after.get('control',{}).get('enabled') is not True:raise RuntimeError('RESEARCH_ENABLE_READBACK_FAILED')
    return {'changed':True,'control':after['control'],'flows':flows,'heartbeat':after.get('heartbeat'),
            'completed_learning':False,'next_check':'Verify genuine reviewed outcomes and peer dependencies; enablement alone is not improvement.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--actor',required=True);a=p.parse_args()
    if not 2<=len(a.actor.strip())<=100 or a.actor.upper() in ('DEREK','XAVIER','AUDREY','SYSTEM'):p.error('a named management actor is required')
    token=os.environ.get('ADMIN_TOKEN','')
    if not token:raise SystemExit('ADMIN_TOKEN is absent; keep it in the deployment environment, never paste it into chat.')
    try:
        with httpx.Client(base_url=BASE,headers={'x-admin-token':token},timeout=20,follow_redirects=False) as c:print(json.dumps(run(c,a.actor,a.apply),default=str,indent=2))
    except Exception as exc:raise SystemExit(type(exc).__name__+': '+str(exc))
if __name__=='__main__':main()
