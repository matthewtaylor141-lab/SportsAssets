"""RC6.3 PR #5 PORT -- CODE THAT RUNS FOR THE SELECTED ACCOUNT RESOLVES IT.

Before this fix, after an activation (independent review, integration and
accounting lenses, A6 and the profitability runner probe):
- agents/capability_work hard-coded ACCOUNT='paper_acct_main' for task specs,
  flow IDs, the SOURCE_CONTEXT_NOT_IN_ACCOUNT check, claims, the control row
  and (capability_runtime) the heartbeat: a research flow about a Day One
  position was refused, admission never saw a Day One settlement, and the
  Slack update's research section (already selected-account scoped) read an
  empty population while workers kept filling the archive's queue;
  slack_bridge.publish_reviews and loop_health's heartbeat source were
  pinned to paper_acct_main too;
- profitability.runner.run_cycle defaulted to C.PAPER_ACCOUNT and the
  scheduled loop calls it with no account, so capacity, positions,
  economics and PAPER capital were the archive's;
- revenue_improvements.propose_due read revenue reliability for
  L.ACCOUNT_ID;
- live_game_state's PostgresStore.fixtures defaulted to paper_acct_main.

Each now resolves the durable PAPER selector (simulated_account_context.
selected_account) unless the caller supplies an account. ALL DATA SYNTHETIC;
one rolled-back transaction per test on a fresh migrated database; no epoch
is activated outside it.
"""
from __future__ import annotations

import contextlib
import json
import time
import uuid

import pytest

from tests.test_day_one_paper_epoch import conn, epoch_database  # noqa: F401
from tests.test_day_one_paper_epoch import PROOF
from tests import paper_harness as H
from sportsassets import bettor_paper_day_one as E
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import capability_work as W
from sportsassets.agents import capability_runtime as CR


async def _acct(c, aid):
    s = await S.active_session(c, aid)
    return {'account_id': aid, 'session_id': s['session_id'],
            'config': s['config'], 'session': s}


async def _buy(c, a, *, at, key, slug, qty=100, px=.5):
    o = H.order(a, key=key, qty=qty, limit=px, at=at, slug=slug)
    r = await L.submit_order(c, o, now=at, fee_fn=H.zero_fee)
    assert r['ok'], r
    await H.observe(c, slug, at + 3, offers=[(px, qty)],
                    bids=[(px - .01, qty)])
    got = await SIM.simulate_order(c, r['order']['order_id'], now=at + 4,
                                   fee_fn=H.zero_fee)
    assert got['fills'], got
    return r['order']


async def _settle(c, a, o, *, at, outcome='LOST'):
    await L.settle(c, account_id=a['account_id'], group_id=o['group_id'],
                   slug=o['us_market_slug'], holding_side='LONG',
                   settlement_event_key='venue-final:' + o['us_market_slug'],
                   outcome=outcome, evidence={'s': 1},
                   evidence_source='TEST_FIXTURE', at=at,
                   session_id=a['session_id'])


async def _activate(c, epoch='rc63-sel'):
    r = await E._activate_verified(c, epoch_id=epoch, request_id=epoch,
                                   proof=PROOF)
    assert await E.selected_account(c) == r['account_id'] != L.ACCOUNT_ID
    return r


@pytest.fixture(autouse=True)
def _queue_follows_the_selector(monkeypatch):
    # the production default: no pinned queue account or control row
    monkeypatch.setattr(W, 'ACCOUNT', None)
    monkeypatch.setattr(W, 'CONTROL', None)


