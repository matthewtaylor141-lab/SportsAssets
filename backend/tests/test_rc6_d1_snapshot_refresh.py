"""ONE SNAPSHOT-ONLY gRPC CALL RE-PROVES THE QUIET PRIORITY MEMBERS (RC6 lane
D1, delivery; market_plane.snapshot_refresh).

The refresh simulation on production's own distributions (tools/
freshness_refresh_sim.py, research-sql run 37870039455) keeps 0.95 of the
priority member-time fresh with the REST budget alone (12 GetOrderBook a
minute) only up to ~134 members; at the RC5 denominator (186) it reaches
0.83-0.93. The venue's own CreateMarketDataSubscription with snapshot_only
("receive only initial snapshot then close stream") returns every named
book in one call: 0.978-0.9997 in every scenario.

  §1  the one request: explicit symbols, snapshot_only, depth 10,
      aggregated, never skip-to-head; an empty list (EVERY instrument), a
      continuous subscription, an over-cap list are refused before the wire
  §2  judging a snapshot book exactly like the stream / the REST refresh
  §3  against a REAL gRPC server in process (the vendored service): the
      call sends exactly that request, the bearer in metadata only, reads
      every book, cancels when complete; a venue refusal is a status
  §4  the refresh against a REAL subscribe-all Manager: one call a minute,
      every quiet member current (origin SNAPSHOT, G in the window), the
      REST plan left only what it did not prove; a symbol not returned
      stays REST's; a refused call holds; the market's own word wins
  §5  the coverage pass counts a snapshot refresh PMX_GRPC, labelled; the
      denominators and the completion numerator carry it
  §6  the REAL run loop with the snapshot call against the in-process
      venue: a quiet candidate is current through ONE snapshot call and no
      REST read (on 412c4962 there is no such call)
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib
import threading
import time
from concurrent import futures

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp

from sportsassets import institutional_stream as IS
from sportsassets.market_plane import active_refresh as AR
from sportsassets.market_plane import populate as POP
from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2 as pb2
from sportsassets.vendor.pmx_proto import \
    marketdatasubscription_pb2_grpc as pb2_grpc
from sportsassets.workers import universal_market_plane as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
HERE = pathlib.Path(__file__).resolve().parent
BOUND = W.FRESH_SLA_S
TOKEN = "tok-NOT-REAL-snapshot"


def SR():
    from sportsassets.market_plane import snapshot_refresh
    return snapshot_refresh


def _rc6():
    spec = importlib.util.spec_from_file_location(
        "_rc6_snap_helpers", HERE / "test_rc6_priority_active_refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _rc6()
T0 = H.T0


def run(coro):
    return asyncio.run(coro)


def _ts(at: float) -> Timestamp:
    t = Timestamp()
    t.FromNanoseconds(int(at * 1e9))
    return t


def upd(sym, *, at=None, state=pb2.INSTRUMENT_STATE_OPEN, bids=((400, 10),),
        offers=((410, 10),), hidden=False, with_state=True):
    kw = dict(symbol=sym, bids=[pb2.BookEntry(px=p, qty=q) for p, q in bids],
              offers=[pb2.BookEntry(px=p, qty=q) for p, q in offers],
              transact_time=_ts(time.time() if at is None else at),
              book_hidden=hidden)
    if with_state:
        kw["state"] = state
    return pb2.CreateMarketDataSubscriptionResponse(
        update=pb2.MarketDataUpdate(**kw))


# ═════════════════════════════════════════════════════════════════════
# §1 the one request
# ═════════════════════════════════════════════════════════════════════

def test_the_request_is_explicit_snapshot_only_aggregated_depth_ten():
    S = SR()
    r = S.snapshot_request(pb2, ["b", "a", "a", " "])
    assert list(r.symbols) == ["a", "b"]
    assert r.snapshot_only is True and r.depth == IS.DEPTH == 10
    assert r.unaggregated is False and r.slow_consumer_skip_to_head is False
    assert S.RPC == "CreateMarketDataSubscription"


@pytest.mark.parametrize("req", [
    pb2.CreateMarketDataSubscriptionRequest(symbols=[], snapshot_only=True),
    pb2.CreateMarketDataSubscriptionRequest(symbols=["a"],
                                            snapshot_only=False),
    pb2.CreateMarketDataSubscriptionRequest(symbols=["a", ""],
                                            snapshot_only=True),
    pb2.CreateMarketDataSubscriptionRequest(symbols=["a"], snapshot_only=True,
                                            unaggregated=True),
    pb2.CreateMarketDataSubscriptionRequest(
        symbols=["a"], snapshot_only=True, slow_consumer_skip_to_head=True),
    pb2.CreateMarketDataSubscriptionRequest(
        symbols=["s%d" % i for i in range(251)], snapshot_only=True),
])
def test_every_other_request_is_refused_before_the_wire(req):
    S = SR()
    with pytest.raises(S.SnapshotRefused):
        S.outbound(req)
    # and the builder can never produce the EVERY-instrument request
    with pytest.raises(S.SnapshotRefused):
        S.snapshot_request(pb2, [])
    assert issubclass(S.SnapshotRefused, IS.OutboundRefused)


# ═════════════════════════════════════════════════════════════════════
# §2 judging one snapshot book
# ═════════════════════════════════════════════════════════════════════

def _dec(resp):
    return IS.decode_update(resp.update, pb2)


def test_a_snapshot_book_is_judged_like_the_stream_and_the_rest_book():
    S = SR()
    asked = {"a"}
    assert S.judge_update(_dec(upd("a")), asked=asked)["outcome"] == "CURRENT"
    assert S.judge_update(_dec(upd("x")), asked=asked)["outcome"] == \
        S.R_SNAPSHOT_NOT_ASKED
    no_ts = _dec(upd("a"))
    no_ts["transact_time"] = None
    assert S.judge_update(no_ts, asked=asked)["outcome"] == \
        AR.R_REFRESH_NO_VENUE_TS
    assert S.judge_update(_dec(upd("a", hidden=True)), asked=asked)[
        "outcome"] == S.R_SNAPSHOT_BOOK_HIDDEN
    assert S.judge_update(_dec(upd("a", state=pb2.INSTRUMENT_STATE_CLOSED)),
                          asked=asked)["outcome"] == AR.R_REFRESH_NOT_OPEN
    assert S.judge_update(_dec(upd("a", bids=((410, 1),), offers=((410, 1),))
                               ), asked=asked)["outcome"] == \
        AR.R_REFRESH_CROSSED
    # no state on the update: the plane's refdata record's (the stream's
    # own rule) -- none at all is unknown, never assumed open
    bare = _dec(upd("a", with_state=False))
    assert S.judge_update(bare, asked=asked)["outcome"] == \
        AR.R_REFRESH_STATE_UNKNOWN
    assert S.judge_update(bare, asked=asked,
                          refdata_state="INSTRUMENT_STATE_OPEN")[
        "outcome"] == "CURRENT"


# ═════════════════════════════════════════════════════════════════════
# §3 against a REAL gRPC server in process
# ═════════════════════════════════════════════════════════════════════

class FakeVenue(pb2_grpc.MarketDataSubscriptionAPIServicer):
    """The venue's snapshot_only semantics as documented: every named book
    once, then the stream ends. Records every request and its metadata."""

    def __init__(self):
        self.requests, self.metadata = [], []
        self.omit, self.abort, self.hold_open = set(), None, False

    def CreateMarketDataSubscription(self, request, context):
        self.requests.append(request)
        self.metadata.append(dict(context.invocation_metadata()))
        if self.abort:
            context.abort(self.abort, "scripted refusal")
        for s in request.symbols:
            if s in self.omit:
                continue
            yield upd(s)
        if self.hold_open:
            time.sleep(5)

    def BiDirectionalStreamMarketData(self, request_iterator, context):
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "not this test")


@pytest.fixture
def venue():
    v = FakeVenue()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb2_grpc.add_MarketDataSubscriptionAPIServicer_to_server(v, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    v.target = "127.0.0.1:%d" % port
    yield v
    server.stop(0)


def _call(venue, syms, **kw):
    return SR().call(TOKEN, syms, target=venue.target,
                     channel_factory=grpc.insecure_channel, **kw)


def test_the_call_sends_exactly_the_request_and_reads_every_book(venue):
    got = _call(venue, ["m-1", "m-2", "m-3"])
    assert got["status"] in ("COMPLETE", "ENDED")
    assert sorted(u["symbol"] for u, _at in got["updates"]) == [
        "m-1", "m-2", "m-3"]
    (req,) = venue.requests
    assert list(req.symbols) == ["m-1", "m-2", "m-3"]
    assert req.snapshot_only is True and req.depth == 10
    # the bearer token travels in metadata only
    assert venue.metadata[0]["authorization"] == "Bearer " + TOKEN
    assert TOKEN not in req.SerializeToString().decode("latin-1")
    for u, at in got["updates"]:
        assert u["transact_time"] is not None and abs(at - time.time()) < 30


def test_a_missing_book_is_ended_not_invented_and_a_refusal_is_a_status(
        venue):
    venue.omit = {"m-2"}
    got = _call(venue, ["m-1", "m-2"])
    assert got["status"] == "ENDED"
    assert [u["symbol"] for u, _a in got["updates"]] == ["m-1"]
    venue.omit, venue.abort = set(), grpc.StatusCode.PERMISSION_DENIED
    got = _call(venue, ["m-1"])
    assert got["status"] == "PERMISSION_DENIED" and got["updates"] == []


def test_a_complete_call_is_cancelled_here_not_waited_on(venue):
    venue.hold_open = True
    t0 = time.time()
    got = _call(venue, ["m-1"], deadline_s=8.0)
    assert got["status"] == "COMPLETE" and time.time() - t0 < 3.0


# ═════════════════════════════════════════════════════════════════════
# §4 the refresh against a REAL subscribe-all Manager
# ═════════════════════════════════════════════════════════════════════

def _quiet(n=5, *, age=400.0):
    clock = H.Clock(T0)
    syms = ["q-%02d" % i for i in range(n)]
    m, books = H.plane(clock, syms)
    for s in syms:
        H.update(books, s, T0)
    clock.t = T0 + age
    books.on_heartbeat()
    ref = AR.ActiveRefresh()
    ref.set_members([H.member(s, 10, T0 + 3600 + i)
                     for i, s in enumerate(syms)], now=clock.t)
    return clock, m, books, ref, syms


def _caller(clock, updates=None, status="ENDED", seen=None):
    """A stand-in for `call`: the venue's books for the symbols asked (or
    the `updates` subset), received at the clock's instant."""
    def caller(token, syms):
        if seen is not None:
            seen.append((token, list(syms)))
        ups = [(IS.decode_update(upd(s).update, pb2), clock.t)
               for s in syms if s in (updates or set(syms))]
        return {"status": status, "updates": ups, "ms": 12.0}
    return caller


