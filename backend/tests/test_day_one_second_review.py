"""Real-database regressions for the independent second PR5 review."""
import importlib
import pathlib
import time
import pytest
from tests.test_day_one_paper_epoch import conn, epoch_database, activate, account, buy
from tests import paper_harness as H
from sportsassets import bettor_paper_day_one as E, bettor_paper_ledger as L, bettor_capital_authority as CA

# Capture the production reader before the legacy-suite autouse fixture seeds
# admission evidence for synthetic order creation. Risk assertions use no stub.
PRODUCTION_FORWARD_ECONOMICS = CA.forward_economics

@pytest.mark.parametrize('second_epoch', [False, True])
async def test_rolled_back_epoch_settlement_is_revised_by_production_pass(conn, second_epoch):
    child = await activate(conn, 'first')
    at = time.time()+1
    order = await buy(conn, await account(conn, child['account_id']), at=at, fill=True)
    await L.settle(conn, account_id=child['account_id'], group_id=order['group_id'],
        slug=order['us_market_slug'], holding_side='LONG', settlement_event_key='venue-final:'+order['us_market_slug'],
        outcome='LOST', evidence={'fixture': True}, evidence_source='TEST_FIXTURE', at=at+100)
    before = [dict(r) for r in await conn.fetch('SELECT * FROM paper_ledger WHERE account_id=$1 ORDER BY seq', child['account_id'])]
    await E.rollback(conn, epoch_id='first', request_id='rollback')
    selected = await activate(conn, 'second') if second_epoch else {'account_id': L.ACCOUNT_ID}
    from sportsassets.agents import paper_xavier as X
    from tests.test_rc6_provenance_refuses_after_change import _valuation
    vid = await _valuation(conn, cid=order['us_market_slug'], price=.5, decided=H.T0)
    await conn.execute('UPDATE external_valuations SET outcome=1,outcome_known=true,outcome_basis=$2,outcome_at=now() WHERE id=$1', vid, next(iter(X.LABEL_BASES)))
    ctx = await account(conn, selected['account_id'])
    result = await X.step_settle(conn, dict(ctx, now=time.time()))
    assert result['corrected'] == 1
    assert (await L.balances(conn, child['account_id']))['cash_usd'] == 500050
    assert (await L.balances(conn, selected['account_id']))['cash_usd'] == 500000
    after = [dict(r) for r in await conn.fetch('SELECT * FROM paper_ledger WHERE account_id=$1 ORDER BY seq', child['account_id'])]
    assert all(row in after for row in before), 'Historical loss entries must remain immutable'

@pytest.mark.parametrize('module,cache', [('agent_work','_LAST_STEP'), ('improvement_clusters','_LAST_STEP'), ('lesson_usage','_LAST')])
async def test_audrey_paper_pass_runs_for_selected_epoch_only(conn, monkeypatch, module, cache):
    new = await activate(conn, 'audit-'+module)
    mod = importlib.import_module('sportsassets.agents.'+module)
    monkeypatch.setattr(mod, cache, {})
    ctx = dict(await account(conn, new['account_id']), now=time.time())
    result = await mod.step(conn, ctx)
    assert result['ran'], result
    monkeypatch.setattr(mod, cache, {})
    refused = await mod.step(conn, dict(await account(conn, L.ACCOUNT_ID), now=time.time()))
    assert not refused['ran'], 'Archived accounts must not run selected-account audit work'

async def test_audrey_slack_snapshot_reads_selected_account_without_sending(conn, monkeypatch):
    old = await account(conn, L.ACCOUNT_ID)
    order = await buy(conn, old, at=H.T0, fill=True)
    await L.settle(conn, account_id=L.ACCOUNT_ID, group_id=order['group_id'],
        slug=order['us_market_slug'], holding_side='LONG', settlement_event_key='slack-old-loss',
        outcome='LOST', evidence={'fixture': True}, evidence_source='TEST_FIXTURE', at=H.T0+100)
    assert (await L.balances(conn, L.ACCOUNT_ID))['cash_usd'] == 499950
    new = await activate(conn, 'slack')
    from sportsassets import slack_updates as SU, bettor_paper_ops as OPS
    original = OPS.audrey_operations
    seen = []
    async def observed(c, **kw):
        seen.append(kw['account_id'])
        return await original(c, **kw)
    monkeypatch.setattr(OPS, 'audrey_operations', observed)
    snapshot = await SU.snapshot(conn, time.time())
    assert snapshot['account']['cash_usd'] == 500000
    assert (await L.balances(conn, L.ACCOUNT_ID))['cash_usd'] == 499950
    assert seen == [new['account_id']]

@pytest.mark.parametrize('isolation', ['repeatable_read', 'serializable'])
async def test_rollback_ddl_refuses_snapshot_isolation(epoch_database, isolation):
    import asyncpg
    ddl = pathlib.Path(__file__).parents[1] / 'migrations/rollback/317_paper_day_one_epoch.down.sql'
    c = await asyncpg.connect(epoch_database)
    tr = c.transaction(isolation=isolation)
    await tr.start()
    try:
        await c.fetchval('SELECT count(*) FROM paper_account_epochs')
        with pytest.raises(asyncpg.RaiseError, match='isolation'):
            await c.execute(ddl.read_text())
    finally:
        await tr.rollback()
        assert await c.fetchval("SELECT to_regclass('paper_account_epochs') IS NOT NULL")
        await c.close()

async def test_pending_archived_shadow_losses_settle_in_current_pass(conn, monkeypatch):
    from sportsassets import bettor_capital_authority as CA
    from sportsassets.agents import xavier_management as XM
    from tests.test_capital_authority import _seed_shadows, _settled, NOW, DEREK
    from unittest.mock import AsyncMock
    old = await account(conn, L.ACCOUNT_ID)
    with monkeypatch.context() as seed:
        seed.setattr(CA, 'settle_shadows', AsyncMock(return_value={}))
        await _seed_shadows(conn, old, DEREK, 25, outcome='LOST', payout=0)
    new = await activate(conn, 'pending-shadows')
    monkeypatch.setattr(CA, '_LAST_RUN', {})
    monkeypatch.setattr(XM, 'settlement_outcome', _settled('LOST', 0))
    before = await conn.fetchval('SELECT count(*) FROM paper_ledger')
    got = await CA.step(conn, dict(await account(conn,new['account_id']), now=NOW))
    assert got['settled'] == 25, got
    assert await conn.fetchval('SELECT count(*) FROM paper_shadow_counterfactual_outcomes') == 25
    assert await conn.fetchval('SELECT count(*) FROM paper_ledger') == before
    forward = await PRODUCTION_FORWARD_ECONOMICS(conn, new['account_id'], DEREK, now=NOW)
    assert forward['ok'] and forward['verdict'] == 'NEGATIVE', forward

def test_account_context_imported_by_shadow_readers_is_select_only():
    import ast
    from sportsassets import simulated_account_context as C
    source = pathlib.Path(C.__file__).read_text()
    tree = ast.parse(source)
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(tree))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    for node in calls:
        if isinstance(node.func.value, ast.Name) and node.func.value.id == 'conn':
            assert node.func.attr in {'fetch', 'fetchval'}
            assert isinstance(node.args[0], ast.Constant)
            assert node.args[0].value.lstrip().upper().startswith('SELECT ')
    assert calls
