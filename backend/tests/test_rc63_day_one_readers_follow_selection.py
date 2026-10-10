"""RC6.3 PR #5 PORT, SECOND REVIEW -- THE ATTRIBUTION, RISK-CONTROL AND
COMMAND READERS THAT STILL READ paper_acct_main AFTER AN ACTIVATION.

Second independent review (accounting lens test_PD / test_PE, integration
lens test_zzrv1_attribution_scope), verified at 47518839:
- intel.attribution.load_paper defaulted to account_id='paper_acct_main', and
  three production callers passed no account: redteam.controls.
  attributed_positions (the red-team ATTRIBUTION / PROFIT_BREAKERS /
  MULTIPLE_TESTING controls and the registered study, /api/command/red-team
  and /api/command/pm-acceptance), pm_bind.scoreboard.read (its claims; its
  ledger reconciliation already read the selected account, so every claimed
  group was absent from the ledger) and paper_loss_attribution.read
  (/api/command/paper/loss-attribution);
- paper_institutional's summary / pnl / performance / positions / blotter /
  attribution / risk reports defaulted to `account_id or _L().ACCOUNT_ID`
  and correlation_graph.read to `PR.PAPER_ACCOUNT_ID`, while their siblings
  (reconciliation_report, export_csv, position_rooms.load) follow the
  selector -- one Command surface showed two populations.

THE FIX, PINNED HERE:
- load_paper has NO default account: an omitted account fails loudly
  (TypeError) and a malformed one refuses by name;
- the risk and learning controls read the selected account's epoch family
  (risk_history_accounts), so an activation neither resets their populations
  nor hides Day One's positions from them; before any epoch that family is
  paper_acct_main alone (unchanged);
- the forward scoreboard reconciles its claims against the ledger of the SAME
  accounts, and stays green with a settled Day One trade;
- the loss attribution, the seven institutional reports and the correlation
  graph read the selected account (and the loss attribution names it).

ALL DATA SYNTHETIC; one rolled-back transaction per test on a fresh migrated
database; no epoch is activated outside it.
"""
from __future__ import annotations

import time

import pytest

from tests.test_day_one_paper_epoch import conn, epoch_database  # noqa: F401
from tests.test_day_one_paper_epoch import PROOF
from tests.test_rc6_p_evcontrols_attribution_read import settled_position
from sportsassets import bettor_paper_day_one as E
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets.intel import attribution as IA
from sportsassets.redteam import controls as RC
from sportsassets.simulated_account_context import risk_history_accounts


async def _acct(c, aid):
    s = await S.active_session(c, aid)
    return {'account_id': aid, 'session_id': s['session_id']}


def _spy_load_paper(monkeypatch):
    seen = []
    real = IA.load_paper

    async def spy(c, **kw):
        seen.append(kw.get('account_id', 'OMITTED'))
        return await real(c, **kw)
    monkeypatch.setattr(IA, 'load_paper', spy)
    return seen


async def _main_and_day_one_positions(c, epoch_id):
    now = time.time()
    main = await _acct(c, L.ACCOUNT_ID)
    m = await settled_position(c, main, at=now - 7200)
    r = await E._activate_verified(c, epoch_id=epoch_id,
                                   request_id=epoch_id, proof=PROOF)
    day1 = await _acct(c, r['account_id'])
    d = await settled_position(c, day1, at=r['opened_at'] + 5)
    return now, r['account_id'], m, d


async def test_load_paper_has_no_default_account(conn):
    with pytest.raises(TypeError):
        await IA.load_paper(conn, now=time.time())
    for bad in (None, '', [], [''], 7):
        with pytest.raises(ValueError, match=IA.R_ACCOUNT_NOT_NAMED):
            await IA.load_paper(conn, now=time.time(), account_id=bad)


async def test_PD_red_team_controls_read_the_epoch_family_with_day_one(conn, monkeypatch):
    # before any epoch the population is paper_acct_main alone (unchanged)
    seen = _spy_load_paper(monkeypatch)
    await RC.attributed_positions(conn, now=time.time())
    assert seen == [[L.ACCOUNT_ID]]
    now, sel, m, d = await _main_and_day_one_positions(conn, 'rc63-rd-pd')
    seen.clear()
    detail: dict = {}
    rows, fx = await RC.attributed_positions(conn, now=time.time() + 60,
                                             detail=detail)
    fam = await risk_history_accounts(conn, sel)
    assert seen == [fam] and fam[0] == sel and L.ACCOUNT_ID in fam
    assert detail['accounts'] == fam
    groups = {r['group_id'] for r in rows}
    # the Day One position reaches the controls; the archive's history stays
    assert d['group_id'] in groups and m['group_id'] in groups
    win, wread = RC.attribution_window(rows, detail, now=time.time() + 60)
    att = RC.attribution(win, read=wread)
    assert att['evidence']['positions_identity_claimed'] == 2, att
    # PROFIT_BREAKERS sees the Day One position as an independent event
    mech = RC.mechanism_rows(win, fx)
    events = {r['event_key'] for r in mech}
    assert {'fx-' + d['slug'], 'fx-' + m['slug']} <= events, events
    pb = RC.profit_breakers(mech, read=wread)
    assert pb['evidence']['mechanisms']['DIRECTIONAL'][
        'independent_events'] == 2, pb


