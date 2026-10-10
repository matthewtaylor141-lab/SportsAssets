"""Independent integration-review regressions; synthetic database only."""
import json
import os
import pathlib
import subprocess
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from tests.test_day_one_paper_epoch import (
    conn, epoch_database, activate, account, buy, green_files, SHA,
)
from tests import paper_harness as H
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_profitability_bind as B
from sportsassets import bettor_capital_authority as CA
from sportsassets import paper_epoch_acceptance as A
from sportsassets import bettor_paper_day_one as E

PRODUCTION_FORWARD_ECONOMICS = CA.forward_economics


async def _valuation(conn, *, cid: str, price: float, decided: float) -> int:
    """A SYNTHETIC entry-experiment valuation, outcome unknown at insert
    (the table's own prospective-only trigger), in the shape the entry
    lane writes. Local copy: the release tree reverted the provenance
    suite this helper was first written in."""
    from sportsassets import bettor_external_shadow as ext
    return await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, probability, executable_price, cost_per_contract, "
        " decision, admissible, refusals, why, payout_event, buy_intent, "
        " ladder_side, record_purpose, decided_at, event_key) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'the-odds-api.com/v4','pinnacle','power','PMUS',$2,$2,"
        " 'HOME','baseball','h2h','FULL_GAME','{}'::jsonb,2,2,"
        " to_timestamp($3 - 5),to_timestamp($3 - 4),0.55,$4,0.0175,"
        " 'NO_TRADE', false, ARRAY['SYNTHETIC_RESEARCH_RECORD'],"
        " 'synthetic test evidence','HOME','ORDER_INTENT_BUY_LONG','ASK',"
        " 'ENTRY_DECISION',to_timestamp($3),'e-' || $2) RETURNING id",
        ext.EXPERIMENT_ID, cid, float(decided), float(price))


async def test_forged_verifier_root_and_shell_sha_cannot_activate(conn,tmp_path,monkeypatch):
    data=green_files(now=time.time())
    files={n:json.dumps(v).encode() for n,v in data.items()}
    import hashlib
    for name,content in files.items(): (tmp_path/name).write_bytes(content)
    (tmp_path/'SHA256SUMS').write_text(''.join(hashlib.sha256(content).hexdigest()+'  '+name+'\n' for name,content in files.items()))
    fake=tmp_path/'fake-gh'
    fake.write_text('#!/bin/sh\nexit 0\n')
    fake.chmod(0o700)
    monkeypatch.setenv('RENDER_GIT_COMMIT',SHA)
    with pytest.raises((TypeError,E.EpochRefused)):
        await E.activate(conn,epoch_id='forged',request_id='forged',evidence_dir=tmp_path,
            release_sha=SHA,bundle='nonexistent-bundle',trusted_root='nonexistent-root',gh=str(fake))
    assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_account_epochs)')


