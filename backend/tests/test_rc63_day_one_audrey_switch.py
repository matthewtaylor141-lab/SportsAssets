"""RC6.3 PR #5 PORT -- AUDREY ON A DESELECTED ACCOUNT.

After an activation (or a rollback) the paper pass runs Audrey's step only
for the newly selected account. Before this fix the outgoing account's day
kept its last 15-minute version (REPORT_EVERY_S): it was never final and it
missed every settlement recorded after that version -- its last realized
loss was in no Audrey report at all, although the ledger and the epoch's
historical receipt held it (independent review, accounting lens, A2/A2r).

THE FIX, PINNED HERE:
- at the switch (bettor_paper_day_one._activate_verified and rollback, under
  the PAPER lock, before the selector moves) the outgoing session gets its
  version of the current day covering everything recorded up to the switch
  (paper_audrey.write_switch_version);
- Audrey's day close (rc6/audrey-final's days_to_close / close_days) also
  closes every other account of the selected account's epoch family
  (risk_history_accounts) whose reported day is over and still open
  (paper_audrey.close_family_days): that day gets its ONE final version.

The no-activation control shows the same sequence on one account: its own
next pass writes the final version carrying the loss. ALL DATA SYNTHETIC;
every test runs in one rolled-back transaction on a fresh migrated database
(tests.test_day_one_paper_epoch.epoch_database); no epoch is activated
outside it.
"""
from __future__ import annotations

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


async def _acct(c, aid):
    s = await S.active_session(c, aid)
    return {'account_id': aid, 'session_id': s['session_id'],
            'config': s['config'], 'session': s}


async def _buy(c, a, *, at, key, slug, qty=1000, px=.5):
    o = H.order(a, key=key, qty=qty, limit=px, at=at, slug=slug)
    r = await L.submit_order(c, o, now=at, fee_fn=H.zero_fee)
    assert r['ok'], r
    await H.observe(c, slug, at + 3, offers=[(px, qty)],
                    bids=[(px - .01, qty)])
    got = await SIM.simulate_order(c, r['order']['order_id'], now=at + 4,
                                   fee_fn=H.zero_fee)
    assert got['fills'], got
    return r['order']


async def _settle_lost(c, a, o, *, at):
    await L.settle(c, account_id=a['account_id'], group_id=o['group_id'],
                   slug=o['us_market_slug'], holding_side='LONG',
                   settlement_event_key='venue-final:' + o['us_market_slug'],
                   outcome='LOST', evidence={'s': 1},
                   evidence_source='TEST_FIXTURE', at=at,
                   session_id=a['session_id'])


async def _versions(c, acct, day):
    return [(r['version'], r['final'], float(r['realized']))
            for r in await c.fetch(
                "SELECT version, final, "
                " (report->'pnl'->>'realized_total_usd')::float8 AS realized"
                "  FROM paper_audrey_reports WHERE account_id=$1 "
                "   AND report_day=$2 ORDER BY version", acct, day)]


async def _step(c, a, at):
    from sportsassets.agents import paper_audrey as PA
    return await PA.step(c, {'account_id': a['account_id'],
                             'session_id': a['session_id'],
                             'session': a['session'], 'now': at})


def _day(a, at):
    from sportsassets.agents import paper_audrey as PA
    return PA.day_bounds(at, a['session'].get('reporting_tz')
                         or 'America/New_York')


