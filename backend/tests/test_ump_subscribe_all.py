"""ONE SUBSCRIBE-ALL MARKET-DATA STREAM, BACKGROUND TOKENS, BOUNDED REFDATA
(completion readiness, venue guidance 2026-10-07).

  §1  defaults: subscription_mode SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST, one
      market-data stream, the explicit fallback capped under the firm's
      pooled 20-stream budget (the package's own assertion included)
  §2  the wire, against a real in-process gRPC server: exactly ONE subscribe
      per connection carrying symbols=[] and depth 10; no idle without
      wanted symbols; subscribe() sends nothing; registry symbols become
      CURRENT, every other instrument is counted and never held; an explicit
      transport still never sends the empty list
  §3  the manager holds exactly one stream whatever the assignments say,
      drops unassigned symbols locally, refuses a second stream
  §4  client-to-server pacing under the per-firm 100/s cap
  §5  the token is refreshed in the background; a healthy stream is never
      cycled to re-authenticate
  §6  refdata: priority first, then one bounded full pull (COMPLETE only on
      the venue's end of pagination, TRUNCATED otherwise), never repolled
      inside the refresh window, new listings by batched symbols; the
      instruments body and the participant header
  §7  the worker's refdata slot against Postgres: registry rows stored,
      a COMPLETE pull proves pending-at-start contracts unlisted, the
      durable receipt read back
"""
from __future__ import annotations

import asyncio
import os
import threading
import time

import grpc
import pytest

from sportsassets import institutional_stream as IS
from sportsassets.market_plane import refdata_universe as RU
from sportsassets.market_plane import registry as R
from sportsassets.market_plane.sharded_stream import Manager
from sportsassets.market_plane.token_keeper import TokenKeeper
from sportsassets.workers import universal_market_plane as W

# the in-process gRPC fake venue and helpers of the transport's own tests
# (tests/ is not a package: loaded by path, not duplicated)
import importlib.util as _ilu  # noqa: E402
import pathlib as _pl  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_grpc_transport_fixtures",
    _pl.Path(__file__).with_name("test_institutional_md_grpc_transport.py"))
_fx = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_fx)
GOOD, REC, SYM, Wait = _fx.GOOD, _fx.REC, _fx.SYM, _fx.Wait
book, heartbeat, in_thread, until = (_fx.book, _fx.heartbeat, _fx.in_thread,
                                     _fx.until)
venue = _fx.venue

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
OTHER = "aec-nba-lal-bos-2026-10-07"
NOT_SPORTS = "pol-us-election-2028"


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    yield
    IS.reset()


# ── §1 defaults ──────────────────────────────────────────────────────

def test_universal_market_plane_defaults_to_single_subscribe_all_stream():
    # the completion package's own assertion, verbatim in substance
    assert W.subscribe_all_enabled({}) is True
    assert W.caps({})[0] == 1
    plan = W.subscription_plan({})
    assert plan["mode"] == "SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST"
    assert plan["streams"] == 1 and plan["assign_max_streams"] == 1
    assert plan["symbols_on_the_wire"] == []
    assert plan["firm_stream_budget"] == 20


def test_explicit_mode_is_only_a_capped_fallback():
    off = W.subscription_plan({"UMP_SUBSCRIBE_ALL": "off",
                               "UMP_MAX_STREAMS": "20"})
    assert off["mode"] == "EXPLICIT_SYMBOL_SHARDS"
    assert off["streams"] == W.EXPLICIT_MAX_STREAMS_CEILING < 20
    books = W.subscription_plan({"UMP_SUBSCRIBE_ALL_MAX_BOOKS": "999999"})
    assert books["books_capacity"] == W.SUBSCRIBE_ALL_MAX_BOOKS_CEILING


# ── §2 the wire ──────────────────────────────────────────────────────

def _books(symbols=(SYM,)):
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    for s in symbols:
        b.set_instrument(s, dict(REC, symbol=s))
    b.want(list(symbols))
    return b


def _all_transport(books, venue, token_fn=lambda: GOOD):
    return IS.GrpcBidiTransport(
        books, token_fn, target=venue.target, subscribe_all=True,
        channel_factory=lambda target: grpc.insecure_channel(target),
        sleep=lambda s: None)


