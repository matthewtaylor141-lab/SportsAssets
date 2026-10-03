"""THE INSTITUTIONAL MARKET-DATA TRANSPORT AGAINST A REAL gRPC SERVER, IN PROCESS.

The server is a fake venue: grpcio's own server, bound to 127.0.0.1 on an
ephemeral port, serving the VENDORED `polymarket.v1.MarketDataSubscriptionAPI`
with scripted responses. The client is the production `GrpcBidiTransport` with
the real generated stubs. Only the channel differs from production: insecure,
because there is no TLS endpoint to talk to. No venue, no network, no secrets.

  §1  books arrive and are CURRENT with their evidence (connection identity,
      venue transact_time, local receipt time); the first request is an
      explicit subscribe with depth; the token reaches metadata only
  §2  a server-ended stream is a GAP until a complete book on a NEW
      connection; then CURRENT again on that connection
  §3  a backwards venue clock is a GAP until an update past the high-water
  §4  auth failure: UNAUTHENTICATED refreshes the token once and retries;
      a second refusal, or PERMISSION_DENIED, fails closed
  §5  an unreachable venue is an error, books stay refused; every message the
      server ever received was a subscribe (non-empty) or a keepalive
"""

from __future__ import annotations

import threading
import time
from concurrent import futures

import grpc
import pytest
from google.protobuf.timestamp_pb2 import Timestamp

from sportsassets import institutional_stream as IS
from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2 as pb2
from sportsassets.vendor.pmx_proto import marketdatasubscription_pb2_grpc as pb2_grpc

SYM = "aec-mlb-sd-mil-2026-10-03"
REC = {"symbol": SYM, "priceScale": "1000", "fractionalQtyScale": "100",
       "state": "INSTRUMENT_STATE_OPEN", "productId": SYM}
GOOD = "tok-NOT-REAL-good"


def _ts(at: float) -> Timestamp:
    t = Timestamp()
    t.FromNanoseconds(int(at * 1e9))
    return t


def book(at=None, bids=((450, 1000),), offers=((470, 500),), symbol=SYM):
    u = pb2.MarketDataUpdate(
        symbol=symbol,
        bids=[pb2.BookEntry(px=p, qty=q) for p, q in bids],
        offers=[pb2.BookEntry(px=p, qty=q) for p, q in offers],
        state=pb2.INSTRUMENT_STATE_OPEN,
        transact_time=_ts(time.time() if at is None else at))
    return pb2.BiDirectionalStreamMarketDataResponse(update=u)


def ack(symbols=(SYM,)):
    return pb2.BiDirectionalStreamMarketDataResponse(
        subscription_ack=pb2.SubscriptionAck(symbols_added=list(symbols),
                                             active_symbols=list(symbols)))


def heartbeat():
    return pb2.BiDirectionalStreamMarketDataResponse(heartbeat=pb2.Heartbeat())


class Wait:
    """A script step: block the server until the test sets it."""

    def __init__(self):
        self.ev = threading.Event()

    def set(self):
        self.ev.set()


class Abort:
    def __init__(self, code):
        self.code = code


class FakeVenue(pb2_grpc.MarketDataSubscriptionAPIServicer):
    """Scripted per connection. Records the authorization metadata and EVERY
    client-to-server message, so a test can prove what the client sent."""

    def __init__(self):
        self.scripts = []
        self.auth = []
        self.received = []
        self.connections = 0
        self.good_tokens = {GOOD}

    def BiDirectionalStreamMarketData(self, request_iterator, context):
        self.connections += 1
        md = dict(context.invocation_metadata())
        token = str(md.get("authorization", "")).replace("Bearer ", "", 1)
        self.auth.append(token)
        script = self.scripts.pop(0) if self.scripts else []
        if script and isinstance(script[0], Abort):
            context.abort(script[0].code, "scripted refusal")
        if token not in self.good_tokens:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "bad token")

        def drain():
            try:
                for req in request_iterator:
                    self.received.append(req)
            except Exception:                                 # noqa: BLE001
                pass
        threading.Thread(target=drain, daemon=True).start()
        for step in script:
            if isinstance(step, Wait):
                step.ev.wait(10)
                continue
            if callable(step):
                step = step()
            yield step

    def CreateMarketDataSubscription(self, request, context):  # never used
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "not this lane")


@pytest.fixture
def venue():
    v = FakeVenue()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    pb2_grpc.add_MarketDataSubscriptionAPIServicer_to_server(v, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    v.target = "127.0.0.1:%d" % port
    yield v
    server.stop(0)


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    yield
    IS.reset()


def books_for(symbols=(SYM,)):
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    for s in symbols:
        b.set_instrument(s, dict(REC, symbol=s))
    b.want(list(symbols))
    return b


def transport(books, venue, token_fn=lambda: GOOD, **kw):
    return IS.GrpcBidiTransport(
        books, token_fn, target=venue.target,
        channel_factory=lambda target: grpc.insecure_channel(target),
        sleep=lambda s: None, **kw)


def in_thread(fn):
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("r", fn()),
                          daemon=True)
    th.start()
    return th, out


def until(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.02)
    raise AssertionError("condition not reached in %.0fs" % timeout)


