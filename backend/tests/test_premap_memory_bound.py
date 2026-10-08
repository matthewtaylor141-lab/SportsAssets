"""THE CATALOGUE SWEEP HOLDS ONE PAGE, NOT THE SWEEP (2026-10-08, the
sportsassets-workers OOM kills at 2 GiB, 2026-10-07 22:19Z and 2026-10-08
01:33:57Z).

What premap.refresh accumulated, measured through the real code path
(refresh -> polymarket_us SDK -> httpx MockTransport -> response.json() ->
PageWalk -> _write_event -> SideKeyGuard -> _upsert) on a synthetic catalogue
of the production size (2,414 events / 86,088 rows, the 01:16:44Z full sweep):

  * the probe page stayed referenced by refresh() for the whole sweep;
  * each page's SDK response and event list stayed referenced while the NEXT
    page was fetched and parsed (a parsed page is ~4.7x its JSON);
  * SideKeyGuard kept every admitted row dict of the refresh (~105 MB at
    86k rows) although a body is only read back inside its own event;
  * every page's raw body and decoded text (~2x its JSON) waited for a GC
    pass: httpx's BoundSyncStream points back at its response.

tracemalloc peak, these tests: the full sweep 203.9 MB before, 80.9 MB after
(bound 109.1 MB); the calendar lane beside the fast lane (they DO overlap:
calendar_refresh runs after _full_loop releases _SWEEP_LOCK, by design,
R30A) 179.0 MB before, 103.4 MB after (bound 156.0 MB).

These tests pin:
  (1) the bound: one sweep never holds more than the page it is parsing plus
      O(rows) key state; two overlapping lanes never more than one page
      each; a page's body is freed when the SDK returns, for premap's reads
      only;
  (2) the overlap that remains is the documented one (calendar beside fast),
      each lane bounded to one page, and the lanes that must not overlap
      (full and fast) still cannot. No new lock: holding a page-lifetime
      lock across the two lanes would serialize their database writes (the
      sweep is write-bound, ~8 ms a row in production), and the fast lane's
      180 s cadence would stretch while the calendar lane runs;
  (3) the rows written are identical, write for write and DELETE for DELETE,
      to the pre-fix code on a fixture that drives every side-key path
      (qualified pair inside one event and across events, a key reused by
      another market, a duplicate listing) over several pages -- the digest
      below was produced by the pre-fix code on this same fixture.
"""
from __future__ import annotations

import asyncio
import datetime as _dtmod
import gc
import hashlib
import json
import time
import tracemalloc

import httpx

from sportsassets import venue_catalogue as vc
from sportsassets.workers import premap

UTC = _dtmod.UTC


# ── a venue-shaped page generator (no catalogue held in memory) ──────────

