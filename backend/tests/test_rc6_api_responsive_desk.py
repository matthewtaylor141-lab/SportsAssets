"""RC6 api-responsive: the desk sweep's page parse runs in a child process.

PRODUCTION. pmus.list_desk_events runs every ~2.5 min in an API worker
thread: 15 venue pages of 100 events with full boards (pages=15
events=1430/1430 markets~79,000, 28-73 s per sweep; render-ops logs
2026-10-09 00:00-01:25Z). A thread shares the interpreter lock with the event
loop: each page's json.loads held it in C for the whole page and the slim
build competed for it in Python. Six of the twenty API loop stalls in the
watchdog's persisted ring (research-sql rc6_api-responsive_loop_stalls.sql,
2026-10-09) fell inside sweeps (2.3-3.3 s in full; the watchdog itself locked
out for up to 0.81 s, its overrun), the sweep thread in `_slim` / `re.sub` /
the response decode.

PINNED HERE:
  * the request is the SDK's own (URL, query encoding, headers, the same
    httpx client -- so the transport gate still paces and counts it) and its
    failures are the SDK's own typed errors (a 429 is still the circuit's);
  * the page is parsed and slimmed by desk_board_parse in a child process
    (pid != ours), and the rows are exactly what the pre-RC6 `_slim` built;
  * a malformed body raises the same ValueError the SDK's response.json()
    raised; a child that cannot run is replaced by the same parse here, and
    the receipt names it;
  * a full production-size sweep leaves the event loop responsive.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time

import httpx
import pytest

from sportsassets import desk_board_parse as DBP
from sportsassets import pmus

#: the loop bound for the sweep: a C-level hold of the whole page parse was
#: 0.3-0.81 s in production (watchdog overrun) for what takes ~0.06-0.15 s
#: on this class of machine; 0.05 s here is under 0.4 s there.
SWEEP_LOOP_GAP_BOUND_S = 0.05


# ── the pre-RC6 slim build, verbatim (pmus._desk_sweep._slim at 412c4962) ──

def _old_px(v):
    try:
        f = float(v)
        return f if 0 < f < 1 else None
    except (TypeError, ValueError):
        return None


def _old_slim(events: dict, got) -> None:
    for ev in got:
        eslug = ev.get("slug") or ev.get("eventSlug") or ""
        if not eslug:
            continue
        e = events.setdefault(eslug, {
            "slug": eslug,
            "title": pmus._clean_title(ev.get("title")) or eslug,
            "league": (eslug.split("-", 1)[0] or "").lower(),
            "start": ev.get("startTime") or ev.get("startDate"),
            "volume_usd": pmus._ev_volume_usd(ev),
            "close_time": (ev.get("endTime") or ev.get("endDate")
                           or None),
            "markets": []})
        for m in ev.get("markets") or []:
            if m.get("closed"):
                continue
            title = (pmus._clean_title(m.get("question")
                                       or m.get("title"))
                     or m.get("slug") or "")
            sides = [x for x in (m.get("marketSides") or [])
                     if isinstance(x, dict)]
            if sides:
                for x in sides:
                    ident = x.get("identifier")
                    desc = x.get("description")
                    if not ident or not desc:
                        continue
                    e["markets"].append({
                        "us_slug": ident,
                        "kind": (ident.split("-", 1)[0]
                                 or "").lower(),
                        "label": f"{title} — {desc}",
                        "price": _old_px(x.get("price")),
                        "sports_market_type_v2":
                            m.get("sportsMarketTypeV2"),
                        "sports_market_type":
                            m.get("sportsMarketType"),
                        "team": (x.get("team") or {}).get("name")
                                if isinstance(x.get("team"), dict)
                                else x.get("team"),
                        "team_id": x.get("teamId")})
            elif m.get("slug"):
                px = next((p for p in (_old_px(m.get(k)) for k in
                           ("bestAsk", "best_ask", "price"))
                           if p is not None), None)
                e["markets"].append({
                    "us_slug": m["slug"],
                    "kind": (m["slug"].split("-", 1)[0]
                             or "").lower(),
                    "label": (f"{title} — {m['outcome']}"
                              if m.get("outcome") else title),
                    "price": px,
                    "sports_market_type_v2":
                        m.get("sportsMarketTypeV2"),
                    "sports_market_type": m.get("sportsMarketType"),
                    "team": None, "team_id": None})


# ── a venue board ────────────────────────────────────────────────────────

def _event(rng, i, n_markets=55):
    lg = rng.choice(["mlb", "nfl", "epl", "nba", "atp", "cfb"])
    slug = "%s-t%d-t%d-2026-10-%02d" % (lg, i, i + 1, 10 + i % 9)
    ev = {"slug": slug if i % 50 else None,
          "eventSlug": slug if i % 50 == 0 else None,
          "id": 100_000 + i,
          "title": rng.choice(["Spread: Team %d vs Team %d (-2.5)" % (i, i),
                               "Team %d vs. Team %d: O/U 3.5" % (i, i),
                               "Team %d vs Team %d - More Markets" % (i, i),
                               "Canadian Open: A%d vs B%d" % (i, i), ""]),
          "startTime": "2026-10-10T18:00:00Z", "endDate": None,
          "volume": rng.choice([None, "", "n/a", rng.random() * 1e6,
                                str(rng.random())]),
          "liquidity": rng.random(), "markets": []}
    if i % 97 == 0:
        ev["slug"] = ev["eventSlug"] = None        # keyed by id only
    for m in range(n_markets):
        mk = {"question": "Team %d vs Team %d: Q%d (x)" % (i, i, m),
              "closed": (m % 23 == 0),
              "sportsMarketTypeV2": rng.choice([None, "MONEYLINE",
                                                "SPREAD", "TOTAL"]),
              "sportsMarketType": rng.choice([None, "x_y"]),
              "description": "lorem ipsum dolor sit amet " * 8}
        if m % 3:
            mk["marketSides"] = [
                {"identifier": "aec-%s-%d-%d" % (slug, m, s),
                 "description": rng.choice(["Home", "Away", ""]),
                 "price": rng.choice([str(rng.random()), "1.5", None]),
                 "team": rng.choice([{"name": "Team %d" % s}, "T", None]),
                 "teamId": s} for s in range(2)]
        else:
            mk["slug"] = rng.choice(["tsc-%s-%d" % (slug, m), None])
            mk["bestAsk"] = rng.choice([None, rng.random(), "0"])
            mk["price"] = rng.random()
            mk["outcome"] = rng.choice([None, "Over"])
        ev["markets"].append(mk)
    return ev


def _board(n_events=1430, seed=21, n_markets=55):
    rng = random.Random(seed)
    evs = [_event(rng, i, n_markets) for i in range(n_events)]
    return evs, [json.dumps(e) for e in evs]


class _Venue:
    """GET /v1/events by offset/limit, answered from pre-serialized events
    (a page body is a join, so the venue costs the sweep nothing)."""

    def __init__(self, evs_json, *, status=200, body=None):
        self.evs_json, self.status, self.body = evs_json, status, body
        self.requests: list = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, headers={"Retry-After": "7"},
                                  json={"message": "slow down"})
        if self.body is not None:
            return httpx.Response(200, content=self.body)
        q = dict(request.url.params)
        off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
        page = self.evs_json[off:off + lim]
        return httpx.Response(200, content=("{\"events\": [%s]}"
                                            % ", ".join(page)).encode())


def _client(handler):
    from polymarket_us import PolymarketUS
    c = PolymarketUS(max_retries=0)
    c._http = httpx.Client(transport=httpx.MockTransport(handler))
    return c


# ═════════════════════════════════════════════════════════════════════

def test_the_rows_are_exactly_the_pre_rc6_slim_rows():
    evs, _ = _board(n_events=300, seed=3)
    old: dict = {}
    _old_slim(old, evs)
    rows = DBP.page_rows({"events": evs + [None, "x", 7]})
    new: dict = {}
    for st in rows["stubs"]:
        s = rows["slims"][st["_i"]]
        if s is None:
            continue
        e = new.setdefault(s["slug"], dict(s, markets=[]))
        e["markets"].extend(s["markets"])
    assert new == old
    assert rows["has_events"] is True
    # the walk's keys, and nothing else of the raw event, travel back
    assert all(set(st) <= {"slug", "eventSlug", "id", "_i"}
               for st in rows["stubs"])
    assert len(rows["stubs"]) == 300            # non-dicts are not events


def test_the_raw_read_is_the_sdks_own_request():
    evs, js = _board(n_events=5, n_markets=2)
    seen_sdk, seen_raw = _Venue(js), _Venue(js)
    q = {"limit": 100, "offset": 95, "active": True, "closed": False,
         "startTimeMin": "2026-10-09T00:00:00Z"}
    _client(seen_sdk).events.list(q)
    body = pmus._desk_raw_events_page(_client(seen_raw), q)
    a, b = seen_sdk.requests[0], seen_raw.requests[0]
    assert (a.method, str(a.url)) == (b.method, str(b.url))
    strip = ("poly-correlation-id",)
    assert {k: v for k, v in a.headers.items() if k not in strip} == \
        {k: v for k, v in b.headers.items() if k not in strip}
    assert json.loads(body) == {"events": evs[95:195]}


def test_a_429_is_the_sdks_typed_error_and_trips_the_circuit(monkeypatch):
    from polymarket_us.errors import RateLimitError
    with pytest.raises(RateLimitError) as sdk:
        _client(_Venue([], status=429)).events.list({"limit": 100})
    with pytest.raises(RateLimitError) as raw:
        pmus._desk_raw_events_page(_client(_Venue([], status=429)),
                                   {"limit": 100})
    penal = []
    from sportsassets import venue_pace as VP
    monkeypatch.setattr(VP, "penalize_observed",
                        lambda **kw: penal.append(kw))
    d_sdk = pmus._desk_rate_limited(sdk.value)
    d_raw = pmus._desk_rate_limited(raw.value)
    assert d_sdk and d_raw and d_sdk["http_status"] == d_raw["http_status"] \
        == 429
    assert d_raw["retry_after_s"] == d_sdk["retry_after_s"] == 7.0
    assert len(penal) == 2


def test_a_malformed_body_raises_as_the_sdk_did():
    with pytest.raises(ValueError) as sdk:
        _client(_Venue([], body=b"{not json")).events.list({"limit": 1})
    with pytest.raises(ValueError) as here:
        pmus._desk_events_page(_client(_Venue([], body=b"{not json")),
                               {"limit": 1})
    assert type(here.value) is type(sdk.value)


def test_the_page_is_parsed_in_a_child_process(monkeypatch):
    monkeypatch.delenv("PMUS_DESK_PARSE_CHILD", raising=False)
    evs, js = _board(n_events=100, seed=5)
    body = ("{\"events\": [%s]}" % ", ".join(js)).encode()
    try:
        rows, where = pmus._desk_parse_body(body)
        assert where == pmus.DESK_PARSE_CHILD
        pool = pmus._DESK_PARSE["pool"]
        pids = [p.pid for p in pool._processes.values()]
        assert pids and os.getpid() not in pids
        assert rows == DBP.page_rows({"events": evs})
        assert pmus.desk_parse_status()["child_running"] is True
    finally:
        pmus.shutdown_desk_parse()


def test_a_child_that_cannot_run_is_replaced_and_named(monkeypatch):
    evs, js = _board(n_events=10, seed=6)
    body = ("{\"events\": [%s]}" % ", ".join(js)).encode()
    monkeypatch.setenv("PMUS_DESK_PARSE_CHILD", "off")
    pmus.shutdown_desk_parse()
    rows, where = pmus._desk_parse_body(body)
    assert where == "IN_PROCESS:DISABLED_BY_CONFIGURATION"
    assert rows == DBP.page_rows({"events": evs})

    class _Broken:
        def submit(self, *a, **k):
            raise RuntimeError("no child today")

        def shutdown(self, **k):
            pass
    monkeypatch.delenv("PMUS_DESK_PARSE_CHILD", raising=False)
    monkeypatch.setitem(pmus._DESK_PARSE, "pool", _Broken())
    monkeypatch.setitem(pmus._DESK_PARSE, "broken_until", 0.0)
    rows, where = pmus._desk_parse_body(body)
    assert where == "IN_PROCESS:RuntimeError"
    assert rows == DBP.page_rows({"events": evs})
    assert pmus._DESK_PARSE["pool"] is None
    assert pmus._DESK_PARSE["broken_until"] > time.time()
    monkeypatch.setitem(pmus._DESK_PARSE, "broken_until", 0.0)


def _sweep_with(monkeypatch, js):
    from sportsassets import venue_pace as VP
    venue = _Venue(js)
    client = _client(venue)
    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    monkeypatch.setattr(pmus, "_desk_cache", {
        "ts": 0.0, "events": [], "blind_at": 0.0, "warned_at": 0.0})
    monkeypatch.setattr(pmus, "_desk_sweep_lock", threading.Lock())
    monkeypatch.setattr(VP, "pace", lambda *a, **k: 0.0)
    return venue


def test_a_production_size_sweep_leaves_the_loop_responsive(monkeypatch):
    """1,430 events x 55 markets in 15 pages, through the real SDK client:
    the longest event-loop gap while the sweep runs (in its worker thread,
    as the API runs it) stays under the bound, and the board is the
    in-process parse's board."""
    monkeypatch.delenv("PMUS_DESK_PARSE_CHILD", raising=False)
    evs, js = _board()
    _sweep_with(monkeypatch, js)

    async def go():
        gaps = [0.0]
        done = asyncio.Event()

        async def tick():
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.002)
                now = time.perf_counter()
                gaps[0] = max(gaps[0], now - last)
                last = now
        t = asyncio.create_task(tick())
        await asyncio.sleep(0.01)
        board = await asyncio.to_thread(pmus.list_desk_events)
        done.set()
        await t
        return board, gaps[0]
    try:
        # the child is started once per process, like the first sweep after
        # a boot; the measured sweep is a warm one
        pmus._desk_parse_body(b"{}")
        board, gap = asyncio.run(go())
        receipt = pmus._desk_cache.get("receipt") or {}
    finally:
        pmus.shutdown_desk_parse()
    assert receipt.get("pages", 0) >= 15
    assert (receipt.get("parse") or {}).get("pages_in_child_process", 0) \
        >= 15
    old: dict = {}
    _old_slim(old, evs)
    want = [e for e in old.values() if e["markets"]]
    assert sorted(board, key=lambda e: e["slug"]) == \
        sorted(want, key=lambda e: e["slug"])
    assert gap < SWEEP_LOOP_GAP_BOUND_S, "the loop was held %.3f s" % gap