def assert_only_subscribe_and_keepalive(venue):
    assert venue.received, "the server saw no client message"
    for req in venue.received:
        cmd = req.WhichOneof("command")
        assert cmd in IS.OUTBOUND_COMMANDS, cmd
        if cmd == "subscribe":
            assert list(req.subscribe.symbols)
            assert all(req.subscribe.symbols)


# ── §1 + §2 books, a disconnect, a fresh book on a new connection ────────

def test_books_disconnect_and_a_fresh_book_on_the_next_connection(venue):
    b = books_for()
    gate1, gate2 = Wait(), Wait()
    venue.scripts = [[ack(), heartbeat(), book(), gate1],
                     [lambda: book(bids=((455, 7),)), gate2]]
    t = transport(b, venue)

    th, out = in_thread(t.run_once)
    r = until(lambda: (lambda x: x if x["ok"] else None)(b.current(SYM)))
    ev = r["evidence"]
    assert r["book"]["best_bid"] == 0.45 and r["book"]["best_offer"] == 0.47
    assert ev["connection"]["seq"] == 1 and ev["connection"]["connected"]
    assert ev["connection"]["id"].startswith("grpc-")
    assert ev["snapshot"]["venue_ts"] is not None
    assert ev["snapshot"]["received_at"] is not None
    assert ev["snapshot"]["lag_ms"] is not None
    assert ev["market"]["state"] == "INSTRUMENT_STATE_OPEN"
    assert ev["market"]["state_source"] == "STREAM"
    first = venue.received[0]
    assert first.WhichOneof("command") == "subscribe"
    assert list(first.subscribe.symbols) == [SYM]
    assert first.depth == IS.DEPTH and first.unaggregated is False
    assert first.snapshot_only is False
    assert venue.auth == [GOOD]

    gate1.set()                               # the server ends the stream
    th.join(10)
    assert out["r"] == "ended"
    r = b.current(SYM)
    assert r["ok"] is False and r["refusal"] == IS.R_GAP_CONNECTION
    assert r["book"] is None

    th, out = in_thread(t.run_once)            # the supervisor reconnects
    r = until(lambda: (lambda x: x if x["ok"] else None)(b.current(SYM)))
    assert r["evidence"]["connection"]["seq"] == 2
    assert r["evidence"]["snapshot"]["resnapshots"] == 1
    assert [x["px"] for x in r["book"]["bids"]] == [455]   # replaced whole
    gate2.set()
    th.join(10)
    assert_only_subscribe_and_keepalive(venue)
    assert len(venue.auth) == 2


def test_each_message_replaces_the_book_whole(venue):
    b = books_for()
    gate = Wait()
    now = time.time()
    venue.scripts = [[book(at=now, bids=((450, 1), (440, 2), (430, 3))),
                      book(at=now + 0.001, bids=((460, 9),), offers=()),
                      gate]]
    t = transport(b, venue)
    th, _ = in_thread(t.run_once)
    r = until(lambda: (lambda x: x if x["ok"] and
                       x["evidence"]["snapshot"]["updates"] == 2
                       else None)(b.current(SYM)))
    assert [x["px"] for x in r["book"]["bids"]] == [460]
    assert r["book"]["offers"] == [] and r["book"]["best_offer"] is None
    gate.set()
    th.join(10)


# ── §3 a backwards venue clock ──────────────────────────────────────────

def test_a_backwards_venue_clock_fails_closed_until_the_order_is_restored(
        venue):
    b = books_for()
    g1, g2 = Wait(), Wait()
    now = time.time()
    venue.scripts = [[book(at=now), book(at=now - 5, bids=((999, 1),)), g1,
                      lambda: book(at=now + 0.5, bids=((451, 3),)), g2]]
    t = transport(b, venue)
    th, _ = in_thread(t.run_once)
    r = until(lambda: (lambda x: x if x["refusal"] == IS.R_GAP_CLOCK
                       else None)(b.current(SYM)))
    assert r["evidence"]["regressions"] == 1 and r["book"] is None
    g1.set()
    r = until(lambda: (lambda x: x if x["ok"] else None)(b.current(SYM)))
    assert [x["px"] for x in r["book"]["bids"]] == [451]     # never the 999
    g2.set()
    th.join(10)


# ── §4 auth failure ─────────────────────────────────────────────────────

def test_unauthenticated_refreshes_the_token_once_then_streams(venue):
    b = books_for()
    tokens = {"cur": "tok-NOT-REAL-stale"}
    invalidated = []

    def token_fn():
        return tokens["cur"]

    def invalidate():
        invalidated.append(1)
        tokens["cur"] = GOOD

    gate = Wait()
    venue.scripts = [[], [book(), gate]]
    t = transport(b, venue, token_fn=token_fn, invalidate_token=invalidate)
    assert t.run_once() == "reauth"
    r = b.current(SYM)
    assert r["refusal"] == IS.R_REFUSED_KEY and "UNAUTHENTICATED" in r["why"]
    assert invalidated == [1]
    th, out = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"])
    assert venue.auth == ["tok-NOT-REAL-stale", GOOD]
    gate.set()
    th.join(10)