def test_one_call_makes_every_quiet_member_current_with_its_origin():
    S = SR()
    clock, m, books, ref, syms = _quiet()
    snap = S.SnapshotRefresh()
    seen = []
    got = run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                        clock=clock, caller=_caller(clock, seen=seen)))
    assert got["asked"] == 5 and got["current"] == 5 and seen[0][0] == TOKEN
    cur = ref.current(m, now=clock.t + 1, bound=BOUND)
    assert set(cur) == set(syms)
    assert all(ref.origin_of(s) == "SNAPSHOT" for s in syms)
    assert ref.digest(now=clock.t + 1, bound=BOUND, mgr=m)[
        "current_via_refresh_by_origin"] == {"SNAPSHOT": 5}
    # the REST plan has nothing left to read
    due, _c = ref.plan(m, now=clock.t + 1, bound=BOUND)
    assert due == []
    # the REST budget was not touched
    assert ref.totals["reads"] == 0 and ref.reads_in_window(clock.t) == 0
    # no second call inside the minute
    clock.t += 30
    assert run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                         clock=clock, caller=_caller(clock, seen=seen))) is None
    assert len(seen) == 1


def test_a_member_is_re_proven_before_its_bound_by_the_next_calls():
    S = SR()
    clock, m, books, ref, syms = _quiet(3)
    snap = S.SnapshotRefresh()
    seen = []
    lapsed = 0
    for _minute in range(30):
        run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                      clock=clock, caller=_caller(clock, seen=seen)))
        for _sec in range(60):
            clock.t += 1.0
            books.on_heartbeat()
            if len(ref.current(m, now=clock.t, bound=BOUND)) < 3:
                lapsed += 1
    # never a second without all three current; at most one call a minute
    assert lapsed == 0
    assert 6 <= len(seen) <= 30


