"""Synthetic local database proofs; never reset the production PAPER account."""
import asyncio
import copy
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from decimal import Decimal

import asyncpg
import pytest
import pytest_asyncio

from sportsassets import bettor_paper_day_one as E
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import bettor_paper_readmodel as RM
from sportsassets import paper_epoch_acceptance as A
from sportsassets.api import command_paper as API
from tests import paper_harness as H

SHA = 'a' * 40
PROOF = {'release_sha': SHA, 'packet_digest': 'b' * 64, 'verified_at': 1}


@pytest.fixture(scope='module')
def epoch_database():
    if not H.DSN:
        pytest.skip('needs RN1X_TEST_DSN')
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(H.DSN)
    name = 'epoch_regression_' + uuid.uuid4().hex
    admin_dsn = urlunsplit(parts._replace(path='/postgres'))
    target = urlunsplit(parts._replace(path='/' + name))
    async def create():
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        from sportsassets.workers import premap
        pool = await asyncpg.create_pool(target)
        try:
            await premap._ensure_table(pool)
        finally:
            await pool.close()
    async def drop():
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute(f'DROP DATABASE "{name}"')
        finally:
            await admin.close()
    asyncio.run(create())
    try:
        subprocess.run([sys.executable, '-m', 'sportsassets.scripts.migrate'],
            env=dict(os.environ, DATABASE_URL=target), check=True, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        yield target
    finally:
        asyncio.run(drop())


@pytest_asyncio.fixture
async def conn(epoch_database):
    c = await asyncpg.connect(epoch_database)
    tr = c.transaction()
    await tr.start()
    await S.ensure_session(c, account_id=L.ACCOUNT_ID, now=H.T0)
    try:
        yield c
    finally:
        await tr.rollback()
        await c.close()


async def activate(c, epoch='day-one'):
    return await E._activate_verified(c, epoch_id=epoch, request_id=epoch, proof=PROOF)


async def account(c, aid):
    s = await S.active_session(c, aid)
    return {'account_id': aid, 'session_id': s['session_id'], 'config': s['config']}


async def buy(c, a, *, at, key='buy', fill=False):
    o = H.order(a, key=key, qty=100, limit=.5, at=at, slug='test-epoch-market')
    r = await L.submit_order(c, o, now=at, fee_fn=H.zero_fee)
    assert r['ok'], r
    if fill:
        await H.observe(c, o['us_market_slug'], at+3, offers=[(.5,100)], bids=[(.49,100)])
        filled = await SIM.simulate_order(c, r['order']['order_id'], now=at+4, fee_fn=H.zero_fee)
        assert filled['fills'], filled
    return r['order']


async def test_exact_opening_and_frozen_risk_policy(conn):
    old = await S.active_session(conn, L.ACCOUNT_ID)
    r = await activate(conn)
    new = await S.active_session(conn)
    assert new['account_id'] == r['account_id'] != L.ACCOUNT_ID
    assert old['effective_config']['risk'] == new['effective_config']['risk']
    assert old['effective_config']['entry'] == new['effective_config']['entry']
    b = r['balances']
    for name in ['cash_usd', 'available_usd', 'total_equity_usd']:
        assert Decimal(str(b[name])) == Decimal('500000.00')
    assert b['reserved_usd'] == b['realized_pnl_usd'] == b['unrealized_pnl_usd'] == 0
    assert not b['open_positions']
    dd = await RM.drawdown(conn)
    assert dd['snapshots'] == 1 and dd['max_drawdown_pct'] == 0
    assert await conn.fetchval('SELECT count(*) FROM paper_fills WHERE account_id=$1', r['account_id']) == 0
    assert await conn.fetchval("SELECT count(*) FROM paper_ledger WHERE account_id=$1 AND kind='INITIAL_FUNDING'", r['account_id']) == 1
    payload = await API.account_payload(conn)
    assert payload['account_id'] == r['account_id'] and payload['epoch']['opening_verified']


async def test_historical_trading_loss_and_later_settlement_correction_stay_archived(conn):
    a = await account(conn, L.ACCOUNT_ID)
    o = await buy(conn, a, at=H.T0, fill=True)
    await L.settle(conn, account_id=L.ACCOUNT_ID, group_id=o['group_id'], slug=o['us_market_slug'], holding_side='LONG',
        settlement_event_key='final', outcome='LOST', evidence={'synthetic': True}, evidence_source='TEST_FIXTURE', at=H.T0+100)
    before = await conn.fetch('SELECT to_jsonb(l) AS row FROM paper_ledger l WHERE account_id=$1 ORDER BY seq', L.ACCOUNT_ID)
    r = await activate(conn)
    assert r['historical_receipt']['balances']['realized_pnl_usd'] == -50
    assert r['historical_receipt']['cash_usd'] == '499950.000000'
    assert before == await conn.fetch('SELECT to_jsonb(l) AS row FROM paper_ledger l WHERE account_id=$1 ORDER BY seq', L.ACCOUNT_ID)
    await L.correct_settlement(conn, account_id=L.ACCOUNT_ID, group_id=o['group_id'], slug=o['us_market_slug'], holding_side='LONG',
        settlement_event_key='final', outcome='WON', evidence={'synthetic_correction': True}, evidence_source='TEST_FIXTURE', at=time.time())
    assert (await L.cash_state(conn,r['account_id']))['cash'] == E.OPENING
    assert (await L.balances(conn,L.ACCOUNT_ID))['cash_usd'] == 500050
    # The original loss entry remains exactly unchanged, even after correction.
    assert before == (await conn.fetch('SELECT to_jsonb(l) AS row FROM paper_ledger l WHERE account_id=$1 ORDER BY seq', L.ACCOUNT_ID))[:len(before)]


@pytest.mark.parametrize('fill', [False, True])
async def test_open_orders_reserves_and_held_positions_refuse_activation(conn, fill):
    await buy(conn, await account(conn,L.ACCOUNT_ID), at=H.T0, fill=fill)
    with pytest.raises(E.EpochRefused, match='OUTSTANDING_OBLIGATIONS'):
        await activate(conn)
    assert await E.selected_account(conn) == L.ACCOUNT_ID
    assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_account_epochs)')
    assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM paper_accounts WHERE account_id LIKE 'paper_day_one_%')")


