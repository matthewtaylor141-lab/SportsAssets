import importlib.util
import json
from pathlib import Path
import httpx
import pytest
spec=importlib.util.spec_from_file_location('activation',Path(__file__).with_name('activate_research.py'));A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)

def test_default_read_only():
 calls=[]
 def handler(r):calls.append(r);return httpx.Response(200,json={'control':{'enabled':False},'work':[]})
 with httpx.Client(base_url=A.BASE,transport=httpx.MockTransport(handler)) as c:out=A.run(c,'Matt Taylor')
 assert not out['changed'] and len(calls)==1 and calls[0].method=='GET'

def test_enable_and_goals_have_stable_keys_and_verify_control():
 calls=[];enabled=False
 def handler(r):
  nonlocal enabled
  calls.append(r)
  if r.method=='GET':return httpx.Response(200,json={'control':{'enabled':enabled}})
  b=json.loads(r.content)
  if r.url.path.endswith('/control'):enabled=b['enabled'];return httpx.Response(200,json=b)
  return httpx.Response(200,json={'created':True,'task_ids':[b['request_id']]})
 with httpx.Client(base_url=A.BASE,transport=httpx.MockTransport(handler)) as c:out=A.run(c,'Matt Taylor',True,100)
 assert out['control']['enabled'] and not out['completed_learning']
 goals=[json.loads(r.content) for r in calls if r.url.path.endswith('/goals')]
 assert len(goals)==3 and len({g['request_id'] for g in goals})==3
 assert all(g['due_at']==86500 for g in goals)
 assert all('activate' not in r.url.path for r in calls)

@pytest.mark.parametrize('status',[302,401,403,500])
def test_failure_never_reported_as_success(status):
 with httpx.Client(base_url=A.BASE,transport=httpx.MockTransport(lambda r:httpx.Response(status,json={}))) as c:
  with pytest.raises(RuntimeError):A.run(c,'Matt Taylor',True)