async def test_A2_activation_archive_day_gets_a_closing_version_with_the_loss(conn):
    main = await _acct(conn, L.ACCOUNT_ID)
    day, start, end = _day(main, time.time())
    slug = 'rc63-a2-' + uuid.uuid4().hex[:6]
    o = await _buy(conn, main, at=start + 60, key='a2', slug=slug)
    from sportsassets.agents import paper_audrey as PA
    v1 = await PA.write_report(conn, session=main['session'],
                               account_id=L.ACCOUNT_ID, now=start + 120)
    assert v1['written']
    # the last position settles LOST after Audrey's last version; the (now
    # flat) account is cut over
    await _settle_lost(conn, main, o, at=start + 180)
    realized = float((await L.balances(conn, L.ACCOUNT_ID))['realized_pnl_usd'])
    assert realized == -500.0
    r = await E._activate_verified(conn, epoch_id='rc63-a2',
                                   request_id='rc63-a2', proof=PROOF)
    # at the switch: a version of the archive's day carrying the loss
    at_switch = await _versions(conn, L.ACCOUNT_ID, day)
    assert at_switch[-1] == (2, False, -500.0), at_switch
    new = await _acct(conn, r['account_id'])
    # Day One's production Audrey step the same day, then the next two days
    for at in (time.time() + 5, end + 86400.0 - 3600,
               end + 2 * 86400.0 - 3600):
        await _step(conn, new, at)
    arch = await _versions(conn, L.ACCOUNT_ID, day)
    finals = [v for v in arch if v[1]]
    assert finals == [(3, True, -500.0)], arch
    # nothing is appended to the closed day after its final version
    assert arch[-1] == finals[0]
    assert float((await L.balances(conn, L.ACCOUNT_ID))['realized_pnl_usd']) \
        == realized


async def test_A2_control_without_activation_the_closing_version_covers_the_loss(conn):
    main = await _acct(conn, L.ACCOUNT_ID)
    day, start, end = _day(main, time.time())
    slug = 'rc63-a2c-' + uuid.uuid4().hex[:6]
    o = await _buy(conn, main, at=start + 60, key='a2c', slug=slug)
    from sportsassets.agents import paper_audrey as PA
    await PA.write_report(conn, session=main['session'],
                          account_id=L.ACCOUNT_ID, now=start + 120)
    await _settle_lost(conn, main, o, at=start + 180)
    await _step(conn, main, end + 86400.0 - 3600)
    arch = await _versions(conn, L.ACCOUNT_ID, day)
    assert [v for v in arch if v[1]] == [(2, True, -500.0)], arch


async def test_A2r_rollback_epoch_last_day_gets_a_closing_version_with_the_loss(conn):
    r = await E._activate_verified(conn, epoch_id='rc63-a2r',
                                   request_id='rc63-a2r', proof=PROOF)
    e1 = await _acct(conn, r['account_id'])
    slug = 'rc63-a2r-' + uuid.uuid4().hex[:6]
    o = await _buy(conn, e1, at=r['opened_at'] + 5, key='a2r', slug=slug)
    await _step(conn, e1, r['opened_at'] + 20)
    await _settle_lost(conn, e1, o, at=r['opened_at'] + 40)
    realized = float((await L.balances(conn, e1['account_id']))
                     ['realized_pnl_usd'])
    assert realized == -500.0
    rb = await E.rollback(conn, epoch_id='rc63-a2r', request_id='rc63-a2r-undo')
    assert rb['rolled_back'] and not rb['idempotent']
    day = await conn.fetchval(
        "SELECT max(report_day) FROM paper_audrey_reports WHERE account_id=$1",
        e1['account_id'])
    at_switch = await _versions(conn, e1['account_id'], day)
    assert at_switch[-1][1:] == (False, -500.0), at_switch
    main = await _acct(conn, L.ACCOUNT_ID)
    _, _, end = _day(main, r['opened_at'] + 40)
    for at in (time.time() + 5, end + 86400.0 - 3600):
        await _step(conn, main, at)
    rep = await _versions(conn, e1['account_id'], day)
    finals = [v for v in rep if v[1]]
    assert len(finals) == 1 and finals[0][2] == realized, rep
    assert rep[-1] == finals[0]


async def _pass(c, at):
    from sportsassets.agents import paper_runtime as R
    from sportsassets.agents import paper_xavier as PX
    from sportsassets.agents import paper_audrey as PA
    out = await R.paper_pass(c, now=at, force=True,
                             steps=[('settle', PX.step_settle),
                                    ('audrey', PA.step)])
    assert out.get('ran'), out
    assert not out['errors'], out['errors']
    return out