async def test_activation_retry_and_rollback_are_idempotent_and_non_destructive(conn):
    one = await activate(conn)
    two = await activate(conn)
    assert one['account_id'] == two['account_id']
    assert await conn.fetchval('SELECT count(*) FROM paper_epoch_events') == 1
    r = await E.rollback(conn, epoch_id='day-one', request_id='undo')
    again = await E.rollback(conn, epoch_id='day-one', request_id='undo')
    assert r['account_id'] == L.ACCOUNT_ID and again['idempotent']
    assert await conn.fetchval('SELECT count(*) FROM paper_account_epochs') == 1
    assert (await L.cash_state(conn,one['account_id']))['cash'] == E.OPENING
    with pytest.raises(E.EpochRefused, match='DIFFERENT_CONTEXT'):
        await activate(conn)


@pytest.mark.parametrize('fill', [False, True])
async def test_rollback_cannot_abandon_orders_or_position_management(conn, fill):
    r = await activate(conn)
    await buy(conn, await account(conn,r['account_id']), at=r['opened_at']+1, fill=fill)
    with pytest.raises(E.EpochRefused, match='OUTSTANDING_OBLIGATIONS'):
        await E.rollback(conn, epoch_id='day-one', request_id='undo')
    assert await E.selected_account(conn) == r['account_id']
    assert (await S.active_session(conn))['account_id'] == r['account_id']


