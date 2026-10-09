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
      stays REST's; a refused call holds; the market's own word wins; the
      token is minted off the loop (a slow mint or the keeper's held lock
      never stalls the plane's loop)
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
    # no state on the update: none at all is unknown, never assumed open;
    # a refdata record's OPEN is not the venue's word now either (OPEN is
    # stated on the update by the venue; review of f1496b80)
    bare = _dec(upd("a", with_state=False))
    assert S.judge_update(bare, asked=asked)["outcome"] == \
        AR.R_REFRESH_STATE_UNKNOWN
    assert S.judge_update(bare, asked=asked,
                          refdata_state="INSTRUMENT_STATE_OPEN")[
        "outcome"] == S.R_SNAPSHOT_FALLBACK_NOT_PROVEN


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


# -- the mint never stops the plane's loop (review of 9d4b920d) ----------
# The production token_fn is TokenKeeper.token: a threading lock the keeper
# thread holds through its own mint, then PMX.Institutional.token(), which
# on an expired cache (a token is reused only 60 s) mints with a
# synchronous HTTP POST. The step is awaited on the plane's loop, beside
# the pass, the heartbeats, the window sampler and the Kalshi runtime.

MINT_S = 0.6        # a slow mint (the worst case is the 35 s POST timeout)
MAX_LAG_S = 0.2     # a blocked loop lags ~MINT_S; a free one about a tick


def _lag_during(make_coro, *, tick=0.02):
    """Run make_coro() on a fresh loop in its own thread beside a ticker:
    (result, the ticker's worst lag in s, the loop thread's ident)."""
    async def main():
        lags = []

        async def ticker():
            while True:
                t = time.monotonic()
                await asyncio.sleep(tick)
                lags.append(time.monotonic() - t - tick)
        tk = asyncio.ensure_future(ticker())
        await asyncio.sleep(0.05)
        got = await make_coro()
        await asyncio.sleep(0.05)
        tk.cancel()
        try:
            await tk
        except asyncio.CancelledError:
            pass
        return got, max(lags)
    out = {}

    def on_thread():
        out["loop_thread"] = threading.get_ident()
        out["got"], out["lag"] = asyncio.run(main())
    th = threading.Thread(target=on_thread)
    th.start()
    th.join()
    return out["got"], out["lag"], out["loop_thread"]


def test_a_slow_mint_runs_off_the_loop_and_the_call_still_goes():
    S = SR()
    clock, m, books, ref, syms = _quiet(3)
    snap = S.SnapshotRefresh()
    minted_on, seen = [], []

    def slow_token():
        minted_on.append(threading.get_ident())
        time.sleep(MINT_S)
        return TOKEN
    got, lag, loop_thread = _lag_during(lambda: snap.step(
        ref, m, token_fn=slow_token, bound=BOUND, clock=clock,
        caller=_caller(clock, seen=seen)))
    assert minted_on and minted_on[0] != loop_thread
    assert lag < MAX_LAG_S, "the loop stalled %.2f s on the mint" % lag
    # the call itself is unchanged: the minted token, every member current
    assert seen[0][0] == TOKEN and got["current"] == 3
    assert set(ref.current(m, now=clock.t + 1, bound=BOUND)) == set(syms)
    assert ref.inflight == set()


def test_the_keepers_lock_held_through_its_mint_does_not_stall_the_loop():
    """The keeper's own 30 s refresh holds its lock through a mint; a
    snapshot call landing then waits for it in a worker thread, not on
    the loop. The real TokenKeeper; a client whose mint is slow."""
    from sportsassets.market_plane.token_keeper import TokenKeeper
    S = SR()

    class SlowClient:
        def __init__(self):
            self.minted_on = []

        def token(self):
            self.minted_on.append(threading.get_ident())
            time.sleep(MINT_S)
            return TOKEN

        def invalidate_token(self):
            pass
    client = SlowClient()
    keeper = TokenKeeper(client)
    clock, m, books, ref, syms = _quiet(2)
    snap = S.SnapshotRefresh()
    seen = []
    keeper_thread = threading.Thread(target=keeper.refresh_once)

    async def go():
        keeper_thread.start()           # the keeper's thread takes the lock
        await asyncio.sleep(0.05)
        return await snap.step(ref, m, token_fn=keeper.token, bound=BOUND,
                               clock=clock, caller=_caller(clock, seen=seen))
    got, lag, loop_thread = _lag_during(go)
    keeper_thread.join()
    assert lag < MAX_LAG_S, "the loop stalled %.2f s on the keeper" % lag
    assert loop_thread not in client.minted_on
    assert keeper.refreshes == 1 and len(client.minted_on) == 2
    assert seen[0][0] == TOKEN and got["current"] == 2


def test_a_slow_failed_mint_is_no_token_held_and_off_the_loop():
    S = SR()
    clock, m, books, ref, syms = _quiet(2)
    snap = S.SnapshotRefresh()

    def failing_token():
        time.sleep(MINT_S)
        raise RuntimeError("mint failed")

    def no_call(tok, ss):
        raise AssertionError("no call without a token")
    got, lag, _lt = _lag_during(lambda: snap.step(
        ref, m, token_fn=failing_token, bound=BOUND, clock=clock,
        caller=no_call))
    assert lag < MAX_LAG_S, "the loop stalled %.2f s on the mint" % lag
    assert got["why"] == S.R_SNAPSHOT_NO_TOKEN and got["asked"] == 0
    assert snap.hold_until == clock.t + S.CALL_EVERY_S
    assert snap.hold_why == S.R_SNAPSHOT_NO_TOKEN
    assert ref.inflight == set()
    # REST still plans both
    due, _c = ref.plan(m, now=clock.t, bound=BOUND)
    assert due == syms


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


# ── whose word a not-open state is (review of 785907f2) ────────────────
#
# A snapshot book usually carries no state (the proto sends it only when
# non-default); judge_update fell back to the books' state -- a refdata
# record or an old stream state, of any age -- and a not-open fallback was
# recorded ACTIVE_REFRESH_MARKET_NOT_OPEN: the freshness window then called
# the member EXTERNAL_UNAVAILABLE (out of the eligible count) and it waited
# 900 s for any re-read, on one stale source.

def test_a_not_open_fallback_is_the_markets_only_when_the_venue_said_it():
    S = SR()
    asked, at = {"a"}, T0
    bare = _dec(upd("a", with_state=False))
    # (a) the state on the update itself is the venue's word
    j = S.judge_update(_dec(upd("a", state=pb2.INSTRUMENT_STATE_SUSPENDED)),
                       asked=asked, fallback={
                           "state": "INSTRUMENT_STATE_OPEN",
                           "state_source": "REFDATA"}, at=at, bound=BOUND)
    assert j["outcome"] == AR.R_REFRESH_NOT_OPEN
    assert j["state_from"] == S.STATE_FROM_UPDATE
    # a refdata record's transient state, or an old stream state: not the
    # venue's word now -- not current, SOFTWARE, never NOT_OPEN
    for fb in ({"state": "INSTRUMENT_STATE_SUSPENDED",
                "state_source": "REFDATA", "received_at": at - 400.0},
               {"state": "INSTRUMENT_STATE_SUSPENDED",
                "state_source": "REFDATA", "received_at": at - 10.0},
               {"state": "INSTRUMENT_STATE_HALTED", "state_source": "STREAM",
                "received_at": at - 400.0},
               {"state": "INSTRUMENT_STATE_PREOPEN", "state_source": "STREAM",
                "received_at": None}):
        j = S.judge_update(bare, asked=asked, fallback=fb, at=at,
                           bound=BOUND)
        assert j["outcome"] == S.R_SNAPSHOT_FALLBACK_NOT_PROVEN, fb
        assert j["state_from"] == S.STATE_FROM_FALLBACK
        assert j["fallback"]["state_source"] == fb["state_source"]
        assert j["fallback"]["received_at"] == fb["received_at"]
    assert S.judge_update(bare, asked=asked,
                          refdata_state="INSTRUMENT_STATE_SUSPENDED")[
        "outcome"] == S.R_SNAPSHOT_FALLBACK_NOT_PROVEN
    # (c) the stream's own state, its newest update inside the bound
    j = S.judge_update(bare, asked=asked, fallback={
        "state": "INSTRUMENT_STATE_HALTED", "state_source": "STREAM",
        "received_at": at - 100.0}, at=at, bound=BOUND)
    assert j["outcome"] == AR.R_REFRESH_NOT_OPEN
    # ... not without the receipt instant and the bound to show it
    assert S.judge_update(bare, asked=asked, fallback={
        "state": "INSTRUMENT_STATE_HALTED", "state_source": "STREAM",
        "received_at": at - 100.0})["outcome"] == \
        S.R_SNAPSHOT_FALLBACK_NOT_PROVEN
    # (b) a terminal state, any age, any source (the held-position rule)
    for src in ("REFDATA", "STREAM"):
        j = S.judge_update(bare, asked=asked, fallback={
            "state": "INSTRUMENT_STATE_EXPIRED", "state_source": src,
            "received_at": at - 9e5}, at=at, bound=BOUND)
        assert j["outcome"] == AR.R_REFRESH_NOT_OPEN, src
    # an OPEN fallback is held to the SAME standard (review of f1496b80):
    # the stream's own OPEN inside the bound is the venue's word ...
    assert S.judge_update(bare, asked=asked, fallback={
        "state": "INSTRUMENT_STATE_OPEN", "state_source": "STREAM",
        "received_at": at - 100.0}, at=at, bound=BOUND)["outcome"] == "CURRENT"
    # ... a refdata record's OPEN, or an old stream OPEN, is not
    for fb in ({"state": "INSTRUMENT_STATE_OPEN", "state_source": "REFDATA"},
               {"state": "INSTRUMENT_STATE_OPEN", "state_source": "REFDATA",
                "received_at": at - 10.0},
               {"state": "INSTRUMENT_STATE_OPEN", "state_source": "STREAM",
                "received_at": at - BOUND - 1.0},
               {"state": "INSTRUMENT_STATE_OPEN", "state_source": "STREAM",
                "received_at": None}):
        assert S.judge_update(bare, asked=asked, fallback=fb, at=at,
                              bound=BOUND)["outcome"] == \
            S.R_SNAPSHOT_FALLBACK_NOT_PROVEN, fb
    # the update's own OPEN needs nothing else
    assert S.judge_update(_dec(upd("a")), asked=asked, fallback={},
                          at=at, bound=BOUND)["outcome"] == "CURRENT"
    from sportsassets import refusal_taxonomy_table as TT
    assert TT.TABLE[S.R_SNAPSHOT_FALLBACK_NOT_PROVEN][0] == "SOFTWARE"


def _one(refdata_state, *, age=400.0):
    """A REAL subscribe-all Manager holding ONE quiet member, q-00, whose
    refdata record says `refdata_state` and whose last stream update
    (stating no state) is `age` s old; and its refresher."""
    from sportsassets.market_plane.sharded_stream import Manager
    clock = H.Clock(T0)
    s = "q-00"
    m = Manager(token_fn=lambda: "x", max_per_stream=1000, max_streams=1,
                subscribe_all=True, clock=clock,
                transport_factory=lambda b, tok, **kw: H._T(b, tok, **kw))
    m.sync({s: 0}, {s: {"symbol": s, "priceScale": "1000",
                        "fractionalQtyScale": "100",
                        "state": refdata_state}})
    books = m.shards[0]["books"]
    books.on_connected("conn-1")
    books.on_update({"symbol": s, "bids": [(400, 100)],
                     "offers": [(410, 100)], "state": None,
                     "transact_time": H.dt(T0)}, received_at=T0)
    clock.t = T0 + age
    books.on_heartbeat()
    ref = AR.ActiveRefresh()
    ref.set_members([H.member(s, 10, T0 + 3600)], now=clock.t)
    return clock, m, ref, s


def _snapshot_of(clock, state=None):
    """A stand-in for `call`: each book asked for, stating `state` (a
    proto enum) or -- as the venue usually sends it -- no state at all."""
    def caller(token, syms):
        return {"status": "ENDED", "updates": [
            (IS.decode_update(upd(s, at=clock.t, with_state=state is not None,
                                  state=state or 0).update, pb2), clock.t)
            for s in syms]}
    return caller


def _window_code(m, ref, s, now):
    from sportsassets.market_plane import freshness_window as FW
    mem = {"contract_id": s, "venue": "POLYMARKET_US", "tier": "CANDIDATE"}
    return FW.classify(mem, mgr=m, refreshed=FW.refreshed_codes(
        ref, m, now=now, sla_s=BOUND), entries=ref.entries, paper={},
        kalshi={}, now=now, sla_s=BOUND)[:2]


def test_a_stateless_book_on_a_stale_not_open_fallback_stays_counted():
    S = SR()
    clock, m, ref, s = _one("INSTRUMENT_STATE_SUSPENDED")
    assert m.current(s, now=clock.t, max_snapshot_age_s=BOUND)["evidence"][
        "market"]["state_source"] == "REFDATA"
    assert _window_code(m, ref, s, clock.t) == (
        "N", "STREAM:SNAPSHOT_OLDER_THAN_THE_BOUND")
    snap = S.SnapshotRefresh()
    got = run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                        clock=clock, caller=_snapshot_of(clock)))
    assert got["returned"] == 1 and got["current"] == 0
    # still eligible and NOT current in the window -- never EXTERNAL
    assert _window_code(m, ref, s, clock.t)[0] == "N"
    assert ref.current(m, now=clock.t, bound=BOUND) == {}
    # the 60 s retry of a read that proved nothing, not the 900 s market wait
    due, counts = ref.plan(m, now=clock.t + 30, bound=BOUND)
    assert due == [] and counts[AR.R_REFRESH_RETRY_WAIT] == 1
    assert ref.plan(m, now=clock.t + 61, bound=BOUND)[0] == [s]
    # named: a SOFTWARE outcome, counted
    assert ref.outcome_of(s) == S.R_SNAPSHOT_FALLBACK_NOT_PROVEN
    assert snap.totals["by_outcome"][S.R_SNAPSHOT_FALLBACK_NOT_PROVEN] == 1
    assert _window_code(m, ref, s, clock.t) == (
        "N", "STREAM:SNAPSHOT_OLDER_THAN_THE_BOUND|REFRESH:"
        + S.R_SNAPSHOT_FALLBACK_NOT_PROVEN)
    # the REST book states the venue's own OPEN: current through it
    clock.t += 61
    ref.start(clock.t, s)
    assert ref.record(s, H.rest_book(s, at=clock.t), at=clock.t)[
        "outcome"] == AR.CURRENT
    assert _window_code(m, ref, s, clock.t) == ("R", None)