async def _lost_valuation(c, slug):
    rid = await c.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, us_market_slug,"
        " contract_selection, sport_family, market, raw_odds, "
        " outcomes_priced, expected_outcomes, decision, admissible, "
        " buy_intent, outcome_basis) VALUES ('rc63','v',"
        " 'EXTERNAL_BOOKMAKER_VALUATION','t','t','power','PMUS',$1,'HOME',"
        " 'soccer','moneyline','{}'::jsonb,2,2,'NO_TRADE',false,"
        " 'ORDER_INTENT_BUY_LONG','VENUE_REPORTED_OUTCOME') RETURNING id",
        slug)
    await c.execute("UPDATE external_valuations SET outcome_known=true, "
                    " outcome=0, outcome_at=now() WHERE id=$1", rid)


@pytest.mark.parametrize('activate', [False, True])
async def test_production_pass_archive_day_closes_with_the_loss(conn, activate):
    """Xavier settles and Audrey reports through the real paper pass (the
    account resolved from the selector): with or without an activation the
    archive's day ends with one final version carrying its realized loss."""
    main = await _acct(conn, L.ACCOUNT_ID)
    day, start, end = _day(main, time.time())
    slug = 'rc63-pp-' + uuid.uuid4().hex[:6]
    await _buy(conn, main, at=start + 60, key='pp-' + slug, slug=slug)
    await _pass(conn, start + 120)                 # Audrey writes a version
    await _lost_valuation(conn, slug)
    p2 = await _pass(conn, start + 300)            # Xavier settles LOST;
    assert p2['steps']['settle']['settled'] >= 1   # Audrey not due (< 900 s)
    led = float((await L.balances(conn, L.ACCOUNT_ID))['realized_pnl_usd'])
    assert led == -500.0
    if activate:
        await E._activate_verified(conn, epoch_id='rc63-pp',
                                   request_id='rc63-pp', proof=PROOF)
        assert await E.selected_account(conn) != L.ACCOUNT_ID
    for at in (time.time() + 5, time.time() + 1000, end + 3600,
               end + 86400 + 3600):
        await _pass(conn, at)
    v = await _versions(conn, L.ACCOUNT_ID, day)
    finals = [x for x in v if x[1]]
    assert len(finals) == 1 and finals[0][2] == led, v
    assert v[-1] == finals[0]


async def test_family_close_touches_no_account_outside_the_family(conn):
    """An unrelated legacy account's open day is not closed by the selected
    account's step; before any epoch the family is the selected account
    alone (close_family_days writes nothing)."""
    from sportsassets.agents import paper_audrey as PA
    main = await _acct(conn, L.ACCOUNT_ID)
    assert await PA.close_family_days(conn, account_id=L.ACCOUNT_ID,
                                      closed_at=time.time() + 10 * 86400) == {}
    other = 'paper_unrelated_' + uuid.uuid4().hex[:8]
    assert (await S.ensure_session(conn, account_id=other, now=H.T0))['ok']
    o = await _acct(conn, other)
    day, start, end = _day(o, time.time())
    await PA.write_report(conn, session=o['session'], account_id=other,
                          now=start + 60)
    await E._activate_verified(conn, epoch_id='rc63-iso',
                               request_id='rc63-iso', proof=PROOF)
    new = await _acct(conn, await E.selected_account(conn))
    out = await _step(conn, new, end + 86400.0)
    assert other not in out['family_closing']
    assert L.ACCOUNT_ID in out['family_closing']
    assert [v[1] for v in await _versions(conn, other, day)] == [False]
    assert main['account_id'] == L.ACCOUNT_ID


# ═════════════════════════════════════════════════════════════════════
# (rc6.3 pr5-port, second review) A REVISION AFTER THE SWITCH DAY CLOSED
# ═════════════════════════════════════════════════════════════════════
# Xavier's production settlement pass keeps applying settlement revisions to
# every family account (paper_xavier.step_settle walks
# risk_history_accounts). Before this fix a deselected account got no
# version after its switch day closed, so a revision recorded later -- a
# realized gain or loss on its ledger -- was in no Audrey report (second
# independent review, accounting lens, test_PA / test_PA2). Now the selected
# account's step writes the family account's current-day version whenever it
# recorded activity after its newest report, and the family day close
# finalizes that day.