def test_a_symbol_not_returned_stays_the_rest_reads_and_is_counted():
    S = SR()
    clock, m, books, ref, syms = _quiet(4)
    snap = S.SnapshotRefresh()
    got = run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                        clock=clock,
                        caller=_caller(clock, updates={"q-00", "q-01"})))
    assert got["returned"] == 2 and got["not_returned"] == 2
    assert snap.totals["by_outcome"][S.R_SNAPSHOT_NOT_RETURNED] == 2
    due, _c = ref.plan(m, now=clock.t + 1, bound=BOUND)
    assert due == ["q-02", "q-03"]
    assert ref.inflight == set()


def test_a_refused_call_holds_and_rest_carries_on():
    S = SR()
    clock, m, books, ref, syms = _quiet(2)
    snap = S.SnapshotRefresh()
    got = run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                        clock=clock, caller=lambda tok, ss: {
                            "status": "UNIMPLEMENTED", "updates": []}))
    assert got["status"] == "UNIMPLEMENTED"
    assert snap.hold_until == clock.t + S.REFUSED_HOLD_S
    assert snap.hold_why.startswith(S.R_SNAPSHOT_REFUSED)
    clock.t += 600
    assert run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                         clock=clock, caller=None)) is None
    assert snap.digest(now=clock.t)["hold_why"].startswith(
        S.R_SNAPSHOT_REFUSED)
    # a transport failure holds HOLD_S only
    snap2 = S.SnapshotRefresh()
    run(snap2.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND, clock=clock,
                   caller=lambda tok, ss: {"status": "UNAVAILABLE",
                                           "updates": []}))
    assert snap2.hold_until == clock.t + S.HOLD_S
    # REST still plans both
    due, _c = ref.plan(m, now=clock.t, bound=BOUND)
    assert due == syms
    # no token: no call, a short hold, said so
    snap3 = S.SnapshotRefresh()
    got = run(snap3.step(ref, m, token_fn=lambda: None, bound=BOUND,
                         clock=clock, caller=None))
    assert got["why"] == S.R_SNAPSHOT_NO_TOKEN


