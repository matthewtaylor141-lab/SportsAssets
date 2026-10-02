import asyncio
import json
import pytest
from sportsassets import pinnapi_feed_runtime as R

class Pool:
    def __init__(self,value=None,block=False):self.value=value;self.block=block
    def acquire(self):return self
    async def __aenter__(self):
        if self.block:await asyncio.Event().wait()
        return self
    async def __aexit__(self,*a):pass
    async def execute(self,sql,key,value,*runtime):
        if sql.startswith('UPDATE'):
            assert "value->>'runtime_id'=$3" in sql
            if not self.value or self.value.get('runtime_id')!=runtime[0]:return 'UPDATE 0'
        self.value=json.loads(value);return 'UPDATE 1'

@pytest.mark.parametrize('value',[None,{}, {'beat_at':1},{'beat_at':110},{'beat_at':'bad'},{'beat_at':'nan'},{'beat_at':'inf'}])
def test_invalid_or_stale_heartbeat(value):
    v=R.heartbeat_view(value,now=100)
    assert v['status'] in ('UNAVAILABLE','STALE_TELEMETRY')
    assert v['authority_proven'] is False

def test_recent_heartbeat_does_not_grant_authority():
    v=R.heartbeat_view({'beat_at':99,'state':'OWNER_SYNCED'},now=100)
    assert v['status']=='RECENT_TELEMETRY' and v['authority_proven'] is False

async def test_final_write_cannot_erase_new_owner():
    p=Pool({'runtime_id':'new','state':'OWNER_SYNCED'})
    assert await R._write_heartbeat(p,{'runtime_id':'old','state':'RELEASED'},final=True)=='UPDATE 0'
    assert p.value['state']=='OWNER_SYNCED'

async def test_final_write_replaces_own_status():
    p=Pool({'runtime_id':'old','state':'OWNER_SYNCED'})
    await R._write_heartbeat(p,{'runtime_id':'old','state':'RELEASED'},final=True)
    assert p.value['state']=='RELEASED'

async def test_write_has_pool_acquisition_deadline(monkeypatch):
    monkeypatch.setattr(R,'HEARTBEAT_WRITE_TIMEOUT_S',.01)
    with pytest.raises(TimeoutError):await R._write_heartbeat(Pool(block=True),{})

@pytest.mark.parametrize('blocked',[False,True])
async def test_shutdown_revokes_and_completes_even_when_store_unavailable(monkeypatch,blocked):
    events=[]
    class Cache:
        def lost(self,reason):events.append('revoke')
    class Owner:
        cache=Cache()
        def stop(self):events.append('stop');self.cache.lost('stop')
    async def beat():
        try:await asyncio.Event().wait()
        finally:events.append('beat-stopped')
    b=asyncio.create_task(beat());await asyncio.sleep(0)
    t=asyncio.create_task(asyncio.sleep(0))
    p=Pool({'runtime_id':'own','state':'OWNER_SYNCED'},block=blocked)
    monkeypatch.setattr(R,'HEARTBEAT_WRITE_TIMEOUT_S',.01)
    monkeypatch.setattr(R,'_STATE',dict(owner=Owner(),task=t,beat=b,pool=p,runtime_id='own',census={}))
    result=await R.shutdown_default(.1)
    assert events[:2]==['stop','revoke'] and b.done()
    assert 'beat-stopped' in events and result['verdict']=='CLOSED'
    if blocked:assert result['terminal_heartbeat']=='UNAVAILABLE:TimeoutError'
    else:assert p.value['state']=='RELEASED'
    assert R.read(1,'x')['ok'] is False