def _team(tid, name, abbr, ordering):
    return {"id": tid, "name": name, "abbreviation": abbr, "league": "mlb",
            "record": "", "logo": "https://polymarket-upload.s3.us-east-2."
            "amazonaws.com/%s-light-09ca60853e.png" % abbr, "alias": name,
            "safeName": name.split()[-1], "homeIcon": "", "awayIcon": "",
            "colorPrimary": "#F5AF05", "providerId": tid % 97,
            "ordering": ordering,
            "longIcon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/"
                        "%s-light-09ca60853e.png" % abbr,
            "shortIcon": "https://polymarket-upload.s3.us-east-2.amazonaws.com/"
                         "%s-light-09ca60853e.png" % abbr,
            "displayAbbreviation": abbr.upper(), "conference": "",
            "providerIds": [{"provider": "PROVIDER_SPORTRADAR",
                             "providerId": "d52d5339-cbdd-43f3-9dfa-%012d" % tid},
                            {"provider": "PROVIDER_SPORTSDATAIO",
                             "providerId": str(tid % 97)}],
            "longIconDark": "https://polymarket-upload.s3.us-east-2.amazonaws."
                            "com/%s-dark-316d4eb6a6.png" % abbr,
            "shortIconDark": "https://polymarket-upload.s3.us-east-2.amazonaws."
                             "com/%s-dark-316d4eb6a6.png" % abbr,
            "color": {"light": "#F5AF05", "dark": "#ECB11C"},
            "imageDisplayType": "IMAGE_DISPLAY_TYPE_LOGO"}


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _market(i, j, st):
    """One inline market the shape and size of the venue's (~4.4 KB of
    JSON, research/beta48/shadow/fixtures_events_block3.json), two sides."""
    slug = "aec-mlb-ta%d-tb%d-m%d" % (i, i, j)
    a, b = "Team A%d" % i, "Team B%d" % i
    sides = []
    for k, (nm, ab, lg) in enumerate(((a, "ta%d" % i, True),
                                      (b, "tb%d" % i, False))):
        sides.append({
            "id": str(9000000 + i * 2000 + j * 2 + k),
            "marketSideType": "MARKET_SIDE_TYPE_INSTRUMENT",
            "identifier": slug, "createdAt": "2026-09-13T04:00:49Z",
            "updatedAt": "2026-09-13T04:00:49Z",
            "description": "%s m%d" % (nm, j), "price": "0.6350",
            "marketId": 5000000 + i * 1000 + j, "long": lg,
            "teamId": 3000 + 2 * i + k,
            "team": _team(3000 + 2 * i + k, nm, ab, ("away", "home")[k]),
            "quote": {"value": "0.6400", "currency": "USD"}, "tradable": True})
    return {
        "id": str(5000000 + i * 1000 + j),
        "question": "Market %d: who will win %s vs %s on %s?" % (j, a, b, _iso(st)),
        "slug": slug, "endDate": _iso(st + _dtmod.timedelta(days=14)),
        "category": "sports", "startDate": "2026-09-13T04:00:49Z",
        "description": ("This market will settle to the winner of the %s vs %s "
                        "MLB game scheduled for %s. Extra innings are included "
                        "if played. If the game is delayed, postponed, or "
                        "suspended and not rescheduled to a date within two "
                        "weeks of the originally scheduled date, the market "
                        "will settle to the last fair market price. Outcome "
                        "sourced from MLB. (m%d)" % (a, b, _iso(st), j)),
        "active": True, "marketType": "moneyline", "closed": False,
        "createdAt": "2026-09-13T04:00:49Z", "updatedAt": "2026-09-14T17:08:33Z",
        "archived": False, "orderPriceMinTickSize": 0.005,
        "gameStartTime": _iso(st), "manualActivation": False,
        "sportsMarketType": "baseball_team_full_game_winner",
        "marketSides": sides,
        "outcomes": json.dumps([a, b]), "outcomePrices": "[\"0.6350\",\"0.6400\"]",
        "ep3Status": "OPEN", "status": "MARKET_STATUS_OPEN",
        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE", "hidden": False,
        "tags": [], "title": "%s vs %s" % (a, b), "feeCoefficient": 0.06,
        "bestBidQuote": {"value": "0.6350", "currency": "USD"},
        "bestAskQuote": {"value": "0.6400", "currency": "USD"},
        "titleShort": "A%d vs B%d" % (i, i), "ep3SyncedAt": "2026-09-14T11:37:40Z",
        "minimumTradeQty": 0.01, "comboEnabled": True}


def _event(i, n_markets, st):
    """One event the shape and size of the venue's (~5.5 KB before its
    markets) with `n_markets` inline markets."""
    tag = lambda k, label, extra=None: dict(
        {"id": str(k), "label": label, "slug": label.lower(),
         "createdAt": "2025-09-25T16:13:27Z", "updatedAt": "2026-09-14T17:10:46Z",
         "subtags": [], "hub": None}, **(extra or {}))
    league = {"id": 5, "name": "MLB", "sportId": 5, "tagId": 4,
              "image": "https://polymarket-upload.s3.us-east-2.amazonaws.com/"
                       "league-images/MLB.png",
              "resolution": "https://www.mlb.com/", "ordering": "away",
              "activeSeriesId": 15, "isOperational": True,
              "automaticResolution": False, "createdAt": "2026-03-05T18:30:42Z",
              "slug": "mlb", "abbreviation": "MLB"}
    ms = [_market(i, j, st) for j in range(n_markets)]
    return {
        "id": str(900000 + i), "ticker": "mlb-ta%d-tb%d" % (i, i),
        "slug": "mlb-ta%d-tb%d" % (i, i),
        "title": "Team A%d vs. Team B%d" % (i, i),
        "description": "Team A%d vs. Team B%d" % (i, i),
        "startDate": _iso(st), "endDate": _iso(st + _dtmod.timedelta(hours=4)),
        "active": True, "closed": False, "archived": False, "category": "sports",
        "createdAt": "2026-09-13T04:00:49Z", "updatedAt": "2026-09-14T17:08:33Z",
        "startTime": _iso(st), "seriesSlug": "mlb-2026", "period": "NS",
        "markets": ms, "gameId": 10079571 + i,
        "sportradarGameId": "5007cc12-312b-4099-9322-%012d" % i,
        "participants": [], "hidden": False,
        "tags": [tag(2, "Sports"), tag(3, "Games"),
                 tag(4, "MLB", {"image": league["image"], "tradable": False,
                                "league": league}),
                 tag(15, "Baseball", {"tradable": False,
                                      "sport": {"id": 5, "name": "Baseball",
                                                "tagId": 15, "slug": "baseball"}})],
        "teams": [_team(3000 + 2 * i, "Team A%d" % i, "ta%d" % i, "away"),
                  _team(3001 + 2 * i, "Team B%d" % i, "tb%d" % i, "home")],
        "sortType": "price",
        "marketGroups": [{"id": g, "title": g.title(), "order": 0,
                          "marketIds": [m["id"] for m in ms[:3]],
                          "initialCollapsed": False, "live": False}
                         for g in ("PRIMARY", "game-lines", "game-lines/spread",
                                   "game-lines/total")],
        "primaryTag": tag(4, "MLB", {"league": league}),
        "combos": {"enabled": True, "maxLegs": 10, "minLegs": 2},
        "marketCounts": {"numMarkets": n_markets, "numActiveMarkets": n_markets}}