def test_the_markets_own_word_ends_a_snapshot_read_and_waits():
    S = SR()
    clock, m, books, ref, syms = _quiet(1)
    snap = S.SnapshotRefresh()
    run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND, clock=clock,
                  caller=_caller(clock)))
    assert set(ref.current(m, now=clock.t, bound=BOUND)) == {"q-00"}
    clock.t += 300 - 80                     # within the lead: due again

    def closed(tok, ss):
        return {"status": "ENDED", "updates": [(IS.decode_update(upd(
            "q-00", state=pb2.INSTRUMENT_STATE_SUSPENDED).update, pb2),
            clock.t)]}
    run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND, clock=clock,
                  caller=closed))
    assert ref.current(m, now=clock.t, bound=BOUND) == {}
    assert ref.outcome_of("q-00") == AR.R_REFRESH_NOT_OPEN
    due, counts = ref.plan(m, now=clock.t + 61, bound=BOUND)
    assert due == [] and counts[AR.R_REFRESH_RETRY_WAIT] == 1


def test_the_snapshot_codes_are_classified():
    from sportsassets import refusal_taxonomy_table as TT
    S = SR()
    for k in dir(S):
        if k.startswith("R_"):
            assert getattr(S, k) in TT.TABLE, k


# ═════════════════════════════════════════════════════════════════════
# §5 the coverage pass, the denominators, the completion numerator
# ═════════════════════════════════════════════════════════════════════

