"""RC6.3 PR #5 PORT -- CHURN AND TURNOVER CONTROLS SURVIVE AN ACCOUNT SWITCH.

bettor_paper_profitability_bind.churn_check (the production check entry_bind
runs for the order's own account) read its six histories -- the reprice
deadband, the hourly ENTRY count, the prior and entered evaluations, the
contract-exit and fixture-exit fills -- by account_id only. Activation moves
order flow to a new account_id, so the re-entry cooldown (3600 s), the
fixture cooldown (1800 s), the recent-refusal cooldown, the reprice deadband
and the 12-entries-per-hour turnover cap silently restarted at zero on Day
One; after a rollback the restored account forgot the epoch's exits
(independent review, risk and integration lenses: R4, C1a, C1b, C1c).

churn_check now reads every one of them across simulated_account_context.
risk_history_accounts(account) -- the account, its registered ancestors and
every epoch of the same root, rolled-back children included -- each row at
its original timestamp. An unrelated legacy account has no lineage and
stays isolated. ALL DATA SYNTHETIC; one rolled-back transaction per test on
a fresh migrated database; no epoch is activated outside it.
"""
from __future__ import annotations

import hashlib
import time
import uuid

import pytest

from tests.test_day_one_paper_epoch import conn, epoch_database  # noqa: F401
from tests import paper_harness as H
from sportsassets import bettor_capital_authority as CA
from sportsassets import bettor_paper_day_one as E
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_profitability_bind as PBIND
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_paper_simulator as SIM

#: forward economics and the stopping rules stay production (conftest)
CAPITAL_AUTHORITY_ENFORCED = True
STRAT = L.DEFAULT_STRATEGY
EXITS = (PBIND.R_CHURN_RECENT_EXIT, PBIND.R_CHURN_FIXTURE_EXIT)


@pytest.fixture(autouse=True)
def _ev_evidence_lifted(monkeypatch):
    # bare synthetic setup orders carry no executable-EV evidence; the churn
    # check itself is called directly and is never stubbed
    monkeypatch.setattr(CA, "missing_ev_evidence_refusal", lambda o: None)


def _proof(tag):
    return {'release_sha': 'a' * 40,
            'packet_digest': hashlib.sha256(tag.encode()).hexdigest(),
            'verified_at': 1}


async def _acct(c, aid):
    s = await S.active_session(c, aid)
    return {'account_id': aid, 'session_id': s['session_id'],
            'config': s['config']}


async def _seed_positive_shadows(c, account_id, strategy, *, n=25, at):
    """Forward-economics evidence for the synthetic setup entries."""
    for i in range(n):
        sid = await c.fetchval(
            "INSERT INTO paper_shadow_counterfactuals (shadow_key, account_id,"
            " strategy, source, capital_refusal, us_market_slug, holding_side,"
            " decided_at, eligible_at, expires_at,"
            " decision_to_execution_delay_s, p, limit_price, qty, levels,"
            " fills, cost_usd, fees_usd, adverse_selection_usd,"
            " total_executable_ev_usd, evidence) VALUES ($1,$2,$3,"
            " 'DECISION_CAPITAL_GATE','CASH_WAIT_FORWARD_ECONOMICS_UNKNOWN',"
            " $4,'LONG',to_timestamp($5),to_timestamp($5+2),"
            " to_timestamp($5+90),2,0.6,0.5,10,'[]','[]',5,0,0,1,'{}') "
            " RETURNING shadow_id",
            'rc63-ck-%s-%s-%d-%s' % (account_id, strategy, i,
                                     uuid.uuid4().hex[:6]),
            account_id, strategy, 'rc63-ck-shadow-%d' % i, at + i)
        await c.execute(
            "INSERT INTO paper_shadow_counterfactual_outcomes (shadow_id,"
            " outcome, payout_per_contract, execution_basis, filled_qty,"
            " exec_cost_usd, exec_fees_usd, counterfactual_pnl_usd, evidence,"
            " settled_at) VALUES ($1,'WON',1,'PROBE',10,5,0,$2,'{}',"
            " to_timestamp($3))", sid, 4.0, at + 1000 + i)


