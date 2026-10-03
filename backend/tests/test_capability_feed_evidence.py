import copy
import json
from unittest.mock import AsyncMock
import pytest
from sportsassets.agents import capability_tools as T, capability_runtime as R, capability_work as W
from tests.test_agent_capabilities import conn, task

def record(at=99):
    return {'beat_at':at,'state':'OWNER_SYNCED','api_key':'never-copy','raw_frames':['secret'],
            'c1_decision_effect':'NONE (observe only)',
            'cache':{'events':8,'markets':177,'markets_age_unknown':177,'prices':{'x':.5}},
            'coverage_census':{'total_contracts':80477,'reconciled':True,'states':{'MATCHED_SUPPORTED':0,'NO_FEED_EVENT':998},'unmatched_event_sample':['hidden']}}

def test_allowlisted_feed_context_keeps_zero_coverage_and_omits_secrets():
    out=T.feed_coverage_evidence(record(),100)
    assert out['status']=='RECENT_TELEMETRY' and out['execution_authority'] is False
    assert out['coverage']['states']['MATCHED_SUPPORTED']==0
    assert out['cache']['markets_age_unknown']==177
    assert 'secret' not in json.dumps(out) and 'never-copy' not in json.dumps(out)
    assert 'prices' not in json.dumps(out) and 'hidden' not in json.dumps(out)

@pytest.mark.parametrize('at',[0,101,'nan','bad'])
def test_stale_future_and_bad_feed_stamps_never_read_as_live(at):
    assert T.feed_coverage_evidence(record(at),100)['status']!='RECENT_TELEMETRY'

async def test_feed_tool_is_read_only_and_does_not_require_arbitrary_record_id():
    c=conn();c.fetchval.return_value=json.dumps(record())
    out=await T.Toolkit('DEREK',100).read(c,'feed_coverage')
    assert out['data']['record_id']=='ingestion_state:pinnapi_feed_last'
    assert c.fetchval.call_args.args[1]=='pinnapi_feed_last'
    c.execute.assert_not_called()
    assert c.transaction.call_args.kwargs['readonly'] is True

async def test_general_research_collects_feed_evidence(monkeypatch):
    from tests.test_pinnapi_feed_runtime import Pool
    c=conn();saved=[];reads=[]
    async def read(self,conn,name,rid):reads.append(name);return {'status':'OK','tool':name}
    async def save(conn,t,records,now):saved.extend(records);return True
    monkeypatch.setattr(T.Toolkit,'read',read);monkeypatch.setattr(W,'save_investigation',save)
    await R.evidence(Pool(c),task(),100)
    assert reads==['account','lessons','role_brief','feed_coverage']
    assert len(saved)==4