def test_one_empty_subscribe_with_depth_and_local_filtering(venue):
    b = _books()
    gate = Wait()
    venue.scripts = [[book(symbol=NOT_SPORTS), book(symbol=OTHER), book(),
                      heartbeat(), gate]]
    t = _all_transport(b, venue)
    assert t.subscription_mode == IS.MODE_SUBSCRIBE_ALL
    th, _ = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"])
    t.subscribe([OTHER, "anything-else"])           # nothing is sent
    until(lambda: len(venue.received) >= 1)
    time.sleep(0.3)
    gate.set()
    th.join(10)
    assert len(venue.received) == 1
    first = venue.received[0]
    assert first.WhichOneof("command") == "subscribe"
    assert list(first.subscribe.symbols) == []
    assert first.depth == IS.DEPTH == 10
    # the two non-registry instruments were counted, never held
    assert t.filtered_updates == 2
    assert b.wanted() == [SYM]
    assert b.digest()["filtered_updates"] == 2
    assert b.digest()["stray_updates"] == 0
    assert b.current(OTHER)["refusal"] == IS.R_NOT_REQUESTED


def test_subscribe_all_connects_without_any_wanted_symbol(venue):
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    gate = Wait()
    venue.scripts = [[book(symbol=NOT_SPORTS), gate]]
    t = _all_transport(b, venue)
    th, _ = in_thread(t.run_once)
    until(lambda: t.filtered_updates == 1)
    gate.set()
    th.join(10)
    assert venue.connections == 1
    assert list(venue.received[0].subscribe.symbols) == []


def test_every_reconnect_sends_exactly_one_empty_subscribe(venue):
    b = _books()
    venue.scripts = [[book()], [book()]]
    t = _all_transport(b, venue)
    assert t.run_once() == "ended"
    assert t.run_once() == "ended"
    # No wait: the fake venue reads a connection's opening command BEFORE it
    # streams (tests/test_institutional_md_grpc_transport.FakeVenue), so by
    # the time each run_once has seen its book that connection's subscribe is
    # on record. This used to poll `until(len(received) >= 2)` for 10 s of
    # wall clock, and on a loaded host the second subscribe was never going
    # to arrive (the call had ended before the server read it).
    assert [r.WhichOneof("command") for r in venue.received] == \
        ["subscribe", "subscribe"]
    assert [list(r.subscribe.symbols) for r in venue.received] == [[], []]


def test_the_empty_list_is_refused_everywhere_else():
    from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2 as pb2
    empty = pb2.BiDirectionalStreamMarketDataRequest(
        subscribe=pb2.SubscribeCommand(symbols=[]))
    assert empty.WhichOneof("command") == "subscribe"
    with pytest.raises(IS.OutboundRefused):
        IS._outbound(empty)
    assert IS._outbound(empty, allow_subscribe_all=True) is empty
    blank = pb2.BiDirectionalStreamMarketDataRequest(
        subscribe=pb2.SubscribeCommand(symbols=["ok", ""]))
    with pytest.raises(IS.OutboundRefused):
        IS._outbound(blank, allow_subscribe_all=True)
    # an explicit transport never builds the empty list ...
    t = IS.GrpcBidiTransport(_books(), lambda: GOOD, sleep=lambda s: None)
    with pytest.raises(IS.OutboundRefused):
        t._subscribe_request([], first=True)
    # ... and a subscribe-all transport builds it only as a first request
    ta = IS.GrpcBidiTransport(_books(), lambda: GOOD, sleep=lambda s: None,
                              subscribe_all=True)
    assert list(ta._subscribe_request([], first=True).subscribe.symbols) == []
    with pytest.raises(IS.OutboundRefused):
        ta._subscribe_request([SYM], first=False)
    # a queued empty subscribe is refused even on a subscribe-all stream
    gen = ta._requests(ta._subscribe_request([], first=True))
    next(gen)
    ta._q.put(empty)
    with pytest.raises(IS.OutboundRefused):
        next(gen)
    ta.stop()


def test_an_explicit_transport_still_idles_without_symbols():
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "x")
    t = IS.GrpcBidiTransport(b, lambda: GOOD, sleep=lambda s: None)
    assert t.run_once() == "idle"
    assert t.subscription_mode == IS.MODE_EXPLICIT


# ── §3 the manager ───────────────────────────────────────────────────

class _T:
    def __init__(self, b, tok, **kw):
        self.b, self.kw, self.subs, self.starts = b, kw, [], 0
        self.subscription_mode = (IS.MODE_SUBSCRIBE_ALL if kw.get(
            "subscribe_all") else IS.MODE_EXPLICIT)

    def start(self):
        self.starts += 1

    def subscribe(self, x):
        self.subs.extend(x)

    def stop(self):
        pass