@pytest.mark.parametrize("refdata,on_update", [
    # (b) a terminal refdata state: the held-position rule, any age
    ("INSTRUMENT_STATE_EXPIRED", None),
    # (a) the venue's state on the snapshot update itself
    ("INSTRUMENT_STATE_OPEN", pb2.INSTRUMENT_STATE_SUSPENDED),
])
def test_the_venues_own_not_open_word_is_still_external_and_waits(
        refdata, on_update):
    S = SR()
    clock, m, ref, s = _one(refdata)
    run(S.SnapshotRefresh().step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                                 clock=clock,
                                 caller=_snapshot_of(clock, on_update)))
    assert ref.outcome_of(s) == AR.R_REFRESH_NOT_OPEN
    assert _window_code(m, ref, s, clock.t) == (
        "X", "REFRESH:MARKET_NOT_OPEN")
    due, counts = ref.plan(m, now=clock.t + 61, bound=BOUND)
    assert due == [] and counts[AR.R_REFRESH_RETRY_WAIT] == 1
    assert ref.plan(m, now=clock.t + AR.RETRY_NOT_OPEN_S + 1,
                    bound=BOUND)[0] == [s]


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




def test_a_book_without_a_state_on_a_quiet_member_is_not_current():
    """The venue states a market's state on an update only when it is not
    the default (CLOSED, enum 0, is the one left off), so an OPEN market's
    snapshot states OPEN. A stateless book on a member the stream has been
    quiet on rests on a fallback older than the bound: not current, kept in
    the denominator, and the REST read (whose body states the market's own
    state) stays free to try it. The same book stating OPEN is current.
    (Replaces test_a_book_without_a_state_takes_the_streams_own_fallback,
    which pinned the defect the review of f1496b80 found.)"""
    S = SR()
    clock, m, books, ref, syms = _quiet(1)
    snap = S.SnapshotRefresh()

    def bare(tok, ss):
        return {"status": "ENDED", "updates": [(IS.decode_update(upd(
            "q-00", with_state=False).update, pb2), clock.t)]}
    got = run(snap.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                        clock=clock, caller=bare))
    assert got["current"] == 0 and got["returned"] == 1
    assert ref.outcome_of("q-00") == S.R_SNAPSHOT_FALLBACK_NOT_PROVEN
    assert ref.origin_of("q-00") is None
    by = snap.digest(now=clock.t)["by_state_from"]
    assert by and all(not k.startswith("UPDATE") for k in by), by
    assert not any(k.endswith("|CURRENT") for k in by), by
    clock.t += 61
    snap2 = S.SnapshotRefresh()

    def stated(tok, ss):
        return {"status": "ENDED", "updates": [(IS.decode_update(upd(
            "q-00", at=clock.t).update, pb2), clock.t)]}
    got = run(snap2.step(ref, m, token_fn=lambda: TOKEN, bound=BOUND,
                         clock=clock, caller=stated))
    assert got["current"] == 1 and ref.origin_of("q-00") == "SNAPSHOT"
    assert snap2.digest(now=clock.t)["by_state_from"] == {"UPDATE|CURRENT": 1}