async def test_loss_budget_and_forward_economics_survive_cutover(conn):
    old = await account(conn, L.ACCOUNT_ID)
    now = time.time()
    o = H.order(old, key='historical-risk-loss', qty=40000, limit=.5,
                at=now-1000, slug='test-epoch-market')
    r = await L.submit_order(conn, o, now=now-1000, fee_fn=H.zero_fee)
    assert r['ok'], r
    from sportsassets import bettor_paper_simulator as SIM
    await H.observe(conn, o['us_market_slug'], now-997,
                    offers=[(.5, 40000)], bids=[(.49, 40000)])
    f = await SIM.simulate_order(conn, r['order']['order_id'], now=now-996,
                                 fee_fn=H.zero_fee)
    assert f['fills']
    await L.settle(conn, account_id=L.ACCOUNT_ID, group_id=o['group_id'],
        slug=o['us_market_slug'], holding_side='LONG',
        settlement_event_key='risk-loss', outcome='LOST', evidence={'fixture':True},
        evidence_source='SYNTHETIC_REVIEW', at=now-900)
    strategy = L.DEFAULT_STRATEGY
    before = await CA.stopping_rules_now(conn, L.ACCOUNT_ID, strategy, now=now)
    forward = await PRODUCTION_FORWARD_ECONOMICS(conn, L.ACCOUNT_ID, strategy, now=now)
    assert forward['ok'] and forward['verdict'] != CA.POSITIVE
    assert before['ok'] and any(r['rule_id']=='LOSS_BUDGET_QUARANTINE' for r in before['firing'])
    new = await activate(conn, 'risk-history')
    after = await CA.stopping_rules_now(conn, new['account_id'], strategy, now=now)
    assert after['firing'] == before['firing']
    assert after['rolling'] == before['rolling']
    assert await PRODUCTION_FORWARD_ECONOMICS(conn, new['account_id'], strategy, now=now) == forward
    assert (await CA.stopping_rules_now(conn, new['account_id'], strategy, now=now, positions=[]))['firing'] == before['firing']
    regime = await B.regime_observations(conn, new['account_id'], strategy, since=now-2000)
    assert sum(len(v['paper']) for v in regime.values()) == 1
    newer = await activate(conn, 'risk-history-next')
    assert (await CA.stopping_rules_now(conn, newer['account_id'], strategy, now=now))['rolling'] == before['rolling']


async def test_rollback_cannot_hide_losses_from_a_rolled_back_child(conn):
    new=await activate(conn,'rollback-loss')
    ctx=await account(conn,new['account_id'])
    now=time.time()+1000
    o=H.order(ctx,key='rollback-real-loss',qty=40000,limit=.5,at=now-900,slug='test-rollback-loss')
    r=await L.submit_order(conn,o,now=now-900,fee_fn=H.zero_fee)
    assert r['ok'],r
    from sportsassets import bettor_paper_simulator as SIM
    await H.observe(conn,o['us_market_slug'],now-897,offers=[(.5,40000)],bids=[(.49,40000)])
    assert (await SIM.simulate_order(conn,r['order']['order_id'],now=now-896,fee_fn=H.zero_fee))['fills']
    await L.settle(conn,account_id=new['account_id'],group_id=o['group_id'],slug=o['us_market_slug'],holding_side='LONG',
        settlement_event_key='rollback-final',outcome='LOST',evidence={'fixture':True},evidence_source='SYNTHETIC_REVIEW',at=now-800)
    before=await CA.stopping_rules_now(conn,new['account_id'],L.DEFAULT_STRATEGY,now=now)
    assert any(x['rule_id']=='LOSS_BUDGET_QUARANTINE' for x in before['firing'])
    await E.rollback(conn,epoch_id='rollback-loss',request_id='rollback-loss-request')
    after=await CA.stopping_rules_now(conn,L.ACCOUNT_ID,L.DEFAULT_STRATEGY,now=now)
    assert after['firing']==before['firing'] and after['rolling']==before['rolling']
    later=await activate(conn,'after-rollback')
    assert (await CA.stopping_rules_now(conn,later['account_id'],L.DEFAULT_STRATEGY,now=now))['rolling']==before['rolling']
    assert (await L.balances(conn,new['account_id']))['realized_pnl_usd']==-20000
    assert (await L.cash_state(conn,later['account_id']))['cash']==500000


async def test_rollback_refuses_to_release_a_new_epoch_quarantine(conn):
    from sportsassets import bettor_strategy_lifecycle as LC
    new=await activate(conn,'rollback-quarantine')
    result=await LC.transition(conn,account_id=new['account_id'],strategy=L.DEFAULT_STRATEGY,
        to_state=LC.QUARANTINED,actor='person:Independent Synthetic Reviewer',why='synthetic rollback restriction',now=time.time())
    assert result['ok'],result
    with pytest.raises(E.EpochRefused,match='ROLLBACK_WOULD_RELEASE_STRATEGY_RESTRICTION'):
        await E.rollback(conn,epoch_id='rollback-quarantine',request_id='rollback-quarantine-request')
    assert await E.selected_account(conn)==new['account_id']
    assert (await LC.current_state(conn,new['account_id'],L.DEFAULT_STRATEGY))['state']==LC.QUARANTINED
    assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM paper_epoch_events WHERE kind='ROLLBACK')")


