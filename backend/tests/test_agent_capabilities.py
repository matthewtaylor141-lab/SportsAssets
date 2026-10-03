"""Research work contracts. No live provider/venue or production writes.

The separate *_postgres file covers actual locking and must run in the release
gate. These tests cover orchestration, scoping, fencing and failure behavior.
"""
import asyncio
import ast
import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock
import pytest
from sportsassets.agents import capability_work as W, capability_tools as T
from sportsassets.agents import capability_runtime as R, capability_experiments as E
from sportsassets.agents import capability_scorecards as S

NOW=1800000000.


@asynccontextmanager
async def transaction(**kwargs):yield


def conn():
    c=AsyncMock();c.transaction=MagicMock(side_effect=transaction);return c


def task(agent='DEREK',**spec):
    return dict(task_id='capwork:test',assignee=agent,title='Review observed fill quality.',status='IN_PROGRESS',
                spec=dict(account_id=W.ACCOUNT,attempts=1,claim_token='owner1',lease_until=NOW+100,dependencies=[],due_at=NOW+86400,context={},**spec),outcome={})


def answer(**kw):
    return dict(status='ANSWERED',provider={'mode':'LLM'},message_id='message:1',answer='The recorded ledger reconciles [F1].',facts=[{'source':'paper_ledger','record_id':'seq:1','field':'balance'}],**kw)


@pytest.mark.parametrize('value',[NOW-1,NOW,NOW+31*86400,float('nan'),float('inf'),True,None])
def test_plan_has_finite_future_bounded_due(value):
    with pytest.raises(ValueError):W.validate_plan('goal','DEREK',3,value,NOW)


@pytest.mark.parametrize('agent,priority',[('OTHER',3),('DEREK',0),('DEREK',True),('DEREK',6)])
def test_agent_priority_validation(agent,priority):
    with pytest.raises(ValueError):W.validate_plan('goal',agent,priority,NOW+100,NOW)


def test_ready_requires_all_dependencies_and_expired_claim():
    t=task();assert not W.ready(t,{},NOW)
    t['spec']['lease_until']=NOW-1;t['spec']['dependencies']=['a','b']
    assert not W.ready(t,{'a':'CLOSED_NO_CHANGE'},NOW)
    assert not W.ready(t,{'a':'CLOSED_NO_CHANGE','b':'WAITING'},NOW)
    assert W.ready(t,{'a':'CLOSED_NO_CHANGE','b':'CLOSED_NO_CHANGE'},NOW)
    t['status']='CANCELLED';assert not W.ready(t,{'a':'CLOSED_NO_CHANGE','b':'CLOSED_NO_CHANGE'},NOW)


def test_fallback_demo_and_uncited_or_objective_only_are_not_reviews():
    assert W.genuine(answer())
    for k,v in [('provider',{'mode':'FALLBACK'}),('status','PENDING'),('facts',[]),('demonstration',True),('facts',[{'source':'agent_tasks','field':'research_objective'}])]:
        a=answer();a[k]=v;assert not W.genuine(a)


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['owner','expired','cancelled'])
async def test_fenced_completion_cannot_record_after_ownership_changes(changed):
    c=conn();t=task();current=copy.deepcopy(t)
    if changed=='owner':current['spec']['claim_token']='newowner'
    if changed=='expired':current['spec']['lease_until']=NOW
    if changed=='cancelled':current['status']='CANCELLED'
    c.fetchrow.return_value=current
    assert not await W.finish(c,t,answer(),NOW)
    c.execute.assert_not_called()


@pytest.mark.asyncio
async def test_genuine_completion_keeps_evidence_and_labels_response():
    c=conn();t=task('XAVIER');t['spec']['context']={'recommendation_id':'rec:1'}
    t['outcome']['investigation']=[{'tool':'account','read_at':NOW-10}];c.fetchrow.return_value=copy.deepcopy(t)
    assert await W.finish(c,t,answer(),NOW)
    calls=[x.args for x in c.execute.call_args_list]
    out=json.loads(calls[0][4]);assert out['investigation']==t['outcome']['investigation'] and out['reviewed']
    assert calls[1][3:5]==('GENUINE_REVIEW','XAVIER')
    assert 'owner_agent=$3' in calls[2][0] and calls[2][-1]==W.ACCOUNT


@pytest.mark.asyncio
async def test_incomplete_review_retries_without_peer_unlock():
    c=conn();t=task();c.fetchrow.return_value=t
    a=answer();a['provider']['mode']='FALLBACK'
    await W.finish(c,t,a,NOW)
    assert c.execute.call_args_list[0].args[2]=='WAITING'
    assert c.execute.call_args_list[1].args[3:5]==('REVIEW_INCOMPLETE','SYSTEM')