async def test_A6_research_queue_follows_the_selected_account(conn):
    from sportsassets import slack_updates as SU
    main = await _acct(conn, L.ACCOUNT_ID)
    now = time.time()
    await W.configure(conn, enabled=True, hourly_limit=10,
                      actor='owner:probe', now=now)
    o_main = await _buy(conn, main, at=H.T0, key='a6-main',
                        slug='rc63-a6-m-' + uuid.uuid4().hex[:6])
    pre = await W.create_flow(conn, source_key='rc63-a6-pre',
                              title='research before cutover', due=now + 86400,
                              actor='owner:probe', now=now,
                              context={'position_id': o_main['group_id']})
    assert pre['created']
    await _settle(conn, main, o_main, at=time.time())
    r = await _activate(conn, 'rc63-a6')
    new = await _acct(conn, r['account_id'])
    o_new = await _buy(conn, new, at=r['opened_at'] + 5, key='a6-new',
                       slug='rc63-a6-n-' + uuid.uuid4().hex[:6])
    post = await W.create_flow(conn, source_key='rc63-a6-post',
                               title='research on day one', due=now + 86400,
                               actor='owner:probe', now=now + 10,
                               context={'position_id': o_new['group_id']})
    assert post['created']
    specs = [W.obj(x['spec']) for x in await conn.fetch(
        "SELECT spec FROM agent_tasks WHERE task_id = ANY($1::text[])",
        post['task_ids'])]
    assert {s['account_id'] for s in specs} == {r['account_id']}
    # the archive's own flow cannot be re-scoped onto Day One, and a main
    # position is not a Day One source context
    with pytest.raises(ValueError, match='SOURCE_CONTEXT_NOT_IN_ACCOUNT'):
        await W.create_flow(conn, source_key='rc63-a6-wrong',
                            title='archive context on day one',
                            due=now + 86400, actor='owner:probe', now=now + 11,
                            context={'position_id': o_main['group_id']})
    # the Slack update's research section reads the population the queue
    # now fills
    research = [dict(x) for x in await conn.fetch(
        "SELECT status, count(*) AS n FROM agent_tasks WHERE kind=$2 "
        "   AND spec->>'account_id'=$1 GROUP BY 1",
        await E.selected_account(conn), SU._kind())]
    assert research and sum(x['n'] for x in research) == 3
    # the queue's own listing is Day One's (and the archive's stays its own)
    assert {t['spec']['account_id'] for t in await W.tasks(conn)} \
        == {r['account_id']}
    assert {t['spec']['account_id'] for t in await W.tasks(
        conn, account=L.ACCOUNT_ID)} == {L.ACCOUNT_ID}


async def test_control_is_inherited_and_claim_and_admission_follow(conn):
    main = await _acct(conn, L.ACCOUNT_ID)
    now = time.time()
    await W.configure(conn, enabled=True, hourly_limit=7,
                      actor='owner:probe', now=now)
    r = await _activate(conn, 'rc63-ctl')
    new = await _acct(conn, r['account_id'])
    c = await W.control(conn)
    # the manager's decision carries across the switch: not silently off
    assert c['account_id'] == r['account_id']
    assert c['enabled'] is True and c['hourly_limit'] == 7
    assert c['inherited_from_account_id'] == L.ACCOUNT_ID
    # a Day One settlement is admitted as research for Day One
    o = await _buy(conn, new, at=r['opened_at'] + 5, key='ctl-new',
                   slug='rc63-ctl-' + uuid.uuid4().hex[:6])
    await _settle(conn, new, o, at=r['opened_at'] + 40)
    created = await CR.admit(conn, time.time() + 60)
    assert created >= 3
    claimed = await W.claim(conn, time.time() + 60)
    assert claimed is not None
    assert claimed['spec']['account_id'] == r['account_id']
    # the archive's control row is untouched by Day One's claim
    main_ctl = await W.control(conn, account=L.ACCOUNT_ID)
    assert main_ctl['spent'] == 0 and main_ctl['account_id'] == L.ACCOUNT_ID
    assert main['account_id'] == L.ACCOUNT_ID


async def test_heartbeat_and_loop_health_follow_the_selector(conn):
    from sportsassets import loop_health as LH
    r = await _activate(conn, 'rc63-hb')
    key = W.heartbeat_key(r['account_id'])
    await conn.execute(
        "INSERT INTO ingestion_state(key,value) VALUES($1,$2::jsonb) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", key,
        json.dumps({'status': 'OK', 'at': time.time(),
                    'account_id': r['account_id']}))
    out = await LH.read(conn, now=time.time())
    loop = [lp for lp in out['loops']
            if lp['name'] == 'agents.capability_runtime'][0]
    text = json.dumps(loop, default=str)
    assert 'ingestion_state:' + key in text
    assert 'agent.capabilities.heartbeat:paper_acct_main' not in text
    assert CR.HEARTBEAT == W.HEARTBEAT_PREFIX + W.SELECTED_ACCOUNT


