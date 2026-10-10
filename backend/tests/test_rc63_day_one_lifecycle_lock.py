"""RC6.3 PR #5 PORT -- ROLLBACK CANNOT BE RACED PAST A CONCURRENT TIGHTENING.

E.rollback checks, under the PAPER advisory lock and the selector row FOR
UPDATE, that the restored account's strategy states are at least as strict
as the epoch's (ROLLBACK_WOULD_RELEASE_STRATEGY_RESTRICTION). Before this
fix bettor_strategy_lifecycle.record / transition took neither lock and
accepted any account, so a named person's QUARANTINE on the selected epoch
could commit after rollback's check: the selector then landed on an account
still ACTIVE and the entry gate admitted entries (independent review, risk
lens C2; verified S1 / S2 / a 30-trial natural race).

LC.record now takes the selector row FOR SHARE (to the end of the caller's
transaction) and refuses a write for a registered account that is not the
selected one (PAPER_EPOCH_ACCOUNT_IS_NOT_SELECTED; transition returns it as
a named refusal). Two real connections on a scratch copy of a freshly
migrated database prove both interleavings:
- S1: the tightening's transaction is open when the rollback starts: the
  rollback waits for it, then refuses by name; the quarantine holds;
- S2: the rollback has read the epoch's state when the tightening starts:
  the tightening waits for the switch, then is refused by name -- nothing
  lands on the archived account.
A tightening of the SELECTED account is never refused. ALL DATA SYNTHETIC;
every scratch database is dropped.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio

from tests.test_day_one_paper_epoch import epoch_database  # noqa: F401
from tests import paper_harness as H
from sportsassets import bettor_paper_day_one as E
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_strategy_lifecycle as LC

STRAT = L.DEFAULT_STRATEGY
PERSON = 'person:rc63-operator-b'


def _proof(tag):
    return {'release_sha': 'a' * 40,
            'packet_digest': hashlib.sha256(tag.encode()).hexdigest(),
            'verified_at': 1}


@pytest_asyncio.fixture
async def scratch(epoch_database):
    """A per-test copy of the module's freshly migrated database (activation
    is committed in it so two connections see it); dropped after."""
    parts = urlsplit(epoch_database)
    admin_dsn = urlunsplit(parts._replace(path='/postgres'))
    name = 'rc63_lc_lock_' + uuid.uuid4().hex[:10]
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute('CREATE DATABASE "%s" TEMPLATE "%s"'
                            % (name, parts.path.lstrip('/')))
    finally:
        await admin.close()
    dsn = urlunsplit(parts._replace(path='/' + name))
    a = await asyncpg.connect(dsn)
    b = await asyncpg.connect(dsn)
    try:
        await S.ensure_session(a, account_id=L.ACCOUNT_ID, now=H.T0)
        r = await E._activate_verified(a, epoch_id='lc-lock',
                                       request_id='lc-lock',
                                       proof=_proof('lc-lock'))
        yield a, b, r['account_id']
    finally:
        await a.close()
        await b.close()
        admin = await asyncpg.connect(admin_dsn)
        try:
            await admin.execute('DROP DATABASE IF EXISTS "%s" WITH (FORCE)'
                                % name)
        finally:
            await admin.close()


async def _gate(c, account_id):
    return await LC.entry_gate(c, account_id=account_id, strategy=STRAT,
                               qty=10, limit=.5, at=time.time())


async def test_S0_control_a_committed_quarantine_refuses_the_rollback(scratch):
    a, b, epoch = scratch
    got = await LC.transition(b, account_id=epoch, strategy=STRAT,
                              to_state=LC.QUARANTINED, actor=PERSON,
                              why='emergency stop', now=time.time())
    assert got['ok'], got
    with pytest.raises(E.EpochRefused,
                       match='ROLLBACK_WOULD_RELEASE_STRATEGY_RESTRICTION'):
        await E.rollback(a, epoch_id='lc-lock', request_id='lc-lock-undo')
    assert await E.selected_account(a) == epoch


async def test_S1_rollback_waits_for_an_open_tightening_then_refuses(scratch):
    a, b, epoch = scratch
    tb = b.transaction()
    await tb.start()
    got = await LC.transition(b, account_id=epoch, strategy=STRAT,
                              to_state=LC.QUARANTINED, actor=PERSON,
                              why='emergency stop', now=time.time())
    assert got['ok'], got
    rb = asyncio.create_task(E.rollback(a, epoch_id='lc-lock',
                                        request_id='lc-lock-undo'))
    await asyncio.sleep(0.5)
    # the rollback is held at the selector row while the tightening is open
    assert not rb.done(), 'rollback ran past an uncommitted tightening'
    await tb.commit()
    with pytest.raises(E.EpochRefused,
                       match='ROLLBACK_WOULD_RELEASE_STRATEGY_RESTRICTION'):
        await asyncio.wait_for(rb, 10)
    selected = await E.selected_account(a)
    assert selected == epoch
    assert (await LC.current_state(a, epoch, STRAT))['state'] \
        == LC.QUARANTINED
    gate = await _gate(a, selected)
    assert gate.get('refusal') == LC.R_LIFECYCLE_QUARANTINED, gate


async def test_S2_a_tightening_that_waited_for_the_switch_is_refused_by_name(scratch, monkeypatch):
    a, b, epoch = scratch
    paused, go = asyncio.Event(), asyncio.Event()
    real = LC.current_state

    async def held(conn, account_id, strategy):
        out = await real(conn, account_id, strategy)
        # pause the ROLLBACK (connection a) right after it read the epoch's
        # state, holding the advisory lock and the selector FOR UPDATE
        if conn is a and account_id == epoch and not paused.is_set():
            paused.set()
            await go.wait()
        return out
    monkeypatch.setattr(LC, 'current_state', held)
    rb = asyncio.create_task(E.rollback(a, epoch_id='lc-lock',
                                        request_id='lc-lock-undo'))
    await asyncio.wait_for(paused.wait(), 10)
    tq = asyncio.create_task(LC.transition(
        b, account_id=epoch, strategy=STRAT, to_state=LC.QUARANTINED,
        actor=PERSON, why='emergency stop', now=time.time()))
    await asyncio.sleep(0.5)
    assert not tq.done(), 'the tightening did not wait for the switch'
    go.set()
    done = await asyncio.wait_for(rb, 10)
    assert done['rolled_back'] and done['account_id'] == L.ACCOUNT_ID
    got = await asyncio.wait_for(tq, 10)
    assert got['ok'] is False
    assert got['refusal'] == LC.R_EPOCH_ACCOUNT_NOT_SELECTED
    # nothing landed on the archived epoch account
    n = await a.fetchval("SELECT count(*) FROM paper_strategy_lifecycle_events"
                         " WHERE account_id=$1", epoch)
    assert n == 0
    assert await E.selected_account(a) == L.ACCOUNT_ID


async def test_the_selected_account_is_always_tightened_and_archives_refuse(scratch):
    a, b, epoch = scratch
    # automatic tightening of the selected account: recorded
    eid = await LC.record(b, account_id=epoch, strategy=STRAT,
                          from_state=LC.ACTIVE_CHALLENGER,
                          to_state=LC.REDUCED_SIZE, rule_id='RC63_PROBE',
                          actor=LC.AUTOMATIC_ACTOR, evidence={'p': 1},
                          why='probe', at=time.time())
    assert isinstance(eid, int)
    # a write for the archived (registered, not selected) main account is
    # refused by name; an unregistered legacy account is unaffected
    with pytest.raises(ValueError, match=LC.R_EPOCH_ACCOUNT_NOT_SELECTED):
        await LC.record(b, account_id=L.ACCOUNT_ID, strategy=STRAT,
                        from_state=LC.ACTIVE_CHALLENGER,
                        to_state=LC.REDUCED_SIZE, rule_id='RC63_PROBE',
                        actor=LC.AUTOMATIC_ACTOR, evidence={'p': 1},
                        why='probe', at=time.time())
    got = await LC.transition(b, account_id=L.ACCOUNT_ID, strategy=STRAT,
                              to_state=LC.QUARANTINED, actor=PERSON,
                              why='archive', now=time.time())
    assert got == {'ok': False, 'refusal': LC.R_EPOCH_ACCOUNT_NOT_SELECTED,
                   'from_state': LC.ACTIVE_CHALLENGER,
                   'to_state': LC.QUARANTINED}
    legacy = 'paper_unrelated_' + uuid.uuid4().hex[:8]
    assert isinstance(await LC.record(
        b, account_id=legacy, strategy=STRAT,
        from_state=LC.ACTIVE_CHALLENGER, to_state=LC.REDUCED_SIZE,
        rule_id='RC63_PROBE', actor=LC.AUTOMATIC_ACTOR, evidence={'p': 1},
        why='probe', at=time.time()), int)