async def _buy_fill(c, a, *, at, key, slug, qty, fixture, px=.5):
    o = H.order(a, key=key, qty=qty, limit=px, at=at, slug=slug,
                fixture=fixture)
    r = await L.submit_order(c, o, now=at, fee_fn=H.zero_fee)
    assert r['ok'], r
    await H.observe(c, slug, at + 3, offers=[(px, qty)],
                    bids=[(px - .01, qty)])
    got = await SIM.simulate_order(c, r['order']['order_id'], now=at + 4,
                                   fee_fn=H.zero_fee)
    assert got['fills'], got
    return r['order']


async def _exit_fill(c, a, o, *, at, slug, qty, fixture, px=.55):
    ex = H.order(a, key=o['idempotency_key'].split(':', 1)[1] + '-exit',
                 qty=qty, limit=px, at=at, slug=slug, direction='SELL',
                 role='EXIT', group_id=o['group_id'], fixture=fixture)
    rr = await L.submit_order(c, ex, now=at, fee_fn=H.zero_fee)
    assert rr['ok'], rr
    await H.observe(c, slug, at + 3, offers=[(px + .02, qty)],
                    bids=[(px, qty)])
    await SIM.simulate_order(c, rr['order']['order_id'], now=at + 4,
                             fee_fn=H.zero_fee)


async def _churn(c, account_id, *, slug, fixture, at):
    return await PBIND.churn_check(c, account_id=account_id, strategy=STRAT,
                                   slug=slug, side='LONG', fixture=fixture,
                                   at=at, ev_per_contract=0.05)


async def test_R4_reentry_cooldown_survives_activation(conn):
    """The reviewer's original R4 assertion."""
    main = await _acct(conn, L.ACCOUNT_ID)
    now = time.time()
    slug, fx = 'rc63-r4-' + uuid.uuid4().hex[:6], 'rc63-r4-fx'
    await _seed_positive_shadows(conn, L.ACCOUNT_ID, STRAT, at=now - 5000)
    o = await _buy_fill(conn, main, at=now - 900, key='r4', slug=slug,
                        qty=100, fixture=fx)
    await _exit_fill(conn, main, o, at=now - 600, slug=slug, qty=100,
                     fixture=fx)
    assert not [p for p in await L.positions(conn, L.ACCOUNT_ID)
                if float(p['open_qty']) > 0]
    before = await _churn(conn, L.ACCOUNT_ID, slug=slug, fixture=fx, at=now)
    r = await E._activate_verified(conn, epoch_id='rc63-r4',
                                   request_id='rc63-r4', proof=_proof('r4'))
    after = await _churn(conn, r['account_id'], slug=slug, fixture=fx,
                         at=r['opened_at'] + 1)
    assert before and before['refusal'] in EXITS
    assert after and after.get('refusal') in EXITS, \
        'churn cooldown cleared by activation'
    # the original exit instant is kept, not the activation's
    assert abs(after['exited_at'] - before['exited_at']) < 1e-3


async def test_C1a_reentry_and_fixture_cooldowns_survive_activation(conn):
    main = await _acct(conn, L.ACCOUNT_ID)
    now = time.time()
    await _seed_positive_shadows(conn, L.ACCOUNT_ID, STRAT, at=now - 9000)
    slug, fx = 'rc63-c1a-' + uuid.uuid4().hex[:6], 'rc63-c1a-fx'
    o = await _buy_fill(conn, main, at=now - 900, key='c1a', slug=slug,
                        qty=100, fixture=fx)
    await _exit_fill(conn, main, o, at=now - 600, slug=slug, qty=100,
                     fixture=fx)
    r = await E._activate_verified(conn, epoch_id='rc63-c1a',
                                   request_id='rc63-c1a', proof=_proof('c1a'))
    t = r['opened_at'] + 1
    same = await _churn(conn, r['account_id'], slug=slug, fixture=fx, at=t)
    assert same and same['refusal'] == PBIND.R_CHURN_RECENT_EXIT
    # another contract on the exited fixture: the fixture cooldown holds
    other = await _churn(conn, r['account_id'],
                         slug='rc63-c1a-other-' + uuid.uuid4().hex[:4],
                         fixture=fx, at=t)
    assert other and other['refusal'] == PBIND.R_CHURN_FIXTURE_EXIT
    # and the windows still end at their original instants
    late = await _churn(conn, r['account_id'], slug=slug, fixture=fx,
                        at=now - 600 + 4 + PBIND.REENTRY_COOLDOWN_S + 5)
    assert late is None