def test_the_manager_holds_exactly_one_stream_in_subscribe_all():
    made = []
    m = Manager(token_fn=lambda: "x", max_per_stream=100, max_streams=20,
                subscribe_all=True, clock=lambda: 1.0,
                transport_factory=lambda b, tok, **kw: made.append(
                    _T(b, tok, **kw)) or made[-1])
    assert m.max_streams == 1
    got = m.sync({"a": 0, "b": 3, "c": 7}, {})
    assert got["ok"] and got["shards"] == 1 and got["symbols"] == 3
    assert got["subscription_mode"] == "SUBSCRIBE_ALL_EMPTY_SYMBOL_LIST"
    assert len(made) == 1 and made[0].kw == {"subscribe_all": True}
    assert m.stream_count() == 1
    assert set(m.shards[0]["books"].wanted()) == {"a", "b", "c"}
    with pytest.raises(RuntimeError, match="STREAM_LIMIT_REACHED"):
        m._new_shard(1)
    # unassigned symbols are dropped locally; still one stream
    got = m.sync({"a": 0, "d": 9}, {})
    assert got["removed"] == 2 and m.subscribed() == ["a", "d"]
    assert set(m.shards[0]["books"].wanted()) == {"a", "d"}
    assert len(made) == 1 and made[0].starts == 1
    # discovery counts what the stream carried outside the books
    bk = m.shards[0]["books"]

    class U:
        price_scale, quantity_scale = 1000, 100
    bk.on_filtered("new-listing", U())
    d = m.discovery(limit=5)
    assert d["instruments_seen_outside_books"] == 1
    assert d["sample"] == ["new-listing"]
    assert bk.seen["new-listing"][1:] == (1000, 100)


def test_explicit_manager_unchanged():
    made = []
    m = Manager(token_fn=lambda: "x", max_per_stream=2, max_streams=2,
                clock=lambda: 1.0,
                transport_factory=lambda b, tok: made.append(_T(b, tok))
                or made[-1])
    assert m.sync({"a": 0, "b": 1})["ok"] and m.stream_count() == 2
    assert m.subscription_mode == "EXPLICIT_SYMBOL_SHARDS"


# ── §4 pacing ────────────────────────────────────────────────────────

def test_outbound_messages_are_paced_under_the_firm_cap(monkeypatch):
    monkeypatch.setattr(IS, "OUTBOUND_RATE_PER_S", 100.0)
    monkeypatch.setattr(IS, "OUTBOUND_BURST", 5)
    t = IS.GrpcBidiTransport(_books(), lambda: GOOD, sleep=lambda s: None)
    t._out_tokens = 5.0
    gen = t._requests(t._subscribe_request([SYM], first=True))
    next(gen)
    for _ in range(25):
        t._q.put(t._keepalive_request())
    t0 = time.monotonic()
    for _ in range(25):
        assert next(gen).WhichOneof("command") == "keepalive"
    assert time.monotonic() - t0 >= 0.15        # 20 beyond the burst at 100/s
    assert t.outbound["paced"] >= 15
    assert t.outbound["sent"] == 26 and t.outbound["keepalive"] == 25
    t.stop()
    assert IS.OUTBOUND_RATE_PER_S <= 100.0


def test_the_default_pace_is_a_tenth_of_the_firm_cap():
    import inspect
    src = inspect.getsource(IS)
    assert "OUTBOUND_RATE_PER_S = 10.0" in src
    assert "OUTBOUND_BURST = 10" in src


# ── §5 tokens ────────────────────────────────────────────────────────

class _Client:
    def __init__(self):
        self.tok, self.mints, self.invalidated = GOOD, 0, 0

    def token(self):
        self.mints += 1
        return self.tok

    def invalidate_token(self):
        self.invalidated += 1


def test_the_keeper_refreshes_in_the_background_and_digests_no_token():
    c = _Client()
    k = TokenKeeper(c, refresh_every_s=0.05)
    k.start()
    until(lambda: k.refreshes >= 3, timeout=5)
    k.stop()
    d = k.digest()
    assert d["mode"] == "BACKGROUND_REFRESH_NO_STREAM_CYCLE"
    assert d["stream_cycled_for_reauth"] is False
    assert GOOD not in repr(d)
    k.invalidate()
    assert c.invalidated == 1


def test_a_refreshed_token_never_cycles_a_healthy_stream(venue):
    c = _Client()
    venue.good_tokens = {GOOD, "tok-NOT-REAL-next"}
    k = TokenKeeper(c, refresh_every_s=0.02)
    b = _books()
    gate = Wait()
    venue.scripts = [[book(), gate]]
    t = _all_transport(b, venue, token_fn=k.token)
    th, _ = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"])
    k.start()
    c.tok = "tok-NOT-REAL-next"                  # the venue's next token
    until(lambda: k.refreshes >= 5, timeout=5)
    assert venue.connections == 1 and b.current(SYM)["ok"]
    gate.set()
    th.join(10)
    k.stop()
    assert venue.auth == [GOOD]                  # read once, at connect


