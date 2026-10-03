"""THE INSTITUTIONAL STREAM: resident books, currency evidence, gaps that fail
closed, and the gRPC transport driven by FAKE generated modules. No network,
no grpcio, no proto bundle.

  §1  current(): CURRENT only with a full update on the live connection,
      within the bounds, priced by refdata scales, market OPEN; every other
      case a precise named refusal; evidence always attached
  §2  gaps fail closed: a dropped connection, a backwards venue clock and a
      venue refusal all refuse until a FRESH full update; a straggler on a
      dead connection never revives a book
  §3  staleness: silence and snapshot age past the lane's own bounds
  §4  market identity: no scale -> refused (never a default); hidden, crossed,
      not-open and unknown-state books refused; subscription errors
  §5  the transport: first request carries symbols+depth, never an empty
      subscribe, keepalive, decoding by documented field names, refusal codes,
      bounded reconnect; the token reaches metadata and nowhere else
  §6  start_default: off by default, identity guard, transport unavailable
"""

from __future__ import annotations

import base64
import json
import threading
from datetime import datetime, timezone

import pytest

from sportsassets import institutional_stream as IS
from sportsassets import market_data_identity as MDI

SYM = "aec-mlb-nyy-bos-2026-10-04"
REC = {"symbol": SYM, "priceScale": "1000", "fractionalQtyScale": "100",
       "state": "INSTRUMENT_STATE_OPEN", "productId": "p1"}
T0 = 1_800_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def ts(offset=0.0):
    return datetime.fromtimestamp(T0 + offset, tz=timezone.utc)


def upd(symbol=SYM, *, t=0.0, bids=((450, 1000),), offers=((470, 500),),
        state="INSTRUMENT_STATE_OPEN", hidden=False):
    return {"symbol": symbol, "bids": list(bids), "offers": list(offers),
            "state": state, "transact_time": ts(t) if t is not None else None,
            "book_hidden": hidden}


def running(clock=None, rec=REC):
    b = IS.ResidentBooks(clock=clock or Clock())
    b.set_state(IS.S_IDLE, "test")
    if rec is not None:
        b.set_instrument(SYM, rec)
    b.want([SYM])
    return b


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    yield
    IS.reset()


# ── §1 current() ─────────────────────────────────────────────────────

def test_a_full_update_on_the_live_connection_is_current_with_evidence():
    c = Clock()
    b = running(c)
    seq = b.on_connected("conn-A")
    c.t += 0.2
    b.on_update(upd(t=0.0))
    c.t += 1.0
    r = b.current(SYM)
    assert r["ok"] is True and r["refusal"] is None
    assert r["book"]["best_bid"] == 0.45 and r["book"]["best_offer"] == 0.47
    assert r["book"]["bids"][0]["size"] == 10.0
    ev = r["evidence"]
    assert ev["connection"]["seq"] == seq and ev["connection"]["id"] == "conn-A"
    assert ev["connection"]["connected"] is True
    assert ev["market"] == {"symbol": SYM, "price_scale": 1000,
                            "qty_scale": 100, "product_id": "p1",
                            "state": "INSTRUMENT_STATE_OPEN",
                            "state_source": "STREAM"}
    snap = ev["snapshot"]
    assert snap["on_current_connection"] is True and snap["updates"] == 1
    assert snap["venue_ts"] == ts(0).isoformat()
    assert snap["received_at"] == T0 + 0.2
    assert snap["lag_ms"] == 200.0 and snap["age_s"] == 1.0
    assert ev["depth"] == {"bids": 1, "offers": 1, "requested": IS.DEPTH}
    assert ev["venue_sequence"] == "NOT_PROVIDED_BY_VENUE"
    assert ev["gap"] is None