@pytest.mark.asyncio
async def test_retry_preserves_original_investigation_for_stable_model_request():
    c=conn();t=task();t['outcome']={'investigation':[{'read_at':NOW-10}]};c.fetchrow.return_value=t
    assert await W.save_investigation(c,t,[{'read_at':NOW}],NOW)
    c.execute.assert_not_called()


@pytest.mark.asyncio
async def test_pending_request_survives_persona_recovery_window_without_burning_attempts():
    c=conn();t=task();t['spec']['attempts']=3;c.fetchrow.return_value=t
    await W.finish(c,t,{'status':'PENDING'},NOW)
    args=c.execute.call_args_list[0].args;s=json.loads(args[3])
    assert args[2]=='WAITING' and s['attempts']==2
    assert s['lease_until']==NOW+65 and s['claim_token'] is None
    assert s['pending_polls']==1


@pytest.mark.asyncio
async def test_pending_poll_budget_is_bounded():
    c=conn();t=task();t['spec']['pending_polls']=6;c.fetchrow.return_value=t
    await W.finish(c,t,{'status':'PENDING'},NOW)
    args=c.execute.call_args_list[0].args
    assert args[2]=='REJECTED' and json.loads(args[4])['error']=='PROVIDER_PENDING_BUDGET_EXHAUSTED'


@pytest.mark.asyncio
async def test_worker_off_makes_no_model_calls(monkeypatch):
    from sportsassets.agents import persona_chat as P
    c=conn();pool=MagicMock();pool.acquire=MagicMock(return_value=transaction_conn(c))
    monkeypatch.setattr(W,'schema',AsyncMock(return_value=True));monkeypatch.setattr(W,'control',AsyncMock(return_value={'enabled':False}))
    model=AsyncMock();monkeypatch.setattr(P,'converse',model)
    assert await R.tick(pool)=={'status':'OFF'};model.assert_not_called()


@asynccontextmanager
async def transaction_conn(c):yield c


@pytest.mark.asyncio
async def test_real_persona_adapter_stays_read_only_and_reuses_request(monkeypatch):
    from sportsassets.agents import persona_chat as P, directives as D, audrey_chat as A
    c=conn();pool=MagicMock();pool.acquire=lambda:transaction_conn(c)
    t=task();monkeypatch.setattr(R,'evidence',AsyncMock(return_value=True))
    model=AsyncMock(return_value=answer());monkeypatch.setattr(P,'converse',model);finish=AsyncMock();monkeypatch.setattr(W,'finish',finish)
    await R.execute(pool,t);await R.execute(pool,t)
    a,b=[x.kwargs for x in model.call_args_list]
    assert a['role']=='command' and a['agent']=='derek'
    assert a['request_id']==b['request_id'] and a['context']['capability_task_id']==t['task_id']
    assert not D.screen_authority(a['message'])['refused'] and not A.route(a['message'])['mutation']
    assert finish.await_count==2


@pytest.mark.asyncio
async def test_model_cancel_never_completes_work(monkeypatch):
    from sportsassets.agents import persona_chat as P
    monkeypatch.setattr(R,'evidence',AsyncMock(return_value=True));monkeypatch.setattr(P,'converse',AsyncMock(side_effect=asyncio.CancelledError))
    finish=AsyncMock();monkeypatch.setattr(W,'finish',finish)
    with pytest.raises(asyncio.CancelledError):await R.execute(None,task())
    finish.assert_not_called()


@pytest.mark.asyncio
async def test_tool_budget_allowlist_and_account_scope():
    c=conn();c.fetchrow.return_value=None;t=T.Toolkit('DEREK',NOW,max_calls=1)
    with pytest.raises(ValueError,match='TOOL_NOT_ALLOWED'):await t.read(c,'https://evil.test')
    out=await t.read(c,'decision','decision:other')
    assert out['status']=='UNAVAILABLE' and out['why']=='DECISION_NOT_IN_ACCOUNT'
    assert c.fetchrow.call_args.args[1]==W.ACCOUNT
    assert c.transaction.call_args.kwargs=={'readonly':True,'isolation':'repeatable_read'}
    with pytest.raises(ValueError,match='TOOL_BUDGET_EXHAUSTED'):await t.read(c,'decision','other')