async def test_failure_after_funding_rolls_back_every_write(conn, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError('synthetic failure after funding')
    monkeypatch.setattr(RM,'snapshot_equity',fail)
    with pytest.raises(RuntimeError, match='after funding'):
        await activate(conn)
    assert await E.selected_account(conn) == L.ACCOUNT_ID
    assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM paper_accounts WHERE account_id LIKE 'paper_day_one_%')")
    assert await conn.fetchval('SELECT count(*) FROM paper_epoch_events') == 0


async def test_stale_orders_and_sessions_cannot_cross_cutover(conn):
    old = await account(conn,L.ACCOUNT_ID)
    stale = H.order(old, key='stale', qty=10, limit=.5)
    r = await activate(conn)
    async with conn.transaction():
        with pytest.raises(asyncpg.RaiseError, match='NOT_SELECTED'):
            async with conn.transaction():
                await L.submit_order(conn,stale,fee_fn=H.zero_fee)
    assert not (await S.ensure_session(conn,account_id=L.ACCOUNT_ID))['ok']
    new = await account(conn,r['account_id'])
    wrong = H.order(new,key='wrong-owner',qty=10,limit=.5,at=r['opened_at']+1)
    wrong['session_id'] = old['session_id']
    with pytest.raises(asyncpg.RaiseError, match='SESSION_ACCOUNT_MISMATCH'):
        async with conn.transaction():
            await L.submit_order(conn,wrong,fee_fn=H.zero_fee)
    stale_new = H.order(new,key='predates',qty=10,limit=.5,at=H.T0)
    with pytest.raises(asyncpg.RaiseError, match='PREDATES_EPOCH'):
        async with conn.transaction():
            await L.submit_order(conn,stale_new,fee_fn=H.zero_fee)


async def test_delayed_new_order_refused_after_rollback(conn):
    r = await activate(conn)
    stale = H.order(await account(conn,r['account_id']),key='late',qty=10,limit=.5,at=r['opened_at']+1)
    await E.rollback(conn,epoch_id='day-one',request_id='undo')
    with pytest.raises(asyncpg.RaiseError, match='NOT_SELECTED'):
        async with conn.transaction():
            await L.submit_order(conn,stale,fee_fn=H.zero_fee)
    assert (await L.cash_state(conn,r['account_id']))['cash'] == E.OPENING


async def test_history_and_epoch_receipts_are_immutable(conn):
    await activate(conn)
    for sql in ['DELETE FROM paper_account_epochs','UPDATE paper_epoch_events SET detail=\'{}\'', 'DELETE FROM paper_ledger']:
        with pytest.raises(asyncpg.RaiseError):
            async with conn.transaction():
                await conn.execute(sql)


async def test_runtime_pass_uses_selected_account_and_keeps_shadow(conn):
    from sportsassets.agents import paper_runtime as R
    r = await activate(conn)
    seen = []
    async def step(c, ctx):
        seen.append(ctx['account_id'])
        assert ctx['config']['epoch_id'] == 'day-one'
        assert ctx['session_id'] == (await S.active_session(c))['session_id']
        return {'action': 'CASH', 'why': 'NO_VALID_OPPORTUNITY_IN_SYNTHETIC_FIXTURE'}
    out = await R.paper_pass(conn, force=True, steps=[('derek',step)])
    assert out['ran'] and seen == [r['account_id']]
    assert out['mutation_attempts'] == 0


async def test_public_activation_requires_deployed_identity_before_any_write(conn, monkeypatch):
    monkeypatch.setenv('RENDER_GIT_COMMIT',SHA)
    monkeypatch.delenv('RENDER_API_KEY',raising=False)
    monkeypatch.setattr(A,'verify_acceptance',lambda *a,**kw:dict(PROOF))
    with pytest.raises(E.EpochRefused,match='DEPLOYMENT_IDENTITY_READER'):
        await E.activate(conn,epoch_id='x',request_id='x',evidence_dir='unused',release_sha=SHA,bundle='unused')
    assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM paper_accounts WHERE account_id LIKE 'paper_day_one_%')")