async def test_models_inherit_provenance_and_empty_refit_does_not_replace_them(conn):
    mid = await B.record_model(conn, account_id=L.ACCOUNT_ID, kind='RESIDUAL',
        payload={'observations':20, 'sentinel_haircut':.35}, at=time.time())
    new = await activate(conn, 'models')
    inherited = await B.latest_models(conn, new['account_id'])
    assert inherited['RESIDUAL']['model_id'] == mid
    assert inherited['RESIDUAL']['sentinel_haircut'] == .35
    assert inherited['RESIDUAL']['source_account_id'] == L.ACCOUNT_ID
    await B.fit_all(conn, account_id=new['account_id'], now=time.time())
    assert (await B.latest_models(conn, new['account_id']))['RESIDUAL']['model_id'] == mid
    assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_profitability_models WHERE account_id=$1 AND observations=0)', new['account_id'])


@pytest.mark.parametrize('anchor', ['verifier','root'])
def test_operator_cannot_replace_pinned_activation_trust_anchor(tmp_path,monkeypatch,anchor):
    fake = tmp_path/'forged-anchor'
    fake.write_text('#!/bin/sh\nexit 0\n')
    if anchor == 'verifier':
        monkeypatch.setattr(A,'GH_PATH',str(fake))
    else:
        monkeypatch.setattr(A,'GH_PATH','/workspace/onboarding/tools/gh')
        monkeypatch.setattr(A,'ROOT_PATH',fake)
    with pytest.raises(ValueError,match='TRUST_ANCHOR_NOT_PINNED'):
        A._trust_anchors()


@pytest.mark.parametrize('mutation', ['healthy','wrong_commit','no_live','two_live','rolling','malformed','unreadable'])
async def test_live_deployment_identity_checks_all_three_services(monkeypatch,mutation):
    import httpx
    monkeypatch.setenv('RENDER_API_KEY','synthetic-read-only-fixture')
    monkeypatch.setenv('RENDER_GIT_COMMIT',SHA)
    urls=[]
    class Client:
        def __init__(self,**kw):
            assert kw['follow_redirects'] is False and kw['trust_env'] is False
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def get(self,url,**kw):
            urls.append(url)
            live={'id':'synthetic-deploy','status':'live','commit':{'id':SHA},'finishedAt':'2026-10-09T17:00:00Z'}
            rows=[{'deploy':live}]
            if mutation=='wrong_commit': live['commit']['id']='f'*40
            elif mutation=='no_live': live['status']='deactivated'
            elif mutation=='two_live': rows.append({'deploy':dict(live)})
            elif mutation=='rolling': rows.append({'deploy':{'status':'update_in_progress'}})
            elif mutation=='malformed': rows={}
            elif mutation=='unreadable': raise httpx.ConnectError('fixture unavailable')
            return httpx.Response(200,json=rows,request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    if mutation=='healthy':
        result=await A.verify_deployed_release(SHA)
        assert set(result)==set(A.SERVICES)
        assert len(urls)==3 and all(u.startswith('https://api.render.com/v1/services/') for u in urls)
    else:
        with pytest.raises(ValueError,match='DEPLOY'):
            await A.verify_deployed_release(SHA)


async def test_deployment_recheck_under_activation_lock_rolls_back_without_funding(conn):
    async def changed(_sha):
        assert await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory')")
        assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_account_epochs)')
        raise ValueError('DEPLOYED_RELEASE_CHANGED_WHILE_WAITING')
    from sportsassets import bettor_paper_day_one as E

    with pytest.raises(ValueError,match='DEPLOYED_RELEASE_CHANGED'):
        await E._activate_verified(conn,epoch_id='changed-release',request_id='changed-release',
            proof={'release_sha':SHA,'packet_digest':'b'*64},deployment_check=changed)
    assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM paper_account_epochs)')
    assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM paper_accounts WHERE account_id LIKE 'paper_day_one_%')")