class _Lane:
    """n events, n_markets in all, start times spread over [lo, hi]."""

    def __init__(self, n_events, n_markets, lo, hi, base):
        self.n, self.m, self.lo, self.hi, self.base = n_events, n_markets, lo, hi, base

    def start(self, k):
        span = (self.hi - self.lo).total_seconds()
        return self.lo + _dtmod.timedelta(seconds=span * (k + 0.5) / self.n)

    def markets_of(self, k):
        q, r = divmod(self.m, self.n)
        return q + (1 if k < r else 0)


class _Venue:
    """events.list over one or more lanes' catalogues: the start-time window
    filter and offset/limit, each page's JSON generated when it is asked for
    (what httpx's response.content is in production), nothing held."""

    def __init__(self, *lanes):
        self.lanes = lanes
        self.requests = 0
        self.max_page_bytes = 0

    def _index(self):
        out = []
        for ln in self.lanes:
            out.extend((ln.start(k).timestamp(), ln, k) for k in range(ln.n))
        out.sort(key=lambda t: t[0])
        return out

    def handler(self, request):
        q = dict(request.url.params)
        self.requests += 1
        ep = lambda s: _dtmod.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        lo = ep(q["startTimeMin"]) if "startTimeMin" in q else float("-inf")
        hi = ep(q["startTimeMax"]) if "startTimeMax" in q else float("inf")
        off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
        sel = [(ln, k) for t, ln, k in self._index() if lo <= t <= hi][off:off + lim]
        body = b'{"events":[' + b",".join(
            json.dumps(_event(ln.base + k, ln.markets_of(k), ln.start(k)),
                       separators=(",", ":")).encode() for ln, k in sel) + b"]}"
        self.max_page_bytes = max(self.max_page_bytes, len(body))
        return httpx.Response(200, content=body,
                              headers={"content-type": "application/json"})


class _Pool:
    """A migrated production database as premap sees it (C6 + 249 columns,
    the receipts table, market_plane_rules), counting and digesting every
    us_premap write and DELETE in order; nothing else is kept."""

    def __init__(self):
        self.writes = 0
        self.deletes = 0
        self._h = hashlib.sha256()

    @staticmethod
    def _norm(v):
        return v.isoformat() if isinstance(v, _dtmod.datetime) else v

    async def execute(self, sql, *a):
        if "INSERT INTO us_premap" in sql or "DELETE FROM us_premap" in sql:
            if "INSERT" in sql:
                self.writes += 1
            else:
                self.deletes += 1
            body = " ".join(sql.split())
            self._h.update(repr((body[:40], [self._norm(v) for v in a])).encode())
        # a database round trip yields to the loop, as asyncpg's does: without
        # it the loop handle that resumed the sweep after its last venue read
        # (and, through that read's Future, the whole SDK response) stays
        # referenced until the next read -- a page production never holds
        await asyncio.sleep(0)
        return "DELETE 0"

    async def executemany(self, sql, rows):
        return None

    async def fetchval(self, sql, *a):
        if "premap-c6-columns" in sql:
            return len(premap._TEAM_COLUMNS)
        if "premap-249-columns" in sql:
            return len(premap._LISTING_COLUMNS)
        if "to_regclass" in sql:
            return True
        return None

    async def fetch(self, sql, *a):
        return []

    def transaction(self):
        pool = self

        class _T:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *e):
                return False
        return _T()

    acquire = transaction

    def digest(self):
        return self._h.hexdigest()