def green_files(now=1000):
    data={'evidence_packet.json':{'built_at':now,'identity':{'judge_sha':A.JUDGE_SHA},'acceptance':{'independent_pm_state':{'source':'acceptance.json','path':'independent_pm_state','value':'GREEN'}}},
          'acceptance.json':{'independent_pm_state':'GREEN','critical_failures':[],'unproven':{}},
          'scorecard_14.json':{'release_sha':SHA,'target':.95,'categories':[{'category':n,'readiness':1.,'passes':True,'unreadable_units':[], 'units':[{'class':'PASS'}], 'components':[{'numerator':100,'denominator':100}]} for n in sorted(A.CATEGORIES)]},
          'gates.json':{'runs':{n:{'head_sha':SHA,'status':'completed','conclusion':'success'} for n in ['backend_tests','capital_critical','commit_guard','engine_diagnostic']}}}
    return data


@pytest.mark.parametrize('mutation,reason',[
 ('red','CONSISTENT_GREEN'),('contradiction','CONSISTENT_GREEN'),('missing_gate','EXACT_SHA'),
 ('wrong_sha','EXACT_SHA'),('fake_rate','BELOW_TARGET'),('empty_denominator','BELOW_TARGET'),
 ('missing_category','INCOMPLETE'),('duplicate_category','INCOMPLETE'),('threshold','THRESHOLD'),
 ('unreadable','NOT_ACCEPTED'),('code_red','HAS_REDS'),('stale','NOT_CURRENT'),('future','NOT_CURRENT')])
def test_adversarial_acceptance_cannot_authorize_reset(mutation,reason):
    d=green_files()
    if mutation in ['red','contradiction']: d['acceptance.json']['independent_pm_state']='RED'
    elif mutation=='missing_gate': del d['gates.json']['runs']['commit_guard']
    elif mutation=='wrong_sha': d['gates.json']['runs']['backend_tests']['head_sha']='c'*40
    elif mutation=='fake_rate': d['scorecard_14.json']['categories'][0]['components'][0]['numerator']=94
    elif mutation=='empty_denominator': d['scorecard_14.json']['categories'][0]['components'][0]['denominator']=0
    elif mutation=='missing_category': d['scorecard_14.json']['categories'].pop()
    elif mutation=='duplicate_category': d['scorecard_14.json']['categories'][0]=d['scorecard_14.json']['categories'][1]
    elif mutation=='threshold': d['scorecard_14.json']['target']=.9
    elif mutation=='unreadable': d['scorecard_14.json']['categories'][0]['unreadable_units']=['X']
    elif mutation=='code_red': d['scorecard_14.json']['categories'][0]['units']=[{'class':'FAIL'}]
    elif mutation=='stale': d['evidence_packet.json']['built_at']=0
    elif mutation=='future': d['evidence_packet.json']['built_at']=1001
    with pytest.raises(E.EpochRefused,match=reason):
        A.validate_contents({n:json.dumps(v).encode() for n,v in d.items()},release_sha=SHA,now=1000)


@pytest.mark.parametrize('signature_rc',[0,1])
def test_actual_bytes_not_verified_boolean_gate_activation(tmp_path,monkeypatch,signature_rc):
    d=green_files()
    files={n:json.dumps(v).encode() for n,v in d.items()}
    for n,b in files.items(): (tmp_path/n).write_bytes(b)
    (tmp_path/'SHA256SUMS').write_text(''.join(hashlib.sha256(b).hexdigest()+'  '+n+'\n' for n,b in files.items()))
    commands=[]
    def verify(command,**kw):
        commands.append(command)
        assert '--deny-self-hosted-runners' in command and A.WORKFLOW in command
        assert command[0] == A.GH_PATH and command[command.index('--signer-digest')+1] == A.JUDGE_SHA
        return subprocess.CompletedProcess(command,signature_rc)
    monkeypatch.setattr(A.subprocess,'run',verify)
    monkeypatch.setattr(A,'_trust_anchors',lambda:A.ROOT_PATH.read_bytes())
    kw=dict(release_sha=SHA,bundle='synthetic-bundle',now=1000)
    if signature_rc:
        with pytest.raises(E.EpochRefused,match='SIGNATURE_VERIFICATION_FAILED'): A.verify_acceptance(tmp_path,**kw)
    else:
        proof=A.verify_acceptance(tmp_path,**kw)
        assert proof['packet_digest']==hashlib.sha256(files['evidence_packet.json']).hexdigest()
        assert len(commands)==2
        (tmp_path/'acceptance.json').write_text('{"independent_pm_state":"RED"}')
        with pytest.raises(E.EpochRefused,match='HASH_MISMATCH'): A.verify_acceptance(tmp_path,**kw)