async def test_production_xavier_applies_archived_settlement_revision(conn):
    old = await account(conn, L.ACCOUNT_ID)
    o = await buy(conn, old, at=H.T0, fill=True)
    await L.settle(conn, account_id=L.ACCOUNT_ID, group_id=o['group_id'],
        slug=o['us_market_slug'], holding_side='LONG', settlement_event_key='venue-final:'+o['us_market_slug'],
        outcome='LOST', evidence={'fixture':True}, evidence_source='TEST_FIXTURE', at=H.T0+100)
    new = await activate(conn, 'correction')
    from sportsassets.agents import paper_xavier as X
    vid = await _valuation(conn,cid=o['us_market_slug'],price=.5,decided=H.T0)
    await conn.execute("UPDATE external_valuations SET outcome=1,outcome_known=true,outcome_basis=$2,outcome_at=now() WHERE id=$1",
        vid, next(iter(X.LABEL_BASES)))
    ctx = await account(conn, new['account_id'])
    ctx['now'] = time.time()
    result = await X.step_settle(conn, ctx)
    assert result['corrected'] == 1
    assert (await L.balances(conn, L.ACCOUNT_ID))['cash_usd'] == 500050
    assert (await L.cash_state(conn,new['account_id']))['cash'] == 500000


async def test_audrey_default_recomputes_selected_account(conn):
    new = await activate(conn, 'audrey')
    ctx = await account(conn, new['account_id'])
    await buy(conn, ctx, at=time.time()+1, key='new-risk', fill=True)
    from sportsassets.agents import audrey_intel_risk as R
    default = await R.recompute(conn, book='PAPER', now=time.time()+10)
    selected = await R.recompute(conn, book='PAPER', now=time.time()+10,
                                 account_id=new['account_id'])
    assert default == selected
    assert default != await R.recompute(conn, book='PAPER', now=time.time()+10,
                                         account_id=L.ACCOUNT_ID)


@pytest.mark.parametrize('runner',['intel','twin'])
async def test_default_shadow_cycle_reads_selected_epoch(conn,monkeypatch,runner):
    import importlib
    new = await activate(conn, 'runner-'+runner)
    ctx = await account(conn, new['account_id'])
    await buy(conn,ctx,at=time.time()+1,key='runner-risk',fill=True)
    run = importlib.import_module('sportsassets.'+runner+'.runner')
    seen=[]
    if runner=='intel':
        target=run.RK
        name='paper_report'
    else:
        target=run
        name='build_streams'
    original=getattr(target,name)
    async def observed(c,**kw):
        seen.append(kw['account_id'])
        return await original(c,**kw)
    monkeypatch.setattr(target,name,observed)
    before=await H.funded_table_counts(conn)
    result=await run.run_cycle(conn,now=time.time()+10,include_actual=False)
    assert result['ran'] and seen==[new['account_id']]
    assert before==await H.funded_table_counts(conn)
    if runner=='intel':
        row=await conn.fetchrow("SELECT primary_value,audrey_value,agrees FROM intel_audrey_risk_checks WHERE run_id=$1 AND book='PAPER' AND metric='open_positions'",result['run_id'])
        assert row and row['primary_value']==row['audrey_value']==1 and row['agrees'] is True


def test_reviewed_judge_identity_is_mandatory():
    data = green_files()
    data['evidence_packet.json']['identity'] = {'judge_sha':'f'*40}
    with pytest.raises(ValueError, match='JUDGE'):
        A.validate_contents({n:json.dumps(v).encode() for n,v in data.items()},
                            release_sha=SHA, now=1000)