def test_the_reviewers_case_a_stale_open_and_an_empty_stateless_book():
    """Review of f1496b80, reproduced: the stream stated OPEN 4 h ago and
    the snapshot returns an EMPTY book with no state (CLOSED, the default,
    omitted). It was CURRENT (G), counted in the graded numerator, and it
    pre-empted the REST read. Now: not current, N in the window, and the
    REST read is planned once the retry wait passes."""
    S = SR()
    clock, m, ref, s = _one("INSTRUMENT_STATE_OPEN", age=4 * 3600.0)

    def empty(tok, ss):
        return {"status": "ENDED", "updates": [(IS.decode_update(upd(
            s, at=clock.t, with_state=False, bids=(), offers=()).update,
            pb2), clock.t)]}
    got = run(S.SnapshotRefresh().step(ref, m, token_fn=lambda: TOKEN,
                                       bound=BOUND, clock=clock,
                                       caller=empty))
    assert got["current"] == 0
    assert ref.outcome_of(s) == S.R_SNAPSHOT_FALLBACK_NOT_PROVEN
    assert _window_code(m, ref, s, clock.t)[0] == "N"
    assert ref.plan(m, now=clock.t + 61, bound=BOUND)[0] == [s]


def test_a_newer_paper_read_stating_not_open_beats_an_older_refresh():
    """classify applies the held-position rule's order to R/G as well: the
    venue's latest word about the market, read inside the bound, first."""
    from sportsassets.market_plane import freshness_window as FW
    clock, m, ref, s = _one("INSTRUMENT_STATE_OPEN")
    ref.start(clock.t, s)
    assert ref.record(s, H.rest_book(s, at=clock.t), at=clock.t)[
        "outcome"] == AR.CURRENT
    mem = {"contract_id": s, "venue": "POLYMARKET_US", "tier": "CANDIDATE"}

    def code(paper, now):
        return FW.classify(mem, mgr=m, refreshed=FW.refreshed_codes(
            ref, m, now=now, sla_s=BOUND), entries=ref.entries,
            paper=paper, kalshi={}, now=now, sla_s=BOUND)[0]
    now = clock.t + 30
    assert code({s: {"at": clock.t - 60, "market_state": "closed"}}, now) == "R"
    assert code({s: {"at": clock.t + 20, "market_state": "closed"}}, now) == "X"
    assert code({s: {"at": clock.t + 20, "market_state": "open"}}, now) == "R"