def _install(monkeypatch, venue, pool):
    from polymarket_us import PolymarketUS

    from sportsassets import venue_pace
    from sportsassets.market_plane import rules

    client = PolymarketUS()
    client._http = httpx.Client(transport=httpx.MockTransport(venue.handler))
    monkeypatch.setattr(premap.pmus, "_get_client", lambda: client)
    monkeypatch.setattr(premap, "get_pool", lambda: asyncio.sleep(0, result=pool))
    monkeypatch.setattr(premap, "_ensure_table", lambda p: asyncio.sleep(0))
    monkeypatch.setattr(premap, "LIST_PACING_S", 0.0)
    monkeypatch.setattr(venue_pace, "MIN_GAP_S", 0.0)
    # the sweep's own working set is measured, not the guard's reaction to it
    monkeypatch.setattr(premap, "memory_over_budget", lambda *a, **k: False)
    monkeypatch.setattr(premap, "_TEAM_COLS_STATE", {"present": None, "at": 0.0})
    monkeypatch.setattr(premap, "_LISTING_COLS_STATE", {"present": None, "at": 0.0})
    # the rules capture runs as in production (migration 312 applied), from
    # an empty process cache so every run measures the same thing
    monkeypatch.setattr(rules, "_SEEN", {})
    monkeypatch.setattr(rules, "_TABLE_STATE", {"present": True, "at": time.time()})
    monkeypatch.setattr(premap, "RULES_CAPTURE", {})
    return client


def _page_parse_peak(client, q) -> int:
    """tracemalloc peak of ONE events.list page read the way the sweep reads
    it (premap._paced_events_list: body bytes, response.text,
    response.json())."""
    gc.collect()
    tracemalloc.start()
    try:
        page = premap._paced_events_list(client, q)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del page
    return peak


def _traced(coro_factory):
    gc.collect()
    tracemalloc.start()
    try:
        out = asyncio.run(coro_factory())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return out, peak


#: the O(rows) state a sweep keeps BY DESIGN, per row written: the side key's
#: (identifier, side_norm) -> (market_slug, intent) record, measured at
#: ~0.36 KB/row, and market_plane.rules' process fingerprint cache, ~0.3 KB
#: per market (~0.15 KB/row at two sides a market). 0.6 KB/row in all (the
#: full sweep's live set at its summary measured 39.7 MB = 0.46 KB/row).
PER_ROW_BYTES = 600
#: logging, the tally, the receipts, asyncio and httpx bookkeeping
FIXED_SLACK = 12 * 2**20
MB = 1e6


def _now():
    return _dtmod.datetime.now(UTC)


def _warm(monkeypatch):
    """Import everything the sweep imports lazily, outside the measurement."""
    now = _now()
    v = _Venue(_Lane(3, 9, now + _dtmod.timedelta(hours=1),
                     now + _dtmod.timedelta(hours=2), 10**6))
    _install(monkeypatch, v, _Pool())
    asyncio.run(premap.refresh())


# ── (1) the bound, at the production size ────────────────────────────────

def test_the_full_sweep_holds_one_page_plus_its_keys(monkeypatch):
    """2,414 events / 43,044 markets / 86,088 rows over 26 pages, the
    01:16:44Z production sweep. Bound: one page's parse peak (the page being
    parsed; the previous one is gone by then) + PER_ROW_BYTES per row +
    FIXED_SLACK. Measured (7.7 MB pages, 44.8 MB page parse peak): before
    203.9 MB against the 109.1 MB bound, after 80.9 MB."""
    _warm(monkeypatch)
    now = _now()
    lane = _Lane(2414, 43044, now - _dtmod.timedelta(hours=11),
                 now + _dtmod.timedelta(hours=95), 0)
    venue = _Venue(lane)
    pool = _Pool()
    client = _install(monkeypatch, venue, pool)
    page_peak = _page_parse_peak(client, {
        "limit": premap.PAGE_LIMIT, "offset": 0,
        "startTimeMin": "2000-01-01T00:00:00Z",
        "startTimeMax": "2100-01-01T00:00:00Z"})
    summary, peak = _traced(premap.refresh)
    assert summary["rows"] == pool.writes == 86088
    assert summary["events"] == 2414 and summary["pages_walked"] == 26
    assert summary["catalogue_complete"] is True
    bound = page_peak + PER_ROW_BYTES * pool.writes + FIXED_SLACK
    print("full sweep: page %.1f MB JSON, page parse peak %.1f MB, sweep peak "
          "%.1f MB, bound %.1f MB" % (venue.max_page_bytes / MB, page_peak / MB,
                                      peak / MB, bound / MB))
    assert peak <= bound, (
        "the sweep held more than the page it parses plus its keys: peak "
        "%.1f MB > bound %.1f MB" % (peak / MB, bound / MB))