@pytest.mark.asyncio
async def test_tool_oversize_is_unavailable_not_truncated_fact(monkeypatch):
    t=T.Toolkit('AUDREY',NOW);monkeypatch.setattr(t,'_read',AsyncMock(return_value={'body':'x'*40001}))
    out=await t.read(conn(),'account');assert out['why']=='EVIDENCE_TOO_LARGE'


@pytest.mark.asyncio
async def test_tool_timeout_is_explicit(monkeypatch):
    t=T.Toolkit('AUDREY',NOW);monkeypatch.setattr(t,'_read',AsyncMock(side_effect=TimeoutError))
    assert (await t.read(conn(),'account'))['why']=='EVIDENCE_READ_TIMEOUT'


@pytest.mark.asyncio
async def test_peer_review_requires_same_account_and_correct_agent():
    from sportsassets.agents.persona_facts import Facts
    c=conn();c.fetchrow.return_value=None;f=Facts()
    out=await T.context_facts(c,f,'xavier','task:wrong')
    assert out['status']=='UNAVAILABLE' and not f.items
    assert c.fetchrow.call_args.args[1:]==('task:wrong',W.KIND,'XAVIER',W.ACCOUNT)


@pytest.mark.asyncio
async def test_peer_opinion_and_evidence_are_attributed():
    from sportsassets.agents.persona_facts import Facts
    c=conn();t=task('XAVIER');t['spec']['dependencies']=['derek:1'];t['outcome']={'investigation':[{'status':'OK','tool':'account','read_at':NOW}]}
    peer=task();peer['status']='CLOSED_NO_CHANGE';peer['outcome']={'reviewed':True,'message_id':'m1','answer':'Testable hypothesis, not established improvement.'}
    c.fetchrow.side_effect=[t,peer];f=Facts();out=await T.context_facts(c,f,'xavier',t['task_id'])
    assert out['status']=='OK' and len(f.items)==3
    assert 'Attributed peer opinion' in f.items[1]['text'] and 'm1' in f.items[1]['text']


def protocol():return {'change_class':'TEST','training':[NOW-200,NOW-100],'evaluation':[NOW+1,NOW+86401],'min_outcomes':10,'rationale':'Test a specified hypothesis'}


@pytest.mark.parametrize('change',[{'evaluation':[NOW-1,NOW+86401]},{'training':[NOW-1,NOW+1]},{'min_outcomes':9},{'min_outcomes':True},{'evaluation':[NOW+1,float('inf')]},{'change_class':'UNREGISTERED'}])
def test_experiment_prospective_constraints(change):
    b=protocol();b.update(change)
    with pytest.raises(ValueError):E.protocol(b,NOW,{'TEST':{'min_evaluation_outcomes':10}})


def test_valid_experiment_retains_sample_floor():
    assert E.protocol(protocol(),NOW,{'TEST':{'min_evaluation_outcomes':10}})[-1]==10


@pytest.mark.asyncio
async def test_repeated_protocol_request_after_window_started_is_idempotent(monkeypatch):
    b=protocol();b['request_id']='request:1';c=conn();c.fetchrow.return_value={'spec':{'request_digest':W.stable(b)},'outcome':{'ok':True,'proposal_id':'p1'}}
    monkeypatch.setattr(W,'control',AsyncMock())
    assert (await E.register(c,b,actor='Matt',now=NOW+100000))['proposal_id']=='p1'
    c.execute.assert_not_called()


def test_scorecard_empty_has_no_fake_rates_or_performance():
    s=S.measure([],[],[],[],NOW)
    assert s['derek']['entries']['rate'] is None
    assert s['xavier']['stale_measures']['rate'] is None
    assert s['forecast_quality']['status']=='NOT_SCORED'


def test_scorecard_sampling_missingness_and_overdue_are_explicit():
    t=task();t['spec']['due_at']=NOW-1
    s=S.measure([{'verdict':'REFUSE','refusal':'STALE'}]*501,[{'measure':{}},{'measure':{'stale':True}}],[{'severity':'CRITICAL'}],[t],NOW)
    assert s['derek']['sample_limited'] and s['derek']['decisions']==500
    assert s['xavier']['missing_measures']==1 and s['xavier']['stale_measures']=={'numerator':1,'denominator':1,'rate':1}
    assert s['audrey']['overdue_task_ids']==[t['task_id']]


def test_no_capability_module_has_execution_provider_or_activation_tools():
    root=Path(W.__file__).parent
    forbidden={'subprocess','requests','httpx','pmus','kalshi','activate_proposal','submit_order','submit_fok','eval','exec','shell'}
    for p in root.glob('capability_*.py'):
        tree=ast.parse(p.read_text())
        for n in ast.walk(tree):
            if isinstance(n,ast.Call):
                name=n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id if isinstance(n.func,ast.Name) else ''
                assert name not in forbidden,(p,name)
            if isinstance(n,ast.Import):assert not forbidden.intersection(a.name for a in n.names)