async def test_C1b_turnover_cap_survives_activation(conn):
    main = await _acct(conn, L.ACCOUNT_ID)
    now = time.time()
    await _seed_positive_shadows(conn, L.ACCOUNT_ID, STRAT, at=now - 9000)
    for i in range(PBIND.MAX_ENTRIES_PER_HOUR):
        oo = H.order(main, key='turn-%d' % i, qty=10, limit=.5,
                     at=now - 500 + i,
                     slug='rc63-turn-%d-%s' % (i, uuid.uuid4().hex[:4]),
                     fixture='rc63-turn-fx-%d' % i)
        rr = await L.submit_order(conn, oo, now=now - 500 + i,
                                  fee_fn=H.zero_fee)
        assert rr['ok'], rr
        await L.release_remainder(conn, order_id=rr['order']['order_id'],
                                  reason='PROBE_CANCEL', at=now - 499 + i,
                                  state='CANCELED')
    other = 'rc63-other-' + uuid.uuid4().hex[:6]
    before = await _churn(conn, L.ACCOUNT_ID, slug=other,
                          fixture='rc63-other-fx', at=now)
    r = await E._activate_verified(conn, epoch_id='rc63-c1b',
                                   request_id='rc63-c1b', proof=_proof('c1b'))
    after = await _churn(conn, r['account_id'], slug=other,
                         fixture='rc63-other-fx', at=r['opened_at'] + 1)
    assert before and before['refusal'] == PBIND.R_TURNOVER_CAP
    assert after and after.get('refusal') == PBIND.R_TURNOVER_CAP, \
        'activation reset the turnover cap'
    assert after['entries_last_hour'] == PBIND.MAX_ENTRIES_PER_HOUR


async def test_C1c_epoch_exit_cooldown_survives_rollback(conn):
    r = await E._activate_verified(conn, epoch_id='rc63-c1c',
                                   request_id='rc63-c1c', proof=_proof('c1c'))
    new, t = r['account_id'], r['opened_at'] + 1
    d1 = await _acct(conn, new)
    await _seed_positive_shadows(conn, new, STRAT, at=t)
    slug, fx = 'rc63-rb-' + uuid.uuid4().hex[:6], 'rc63-rb-fx'
    o = await _buy_fill(conn, d1, at=t + 2000, key='rb', slug=slug, qty=100,
                        fixture=fx)
    await _exit_fill(conn, d1, o, at=t + 2100, slug=slug, qty=100,
                     fixture=fx)
    in_epoch = await _churn(conn, new, slug=slug, fixture=fx, at=t + 2200)
    await E.rollback(conn, epoch_id='rc63-c1c', request_id='rc63-c1c-undo')
    after = await _churn(conn, L.ACCOUNT_ID, slug=slug, fixture=fx,
                         at=t + 2200)
    assert in_epoch and in_epoch['refusal'] in EXITS
    assert after and after.get('refusal') in EXITS, \
        'rollback cleared the epoch exit cooldown'


async def test_an_unrelated_account_stays_isolated(conn):
    """Lineage, not pooling: a legacy account outside the epoch family does
    not inherit the main account's churn history."""
    main = await _acct(conn, L.ACCOUNT_ID)
    now = time.time()
    await _seed_positive_shadows(conn, L.ACCOUNT_ID, STRAT, at=now - 9000)
    slug, fx = 'rc63-iso-' + uuid.uuid4().hex[:6], 'rc63-iso-fx'
    o = await _buy_fill(conn, main, at=now - 900, key='iso', slug=slug,
                        qty=100, fixture=fx)
    await _exit_fill(conn, main, o, at=now - 600, slug=slug, qty=100,
                     fixture=fx)
    await E._activate_verified(conn, epoch_id='rc63-iso',
                               request_id='rc63-iso', proof=_proof('iso'))
    stranger = 'paper_unrelated_' + uuid.uuid4().hex[:8]
    assert await _churn(conn, stranger, slug=slug, fixture=fx,
                        at=now) is None