async def test_slack_reviews_are_the_selected_accounts(conn, monkeypatch):
    from sportsassets import slack_bridge as SB
    monkeypatch.setattr(SB, 'settings', lambda agent: {
        'token': 'xoxb-test', 'team': 'T1', 'workroom': 'C1',
        'channels': {'C1'}, 'secret': None, 'app': None, 'managers': set()})
    now = time.time()
    r = await _activate(conn, 'rc63-slack')
    new = await _acct(conn, r['account_id'])
    o = await _buy(conn, new, at=r['opened_at'] + 5, key='sl-new',
                   slug='rc63-sl-' + uuid.uuid4().hex[:6])
    flow = await W.create_flow(conn, source_key='rc63-slack',
                               title='day one review', due=now + 86400,
                               actor='owner:probe', now=now,
                               context={'position_id': o['group_id']})
    tid = flow['task_ids'][0]
    await conn.execute(
        "UPDATE agent_tasks SET status='CLOSED_NO_CHANGE', "
        " outcome=$2::jsonb WHERE task_id=$1", tid,
        json.dumps({'reviewed': True, 'message_id': 'msg-rc63',
                    'answer': 'Recorded review [F1].'}))
    await conn.execute(
        "INSERT INTO agent_task_events(task_id,at,kind,actor,detail) "
        "VALUES($1, now(), 'GENUINE_REVIEW', 'DEREK', '{}'::jsonb)", tid)
    await SB.publish_reviews(conn)
    rows = await conn.fetch(
        "SELECT agent, message_id FROM agent_slack_delivery "
        " WHERE message_id='msg-rc63'")
    assert [(x['agent'], x['message_id']) for x in rows] == \
        [('derek', 'msg-rc63')]


async def test_profitability_run_cycle_defaults_to_the_selected_account(conn, monkeypatch):
    from sportsassets.profitability import runner as PR
    seen = []

    def spy(name):
        async def fn(*a, **k):
            seen.append((name, k.get('account_id')))
            if name == 'capacity_candidates':
                return []          # the component goes on to its rates
            raise RuntimeError('rc63-spy: ' + name)
        return fn
    for name in ('capacity_candidates', 'capacity_rates', 'paper_positions',
                 'paper_capital'):
        monkeypatch.setattr(PR.R, name, spy(name))
    r = await _activate(conn, 'rc63-prof')
    out = await PR.run_cycle(conn, now=time.time())
    assert out.get('ran', True) is not False, out
    assert seen, 'no PAPER reader was reached'
    assert {acct for _, acct in seen} == {r['account_id']}, seen
    # (CAPITAL runs only after ECONOMICS succeeds; the spy fails it)
    assert {n for n, _ in seen} >= {'capacity_candidates', 'capacity_rates',
                                    'paper_positions'}
    # an explicit account still wins
    seen.clear()
    await PR.run_cycle(conn, now=time.time(), account_id=L.ACCOUNT_ID)
    assert {acct for _, acct in seen} == {L.ACCOUNT_ID}


async def test_revenue_improvements_read_the_selected_account(conn, monkeypatch):
    from sportsassets.agents import revenue_improvements as RI
    from sportsassets.revenue_reliability import read as RR
    seen = []

    async def spy(c, *, account_id, now=None, **k):
        seen.append(account_id)
        return {'status': 'UNAVAILABLE', 'why': 'rc63-spy'}
    monkeypatch.setattr(RR, 'read', spy)
    r = await _activate(conn, 'rc63-rev')
    out = await RI.propose_due(conn, now=time.time(), force=True)
    assert out.get('refusal') in ('READBACK_UNAVAILABLE',
                                  'IMPROVEMENT_SCHEMA_ABSENT'), out
    if out.get('refusal') == 'READBACK_UNAVAILABLE':
        assert seen == [r['account_id']]


async def test_live_game_fixtures_default_to_the_selected_account(conn):
    from sportsassets.live_game_state.storage import PostgresStore
    r = await _activate(conn, 'rc63-lgs')

    class Proxy:
        def __init__(self, c):
            self.c, self.args = c, []

        async def fetchval(self, *a):
            return await self.c.fetchval(*a)

        def transaction(self, **kw):
            @contextlib.asynccontextmanager
            async def tr():
                yield
            return tr()

        async def execute(self, *a):
            return None

        async def fetch(self, sql, *args):
            self.args.append(args)
            return []

    proxy = Proxy(conn)

    class Pool:
        @contextlib.asynccontextmanager
        async def acquire(self):
            yield proxy
    await PostgresStore(Pool()).fixtures()
    assert proxy.args == [(r['account_id'],)]
    await PostgresStore(Pool()).fixtures(account_id=L.ACCOUNT_ID)
    assert proxy.args[-1] == (L.ACCOUNT_ID,)