# ── §6 refdata planner ───────────────────────────────────────────────

def _page(symbols, token=None, eof=False, status=200):
    return {"status": status, "body": {
        "instruments": [{"symbol": s, "priceScale": "1000",
                         "fractionalQtyScale": "100"} for s in symbols],
        "nextPageToken": token or "", "eof": eof}}


def test_priority_first_then_one_full_pull_complete_on_eof():
    p = RU.Planner(calls_per_minute=5)
    a = p.next_action(now=0.0, priority_pending=["held-1"])
    assert a["kind"] == RU.A_PRIORITY and a["symbols"] == ["held-1"]
    out = p.record(a, _page([]), now=0.0)
    assert out["unlisted"] == ["held-1"]        # a 200 that omitted it
    assert p.next_action(now=5.0) is None       # 5/min: 12 s apart
    a = p.next_action(now=12.0, priority_pending=[])
    assert a["kind"] == RU.A_PAGE and a["token"] is None and a.get("start")
    assert p.body_for(a) == {"pageSize": 1000}
    out = p.record(a, _page(["x1", "x2"], token="t2"), now=12.0)
    assert out["finished"] is None
    a = p.next_action(now=24.0)
    assert a["kind"] == RU.A_PAGE and a["token"] == "t2"
    assert p.body_for(a) == {"pageSize": 1000, "pageToken": "t2"}
    out = p.record(a, _page(["x3"], eof=True), now=24.0)
    fin = out["finished"]
    assert fin["status"] == RU.COMPLETE and fin["pages"] == 2
    assert fin["instruments"] == 3 and fin["_seen"] == {"x1", "x2", "x3"}
    # never repolled inside the refresh window; new listings by symbols
    a = p.next_action(now=36.0, other_pending=["new-1", "new-2"])
    assert a["kind"] == RU.A_NEW and a["symbols"] == ["new-1", "new-2"]
    assert p.body_for(a) == {"pageSize": 1000, "symbols": ["new-1", "new-2"]}


def test_a_pull_out_of_budget_is_truncated_never_complete():
    p = RU.Planner(calls_per_minute=6, max_pages=2)
    a = p.next_action(now=0.0)
    p.record(a, _page(["a"], token="t"), now=0.0)
    a = p.next_action(now=10.0)
    fin = p.record(a, _page(["b"], token="t3"), now=10.0)["finished"]
    assert fin["status"] == RU.TRUNCATED and fin["why"] == "MAX_PAGES"
    assert p.last_complete_at is None and p.full_due(20.0)
    p2 = RU.Planner(calls_per_minute=6, max_pull_s=30)
    a = p2.next_action(now=0.0)
    p2.record(a, _page(["a"], token="t"), now=0.0)
    a = p2.next_action(now=40.0)
    assert p2.record(a, _page(["b"], token="t"), now=40.0)["finished"][
        "why"] == "MAX_PULL_S"
    p3 = RU.Planner(calls_per_minute=6)
    for i in range(RU.MAX_PAGE_FAILURES):
        a = p3.next_action(now=10.0 * i)
        got = p3.record(a, {"status": 500, "body": None}, now=10.0 * i)
    assert got["finished"]["status"] == RU.TRUNCATED
    assert got["finished"]["why"].startswith("PAGE_UNREADABLE")


def test_a_restart_inside_the_window_does_not_repoll_and_429_backs_off():
    p = RU.Planner(calls_per_minute=5, last_complete_at=1000.0)
    assert p.next_action(now=1000.0 + 3600) is None
    assert p.full_due(1000.0 + RU.FULL_REFRESH_S)
    a = p.next_action(now=5000.0, priority_pending=["h"])
    out = p.record(a, {"status": 429, "body": {"code": 8}}, now=5000.0)
    assert out["unlisted"] == []                 # a refusal proves nothing
    assert p.next_call_at == 5000.0 + RU.BACKOFF_429_S
    assert p.totals["http_429"] == 1


def test_the_budget_is_never_above_the_venue_cap():
    assert RU.calls_per_min({}) == 5
    assert RU.calls_per_min({"UMP_REFDATA_CALLS_PER_MIN": "60"}) == 6
    assert RU.Planner(calls_per_minute=60).interval_s == 10.0
    d = RU.Planner().digest(now=0.0)
    assert d["state_change_stream"]["status"] == "VENUE_SCHEMA_NOT_PUBLISHED"