def test_two_overlapping_lanes_hold_one_page_each(monkeypatch):
    """The calendar lane (630 events / 44,496 rows, 01:23:48Z) and the fast
    lane (848 events / 13,518 rows) run AT THE SAME TIME in production --
    calendar_refresh is called after _full_loop releases _SWEEP_LOCK. Bound:
    one page parse peak per lane + PER_ROW_BYTES per row + FIXED_SLACK.
    Measured (page parse peaks 86.3 + 22.2 MB): before 179.0 MB against the
    156.0 MB bound, after 103.4 MB."""
    _warm(monkeypatch)
    now = _now()
    cal = _Lane(630, 22248, now + _dtmod.timedelta(hours=98),
                now + _dtmod.timedelta(days=300), 0)
    fast = _Lane(848, 6759, now - _dtmod.timedelta(hours=2),
                 now + _dtmod.timedelta(hours=13), 10**5)
    venue = _Venue(cal, fast)
    pool = _Pool()
    client = _install(monkeypatch, venue, pool)
    wide = {"limit": premap.PAGE_LIMIT, "offset": 0}
    cal_peak = _page_parse_peak(client, dict(
        wide, startTimeMin=_iso(now + _dtmod.timedelta(hours=97)),
        startTimeMax="2100-01-01T00:00:00Z"))
    fast_peak = _page_parse_peak(client, dict(
        wide, startTimeMin=_iso(now - _dtmod.timedelta(hours=3)),
        startTimeMax=_iso(now + _dtmod.timedelta(hours=14))))

    async def both():
        return await asyncio.gather(premap.calendar_refresh(), premap.fast_refresh())

    (c, f), peak = _traced(both)
    assert c["rows"] == 44496 and f["rows"] == 13518
    assert pool.writes == 44496 + 13518
    bound = cal_peak + fast_peak + PER_ROW_BYTES * pool.writes + FIXED_SLACK
    print("calendar beside fast: page parse peaks %.1f + %.1f MB, peak %.1f MB, "
          "bound %.1f MB" % (cal_peak / MB, fast_peak / MB, peak / MB, bound / MB))
    assert peak <= bound, "peak %.1f MB > bound %.1f MB" % (peak / MB, bound / MB)


def test_a_page_body_is_freed_when_the_sdk_returns_and_only_for_premap(monkeypatch):
    """httpx's BoundSyncStream points back at its response, so a read body
    (bytes + the SDK's decoded text) waits for a GC pass -- measured 1 to 12
    events.list bodies alive at once. A premap catalogue read's response is
    now freed by reference counting the moment the SDK returns the parsed
    page; a read by any other caller of the same shared client is left
    exactly as httpx leaves it."""
    import weakref

    from polymarket_us import PolymarketUS

    from sportsassets import venue_pace

    monkeypatch.setattr(premap, "LIST_PACING_S", 0.0)
    monkeypatch.setattr(venue_pace, "MIN_GAP_S", 0.0)
    body = json.dumps({"events": [{"slug": "e1", "markets": []}]}).encode()
    client = PolymarketUS()
    client._http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body,
                                       headers={"content-type": "application/json"})))
    seen = []
    real_send = client._http._send_single_request

    def spy(request):
        r = real_send(request)
        seen.append(weakref.ref(r))
        return r

    monkeypatch.setattr(client._http, "_send_single_request", spy)
    gc.collect()
    gc.disable()
    try:
        page = premap._paced_events_list(client, {"limit": 1})
        assert page == {"events": [{"slug": "e1", "markets": []}]}
        assert seen[-1]() is None, "the premap read's response outlived the SDK call"
        assert client.events.list({"limit": 1}) == page     # another caller
        assert seen[-1]() is not None, "a non-premap read must be left as httpx leaves it"
        premap._paced_events_list(client, {"limit": 1})
        assert seen[-1]() is None
    finally:
        gc.enable()
    assert client._http.event_hooks["response"].count(premap._body_release_hook()) == 1


# ── (2) which lanes overlap ──────────────────────────────────────────────