def test_not_running_not_requested_and_pending_are_named():
    b = IS.ResidentBooks(clock=Clock())
    assert b.current(SYM)["refusal"] == IS.R_NOT_RUNNING
    b = running()
    assert b.current("other")["refusal"] == IS.R_NOT_REQUESTED
    assert b.current(SYM)["refusal"] == IS.R_NO_CONNECTION
    b.on_connected()
    r = b.current(SYM)
    assert r["refusal"] == IS.R_SNAPSHOT_PENDING and r["book"] is None
    assert r["evidence"]["snapshot"]["on_current_connection"] is False


def test_the_module_read_never_raises_and_refuses_when_nothing_runs():
    r = IS.current(SYM)
    assert r["ok"] is False and r["refusal"] == IS.R_NOT_RUNNING
    assert "start_default() has not run" in r["why"]


def test_each_update_replaces_the_book_whole():
    c = Clock()
    b = running(c)
    b.on_connected()
    b.on_update(upd(t=0, bids=((450, 1000), (440, 200))))
    c.t += 1
    b.on_update(upd(t=1, bids=((455, 10),)))
    r = b.current(SYM)
    assert [x["px"] for x in r["book"]["bids"]] == [455]
    assert r["evidence"]["snapshot"]["updates"] == 2


# ── §2 gaps fail closed ──────────────────────────────────────────────

def test_a_dropped_connection_is_a_gap_until_a_fresh_snapshot():
    c = Clock()
    b = running(c)
    b.on_connected("A")
    b.on_update(upd(t=0))
    assert b.current(SYM)["ok"]
    b.on_disconnected("RST_STREAM")
    r = b.current(SYM)
    assert r["refusal"] == IS.R_GAP_CONNECTION
    assert r["evidence"]["gap"]["reason"] == IS.R_GAP_CONNECTION
    # a straggler on the dead connection is NOT a resnapshot
    b.on_update(upd(t=1))
    assert b.current(SYM)["refusal"] == IS.R_GAP_CONNECTION
    # reconnect: still a gap until a full update arrives on THIS connection
    b.on_connected("B")
    assert b.current(SYM)["refusal"] == IS.R_GAP_CONNECTION
    c.t += 0.5
    b.on_update(upd(t=0.5))     # a fresh connection's clock is its own
    r = b.current(SYM)
    assert r["ok"] is True
    assert r["evidence"]["snapshot"]["resnapshots"] == 1
    assert r["evidence"]["connection"]["id"] == "B"
    assert b.digest()["stray_updates"] == 1


def test_a_backwards_venue_clock_is_a_gap_until_the_order_is_restored():
    c = Clock()
    b = running(c)
    b.on_connected()
    b.on_update(upd(t=5))
    b.on_update(upd(t=3, bids=((999, 1),)))       # out of order: not applied
    r = b.current(SYM)
    assert r["refusal"] == IS.R_GAP_CLOCK
    assert r["evidence"]["regressions"] == 1
    assert r["evidence"]["gap"]["high_water"] == ts(5).isoformat()
    # an update without a venue clock cannot show the order restored
    b.on_update(upd(t=None))
    assert b.current(SYM)["refusal"] == IS.R_GAP_CLOCK
    b.on_update(upd(t=6))
    r = b.current(SYM)
    assert r["ok"] is True and r["book"]["bids"][0]["px"] == 450


def test_a_venue_refusal_refuses_everything_and_needs_a_new_connection():
    c = Clock()
    b = running(c)
    b.on_connected()
    b.on_update(upd(t=0))
    b.on_refused("PERMISSION_DENIED")
    r = b.current(SYM)
    assert r["refusal"] == IS.R_REFUSED_KEY and "PERMISSION_DENIED" in r["why"]
    b.on_connected()
    assert b.current(SYM)["refusal"] == IS.R_GAP_CONNECTION
    b.on_update(upd(t=1))
    assert b.current(SYM)["ok"]


def test_gave_up_refuses():
    b = running()
    b.on_gave_up(8)
    assert b.current(SYM)["refusal"] == IS.R_GAVE_UP


def test_an_update_without_a_venue_timestamp_is_refused():
    b = running()
    b.on_connected()
    b.on_update(upd(t=None))
    assert b.current(SYM)["refusal"] == IS.R_NO_VENUE_TS


