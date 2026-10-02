"""Held-feed boundary behavior, using actual cache/parser/devig code.

No network, database, owner, or order execution. A private import namespace
keeps these tests independent of the API's optional dependencies. Only the
owner/probe modules are inert; matching helpers are compiled from the real
ext-loop source, and the catalogue connection is an explicitly fake input.
Run: python -m unittest discover -s backend/tests -p test_held_feed_boundary.py
The production DB/agent suites still need the normal release gate.
"""
import ast
import asyncio
import importlib
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / 'sportsassets'
PKG = '_bettor_held_boundary_tests'
pkg = types.ModuleType(PKG)
pkg.__path__ = [str(ROOT)]
sys.modules[PKG] = pkg
for name in ('pinnapi_owner', 'pinnapi_probe', 'workers'):
    mod = types.ModuleType(PKG + '.' + name)
    if name == 'workers':
        mod.__path__ = []
    sys.modules[mod.__name__] = mod

ext = types.ModuleType(PKG + '.workers.ext_pinnacle_loop')
source = ROOT / 'workers/ext_pinnacle_loop.py'
names = {'AFFILIATION_MARKERS', 'SQUAD_QUALIFIERS', '_fold', '_team_tokens',
         '_sides_of', '_qualifiers', '_same_team'}
tree = ast.parse(source.read_text())
body = [n for n in tree.body if
        (isinstance(n, ast.FunctionDef) and n.name in names) or
        (isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in names for t in n.targets))]
exec(compile(ast.Module(body=body, type_ignores=[]), str(source), 'exec'),
     ext.__dict__)
sys.modules[ext.__name__] = ext
F = importlib.import_module(PKG + '.pinnapi_feed')
C = importlib.import_module(PKG + '.pinnapi_census')
R = importlib.import_module(PKG + '.pinnapi_feed_runtime')

AT = 1_790_600_000.0
START = AT + 3600
EID = 501


def fixture(*, age=5, sport=1):
    teams = ('Flamengo', 'Palmeiras') if sport == 1 else (
        'Atlanta Braves', 'Los Angeles Dodgers')
    prices = [{'designation': 'home', 'price': 300},
              {'designation': 'away', 'price': -125}]
    if sport == 1:
        prices.append({'designation': 'draw', 'price': 250})
    moved = [dict(p, price=p['price'] + 5) for p in prices]
    market = lambda ps: dict(key=F.FULL_GAME_MONEYLINE_KEY,
                            type='moneyline', period=0, status='open', prices=ps)
    cache = F.FeedCache()
    epoch = cache.new_connection([('live', sport)])
    ev = dict(id=EID, startTime=datetime.fromtimestamp(
        START, timezone.utc).isoformat(), participants=[
            dict(name=teams[0], alignment='home'),
            dict(name=teams[1], alignment='away')], markets=[market(prices)])
    cache.apply(dict(type='snapshot', stream='live', sport_id=sport,
                     ts=(AT-age-60)*1000, events=[ev]), epoch=epoch,
                received_ms=(AT-age-60)*1000)
    cache.apply(dict(type='live', sport_id=sport, op='upd',
                     ts=(AT-age)*1000, rec=dict(id=EID, markets=[market(moved)])),
                epoch=epoch, received_ms=(AT-age)*1000+1)
    row = dict(event_title=' vs '.join(teams), kind='side', line=None,
               sports_type=('soccer_team_full_time_winner' if sport == 1 else
                            'baseball_team_full_game_winner'), game_start=START,
               event_slug='ev-held', team_name=teams[0], team_league=None)
    return cache, row, teams[0]


def event_rows(row):
    # the venue's structured records for BOTH sides of the held event, as the
    # census groups them (identity is never the display title)
    return [dict(row, team_name=t) for t in row['event_title'].split(' vs ')]