async def test_concurrent_activation_funds_once():
    """Eight independent transactions serialize, creating one opening entry."""
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(H.DSN)
    name = 'epoch_concurrency_' + uuid.uuid4().hex
    admin_dsn = urlunsplit(parts._replace(path='/postgres'))
    target = urlunsplit(parts._replace(path='/' + name))
    admin = await asyncpg.connect(admin_dsn)
    created = False
    try:
        # Owned disposable fresh DB, independent of other test connections.
        await admin.execute(f'CREATE DATABASE "{name}"')
        created = True
        from sportsassets.workers import premap
        pool = await asyncpg.create_pool(target)
        try:
            await premap._ensure_table(pool)
        finally:
            await pool.close()
        subprocess.run([sys.executable, '-m', 'sportsassets.scripts.migrate'],
            env=dict(os.environ, DATABASE_URL=target), check=True, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        seed = await asyncpg.connect(target)
        try:
            await S.ensure_session(seed, account_id=L.ACCOUNT_ID, now=H.T0)
        finally:
            await seed.close()
        async def run():
            c = await asyncpg.connect(target)
            try:
                return await activate(c, 'concurrent-day-one')
            finally:
                await c.close()
        results = await asyncio.gather(*(run() for _ in range(8)))
        assert len({r['account_id'] for r in results}) == 1
        c = await asyncpg.connect(target)
        try:
            aid = results[0]['account_id']
            assert await c.fetchval('SELECT count(*) FROM paper_epoch_events') == 1
            assert await c.fetchval('SELECT count(*) FROM paper_ledger WHERE account_id=$1', aid) == 1
            assert (await L.cash_state(c, aid))['cash'] == Decimal('500000')
        finally:
            await c.close()
    finally:
        if created:
            await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def test_binary_rollback_compatibility_is_only_proven_before_activation(conn):
    import importlib.util
    spec = importlib.util.spec_from_file_location('epoch_upgrade_receipt', pathlib.Path(__file__).parents[1] / 'tools/upgrade_path_receipt.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    dormant = await tool.snapshot(conn)
    assert len(dormant['paper_orders']['proven_dormant_epoch_triggers']) == 1
    await activate(conn)
    active = await tool.snapshot(conn)
    assert 'proven_dormant_epoch_triggers' not in active['paper_orders']
    sql = (pathlib.Path(__file__).parents[1] / 'migrations/rollback/317_paper_day_one_epoch.down.sql').read_text()
    with pytest.raises(asyncpg.RaiseError, match='epoch evidence must be preserved'):
        async with conn.transaction():
            await conn.execute(sql)


async def test_new_epoch_position_gets_real_xavier_protection_without_touching_archive(conn):
    r = await activate(conn)
    a = await account(conn, r['account_id'])
    before = await conn.fetch('SELECT to_jsonb(l) AS row FROM paper_ledger l WHERE account_id=$1 ORDER BY seq', L.ACCOUNT_ID)
    o = await buy(conn, a, at=r['opened_at']+1, fill=True)
    sess = await S.active_session(conn)
    ctx = dict(a, config=sess['effective_config'], now=r['opened_at']+6, fee_fn=H.zero_fee)
    protected = await H.protect(conn, ctx, o['group_id'], at=r['opened_at']+6)
    assert protected
    for oid in protected:
        row = await conn.fetchrow('SELECT account_id,session_id,role,state FROM paper_orders WHERE order_id=$1', oid)
        assert dict(row) == {'account_id': a['account_id'], 'session_id': a['session_id'], 'role': 'STANDING_PROTECTION', 'state': 'RESTING'}
    assert before == await conn.fetch('SELECT to_jsonb(l) AS row FROM paper_ledger l WHERE account_id=$1 ORDER BY seq', L.ACCOUNT_ID)
    with pytest.raises(E.EpochRefused, match='OUTSTANDING_OBLIGATIONS'):
        await E.rollback(conn, epoch_id='day-one', request_id='cannot-abandon-xavier')


async def test_cross_account_fill_is_rejected_by_database(conn):
    old = await account(conn, L.ACCOUNT_ID)
    r = await activate(conn)
    o = await buy(conn, await account(conn, r['account_id']), at=r['opened_at']+1, fill=True)
    with pytest.raises(asyncpg.RaiseError, match='PAPER_FILL_ORDER_ACCOUNT_MISMATCH'):
        async with conn.transaction():
            await conn.execute("INSERT INTO paper_fills SELECT (jsonb_populate_record(NULL::paper_fills,to_jsonb(f)||jsonb_build_object('fill_id',$2::text,'account_id',$3::text,'session_id',$4::text))).* FROM paper_fills f WHERE order_id=$1 LIMIT 1", o['order_id'], 'paper_f_'+uuid.uuid4().hex, old['account_id'], old['session_id'])


async def test_expired_acceptance_cannot_activate_after_lock_wait(conn):
    with pytest.raises(E.EpochRefused, match='EXPIRED_WAITING_FOR_ACCOUNT_LOCK'):
        await E._activate_verified(conn, epoch_id='expired', request_id='expired', proof=dict(PROOF, valid_until=1))
    assert await E.selected_account(conn) == L.ACCOUNT_ID
    assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_account_epochs)')


async def test_upgrade_proof_refuses_changed_guard_execution_context(conn):
    import importlib.util
    spec = importlib.util.spec_from_file_location('epoch_upgrade_context', pathlib.Path(__file__).parents[1] / 'tools/upgrade_path_receipt.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    original = await tool.snapshot(conn)
    assert original['paper_orders']['proven_dormant_epoch_triggers']
    await conn.execute('ALTER FUNCTION paper_epoch_order_owner() SECURITY DEFINER')
    changed = await tool.snapshot(conn)
    assert 'proven_dormant_epoch_triggers' not in changed['paper_orders']


def test_simulated_selector_has_no_writer_dependencies_or_calls():
    import ast
    from sportsassets import simulated_account_context as C
    tree = ast.parse(pathlib.Path(C.__file__).read_text())
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(tree))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert all(n.func.attr in ('fetchval', 'fetch', 'startswith') for n in calls)
    for call in calls:
        if call.func.attr in ('fetchval', 'fetch'):
            assert isinstance(call.args[0], ast.Constant) and call.args[0].value.startswith('SELECT ')


@pytest.mark.parametrize('module_name,endpoint_name', [
    ('command_capital_authority','paper_capital_authority'),
    ('command_profitability_scoreboard','paper_profitability_scoreboard'),
    ('command_turnaround','paper_turnaround'),
    ('command_confidence_ladder','confidence_ladder'),
    ('command_sleeves','profitability_sleeves'),
    ('command_validation','profitability_validation'),
    ('command_revenue_reliability','revenue_readiness'),
])
async def test_hot_management_cache_cannot_cross_epoch(conn,monkeypatch,module_name,endpoint_name):
    import importlib
    module = importlib.import_module('sportsassets.api.' + module_name)
    class Acquire:
        async def __aenter__(self): return conn
        async def __aexit__(self,*args): pass
    class Pool:
        def acquire(self): return Acquire()
    async def pool(): return Pool()
    seen = []
    async def read(c,*args,account_id,**kwargs):
        seen.append(account_id)
        return {'account_id':account_id,'status':'OK','data':{'account_id':account_id}}
    monkeypatch.setattr(module,'_pool',pool)
    if module_name == 'command_revenue_reliability':
        from sportsassets.revenue_reliability import read as RR
        monkeypatch.setattr(RR,'read',read)
    else:
        monkeypatch.setattr(module,'_read' if module_name in {'command_confidence_ladder','command_sleeves','command_validation'} else 'read',read)
    module._CACHE.clear()
    try:
        endpoint = getattr(module,endpoint_name)
        from fastapi import Response
        kwargs = {'sleeve':'INVESTMENT'} if module_name == 'command_sleeves' else {'since':None} if module_name == 'command_validation' else {'response':Response()} if module_name == 'command_revenue_reliability' else {}
        old = await endpoint(**kwargs)
        r = await activate(conn)
        new = await endpoint(**kwargs)
        again = await endpoint(**kwargs)
        account = lambda out: out.get('account_id') or out['data']['account_id']
        assert account(old) == L.ACCOUNT_ID
        assert account(new) == account(again) == r['account_id']
        assert seen == [L.ACCOUNT_ID,r['account_id']]
        await E.rollback(conn,epoch_id='day-one',request_id='cache-rollback')
        assert account(await endpoint(**kwargs)) == L.ACCOUNT_ID
        async def broken_selector(c):
            raise ValueError('invalid account pointer')
        monkeypatch.setattr(L,'selected_account',broken_selector)
        assert (await endpoint(**kwargs))['status'] == 'UNAVAILABLE'
    finally:
        module._CACHE.clear()


@pytest.mark.parametrize('view',['live','curve'])
async def test_equity_cache_cannot_cross_epoch(conn,monkeypatch,view):
    from sportsassets.api import command_equity as E
    from sportsassets import bettor_paper_day_one as EPOCH
    from fastapi import Request,Response
    class Acquire:
        async def __aenter__(self): return conn
        async def __aexit__(self,*args): pass
    class Pool:
        def acquire(self): return Acquire()
    async def pool(): return Pool()
    seen=[]
    async def payload(c,*,account_id,**kwargs):
        seen.append(account_id)
        return {'account_id':account_id,'etag':account_id,'seq':1}
    monkeypatch.setattr(E,'_pool',pool)
    monkeypatch.setattr(E,'live_payload' if view=='live' else 'curve_payload',payload)
    E._LIVE.update(at=0,payload=None)
    E._CURVES.clear()
    async def endpoint():
        if view=='live': return await E._cached_live()
        return await E.equity_curve(Request({'type':'http','headers':[]}),Response(),book='PAPER',venue=None,window='1d')
    try:
        old=await endpoint()
        r=await activate(conn)
        new=await endpoint()
        again=await endpoint()
        assert old['account_id']==L.ACCOUNT_ID
        assert new['account_id']==again['account_id']==r['account_id']
        assert seen==[L.ACCOUNT_ID,r['account_id']]
        await EPOCH.rollback(conn,request_id='cache-rollback',epoch_id='day-one')
        restored=await endpoint()
        assert restored['account_id']==L.ACCOUNT_ID
    finally:
        E._LIVE.update(at=0,payload=None)
        E._CURVES.clear()


@pytest.mark.parametrize('state',['QUARANTINED','SHADOW_ONLY','RETIRED','REDUCED_SIZE'])
async def test_registered_epoch_inherits_strategy_restrictions(conn,state):
    from sportsassets import bettor_strategy_lifecycle as LC
    await LC.record(conn,account_id=L.ACCOUNT_ID,strategy='DEREK',
        from_state=LC.ACTIVE_CHALLENGER,to_state=state,rule_id='historical-risk',
        actor=LC.AUTOMATIC_ACTOR,evidence={'historical_loss_retained':True},
        why='synthetic historical restriction',at=H.T0)
    r=await activate(conn)
    cur=await LC.current_state(conn,r['account_id'],'DEREK')
    assert cur['state']==state
    gate=await LC.decision_gate(conn,account_id=r['account_id'],strategy='DEREK',at=time.time())
    assert gate['state']==state
    if state in LC.NO_ENTRY_STATES:
        assert gate['refusal']==LC.ENTRY_STATE_REFUSAL[state] and gate['size_factor']==0
    else:
        assert gate['size_factor']==LC.decision_size_factor(state)<1
    assert (await LC.current_state(conn,L.ACCOUNT_ID,'DEREK'))['state']==state


async def test_epoch_strategy_recovery_requires_named_person_and_preserves_source_quarantine(conn):
    from sportsassets import bettor_strategy_lifecycle as LC
    await LC.record(conn,account_id=L.ACCOUNT_ID,strategy='DEREK',from_state=LC.ACTIVE_CHALLENGER,
        to_state=LC.QUARANTINED,rule_id='historical-risk',actor=LC.AUTOMATIC_ACTOR,
        evidence={'synthetic':True},why='historical restriction',at=H.T0)
    first=await activate(conn)
    second=await activate(conn,'second-epoch')
    acct=second['account_id']
    inherited=await LC.current_state(conn,acct,'DEREK')
    assert inherited['state']==LC.QUARANTINED and inherited['inherited_from_account_id']==L.ACCOUNT_ID
    denied=await LC.transition(conn,account_id=acct,strategy='DEREK',to_state=LC.SHADOW_ONLY,
        actor=LC.AUTOMATIC_ACTOR,why='must not automatically recover')
    assert not denied['ok']
    allowed=await LC.transition(conn,account_id=acct,strategy='DEREK',to_state=LC.SHADOW_ONLY,
        actor='person:synthetic-owner',why='explicit synthetic recovery review')
    assert allowed['ok']
    event=await conn.fetchrow('SELECT * FROM paper_strategy_lifecycle_events WHERE event_id=$1',allowed['event_id'])
    assert event['actor']=='person:synthetic-owner' and event['from_state']==LC.QUARANTINED
    assert event['rules_sha']==LC.RULES_SHA and event['evidence'] and event['why']
    assert (await LC.current_state(conn,first['account_id'],'DEREK'))['state']==LC.QUARANTINED
    assert (await LC.current_state(conn,L.ACCOUNT_ID,'DEREK'))['state']==LC.QUARANTINED
    await LC.transition(conn,account_id=acct,strategy='DEREK',to_state=LC.REDUCED_SIZE,
        actor='person:synthetic-owner',why='separate declared step')
    denied=await LC.transition(conn,account_id=acct,strategy='DEREK',to_state=LC.ACTIVE_CHALLENGER,
        actor='person:synthetic-owner',why='no forward samples')
    assert not denied['ok'] and denied['refusal']==LC.R_TRANSITION_NO_FORWARD_EVIDENCE


@pytest.mark.parametrize('fault',['cycle','read_error','too_deep'])
async def test_malformed_epoch_policy_ancestry_fails_closed(monkeypatch,fault):
    from sportsassets import bettor_strategy_lifecycle as LC
    async def schema(c): return True
    monkeypatch.setattr(LC,'schema',schema)
    class Broken:
        async def fetchrow(self,*args): return None
        async def fetchval(self,sql,*args):
            if 'to_regclass' in sql: return True
            if fault=='read_error': raise RuntimeError('unreadable inherited policy')
            return args[0] if fault=='cycle' else args[0]+'_parent'
    gate=await LC.decision_gate(Broken(),account_id='registered_epoch',strategy='DEREK',at=1000)
    assert not gate['ok'] and gate['size_factor']==0
    assert gate['refusal']==LC.R_LIFECYCLE_UNREADABLE

