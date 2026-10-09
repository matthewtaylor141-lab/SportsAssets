import asyncio
import datetime as dt
from unittest.mock import AsyncMock

import pytest
from sportsassets.agents import learning_context as L

NOW=1800000000.0

def row(i='1', **kw):
    r=dict(lesson_id='paper-lesson-'+i,account_id='paper_acct_main',agent_id='DEREK',
           kind='FILL_QUALITY',strategy='EXPLORATION',series_key=i,version=1,
           statement='Observed fills differed from optimistic bounds.',
           window_end=NOW-60,learned_at=NOW-30,
           provenance={'record_count':4,'ids_sha256':'a'*64},
           evidence_category='SIMULATED_FILL_VS_OPTIMISTIC_BOUND',basis='FORWARD_RECORDS_ONLY')
    r.update(kw);return r

def select(rows,question='fills'):
    return L.select(rows,account_id='paper_acct_main',agent='derek',question=question,now=NOW)

def test_other_account_and_other_agent_are_never_memory():
    r=select([row('a',account_id='other'),row('b',agent_id='XAVIER'),row()])
    assert [x['lesson_id'] for x in r['lessons']]==['paper-lesson-1']
    assert r['rejected']==['SCOPE_MISMATCH','SCOPE_MISMATCH']

def test_latest_version_wins_and_invalid_new_version_never_revives_old():
    assert select([row(version=1),row(version=2,statement='Corrected')])['lessons'][0]['statement']=='Corrected'
    assert not select([row(version=1),row(version=2,window_end=NOW+1)])['lessons']

@pytest.mark.parametrize('field,value', [('window_end',NOW+0.0001),('learned_at',NOW+1),('window_end',float('nan')),('window_end',dt.datetime(2026,1,1)),('basis','REHEARSAL'),('provenance',{}),('provenance',{'record_count':0,'ids_sha256':'a'*64}),('provenance',{'record_count':True,'ids_sha256':'a'*64})])
def test_unverified_or_future_memory_excluded(field,value):
    assert not select([row(**{field:value})])['lessons']

def test_relevance_beats_recency_and_keeps_historical_label():
    r=select([row('fee',kind='FEES',statement='Fees consume profit.',window_end=NOW-15*86400),row('new',window_end=NOW-1)],'Why are fees consuming edge?')
    assert r['lessons'][0]['lesson_id']=='paper-lesson-fee'
    assert r['lessons'][0]['historical']
    assert r['authority']=='OBSERVATIONS_ONLY_NO_POLICY_CHANGE'

def test_output_budget_and_excerpt_disclosure():
    r=select([row(str(i),statement='fill '*1000) for i in range(60)])
    assert r['considered']==48 and len(r['lessons'])==3
    assert all(len(x['statement'])<=1200 and x['statement_shortened'] for x in r['lessons'])

@pytest.mark.asyncio
async def test_sql_read_is_bounded_and_scope_is_server_supplied():
    conn=AsyncMock();conn.fetch.return_value=[row()]
    result=await L.retrieve(conn,account_id='paper_acct_main',agent='derek',question='fees',now=NOW)
    args=conn.fetch.call_args.args
    assert args[1:]==('paper_acct_main','DEREK',48)
    assert 'n.account_id=l.account_id' in args[0] and 'LIMIT $3' in args[0]
    assert len(result['lessons'])==1

@pytest.mark.asyncio
async def test_hung_memory_read_has_deadline():
    async def hung(*args): await asyncio.sleep(3)
    conn=AsyncMock();conn.fetch.side_effect=hung
    with pytest.raises(TimeoutError):
        await L.retrieve(conn,account_id='paper_acct_main',agent='derek',question='',now=NOW)

@pytest.mark.asyncio
async def test_real_persona_fact_path_receives_scoped_evidence(monkeypatch):
    from sportsassets.agents import persona_facts as F, paper_benchmark as B
    monkeypatch.setattr(F,'_regclass',AsyncMock(return_value=True))
    monkeypatch.setattr(B,'cg_parameters',AsyncMock(return_value={'values':{'min_gross_edge_pp':.5},'version_id':'v2','source':'record'}))
    conn=AsyncMock();conn.fetch.return_value=[row(),row('other',account_id='other')]
    conn.fetchval.return_value=False  # No epoch-selector schema in this legacy fixture.
    facts=F.Facts();out=await F._agent_memory(conn,facts,'derek',question='fill quality',now=NOW)
    assert len(out['lessons'])==1
    text=' '.join(f['text'] for f in facts.items)
    assert 'SIMULATED_FILL_VS_OPTIMISTIC_BOUND' in text and 'not an instruction' in text
    assert 'paper-lesson-other' not in str(facts.items)