class HeldBoundary(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cache, self.row, self.payout = fixture()
        self.owner = types.SimpleNamespace(cache=self.cache, sport_ids=[1, 6])
        self.old_owner = R._STATE.get('owner')
        R._STATE['owner'] = self.owner

    def tearDown(self):
        R._STATE['owner'] = self.old_owner

    def quote(self, row=None, view=None, **kw):
        r = self.row if row is None else row
        args = dict(payout_event=self.payout, payout_is_complement=False,
                    at=AT, max_age_s=30, sport_ids=[1, 6], synced=True,
                    event_rows=event_rows(r),
                    view=C.feed_event_view(self.cache) if view is None else view)
        args.update(kw)
        return R.held_quote(r, **args)

    async def held(self, conn):
        return await R.held_moneyline(conn, us_market_slug='held-market',
            payout_event=self.payout, payout_is_complement=False,
            at=AT, max_age_s=30)

    def test_wrong_family_refused_even_when_matcher_says_supported(self):
        for st in ('soccer_team_first_half_winner', 'baseball_team_first_five_winner',
                   'baseball_player_home_runs', 'soccer_brazil', ''):
            with self.subTest(st=st):
                row = dict(self.row, sports_type=st)
                # Classification itself is insufficient. The consumer must
                # refuse even if a future/legacy matcher reports a match.
                with patch.object(C, 'contract_match', return_value=(C.S_SUPPORTED, EID, 1)), \
                     patch.object(R, 'read', wraps=R.read) as read:
                    out = self.quote(row)
                self.assertEqual(out.get('reason'), 'HELD_VENUE_TYPE_NOT_PROVED_FULL_GAME_MONEYLINE')
                self.assertFalse(out['ok'])
                read.assert_not_called()

    def test_verified_full_time_three_way_and_complement_still_work(self):
        yes = self.quote()
        no = self.quote(payout_event='NOT('+self.payout+')', payout_is_complement=True)
        self.assertTrue(yes['ok'])
        self.assertTrue(no['ok'])
        self.assertEqual(yes['devig']['outcomes'], 3)
        self.assertAlmostEqual(yes['p'] + no['p'], 1)

    def test_verified_full_game_baseball_still_works(self):
        cache, row, team = fixture(sport=6)
        R._STATE['owner'] = types.SimpleNamespace(cache=cache, sport_ids=[6])
        out = self.quote(row, view=C.feed_event_view(cache), payout_event=team)
        self.assertTrue(out['ok'])
        self.assertEqual(out['devig']['outcomes'], 2)

    def test_missing_and_nonfinite_venue_start_refuse(self):
        for value in (None, float('nan'), float('inf'), 'bad'):
            with self.subTest(value=value):
                self.assertEqual(self.quote(dict(self.row, game_start=value)).get('reason'),
                                 'HELD_FIXTURE_TIME_NOT_PROVED')

    def test_missing_nonfinite_or_wrong_provider_start_refuse(self):
        for value in (None, float('nan'), float('inf'), START+86400):
            view = C.feed_event_view(self.cache)
            view[1][0]['start'] = value
            with patch.object(C, 'contract_match', return_value=(C.S_SUPPORTED, EID, 1)):
                self.assertEqual(self.quote(view=view).get('reason'), 'HELD_FIXTURE_TIME_NOT_PROVED')

    def test_invalid_evaluation_time_or_age_limit_refuse(self):
        for kw in ({'at': float('nan')}, {'max_age_s': float('inf')}, {'max_age_s': -1}):
            self.assertEqual(self.quote(**kw).get('reason'), 'HELD_FIXTURE_TIME_NOT_PROVED')

    async def test_hung_catalogue_read_has_named_timeout(self):
        class Conn:
            cancelled = False
            async def fetchrow(self, *a):
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled = True
        conn = Conn()
        with patch.object(R, 'HELD_LOOKUP_TIMEOUT_S', 0.01, create=True):
            out = await asyncio.wait_for(self.held(conn), timeout=0.25)
        self.assertEqual(out.get('reason'), 'HELD_MARKET_CATALOGUE_TIMEOUT')
        self.assertTrue(conn.cancelled)

    async def test_catalogue_wait_consumes_freshness_budget(self):
        self.cache, self.row, self.payout = fixture(age=29.99)
        self.owner.cache = self.cache
        row = self.row
        class Conn:
            async def fetchrow(self, *a):
                return row
            async def fetch(self, *a):
                return event_rows(row)
        clock = types.SimpleNamespace(monotonic=unittest.mock.Mock(side_effect=[10, 10.02]))
        with patch.object(R, 'time', clock):
            out = await self.held(Conn())
        self.assertFalse(out['ok'])
        self.assertEqual(out.get('reason'), F.R_STALE)
        self.assertGreater(out['provenance']['quote_age_s'], 30)

    async def test_owner_replacement_during_lookup_is_refused(self):
        row, cache = self.row, self.cache
        class Conn:
            async def fetchrow(self, *a):
                R._STATE['owner'] = types.SimpleNamespace(cache=cache, sport_ids=[1,6])
                return row
            async def fetch(self, *a):
                return event_rows(row)
        out = await self.held(Conn())
        self.assertFalse(out['ok'])
        self.assertEqual(out.get('reason'), F.R_NO_AUTHORITY)

    async def test_revoked_owner_skips_catalogue(self):
        self.cache.lost('test revocation')
        class Conn:
            async def fetchrow(self, *a):
                raise AssertionError('should not read DB')
        out = await self.held(Conn())
        self.assertEqual(out.get('reason'), F.R_NO_AUTHORITY)

    async def test_lookup_failure_has_distinct_reason(self):
        class Conn:
            async def fetchrow(self, *a):
                raise RuntimeError('database unavailable')
        out = await self.held(Conn())
        self.assertEqual(out['reason'], R.R_CATALOGUE_UNREADABLE)
        self.assertEqual(out['error'], 'RuntimeError')


if __name__ == '__main__':
    unittest.main()