def test_queries_parse_as_postgres():
    from pglast import parse_sql
    root=Path(W.__file__).parent;count=0
    for p in root.glob('capability_*.py'):
        tree=ast.parse(p.read_text())
        fragments={id(x) for n in ast.walk(tree) if isinstance(n,(ast.JoinedStr,ast.BinOp)) for x in ast.walk(n)}
        for n in ast.walk(tree):
            if id(n) not in fragments and isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith(('SELECT ','INSERT ','UPDATE ')):
                parse_sql(n.value);count+=1
    assert count>=30


def test_offices_include_intelligence_and_existing_tabs_and_context_request():
    from sportsassets.api.agent_pages import _cc_page_html
    for agent in ('derek','xavier','audrey'):
        h=_cc_page_html(agent)
        for token in ('THE LEARNING WORKBENCH',"['Plans','Collaboration','Investigate','Intelligence','Experiments','Scorecard']",'body.context={capability_task_id:',"/api/command/agents/capabilities"):
            assert token in h


@pytest.mark.asyncio
async def test_a_new_attempt_asks_afresh_but_a_rerun_of_the_same_claim_does_not(monkeypatch):
    from sportsassets.agents import persona_chat as P
    c=conn();pool=MagicMock();pool.acquire=lambda:transaction_conn(c)
    monkeypatch.setattr(R,'evidence',AsyncMock(return_value=True))
    model=AsyncMock(return_value=answer());monkeypatch.setattr(P,'converse',model)
    monkeypatch.setattr(W,'finish',AsyncMock())
    def at(n):
        t=task();t['spec']['attempts']=n;return t
    first,second,third=at(1),at(2),at(3)
    for t in (first,first,second,second,third):await R.execute(pool,t)
    ids=[x.kwargs['request_id'] for x in model.call_args_list]
    assert ids[0]==ids[1] and ids[2]==ids[3]
    assert len({ids[0],ids[2],ids[4]})==3
    # attempt 1 keeps the id earlier releases used, so an in-flight request
    # survives the deploy
    assert ids[0]=='capreview:'+W.stable('capwork:test')
    from sportsassets.agents import directives as D
    assert all(D.valid_request_id(i) for i in ids)


def _lesson(n):
    return {'lesson_id':'paperlesson:%02d'%n,'kind':'MANAGEMENT_OUTCOMES','learned_at':NOW-3600*n,
            'statement':'exiting at the first review would have differed by $-11.33',
            'metrics':{'reviews':6000+n,'counterfactual_exit_at_first_review':{'positions':[
                {'group_id':'paperexpgrp:%d:%d'%(n,g),'realized_pnl_usd':2.91,'exit_minus_realized_usd':-11.33,
                 'exit_at_first_review_net_usd':-8.42,'note':'x'*300} for g in range(6)]}}}


def test_an_oversized_evidence_item_is_shortened_not_dropped():
    item={'status':'OK','tool':'lessons','read_at':NOW,'data':[_lesson(n) for n in range(10)]}
    assert len(json.dumps(item))>T.EVIDENCE_FACT_CHARS
    fitted,content=T.bounded_evidence(item)
    assert fitted is not None and len(content)<=T.EVIDENCE_FACT_CHARS
    assert fitted['data'][0]['lesson_id']=='paperlesson:00'          # newest kept
    assert fitted['shortened_for_fact_budget']['full_chars']==len(json.dumps(item))
    assert '-8.42' in content and '2.91' in content                    # values unchanged
    small={'status':'OK','tool':'account','read_at':NOW}
    assert T.bounded_evidence(small)==(small,json.dumps(small))        # untouched


@pytest.mark.asyncio
async def test_the_shortened_lessons_ground_their_own_figures():
    from sportsassets.agents.persona_facts import Facts
    from sportsassets.agents import persona_chat as P
    c=conn();t=task('XAVIER')
    t['outcome']={'investigation':[{'status':'OK','tool':'lessons','read_at':NOW,'data':[_lesson(n) for n in range(10)]}]}
    c.fetchrow.side_effect=[t];f=Facts();out=await T.context_facts(c,f,'xavier',t['task_id'])
    assert out['status']=='OK' and any(x['field']=='investigation_0' for x in f.items)
    reply='Exiting at the first review would have netted minus $8.42 against a realised $2.91.'
    assert P.ungrounded_numbers(reply,f.items)==[]