# ── §3 staleness ─────────────────────────────────────────────────────

def test_silence_past_the_liveness_bound_is_stale():
    c = Clock()
    b = running(c)
    b.on_connected()
    b.on_update(upd(t=0))
    c.t += IS.MAX_SILENCE_S + 0.1
    assert b.current(SYM)["refusal"] == IS.R_SILENT
    b.on_heartbeat()
    assert b.current(SYM)["ok"]


def test_a_snapshot_past_the_bound_is_stale_even_with_heartbeats():
    c = Clock()
    b = running(c)
    b.on_connected()
    b.on_update(upd(t=0))
    for _ in range(4):
        c.t += 10
        b.on_heartbeat()
    r = b.current(SYM)
    assert c.t - T0 > IS.MAX_SNAPSHOT_AGE_S
    assert r["refusal"] == IS.R_SNAPSHOT_OLD


def test_the_bounds_are_the_lanes_own():
    from sportsassets import bettor_stream_currency as sc
    assert IS.MAX_SILENCE_S == sc.MAX_SILENCE_S
    assert IS.MAX_SNAPSHOT_AGE_S == sc.MAX_SNAPSHOT_AGE_S


# ── §4 market identity and market state ──────────────────────────────

def test_no_refdata_scale_is_refused_never_defaulted():
    b = running(rec=None)
    b.on_connected()
    b.on_update(upd(t=0))
    assert b.current(SYM)["refusal"] == IS.R_NO_SCALE
    b.set_instrument(SYM, {"priceScale": "0", "fractionalQtyScale": "100"})
    assert b.current(SYM)["refusal"] == IS.R_NO_SCALE


def test_hidden_crossed_closed_and_unknown_state_books_are_refused():
    b = running()
    b.on_connected()
    b.on_update(upd(t=0, hidden=True))
    assert b.current(SYM)["refusal"] == IS.R_BOOK_HIDDEN
    b.on_update(upd(t=1, bids=((480, 1),), offers=((470, 1),)))
    assert b.current(SYM)["refusal"] == IS.R_CROSSED
    b.on_update(upd(t=2, state="INSTRUMENT_STATE_HALTED"))
    r = b.current(SYM)
    assert r["refusal"] == IS.R_NOT_OPEN and "HALTED" in r["why"]
    b2 = running(rec={"priceScale": "1000", "fractionalQtyScale": "100"})
    b2.on_connected()
    b2.on_update(upd(t=0, state=None))
    assert b2.current(SYM)["refusal"] == IS.R_STATE_UNKNOWN


def test_refdata_state_is_used_when_the_stream_omits_it():
    b = running()
    b.on_connected()
    b.on_update(upd(t=0, state=None))
    r = b.current(SYM)
    assert r["ok"] and r["evidence"]["market"]["state_source"] == "REFDATA"


def test_subscription_errors():
    b = running()
    b.on_connected()
    b.on_subscription_error("ALREADY_SUBSCRIBED", "dup", [SYM])
    assert b.current(SYM)["refusal"] == IS.R_SNAPSHOT_PENDING
    b.on_subscription_error("INVALID_SYMBOL", "no such", [SYM])
    r = b.current(SYM)
    assert r["refusal"] == IS.R_REFUSED_SYMBOL and "INVALID_SYMBOL" in r["why"]
    assert b.digest()["subscription_errors"][-1]["code"] == "INVALID_SYMBOL"


# ── §5 the transport, on fake generated modules ──────────────────────