def _mem_bound_module():
    spec = importlib.util.spec_from_file_location(
        "_rc6d1_mem_bound", HERE / "test_market_plane_memory_bound.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_coverage_pass_counts_a_snapshot_read_pmx_grpc_labelled(
        monkeypatch):
    MB = _mem_bound_module()
    monkeypatch.setattr(POP, "external_codes", lambda: {MB.EXT})
    cs, ev, fresh = MB.universe(230)
    pri = [c for c, r in sorted(cs.items()) if r["active"]
           and r["priority"] <= POP.P_CANDIDATE]
    quiet = [c for c in pri if c not in fresh and c not in ev["rest"]][:6]
    rest_ones, snap_ones = quiet[:2], quiet[2:5]
    refreshed = {c: MB.NOW - 30.0 for c in rest_ones}
    refreshed.update({c: (MB.NOW - 20.0, "SNAPSHOT") for c in snap_ones})
    base, _bc = run(MB._cov(POP.coverage_pass, cs, ev, fresh))
    got, _gc = run(MB._cov(POP.coverage_pass, cs, ev, fresh,
                           refreshed=refreshed))
    b, g = base["freshness_tiers"]["PRIORITY"], got["freshness_tiers"][
        "PRIORITY"]
    assert g["PMX_GRPC"] == b["PMX_GRPC"] + 3
    assert g["REST_RECOVERY"] == b["REST_RECOVERY"] + 2
    assert g["NONE"] == b["NONE"] - 5
    assert got["pmx_grpc_by_origin"]["PRIORITY"] == {
        "STREAM": b["PMX_GRPC"], "PLANE_SNAPSHOT_REFRESH": 3}
    assert got["rest_recovery_by_origin"]["PRIORITY"][
        "PLANE_ACTIVE_REFRESH"] == 2
    # the denominators split PMX_GRPC back into stream and snapshot
    den = run(W.freshness_denominators(
        None, got, {"active": 230}, {"overflow_count": 0}, subscribed=1,
        fresh=1, now=MB.NOW))["priority_universe"]
    assert den["current_pmx_snapshot_refresh"] == 3
    assert den["current_pmx_stream"] == b["PMX_GRPC"]
    from sportsassets.completion import read as CR
    md = CR.market_data_block({"freshness": {"priority_universe": den}},
                              None, {})
    assert md["priority_freshness"]["numerator"] == (
        den["current_pmx_stream"] + den["current_rest_fallback"]
        + den["current_pmx_snapshot_refresh"])


# ═════════════════════════════════════════════════════════════════════
# §6 the REAL run loop, the snapshot call against the in-process venue
# ═════════════════════════════════════════════════════════════════════

QUIET = "atc-rc6d1-snap-2026-10-10-q"


