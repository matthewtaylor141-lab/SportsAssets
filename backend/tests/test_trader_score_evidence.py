"""(developer pass) the append-only display-score adapter: identity, ordering,
bounds. Package tests run against the integrated repository code."""
import ast
import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from sportsassets import trader_score_evidence as S


class Tx:
    def __init__(self,c):self.c=c
    async def __aenter__(self):self.c.in_tx=True;return self
    async def __aexit__(self,*a):self.c.in_tx=False
class Conn:
    def __init__(self,prev=None):self.prev=prev;self.calls=[];self.in_tx=False
    def transaction(self):return Tx(self)
    async def execute(self,sql,*args):
        assert self.in_tx;self.calls.append((sql,args))
    async def fetchrow(self,sql,*args):
        assert self.in_tx;self.calls.append((sql,args));return self.prev
    async def fetchval(self,sql,*args):
        assert self.in_tx;self.calls.append((sql,args));return args[0]

def payload():return {'event_id':'e1','provider_event_id':'123','home_id':'h1','away_id':'a1','sport':'MLB','league':'MLB','source':'licensed-provider','source_at':999,'home_score':0,'away_score':0,'source_sequence':1}
def mapping():return dict(payload(),verified=True,evidence_id='certified-binding-1')

def test_score_evidence_is_serialized_and_append_only():
    c=Conn();r=asyncio.run(S.record(c,payload(),mapping=mapping(),now=1000))
    assert r['recorded'] and r['authority']=='DISPLAY_ONLY'
    assert 'pg_advisory_xact_lock' in c.calls[0][0]
    assert 'INSERT INTO market_plane_events' in c.calls[-1][0]
    assert not c.in_tx
    persisted=json.loads(c.calls[-1][1][2]);assert persisted['source_at']==999.0

@pytest.mark.parametrize('field',['event_id','provider_event_id','home_id','away_id','sport','league','source'])
def test_score_every_identity_dimension_must_match(field):
    p=payload();p[field]='wrong';c=Conn()
    assert not asyncio.run(S.record(c,p,mapping=mapping(),now=1000))['recorded']
    assert c.calls==[]

def test_score_out_of_order_cannot_replace_newer_state():
    c=Conn(prev={'event_key':'old','payload':dict(payload(),source_at=1000)})
    r=asyncio.run(S.record(c,payload(),mapping=mapping(),now=1000))
    assert r['why']=='SCORE_SOURCE_TIME_REGRESSION'
    assert not any(sql.startswith('INSERT') for sql,args in c.calls)

def test_same_timestamp_conflicting_score_requires_higher_sequence():
    c=Conn(prev={'event_key':'old','payload':dict(payload(),source_sequence=2)})
    r=asyncio.run(S.record(c,payload(),mapping=mapping(),now=1000))
    assert r['why']=='SCORE_SAME_TIME_CONFLICT_WITHOUT_ADVANCING_SEQUENCE'

def test_known_score_correction_with_advancing_sequence_can_append():
    c=Conn(prev={'event_key':'old','payload':payload()});p=dict(payload(),source_sequence=2,home_score=1)
    assert asyncio.run(S.record(c,p,mapping=mapping(),now=1000))['recorded']

def test_scoring_never_infers_unverified_fixture():
    c=Conn();m=mapping();m['verified']=False
    assert not asyncio.run(S.record(c,payload(),mapping=m,now=1000))['recorded'] and not c.calls

def test_score_oversize_is_bounded_before_database_access():
    c=Conn();p=dict(payload(),last_play='x'*65537)
    assert asyncio.run(S.record(c,p,mapping=mapping(),now=1000))['why']=='SCORE_PAYLOAD_TOO_LARGE'
    assert not c.calls

def test_score_nan_in_other_raw_field_fails_closed():
    c=Conn();p=dict(payload(),other=float('nan'))
    assert asyncio.run(S.record(c,p,mapping=mapping(),now=1000))['why']=='SCORE_PAYLOAD_NOT_FINITE_JSON'