def test_the_instruments_body_and_participant_header():
    from sportsassets import pmx_institutional as PMX
    assert PMX.instruments_body(symbols=["b", "a", "a"]) == {
        "pageSize": 1000, "symbols": ["a", "b"]}
    with pytest.raises(PMX.NotAReadPath):
        PMX.instruments_body(symbols=[str(i) for i in range(1001)])
    assert PMX.instruments_body(page_size=5000) == {"pageSize": 1000}
    c = PMX.Institutional(env={"PMX_CLIENT_ID": "cid"})
    h = c._headers("t", "r")
    assert "x-participant-id" not in h
    c2 = PMX.Institutional(env={"PMX_CLIENT_ID": "cid",
                                "PMX_PARTICIPANT_ID": "firms/f/users/u"})
    assert c2._headers("t", "r")["x-participant-id"] == "firms/f/users/u"
    assert "instruments" in PMX.READ_ONLY_PATHS and len(PMX.READS) == 3


# ── §7 the worker's refdata slot against Postgres ───────────────────

class _RefClient:
    def __init__(self, pages):
        self.pages, self.bodies = list(pages), []

    def read_instruments(self, body):
        self.bodies.append(dict(body))
        if "symbols" in body:
            return _page([s for s in body["symbols"] if s.startswith("mp-l")])
        return self.pages.pop(0)


@pg
def test_the_worker_slot_stores_proves_unlisted_and_records_the_receipt():
    import asyncpg

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()

        class Pool:
            def acquire(self):
                class A:
                    async def __aenter__(self_):
                        return c

                    async def __aexit__(self_, *a):
                        return False
                return A()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM market_plane_events WHERE kind=$1",
                            R.REFDATA_FULL_PULL_KIND)
            for cid, pri in (("mp-held", 0), ("mp-l1", 80), ("mp-l2", 80),
                             ("mp-gone", 80)):
                await c.execute(
                    "INSERT INTO market_plane_registry (contract_id, venue, "
                    " active, desired_subscription, updated_at, priority) "
                    "VALUES ($1,'POLYMARKET_US',true,true,now(),$2) "
                    "ON CONFLICT (contract_id) DO UPDATE SET active=true, "
                    " refdata=NULL, refdata_at=NULL, priority=$2",
                    cid, pri)
            client = _RefClient([_page(["mp-l1", "not-in-registry"],
                                       token="t2"),
                                 _page(["mp-l2"], eof=True)])
            planner = RU.Planner(calls_per_minute=6)
            att: dict = {}
            now = time.time()
            s1 = await W.refdata_step(Pool(), client, planner, att, now=now)
            assert s1["action"] == RU.A_PRIORITY      # held first
            assert client.bodies[0]["symbols"] == ["mp-held"]
            assert s1["unlisted"] == 1
            s2 = await W.refdata_step(Pool(), client, planner, att,
                                      now=now + 10)
            assert s2["action"] == RU.A_PAGE and s2["stored"] == 1
            s3 = await W.refdata_step(Pool(), client, planner, att,
                                      now=now + 20)
            fin = s3["finished"]
            assert fin["status"] == RU.COMPLETE and fin["proven_unlisted"] == 1
            rows = {r["contract_id"]: r["refdata"] for r in await c.fetch(
                "SELECT contract_id, refdata::text AS refdata "
                "  FROM market_plane_registry WHERE contract_id LIKE 'mp-%'")}
            assert '"priceScale"' in rows["mp-l1"]
            assert '"priceScale"' in rows["mp-l2"]
            assert "COMPLETE_FULL_PULL_OMITTED" in rows["mp-gone"]
            assert "BY_SYMBOL_READ_200_OMITTED" in rows["mp-held"]
            assert await R.last_complete_full_pull(c) == pytest.approx(
                fin["finished_at"])
            # a restart reads the receipt and does not repoll
            p2 = RU.Planner(last_complete_at=await R.last_complete_full_pull(c))
            assert p2.next_action(now=time.time()) is None
        finally:
            await tr.rollback()
            await c.close()
    asyncio.run(go())


def test_the_snapshot_names_the_mode_and_the_stream_count():
    import inspect
    src = inspect.getsource(W.snapshot)
    assert '"subscription_mode"' in src and '"market_data_streams"' in src
    assert '"firm_stream_budget"' in src and "runtime_resources()" in src


def test_threads_are_daemon_and_named():
    k = TokenKeeper(_Client(), refresh_every_s=10)
    k.start()
    names = {t.name: t.daemon for t in threading.enumerate()}
    assert names.get("pmx-token-keeper") is True
    k.stop()