def test_the_full_and_fast_lanes_never_overlap_and_the_calendar_lane_is_the_one_that_can():
    """The only overlap is calendar beside fast (measured and bounded above).
    The full sweep and the fast lane stay mutually exclusive under
    _SWEEP_LOCK, and the calendar lane runs after the full sweep in the same
    loop iteration, so the two heavy lanes never overlap each other."""
    import inspect

    full = inspect.getsource(premap._full_loop)
    assert "async with _SWEEP_LOCK" in full
    # the full sweep and the calendar lane are sequential in one coroutine
    assert full.index("summary = await refresh()") < full.index("await calendar_refresh()")
    assert "async with _SWEEP_LOCK" in inspect.getsource(premap._fast_loop)


def test_a_full_sweep_and_a_fast_sweep_cannot_run_at_once(monkeypatch):
    """Both supervised loops driven for real (the sweeps stubbed to take
    time): many cycles of each, never two at once."""
    running, overlap, ran = set(), [], {"full": 0, "fast": 0}

    async def _slow(name):
        running.add(name)
        if len(running) > 1:
            overlap.append(set(running))
        ran[name] += 1
        await real_sleep(0.01)
        running.discard(name)
        return {"calendar_eligible": False}

    real_sleep = asyncio.sleep

    async def _short_sleep(s, *a, **k):
        return await real_sleep(min(s, 0.002), *a, **k)

    # a fresh lock: the module's asyncio.Lock binds to the first loop it
    # makes a waiter in, and this test makes one
    monkeypatch.setattr(premap, "_SWEEP_LOCK", asyncio.Lock())
    monkeypatch.setattr(premap, "refresh", lambda **k: _slow("full"))
    monkeypatch.setattr(premap, "fast_refresh", lambda: _slow("fast"))
    monkeypatch.setattr(premap, "_record_calendar_skipped",
                        lambda s: real_sleep(0))
    monkeypatch.setattr(premap.asyncio, "sleep", _short_sleep)

    async def scenario():
        t = [asyncio.create_task(premap._full_loop()),
             asyncio.create_task(premap._fast_loop())]
        await real_sleep(0.5)
        for x in t:
            x.cancel()
        await asyncio.gather(*t, return_exceptions=True)

    asyncio.run(scenario())
    assert ran["full"] >= 3 and ran["fast"] >= 3, ran
    assert overlap == []


# ── (3) the rows written are the pre-fix rows ────────────────────────────

FROZEN_NOW = _dtmod.datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


def _side(ident, desc, long=None, team=None):
    s = {"identifier": ident, "description": desc}
    if long is not None:
        s["long"] = long
    if team:
        s["team"] = team
    return s


def _mk(slug, q, sides, st, smt="baseball_team_full_game_winner"):
    return {"slug": slug, "question": q, "title": q, "closed": False,
            "active": True, "gameStartTime": _iso(st), "sportsMarketType": smt,
            "status": "MARKET_STATUS_OPEN", "description": "Rules for %s." % slug,
            "marketSides": sides}