def test_a_second_unauthenticated_is_a_refusal(venue):
    b = books_for()
    venue.good_tokens = set()
    t = transport(b, venue, token_fn=lambda: "tok-NOT-REAL-x",
                  invalidate_token=lambda: None)
    assert t.run_once() == "reauth"
    assert t.run_once() == "refused"
    assert b.state == IS.S_REFUSED
    assert b.current(SYM)["refusal"] == IS.R_REFUSED_KEY


def test_permission_denied_is_a_refusal_and_no_token_opens_nothing(venue):
    b = books_for()
    venue.scripts = [[Abort(grpc.StatusCode.PERMISSION_DENIED)]]
    t = transport(b, venue, invalidate_token=lambda: None)
    assert t.run_once() == "refused"
    assert b.current(SYM)["refusal"] == IS.R_REFUSED_KEY
    n = venue.connections
    t2 = transport(b, venue, token_fn=lambda: None)
    assert t2.run_once() == "refused"
    assert venue.connections == n


def test_a_token_function_that_raises_opens_nothing(venue):
    b = books_for()

    def boom():
        raise RuntimeError("key does not decode")
    assert transport(b, venue, token_fn=boom).run_once() == "refused"
    assert venue.connections == 0


def test_the_supervised_loop_reconnects_with_bounded_backoff(venue):
    b = books_for()
    gate = Wait()
    venue.scripts = [[book()],               # delivers, then ends
                     [Abort(grpc.StatusCode.UNAVAILABLE)],
                     [book(), gate]]
    sleeps = []
    t = transport(b, venue)

    def sleep(s):
        sleeps.append(s)
    t._sleep = sleep
    th = threading.Thread(target=t.run, daemon=True)
    th.start()
    until(lambda: venue.connections >= 3 and b.current(SYM)["ok"])
    t.stop()
    gate.set()
    th.join(10)
    assert sleeps[0] == IS.RECONNECT_BACKOFF_S[0]          # after 'ended'
    assert sleeps[1] == IS.RECONNECT_BACKOFF_S[1]          # after one error
    assert all(s <= max(IS.RECONNECT_BACKOFF_S) for s in sleeps)


# ── §5 unreachable venue; what the client sends ─────────────────────────

def test_an_unreachable_venue_is_an_error_and_books_stay_refused():
    b = books_for()
    s = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    port = s.add_insecure_port("127.0.0.1:0")     # bound, then never started
    s.stop(0)
    # grpc's own first-connect backoff would hold a refused connect ~20 s;
    # shortened here so the test is quick. Production keeps the default.
    fast = [("grpc.min_reconnect_backoff_ms", 100),
            ("grpc.initial_reconnect_backoff_ms", 100)]
    t = IS.GrpcBidiTransport(
        b, lambda: GOOD, target="127.0.0.1:%d" % port,
        channel_factory=lambda target: grpc.insecure_channel(target,
                                                             options=fast),
        sleep=lambda s: None)
    assert t.run_once() == "error"
    assert b.state == IS.S_RECONNECTING and "UNAVAILABLE" in b.state_why
    assert b.current(SYM)["ok"] is False


def test_a_live_subscribe_and_a_keepalive_reach_the_server_as_built(venue):
    b = books_for()
    gate = Wait()
    venue.scripts = [[book(), gate]]
    t = transport(b, venue)
    th, _ = in_thread(t.run_once)
    until(lambda: b.current(SYM)["ok"])
    b.want(["aec-mlb-nyy-bos-2026-10-04"])
    t.subscribe(["aec-mlb-nyy-bos-2026-10-04"])
    t._q.put(t._keepalive_request())
    until(lambda: len(venue.received) >= 3)
    gate.set()
    th.join(10)
    cmds = [r.WhichOneof("command") for r in venue.received]
    assert cmds == ["subscribe", "subscribe", "keepalive"]
    assert list(venue.received[1].subscribe.symbols) == [
        "aec-mlb-nyy-bos-2026-10-04"]
    assert_only_subscribe_and_keepalive(venue)


def test_keepalive_after_idle_with_the_real_messages():
    class Clock:
        t = 1_800_000_000.0

        def __call__(self):
            return self.t
    c = Clock()
    b = books_for()
    t = IS.GrpcBidiTransport(b, lambda: GOOD, clock=c, sleep=lambda s: None)
    first = t._subscribe_request([SYM], first=True)
    gen = t._requests(first)
    assert next(gen) is first
    c.t += IS.KEEPALIVE_S + 1
    ka = next(gen)
    assert ka.WhichOneof("command") == "keepalive"
    t.stop()


def test_the_token_never_leaves_the_metadata(venue):
    import json
    b = books_for()
    gate = Wait()
    venue.scripts = [[book(), gate]]
    t = transport(b, venue)
    th, _ = in_thread(t.run_once)
    r = until(lambda: (lambda x: x if x["ok"] else None)(b.current(SYM)))
    gate.set()
    th.join(10)
    assert GOOD not in json.dumps(r, default=str)
    assert GOOD not in json.dumps(b.digest(), default=str)
    assert GOOD not in json.dumps(IS.digest(), default=str)