async def _all_versions(c, acct):
    return [(str(r['report_day']), r['version'], r['final'],
             float(r['realized']))
            for r in await c.fetch(
                "SELECT report_day, version, final, "
                " (report->'pnl'->>'realized_total_usd')::float8 AS realized"
                "  FROM paper_audrey_reports WHERE account_id=$1 "
                " ORDER BY report_day, version", acct)]


async def _revise(c, slug, decided, *, won):
    from sportsassets.agents import paper_xavier as X
    from tests.test_day_one_release_review import _valuation
    vid = await _valuation(c, cid=slug, price=.5, decided=decided)
    await c.execute(
        "UPDATE external_valuations SET outcome=$2, outcome_known=true, "
        " outcome_basis=$3, outcome_at=to_timestamp($4) WHERE id=$1",
        vid, 1 if won else 0, next(iter(X.LABEL_BASES)), float(decided) + 100)


@pytest.mark.parametrize('activate', [False, True])
async def test_PA_a_revision_after_the_switch_day_reaches_an_audrey_report(conn, activate):
    main = await _acct(conn, L.ACCOUNT_ID)
    day, start, end = _day(main, time.time())
    slug = 'rc63-pa-' + uuid.uuid4().hex[:6]
    o = await _buy(conn, main, at=start + 60, key='pa-' + slug, slug=slug)
    await _pass(conn, start + 120)
    await _settle_lost(conn, main, o, at=start + 180)
    if activate:
        await E._activate_verified(conn, epoch_id='rc63-pa',
                                   request_id='rc63-pa', proof=PROOF)
    await _pass(conn, end + 3600)             # the switch day closes
    before = await _all_versions(conn, L.ACCOUNT_ID)
    assert [v[3] for v in before if v[0] == str(day) and v[2]] == [-500.0], \
        before
    # the venue revises the settlement (LOST -> WON) the next day; the
    # production pass's Xavier applies it to paper_acct_main
    await _revise(conn, slug, start + 60, won=True)
    p = await _pass(conn, end + 7200)
    assert p['steps']['settle']['corrected'] == 1, p['steps']['settle']
    led = float((await L.balances(conn, L.ACCOUNT_ID))['realized_pnl_usd'])
    assert led == 500.0
    for at in (end + 8200, end + 86400 + 3600, end + 2 * 86400 + 3600):
        await _pass(conn, at)
    after = await _all_versions(conn, L.ACCOUNT_ID)
    # every earlier version is unchanged (append-only)
    assert after[:len(before)] == before
    nxt = str(PA_day(end + 7200))
    finals = [v for v in after if v[0] == nxt and v[2]]
    # the revision day has exactly one final version carrying the revision
    assert len(finals) == 1 and finals[0][3] == led, after
    assert [v for v in after if v[0] == nxt][-1] == finals[0]


def PA_day(at):
    from sportsassets.agents import paper_audrey as PA
    return PA.day_bounds(at)[0]


async def test_PA2_a_late_revision_on_a_rolled_back_epoch_reaches_an_audrey_report(conn):
    r = await E._activate_verified(conn, epoch_id='rc63-pa2',
                                   request_id='rc63-pa2', proof=PROOF)
    e1 = await _acct(conn, r['account_id'])
    slug = 'rc63-pa2-' + uuid.uuid4().hex[:6]
    o = await _buy(conn, e1, at=r['opened_at'] + 5, key='pa2', slug=slug)
    await _pass(conn, r['opened_at'] + 20)
    await L.settle(conn, account_id=e1['account_id'], group_id=o['group_id'],
                   slug=slug, holding_side='LONG',
                   settlement_event_key='venue-final:' + slug, outcome='WON',
                   evidence={'s': 1}, evidence_source='TEST_FIXTURE',
                   at=r['opened_at'] + 40, session_id=e1['session_id'])
    rb = await E.rollback(conn, epoch_id='rc63-pa2', request_id='rc63-pa2-rb')
    assert rb['rolled_back']
    _, _, end = _day(e1, r['opened_at'] + 40)
    await _pass(conn, end + 3600)             # the epoch's last day closes
    before = await _all_versions(conn, e1['account_id'])
    assert [v[3] for v in before if v[2]] == [500.0], before
    await _revise(conn, slug, r['opened_at'] + 5, won=False)
    p = await _pass(conn, end + 7200)
    assert p['steps']['settle']['accounts'][e1['account_id']]['corrected'] == 1
    led = float((await L.balances(conn, e1['account_id']))['realized_pnl_usd'])
    assert led == -500.0
    for at in (end + 86400 + 3600, end + 2 * 86400 + 3600):
        await _pass(conn, at)
    after = await _all_versions(conn, e1['account_id'])
    assert after[:len(before)] == before
    nxt = str(PA_day(end + 7200))
    finals = [v for v in after if v[0] == nxt and v[2]]
    assert len(finals) == 1 and finals[0][3] == led, after