async def test_plain_psql_cannot_destroy_activated_epoch_evidence(epoch_database):
    parts = urlsplit(epoch_database)
    scratch = 'epoch_down_review_' + uuid.uuid4().hex
    admin = await asyncpg.connect(urlunsplit(parts._replace(path='/postgres')))
    await admin.execute(f'CREATE DATABASE "{scratch}" TEMPLATE "{parts.path[1:]}"')
    dsn = urlunsplit(parts._replace(path='/'+scratch))
    c = await asyncpg.connect(dsn)
    try:
        from sportsassets import bettor_paper_session as S
        await S.ensure_session(c, account_id=L.ACCOUNT_ID, now=H.T0)
        await activate(c, 'down-refusal')
        script = pathlib.Path(__file__).resolve().parents[1]/'migrations/rollback/317_paper_day_one_epoch.down.sql'
        result = subprocess.run(['psql', dsn, '-f', str(script)], capture_output=True, text=True, timeout=30)
        assert 'Refusing rollback 317' in result.stderr
        assert await c.fetchval("SELECT to_regclass('paper_account_epochs') IS NOT NULL")
        assert await c.fetchval('SELECT count(*) FROM paper_account_epochs') == 1
        assert await c.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname='paper_epoch_order_owner_trg'") == 1
    finally:
        await c.close()
        await admin.execute(f'DROP DATABASE "{scratch}"')
        await admin.close()


async def test_rollback_ddl_serializes_with_concurrent_activation(epoch_database):
    """A natural DDL lock wait must not invalidate the evidence precheck."""
    import asyncio
    parts = urlsplit(epoch_database)
    scratch = 'epoch_down_race_' + uuid.uuid4().hex
    admin = await asyncpg.connect(urlunsplit(parts._replace(path='/postgres')))
    await admin.execute(f'CREATE DATABASE "{scratch}" TEMPLATE "{parts.path[1:]}"')
    dsn = urlunsplit(parts._replace(path='/'+scratch))
    owner, blocker, ddl = [await asyncpg.connect(dsn) for _ in range(3)]
    hold = blocker.transaction()
    activation = owner.transaction()
    task = None
    try:
        from sportsassets import bettor_paper_session as S
        await S.ensure_session(owner, account_id=L.ACCOUNT_ID, now=H.T0)
        await hold.start()
        await blocker.execute('LOCK TABLE paper_orders IN ROW EXCLUSIVE MODE')
        await activation.start()
        await owner.execute('SELECT pg_advisory_xact_lock($1)', E.LOCK)
        await owner.execute('LOCK TABLE paper_orders IN ACCESS SHARE MODE')
        script = pathlib.Path(__file__).resolve().parents[1]/'migrations/rollback/317_paper_day_one_epoch.down.sql'
        task = asyncio.create_task(ddl.execute(script.read_text()))
        for _ in range(200):
            if await owner.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", ddl.get_server_pid()):
                break
            await asyncio.sleep(.01)
        else:
            pytest.fail('DDL did not reach the controlled lock wait')
        created = await activate(owner, 'down-activation-race')
        await activation.commit()
        await hold.commit()
        outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        assert isinstance(outcome, asyncpg.RaiseError), 'DDL erased evidence committed after its precheck'
        assert 'Refusing rollback 317' in str(outcome)
        assert await owner.fetchval("SELECT to_regclass('paper_account_epochs') IS NOT NULL")
        assert await owner.fetchval('SELECT count(*) FROM paper_account_epochs') == 1
        assert await owner.fetchval('SELECT account_id FROM paper_epoch_control WHERE singleton') == created['account_id']
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for c in (owner, blocker, ddl):
            await c.close()
        await admin.execute(f'DROP DATABASE "{scratch}"')
        await admin.close()