async def test_PD_the_scoreboard_reconciles_the_same_accounts_and_stays_green(conn, monkeypatch):
    from sportsassets.pm_bind import scoreboard as SBD
    now, sel, m, d = await _main_and_day_one_positions(conn, 'rc63-rd-sb')
    seen = _spy_load_paper(monkeypatch)
    led_seen = []
    real = SBD.ledger_positions

    async def spy_led(c, acct):
        led_seen.append(acct)
        return await real(c, acct)
    monkeypatch.setattr(SBD, 'ledger_positions', spy_led)
    out = await SBD.read(conn, now=time.time() + 60)
    fam = await risk_history_accounts(conn, sel)
    assert seen == [fam]
    assert led_seen == fam and out['paper_accounts'] == fam
    lr = out['ledger_reconciliation']
    assert lr['groups_absent_from_ledger'] == [], lr
    assert lr['green'] is True, lr
    assert lr['groups_reconciled'] >= 1, lr


async def test_PD_loss_attribution_reads_and_names_the_selected_account(conn, monkeypatch):
    from sportsassets import paper_loss_attribution as PLA
    now, sel, m, d = await _main_and_day_one_positions(conn, 'rc63-rd-la')
    seen = _spy_load_paper(monkeypatch)
    out = await PLA.read(conn, now=time.time() + 60)
    assert seen == [sel]
    assert out['account_id'] == sel
    assert out['positions_read']['accounts'] == [sel]


REPORTS = ('summary_report', 'pnl_report', 'performance_report',
           'positions_report', 'blotter_report', 'attribution_report',
           'risk_report', 'reconciliation_report')


async def test_PE_every_institutional_report_and_the_correlation_graph_read_the_selected_account(conn, monkeypatch):
    from sportsassets import paper_institutional as PI
    from sportsassets import correlation_graph as CG
    from sportsassets import position_rooms as PRM
    before = {}
    for name in REPORTS:
        rep = await getattr(PI, name)(conn, now=time.time())
        before[name] = rep.get('account_id')
    assert set(before.values()) == {L.ACCOUNT_ID}, before
    r = await E._activate_verified(conn, epoch_id='rc63-rd-pe',
                                   request_id='rc63-rd-pe', proof=PROOF)
    sel = r['account_id']
    day1 = await _acct(conn, sel)
    await settled_position(conn, day1, at=r['opened_at'] + 5)
    after = {}
    for name in REPORTS:
        rep = await getattr(PI, name)(conn, now=time.time() + 30)
        after[name] = rep.get('account_id')
    assert set(after.values()) == {sel}, after
    seen = []
    real = PRM._paper

    async def spy(c, at, acct):
        seen.append(acct)
        return await real(c, at, acct)
    monkeypatch.setattr(PRM, '_paper', spy)
    await CG.read(conn, now=time.time() + 30)
    assert seen == [sel]
    # an explicit account is still honoured (the archive route's reads)
    seen.clear()
    await CG.read(conn, now=time.time() + 30, account_id=L.ACCOUNT_ID)
    assert seen == [L.ACCOUNT_ID]
    rep = await PI.summary_report(conn, account_id=L.ACCOUNT_ID,
                                  now=time.time() + 30)
    assert rep['account_id'] == L.ACCOUNT_ID


async def test_PE_the_command_report_routes_serve_the_selected_account(conn, monkeypatch):
    """Every /api/command/paper/reports/* section, called through its route
    handler (no account, as served) after activation."""
    import contextlib
    from sportsassets.api import paper_reports_routes as R

    class _Pool:
        @contextlib.asynccontextmanager
        async def acquire(self):
            yield conn

    async def _pool():
        return _Pool()
    monkeypatch.setattr(R, '_pool', _pool)
    r = await E._activate_verified(conn, epoch_id='rc63-rd-rt',
                                   request_id='rc63-rd-rt', proof=PROOF)
    sel = r['account_id']
    got = {
        'summary': await R.reports_summary(),
        'pnl': await R.reports_pnl(period='ALL', start=None, end=None),
        'performance': await R.reports_performance(),
        'positions': await R.reports_positions(),
        'blotter': await R.reports_blotter(kind='both', limit=50, offset=0),
        'attribution': await R.reports_attribution(),
        'risk': await R.reports_risk(),
        'reconciliation': await R.reports_reconciliation()}
    accounts = {k: v.get('account_id') for k, v in got.items()}
    assert set(accounts.values()) == {sel}, accounts


# ═════════════════════════════════════════════════════════════════════
# (second review, non-blocking, taken) THE COMMAND VIEWS OF THE ARCHIVE
# ═════════════════════════════════════════════════════════════════════