async def test_a_family_account_with_nothing_new_gets_no_version(conn):
    """No activity after its newest report: the family close writes nothing
    beyond the switch day's one final version, however many days pass."""
    main = await _acct(conn, L.ACCOUNT_ID)
    day, start, end = _day(main, time.time())
    slug = 'rc63-pq-' + uuid.uuid4().hex[:6]
    o = await _buy(conn, main, at=start + 60, key='pq-' + slug, slug=slug)
    await _pass(conn, start + 120)
    await _settle_lost(conn, main, o, at=start + 180)
    await E._activate_verified(conn, epoch_id='rc63-pq',
                               request_id='rc63-pq', proof=PROOF)
    for at in (end + 3600, end + 7200, end + 86400 + 3600,
               end + 2 * 86400 + 3600):
        await _pass(conn, at)
    arch = await _all_versions(conn, L.ACCOUNT_ID)
    assert {v[0] for v in arch} == {str(day)}, arch
    assert arch[-1] == (str(day), 3, True, -500.0), arch


async def test_a_switch_whose_report_clock_ran_ahead_still_reports_up_to_the_switch(conn):
    """The outgoing account already reported a later day (a pass clock ahead
    of the database's): its switch version is written on that newest day
    with everything recorded, instead of being silently refused as
    ALREADY_FINAL on the switch instant's day."""
    r = await E._activate_verified(conn, epoch_id='rc63-ahead',
                                   request_id='rc63-ahead', proof=PROOF)
    e1 = await _acct(conn, r['account_id'])
    slug = 'rc63-ahead-' + uuid.uuid4().hex[:6]
    o = await _buy(conn, e1, at=r['opened_at'] + 5, key='ahead', slug=slug)
    _, _, end = _day(e1, r['opened_at'] + 5)
    await _step(conn, e1, r['opened_at'] + 20)
    await _step(conn, e1, end + 3600)         # today closed, tomorrow v1
    await _settle_lost(conn, e1, o, at=end + 3700)
    rb = await E.rollback(conn, epoch_id='rc63-ahead',
                          request_id='rc63-ahead-rb')
    assert rb['rolled_back']
    newest = (await _all_versions(conn, e1['account_id']))[-1]
    assert newest[0] == str(PA_day(end + 3600)) and not newest[2]
    assert newest[3] == -500.0, newest


async def test_a_switch_version_that_is_not_stored_refuses_the_switch(conn, monkeypatch):
    """A version a writer outside the per-day lock took (VERSION_TAKEN) is no
    switch report: the activation is refused by name and nothing moves."""
    from sportsassets.agents import paper_audrey as PA
    real = PA.write_report

    async def taken(c, **kw):
        if kw.get('account_id') == L.ACCOUNT_ID:
            out = await real(c, **dict(kw, now=kw['now']))
            return dict(out, written=False, why='VERSION_TAKEN')
        return await real(c, **kw)
    monkeypatch.setattr(PA, 'write_report', taken)
    with pytest.raises(E.EpochRefused,
                       match=E.R_OUTGOING_AUDREY_REPORT_FAILED):
        await E._activate_verified(conn, epoch_id='rc63-held',
                                   request_id='rc63-held', proof=PROOF)
    assert await E.selected_account(conn) == L.ACCOUNT_ID
    assert await conn.fetchval("SELECT count(*) FROM paper_account_epochs") == 0