def _fixture_events():
    """25 window events (paged 10 at a time: the probe, an overlap, several
    pages) carrying every side-key path, then calendar events beyond +96 h."""
    evs = []
    for i in range(25):
        st = FROZEN_NOW + _dtmod.timedelta(hours=-10 + i * 4)
        a, b = "Club A%d" % i, "Club B%d" % i
        day = st.strftime("%Y-%m-%d")
        ms = [
            _mk("aec-mlb-a%d-b%d-%s" % (i, i, day), "%s vs %s" % (a, b),
                [_side("aec-mlb-a%d-b%d-%s" % (i, i, day), a, True,
                       {"abbreviation": "a%d" % i, "name": a, "safeName": a,
                        "league": "mlb", "id": 100 + i}),
                 _side("aec-mlb-a%d-b%d-%s" % (i, i, day), b, False,
                       {"abbreviation": "b%d" % i, "name": b, "safeName": b,
                        "league": "mlb", "id": 200 + i})], st),
            _mk("asc-mlb-a%d-b%d-%s-1pt5" % (i, i, day),
                "Will the %s cover -1.5 vs the %s?" % (a, b),
                [_side("asc-mlb-a%d-b%d-%s-1pt5" % (i, i, day), "1.50", True),
                 _side("asc-mlb-a%d-b%d-%s-1pt5" % (i, i, day), "1.50", False)],
                st, "baseball_spread"),
            _mk("tsc-mlb-a%d-b%d-%s-8pt5" % (i, i, day), "O/U 8.5",
                [_side("tsc-mlb-a%d-b%d-%s-8pt5-over" % (i, i, day), "Over 8.5"),
                 _side("tsc-mlb-a%d-b%d-%s-8pt5-under" % (i, i, day), "Under 8.5")],
                st, "baseball_total"),
        ]
        if i % 6 == 2:
            # a qualified pair INSIDE one event, then a third side on the same
            # key with no marker (the guard reads the first row's body again)
            sl = "tsc-mlb-a%d-b%d-%s-x" % (i, i, day)
            ms.append(_mk(sl, "Total 2.5", [_side(sl, "Total 2.5", True),
                                            _side(sl, "total 2.5", False),
                                            _side(sl, "TOTAL 2.5")], st,
                          "baseball_total"))
        if i == 3:
            ms.append(_mk("shared-cross-1", "Draw?", [_side("shared-cross-1", "Draw", True)], st))
            ms.append(_mk("mkt-a", "Yes?", [_side("reused-id", "Yes", True)], st))
            ms.append(_mk("dup-mkt", "Dup?", [_side("dup-mkt-y", "Yes", True),
                                              _side("dup-mkt-n", "No", False)], st))
        if i == 17:
            # the same market, the other intent, an EARLIER event's body:
            # qualified across events (DELETE of the unqualified row)
            ms.append(_mk("shared-cross-1", "Draw?", [_side("shared-cross-1", "draw", False)], st))
            # another market reusing a key: refused, never overwritten
            ms.append(_mk("mkt-b", "Yes?", [_side("reused-id", "Yes", True)], st))
            # the same market listed again by another event: one row
            ms.append(_mk("dup-mkt", "Dup?", [_side("dup-mkt-y", "Yes", True),
                                              _side("dup-mkt-n", "No", False)], st))
        evs.append({"slug": "mlb-a%d-b%d-%s" % (i, i, day),
                    "title": "%s vs. %s" % (a, b), "category": "sports",
                    "startTime": _iso(st), "endDate": _iso(st + _dtmod.timedelta(hours=4)),
                    "active": True, "closed": False, "markets": ms,
                    "marketCounts": {"numMarkets": len(ms)}})
    for i in range(6):
        st = FROZEN_NOW + _dtmod.timedelta(days=6 + i * 9)
        sl = "mlb-champ-%d" % i
        evs.append({"slug": sl, "title": "Champion %d" % i, "category": "sports",
                    "startTime": _iso(st), "endDate": _iso(st + _dtmod.timedelta(days=40)),
                    "active": True, "closed": False,
                    "markets": [_mk("%s-t%d" % (sl, j), "Will Team %d win?" % j,
                                    [_side("%s-t%d" % (sl, j), "Team %d" % j, True)],
                                    st, "futures") for j in range(4)]})
    return evs


class _FixtureVenue:
    def __init__(self, evs):
        self.evs = sorted(evs, key=lambda e: e["startTime"])

    def handler(self, request):
        q = dict(request.url.params)
        lo, hi = q.get("startTimeMin", ""), q.get("startTimeMax", "~")
        sel = [e for e in self.evs if lo <= e["startTime"] <= hi]
        off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
        return httpx.Response(200, json={"events": sel[off:off + lim]})


