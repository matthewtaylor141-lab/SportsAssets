"""HTTP authority, validation and failure contracts without production I/O."""
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI,HTTPException
from starlette.testclient import TestClient
from sportsassets.api import agent_capabilities as C


@pytest.fixture
def client(monkeypatch):
    app=FastAPI();app.include_router(C.router)
    app.dependency_overrides[C.require_read]=lambda:'command'
    async def deny():raise HTTPException(403,'CONTROL_REQUIRED')
    app.dependency_overrides[C.require_write]=deny
    use=AsyncMock(return_value={'status':'OK','read_at':1});monkeypatch.setattr(C,'use',use)
    return TestClient(app),app,use


def test_reads_no_store_and_cannot_mutate(client):
    cli,app,use=client
    r=cli.get(C.BASE);assert r.status_code==200 and r.headers['Cache-Control']=='no-store'
    for path in ('goals','control','experiments','work/task1/cancel'):
        assert cli.post(C.BASE+'/'+path,json={'actor':'Matt'}).status_code==403
    assert use.await_count==1


@pytest.mark.parametrize('path,body',[('goals',{'actor':'SYSTEM','title':'a'}),('goals',{'actor':'Matt','sql':'DELETE'}),('control',{'actor':'DEREK','enabled':True}),('experiments',{'actor':'Matt','activate':True})])
def test_named_authority_and_fields_are_checked_before_work(client,path,body):
    cli,app,use=client;app.dependency_overrides[C.require_write]=lambda:'admin'
    assert cli.post(C.BASE+'/'+path,json=body).status_code==400
    use.assert_not_called()


def test_cross_origin_and_oversize_writes_refused(client):
    cli,app,use=client;app.dependency_overrides[C.require_write]=lambda:'admin'
    assert cli.post(C.BASE+'/goals',json={'actor':'Matt'},headers={'Origin':'https://untrusted.test'}).status_code==403
    assert cli.post(C.BASE+'/goals',content='x'*12001).status_code==413
    use.assert_not_called()


def test_goal_write_preserves_request_and_main_account_server_scope(client,monkeypatch):
    cli,app,_=client;app.dependency_overrides[C.require_write]=lambda:'admin'
    create=AsyncMock(return_value={'created':True,'task_ids':['one','two','three']});monkeypatch.setattr(C.W,'create_flow',create)
    async def use(fn,write=False):assert write;return await fn(None)
    monkeypatch.setattr(C,'use',use)
    b={'actor':'Matt','request_id':'stable-request-1','title':'Investigate book delays','due_at':1800000010,'first':'XAVIER','priority':4}
    r=cli.post(C.BASE+'/goals',json=b);assert r.status_code==200
    assert create.call_args.kwargs['source_key']=='management:stable-request-1'
    assert 'account_id' not in create.call_args.kwargs
    assert cli.post(C.BASE+'/goals',json=dict(b,account_id='another-account')).status_code==400


def test_read_failure_returns_503_not_zero_records(client,monkeypatch):
    cli,app,_=client
    monkeypatch.setattr(C,'use',AsyncMock(side_effect=HTTPException(503,'UNAVAILABLE')))
    r=cli.get(C.BASE);assert r.status_code==503 and 'work' not in r.json()


def test_all_routes_have_explicit_read_or_control_dependency():
    for route in C.router.routes:
        expected=C.require_write if 'POST' in route.methods else C.require_read
        assert expected in [d.call for d in route.dependant.dependencies]