@pg
def test_the_run_loop_proves_a_quiet_member_with_one_snapshot_call(
        monkeypatch, venue):
    import asyncpg
    from sportsassets import pmx_institutional as PMX
    from sportsassets.market_plane import sharded_stream as SS
    S = SR()
    H._Client.reads = []
    snaps, beats = [], []
    real_call = S.call

    def call_here(token, syms, **kw):
        return real_call(token, syms, target=venue.target,
                         channel_factory=grpc.insecure_channel)

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        one = asyncio.Lock()

        class Pool:
            def acquire(self):
                class A:
                    async def __aenter__(self_):
                        await one.acquire()
                        return c

                    async def __aexit__(self_, *a):
                        one.release()
                        return False
                return A()

        async def get_pool():
            return Pool()

        async def hb(service, status="ok", detail=None, con=None):
            beats.append((service, status, detail))
            if len(beats) >= 1:
                raise asyncio.CancelledError()
        real_snapshot = W.snapshot

        async def snapshot(*a, **kw):
            s = await real_snapshot(*a, **kw)
            snaps.append(json.loads(json.dumps(s, default=str)))
            return s
        real_cov = POP.coverage_pass

        async def slow_coverage(*a, **kw):
            await asyncio.sleep(1.0)
            return await real_cov(*a, **kw)

        async def required(conn):
            return set(), {QUIET}, True
        monkeypatch.setattr(W, "get_pool", get_pool)
        monkeypatch.setattr(W, "heartbeat", hb)
        monkeypatch.setattr(W, "snapshot", snapshot)
        monkeypatch.setattr(W, "INTERVAL_S", 0.0)
        monkeypatch.setattr(W, "FRESHNESS_TICK_S", 0.02, raising=False)
        monkeypatch.setattr(POP, "coverage_pass", slow_coverage)
        monkeypatch.setattr(S, "call", call_here)
        # REST would also make it current: hold it so only the snapshot can
        monkeypatch.setattr(AR.ActiveRefresh, "slot",
                            lambda self, now: (False, AR.R_REFRESH_BUDGET))
        monkeypatch.setattr(W, "stream_arming",
                            lambda *a, **k: {"armed": True, "why": None})
        monkeypatch.setattr(W, "TokenKeeper", H._Keeper)
        monkeypatch.setattr(PMX, "Institutional", H._Client)
        monkeypatch.setattr(W, "Manager", lambda **kw: SS.Manager(
            transport_factory=lambda b, tok, **k: H._QuietTransport(
                b, tok, **k), **kw))
        monkeypatch.setattr(POP, "required_sets_read", required)
        monkeypatch.setenv("KALSHI_CATALOGUE", "off")
        monkeypatch.delenv("UMP_ACTIVE_REFRESH", raising=False)
        monkeypatch.delenv("UMP_SNAPSHOT_REFRESH", raising=False)
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            await c.execute(
                "INSERT INTO market_plane_registry (contract_id, venue, "
                " active, desired_subscription, updated_at, priority, "
                " required_reason, event_start, last_seen_at, refdata) "
                "VALUES ($1, 'POLYMARKET_US', true, true, now(), 10, "
                " 'EVALUATED_CANDIDATE', now() + interval '20 hours', now(),"
                " $2::jsonb) ON CONFLICT (contract_id) DO UPDATE SET "
                " active = true, priority = 10, refdata = excluded.refdata",
                QUIET, json.dumps({"symbol": QUIET, "priceScale": "1000",
                                   "fractionalQtyScale": "100",
                                   "state": H.OPEN}))
            with pytest.raises(asyncio.CancelledError):
                await W.run()
        finally:
            await tr.rollback()
            await c.close()
    run(go())
    assert beats and beats[0][1] != "error", beats[0]
    pu = snaps[0]["freshness"]["priority_universe"]
    cen = pu["census"]
    assert cen["current_via_refresh"] == 1, cen
    assert cen["current_via_refresh_by_origin"] == {"SNAPSHOT": 1}
    # ONE snapshot-only call naming the member; no REST book read
    assert len(venue.requests) == 1
    assert list(venue.requests[0].symbols) == [QUIET]
    assert venue.requests[0].snapshot_only is True
    assert H._Client.reads == []
    sr = pu["snapshot_refresh"]
    assert sr["enabled"] is True and sr["totals"]["current"] == 1
    assert beats[0][2]["freshness_task"]["last_snapshot_call"]["current"] \
        == 1