class _Msg:
    """A stand-in for a generated protobuf message: attributes + HasField."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def HasField(self, name):
        return self.__dict__.get(name) is not None


class FakePb2:
    @staticmethod
    def BiDirectionalStreamMarketDataRequest(**kw):
        return ("REQ", kw)

    @staticmethod
    def SubscribeCommand(symbols):
        return ("SUB", tuple(symbols))

    @staticmethod
    def KeepAliveCommand():
        return ("KEEPALIVE",)


class FakeStatus:
    def __init__(self, name):
        self.name = name


class FakeRpcError(Exception):
    def __init__(self, name):
        super().__init__(name)
        self._name = name

    def code(self):
        return FakeStatus(self._name)


class FakeRefdata:
    class InstrumentState:
        @staticmethod
        def Name(v):
            return {1: "INSTRUMENT_STATE_OPEN",
                    6: "INSTRUMENT_STATE_HALTED"}[v]


def grpc_update(symbol=SYM, secs=int(T0), nanos=0, state=1, hidden=False,
                bids=((450, 1000),), offers=((470, 500),)):
    entries = lambda side: [_Msg(px=p, qty=q) for p, q in side]  # noqa: E731
    return _Msg(symbol=symbol, bids=entries(bids), offers=entries(offers),
                state=state,
                transact_time=_Msg(seconds=secs, nanos=nanos),
                book_hidden=hidden)


class FakeVenue:
    """grpc + pb2_grpc in one: records the channel target, the metadata and
    every request; serves a scripted list of responses or raises."""

    def __init__(self, script):
        self.script = list(script)
        self.targets, self.metadata, self.requests = [], [], []
        self.closed = 0

    # grpc module surface
    def ssl_channel_credentials(self):
        return "TLS"

    def secure_channel(self, target, creds, options=None):
        assert creds == "TLS"
        assert dict(options or ()) == dict(IS.CHANNEL_OPTIONS)
        self.targets.append(target)
        venue = self

        class Ch:
            def close(self_inner):
                venue.closed += 1
        return Ch()

    # pb2_grpc module surface
    def MarketDataSubscriptionAPIStub(self, channel):
        venue = self

        class Stub:
            def BiDirectionalStreamMarketData(self_inner, requests, metadata):
                venue.metadata.append(metadata)
                venue.requests.append(next(requests))    # the first request
                step = venue.script.pop(0)

                def gen():
                    for item in step:
                        if isinstance(item, Exception):
                            raise item
                        yield item
                return gen()
        return Stub()


def make_transport(books, venue, token="tok-NOT-REAL-123", clock=None):
    return IS.GrpcBidiTransport(
        books, lambda: token,
        modules=(venue, FakePb2, venue, FakeRefdata),
        clock=clock or Clock(), sleep=lambda s: None)


def test_decode_update_uses_the_documented_field_names():
    d = IS.decode_update(grpc_update(nanos=500_000_000), FakeRefdata)
    assert d == {"symbol": SYM, "bids": [(450, 1000)], "offers": [(470, 500)],
                 "state": "INSTRUMENT_STATE_OPEN",
                 "transact_time": ts(0.5), "book_hidden": False}
    d = IS.decode_update(_Msg(symbol=SYM, bids=[], offers=[], state=None,
                              transact_time=None, book_hidden=False))
    assert d["state"] is None and d["transact_time"] is None


def test_one_connection_end_to_end():
    c = Clock()
    books = running(c)
    hb = _Msg(heartbeat=_Msg(), update=None, subscription_ack=None,
              subscription_error=None)
    ack = _Msg(heartbeat=None, update=None, subscription_error=None,
               subscription_ack=_Msg(symbols_added=[SYM], symbols_removed=[],
                                     active_symbols=[SYM]))
    up = _Msg(heartbeat=None, subscription_ack=None, subscription_error=None,
              update=grpc_update())
    venue = FakeVenue([[ack, hb, up]])
    t = make_transport(books, venue, clock=c)
    got = t.run_once()
    assert got == "ended"
    assert venue.targets == ["grpc-api.prod.polymarketexchange.com:443"]
    assert venue.requests[0] == ("REQ", {"subscribe": ("SUB", (SYM,)),
                                         "depth": IS.DEPTH})
    assert venue.metadata == [[("authorization", "Bearer tok-NOT-REAL-123")]]
    assert venue.closed == 1
    # the server completed the stream -> the book is a gap now, not current
    r = books.current(SYM)
    assert r["refusal"] == IS.R_GAP_CONNECTION
    assert r["evidence"]["snapshot"]["updates"] == 1
    assert "tok-NOT-REAL-123" not in json.dumps(books.digest(), default=str)
    assert "tok-NOT-REAL-123" not in json.dumps(r, default=str)


def test_the_book_is_current_while_the_stream_is_live():
    c = Clock()
    books = running(c)
    seen = {}

    class Probe:
        def HasField(self, name):
            # evaluated mid-stream: read the book while the connection lives
            seen["r"] = books.current(SYM)
            return False

    up = _Msg(heartbeat=None, subscription_ack=None, subscription_error=None,
              update=grpc_update())
    venue = FakeVenue([[up, Probe()]])
    make_transport(books, venue, clock=c).run_once()
    assert seen["r"]["ok"] is True


def test_never_an_empty_subscribe():
    books = running()
    books2 = IS.ResidentBooks(clock=Clock())
    books2.set_state(IS.S_IDLE, "x")
    venue = FakeVenue([])
    t = make_transport(books2, venue)
    assert t.run_once() == "idle"
    assert venue.targets == [] and venue.requests == []
    assert books.wanted() == [SYM]


@pytest.mark.parametrize("code", ["UNAUTHENTICATED", "PERMISSION_DENIED"])
def test_auth_failures_are_refusals(code):
    books = running()
    venue = FakeVenue([[FakeRpcError(code)]])
    assert make_transport(books, venue).run_once() == "refused"
    assert books.current(SYM)["refusal"] == IS.R_REFUSED_KEY


def test_no_token_is_a_refusal_and_opens_nothing():
    books = running()
    venue = FakeVenue([])
    t = make_transport(books, venue, token=None)
    assert t.run_once() == "refused"
    assert venue.targets == []


def test_transient_errors_back_off_and_the_bound_gives_up():
    books = running()
    venue = FakeVenue([[FakeRpcError("UNAVAILABLE")]
                       for _ in range(IS.MAX_CONSECUTIVE_FAILURES)])
    sleeps = []
    t = IS.GrpcBidiTransport(books, lambda: "tok",
                             modules=(venue, FakePb2, venue, FakeRefdata),
                             clock=Clock(), sleep=None)

    def sleep(s):
        sleeps.append(s)
        if s == IS.RESTART_AFTER_GAVE_UP_S:
            t.stop()
    t._sleep = sleep
    t.run()
    assert books.state == IS.S_GAVE_UP
    assert sleeps[:3] == list(IS.RECONNECT_BACKOFF_S[1:4])
    assert sleeps[-1] == IS.RESTART_AFTER_GAVE_UP_S
    assert t.attempts == IS.MAX_CONSECUTIVE_FAILURES


def test_keepalive_after_idle():
    c = Clock()
    books = running(c)
    t = make_transport(books, FakeVenue([]), clock=c)
    gen = t._requests("FIRST")
    assert next(gen) == "FIRST"
    c.t += IS.KEEPALIVE_S + 1
    assert next(gen) == ("REQ", {"keepalive": ("KEEPALIVE",)})
    t.subscribe(["new-sym"])
    assert next(gen) == ("REQ", {"subscribe": ("SUB", ("new-sym",))})
    t.subscribe(["new-sym"])                      # already on the stream
    assert t._q.empty()
    t.stop()


def test_the_transport_source_has_no_order_path():
    import inspect
    src = inspect.getsource(IS).lower()
    for verb in ("insertorder", "cancelorder", "place_order", "submit_order",
                 "orderentryapi", "/v1/trading"):
        assert verb not in src


# ── §6 start_default ─────────────────────────────────────────────────

def _pmx_env(**extra):
    env = {"PMX_CLIENT_ID": "cid-not-real", "PMX_KEY_ID": "kid-not-real",
           "PMX_PRIVATE_KEY_B64": base64.b64encode(b"x").decode(),
           "PMX_PARTICIPANT_ID": "firms/x/users/y",
           "PMUS_KEY_ID": "funded-not-real"}
    env.update(extra)
    return env


def test_start_default_is_off_by_default():
    got = IS.start_default(env=_pmx_env())
    assert got["state"] == IS.S_DISABLED and got["started"] is False
    assert IS.current(SYM)["refusal"] == IS.R_NOT_RUNNING


def test_start_default_refuses_an_execution_identity():
    env = _pmx_env(INSTITUTIONAL_MD_STREAM="on", PMX_KEY_ID="funded-not-real")
    got = IS.start_default(env=env, available=lambda: (True, None))
    assert got["state"] == IS.S_CREDENTIAL
    assert got["why"] == MDI.G_IS_FUNDED
    got = IS.start_default(env={"INSTITUTIONAL_MD_STREAM": "on"},
                           available=lambda: (True, None))
    assert got["why"] == MDI.G_ABSENT


def test_start_default_reports_a_missing_transport():
    got = IS.start_default(env=_pmx_env(INSTITUTIONAL_MD_STREAM="on"))
    ok, _why = IS.transport_available()
    if not ok:
        assert got["state"] == IS.S_TRANSPORT_UNAVAILABLE
        assert "polymarket.v1" in got["why"] or "grpc" in got["why"]
        assert IS.current(SYM)["refusal"] == IS.R_NOT_RUNNING


def test_start_default_arms_a_transport_with_the_guarded_credential():
    built = []

    class T:
        def __init__(self, books, token_fn):
            built.append((books, token_fn))
            self.subs = []

        def start(self):
            pass

        def subscribe(self, s):
            self.subs.append(list(s))

        def stop(self):
            pass

    got = IS.start_default(env=_pmx_env(INSTITUTIONAL_MD_STREAM="on"),
                           transport_factory=T, token_fn=lambda: "t",
                           available=lambda: (True, None))
    assert got["started"] is True and len(built) == 1
    assert IS.want([SYM]) == {"queued": 1}
    assert IS._TRANSPORT.subs == [[SYM]]
    IS.set_instrument(SYM, REC)
    r = IS.current(SYM)
    assert r["refusal"] == IS.R_NO_CONNECTION
    d = IS.digest()
    assert d["start"]["state"] == IS.S_IDLE and d["symbols"] == 1
    again = IS.start_default(env=_pmx_env(INSTITUTIONAL_MD_STREAM="on"),
                             transport_factory=T, token_fn=lambda: "t",
                             available=lambda: (True, None))
    assert again["started"] is False and len(built) == 1


def test_concurrent_reads_and_writes_do_not_raise():
    c = Clock()
    b = running(c)
    b.on_connected()
    errors = []

    def writer():
        try:
            for i in range(300):
                b.on_update(upd(t=i * 0.001))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader():
        try:
            for _ in range(300):
                b.current(SYM)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    th = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    assert not errors and b.current(SYM)["ok"]


def test_a_silent_stream_is_cancelled_by_the_watchdog():
    c = Clock()
    books = running(c)
    assert books.silence_s() is None          # no connection, no silence
    books.on_connected()
    t = make_transport(books, FakeVenue([]), clock=c)
    cancelled = threading.Event()

    class Call:
        def cancel(self):
            cancelled.set()

    done = t._watchdog(Call())
    try:
        c.t += IS.WATCHDOG_S - 1
        assert not cancelled.wait(1.5)
        c.t += 2
        assert cancelled.wait(5.0)
    finally:
        done.set()


def test_a_symbol_asked_for_mid_connect_is_not_lost():
    c = Clock()
    books = running(c)
    venue = FakeVenue([[]])
    t = make_transport(books, venue, clock=c)
    real_wanted = books.wanted

    def wanted_then_ask():
        out = real_wanted()
        books.want(["late-sym"])
        t.subscribe(["late-sym"])
        return out
    books.wanted = wanted_then_ask
    t.run_once()
    assert ("REQ", {"subscribe": ("SUB", ("late-sym",))}) == t._q.get_nowait()