def _frozen_clock(monkeypatch):
    class _Frozen(_dtmod.datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN_NOW if tz is not None else FROZEN_NOW.replace(tzinfo=None)

    monkeypatch.setattr(_dtmod, "datetime", _Frozen)


def _fixture_digest(monkeypatch) -> dict:
    _frozen_clock(monkeypatch)
    monkeypatch.setenv("PREMAP_RULES_CAPTURE", "off")
    pool = _Pool()
    venue = _FixtureVenue(_fixture_events())
    _install(monkeypatch, venue, pool)
    monkeypatch.setattr(premap, "PAGE_LIMIT", 10)
    out = {}

    async def lanes():
        for name, fn in (("full", premap.refresh),
                         ("calendar", premap.calendar_refresh),
                         ("fast", premap.fast_refresh)):
            s = await fn()
            out[name] = (s["rows"], s["side_keys"]["qualified_pairs"],
                         s["side_keys"]["refused_cross_market"])

    asyncio.run(lanes())
    out["writes"], out["deletes"], out["digest"] = (pool.writes, pool.deletes,
                                                    pool.digest())
    return out


#: produced by the PRE-FIX code (HEAD 378cf1f7, venue_catalogue / premap as
#: they stood) on this fixture, identical under PYTHONHASHSEED 1, 2 and 77:
#: per lane (rows, qualified pairs, refused keys), then every us_premap
#: INSERT and DELETE in order with its parameters (12 DELETEs: 11 qualified
#: pairs -- one of them across events -- and the full lane's prune)
PRE_FIX = {"full": (167, 9, 1), "calendar": (24, 0, 0), "fast": (37, 2, 0),
           "writes": 228, "deletes": 12,
           "digest": "3ba4eabd580fcdfcce19aa974f96dd1c8dec6372fc93124b626571f20e7941d2"}


def test_the_rows_written_are_the_pre_fix_rows(monkeypatch):
    got = _fixture_digest(monkeypatch)
    got = {k: tuple(v) if isinstance(v, list) else v for k, v in got.items()}
    assert got == PRE_FIX


def test_the_guard_matches_the_pre_fix_guard_on_every_sequence():
    """SideKeyGuard as it stood before (kept here verbatim as the oracle,
    bodies held for the refresh) against the bounded one with end_event()
    between events, on randomized collisions inside and across events."""
    import random

    class OldGuard:
        def __init__(self):
            self._held, self.qualified, self.refused, self.examples = {}, 0, 0, []

        _marker = staticmethod(vc.SideKeyGuard._marker)

        def admit(self, row):
            key = (row.get("identifier"), row.get("side_norm"))
            prior = self._held.get(key)
            if prior is None:
                self._held[key] = {"market_slug": row.get("market_slug"),
                                   "intent": row.get("intent"), "row": row}
                return row, None
            if prior["market_slug"] != row.get("market_slug"):
                self.refused += 1
                if len(self.examples) < vc.MAX_EXAMPLES:
                    self.examples.append({"case": vc.D_SIDE_KEY_HELD_BY_ANOTHER_MARKET,
                                          "identifier": key[0], "side_norm": key[1],
                                          "held_by": prior["market_slug"],
                                          "refused": row.get("market_slug")})
                return None, None
            if prior["intent"] == row.get("intent"):
                return None, None
            first = dict(prior["row"])
            first["side_norm"] = "%s [%s]" % (key[1], self._marker(first.get("intent")))
            second = dict(row)
            second["side_norm"] = "%s [%s]" % (key[1], self._marker(row.get("intent")))
            if first["side_norm"] == second["side_norm"]:
                self.refused += 1
                return None, None
            self.qualified += 1
            self._held[(key[0], first["side_norm"])] = {
                "market_slug": first.get("market_slug"),
                "intent": first.get("intent"), "row": first}
            self._held[(key[0], second["side_norm"])] = {
                "market_slug": second.get("market_slug"),
                "intent": second.get("intent"), "row": second}
            if len(self.examples) < vc.MAX_EXAMPLES:
                self.examples.append({"case": vc.K_SIDE_KEY_QUALIFIED,
                                      "identifier": key[0], "side_norm": key[1],
                                      "market_slug": row.get("market_slug")})
            return second, {"rewrite": first, "delete_side_norm": key[1]}

    def write_event(guard, rows, sink):
        # premap._write_event's use of the guard, verbatim in effect
        to_write = []
        for r0 in rows:
            r1, fix = guard.admit(r0)
            if fix is not None:
                for j, w in enumerate(to_write):
                    if (w.get("identifier"), w.get("side_norm"), w.get("market_slug")) == (
                            fix["rewrite"].get("identifier"), fix["delete_side_norm"],
                            fix["rewrite"].get("market_slug")):
                        to_write[j] = fix["rewrite"]
                sink.append(("DELETE", fix["rewrite"].get("identifier"),
                             fix["delete_side_norm"]))
            if r1 is not None:
                to_write.append(r1)
        sink.extend(("WRITE", tuple(sorted(r.items()))) for r in to_write)

    intents = ["ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT", None]
    for seed in range(300):
        rnd = random.Random(seed)
        old, new = OldGuard(), vc.SideKeyGuard()
        a, b = [], []
        for _event_no in range(rnd.randint(1, 6)):
            rows = [{"identifier": "id%d" % rnd.randint(0, 3),
                     "side_norm": rnd.choice(["yes", "no", "over 2 5"]),
                     "market_slug": "m%d" % rnd.randint(0, 2),
                     "intent": rnd.choice(intents), "n": rnd.random()}
                    for _ in range(rnd.randint(1, 8))]
            write_event(old, [dict(r) for r in rows], a)
            write_event(new, [dict(r) for r in rows], b)
            new.end_event()
        assert a == b, seed
        assert (old.qualified, old.refused, old.examples) == \
            (new.receipt()["qualified_pairs"], new.receipt()["refused_cross_market"],
             new.receipt()["examples"]), seed
    assert new._rows == {}, "no row body outlives its event"