def _route_pool(monkeypatch, module, conn):
    import contextlib

    class _Pool:
        @contextlib.asynccontextmanager
        async def acquire(self):
            yield conn

    async def _pool():
        return _Pool()
    monkeypatch.setattr(module, '_pool', _pool)


async def test_the_archive_view_pages_to_the_first_entry_and_labels_the_selected_book(conn, monkeypatch):
    """The archive route returned the newest 100 ledger rows with no paging
    (the earlier reviewer's A3: the first historical loss was unreachable)
    and labelled any paper id HISTORICAL, the live Day One account too."""
    from sportsassets.api import command_paper as API
    from sportsassets import bettor_capital_authority as CA
    from tests.test_rc63_day_one_audrey_switch import _buy, _settle_lost

    async def _no_rules(*a, **k):       # the read, not the entry gate
        return {'ok': True, 'firing': [], 'state': 'ACTIVE_CHALLENGER'}
    monkeypatch.setattr(CA, 'stopping_rules_now', _no_rules)
    main = await _acct(conn, L.ACCOUNT_ID)
    main['config'] = (await S.active_session(conn, L.ACCOUNT_ID))['config']
    t0 = time.time() - 86400
    first = None
    for i in range(40):                 # 40 round trips: > 100 entries
        slug = 'rc63-arch-%d-%s' % (i, time.time_ns() % 100000)
        o = await _buy(conn, main, at=t0 + i * 60, key='arch-%d' % i,
                       slug=slug, qty=10, px=.5)
        await _settle_lost(conn, main, o, at=t0 + i * 60 + 5)
    first = await conn.fetchval(
        "SELECT min(seq) FROM paper_ledger WHERE account_id=$1 "
        "   AND kind='SETTLEMENT'", L.ACCOUNT_ID)
    total = await conn.fetchval(
        "SELECT count(*) FROM paper_ledger WHERE account_id=$1", L.ACCOUNT_ID)
    assert total > 100
    r = await E._activate_verified(conn, epoch_id='rc63-rd-arch',
                                   request_id='rc63-rd-arch', proof=PROOF)
    _route_pool(monkeypatch, API, conn)
    seen, cursor, pages = [], None, 0
    while True:
        got = await API.historical_account(L.ACCOUNT_ID, before_seq=cursor,
                                           limit=50)
        assert got['label'] == API.LABEL_HISTORICAL
        seen += [e['sequence'] for e in got['ledger']]
        pages += 1
        cursor = got['page']['next_before_seq']
        if cursor is None:
            break
    assert first in seen and len(seen) == total and pages >= 3
    assert seen == sorted(seen, reverse=True)
    live = await API.historical_account(r['account_id'])
    assert live['label'] == API.LABEL_SELECTED
    assert 'HISTORICAL' not in live['label']
    assert live['selected_account_id'] == r['account_id']


async def test_audrey_reports_of_a_family_account_and_the_epoch_pointer(conn, monkeypatch):
    from sportsassets.api import command_paper as API
    from fastapi import HTTPException
    _route_pool(monkeypatch, API, conn)
    r = await E._activate_verified(conn, epoch_id='rc63-rd-aud',
                                   request_id='rc63-rd-aud', proof=PROOF)
    sel = r['account_id']
    arch = await API.paper_audrey(account_id=L.ACCOUNT_ID)
    assert arch['account_id'] == L.ACCOUNT_ID
    # the switch-day version written at activation is reachable
    assert arch['daily_reports']['status'] == 'OK', arch['daily_reports']
    assert {x['account_id'] for x in arch['daily_reports']['data']} == {
        L.ACCOUNT_ID}
    cur = await API.paper_audrey()
    assert cur['account_id'] == sel
    with pytest.raises(HTTPException) as exc:
        await API.paper_audrey(account_id='paper_unrelated_x')
    assert exc.value.status_code == 404
    rb = await E.rollback(conn, epoch_id='rc63-rd-aud',
                          request_id='rc63-rd-aud-rb')
    assert rb['rolled_back']
    d1 = await API.day_one_epoch()
    assert d1['day_one'] is False
    eps = d1['registered_epochs']
    assert [(e['account_id'], e['selected'], e['rolled_back'])
            for e in eps] == [(sel, False, True)]
    back = await API.paper_audrey(account_id=sel)
    assert back['account_id'] == sel
    assert back['daily_reports']['status'] == 'OK'


async def test_A5_switched_readers_name_their_population(conn):
    from sportsassets.api import command_validation as CV
    from sportsassets.api import command_profitability_scoreboard as PS
    r = await E._activate_verified(conn, epoch_id='rc63-rd-a5',
                                   request_id='rc63-rd-a5', proof=PROOF)
    now = time.time()
    v = await CV._read(conn, since=None, since_source='PRODUCTION_CUTOVER',
                       now=now)
    assert v['account_id'] == r['account_id'], v.get('why')
    p = await PS.read(conn, account_id=r['account_id'], now=now)
    if p['status'] == 'OK':
        assert p['account_id'] == r['account_id']
