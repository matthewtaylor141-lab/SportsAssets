"""THE DECISION PROCESS'S MARKET-DATA SUBSCRIPTION, DRIVEN THROUGH A FAKE VENUE.

No network: the container cannot reach the venue, and a test that needed it
would prove nothing about the code. Every test here drives the REAL
`MarketStream._main` socket loop (or the real supervisor's event handlers) with
a fake transport that speaks the five SDK calls the stream uses -- `on`,
`connect`, `subscribe_market_data`, `subscribe_trades`, `close` -- plus
`is_connected`.

  §1  lifecycle: connect, subscribe the wanted markets (book only), reach
      CURRENT, clean shutdown; the credential goes to the transport and nowhere
      else
  §2  reconnect: exponential backoff, a bound that ends the loop, a handshake
      refusal that is never retried, resubscription after a reconnect, and the
      historic loop unchanged for the callers that predate the bound
  §3  gap: a drop is a GAP and the book is not CURRENT again until a full book
      arrives on the new connection; a backwards venue clock is a GAP too
  §4  staleness: silence past the liveness bound, a book past the snapshot
      bound, the silence watchdog, a dead socket
  §5  readiness and refusal: every state, named; a venue error refuses the
      markets it names; nothing installed is NOT_SUBSCRIBED
  §6  the decision: currency passes ONLY when the subscription says CURRENT and
      P5 holds, and is refused by name otherwise -- with the part of M1 that
      was missing named beside the unchanged refusal
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import time
import types

import pytest

from sportsassets import bettor_market_stream as MS
from sportsassets import bettor_market_subscription as SUB
from sportsassets import bettor_stream_currency as SC
from sportsassets import bettor_venue_currency as VC
from sportsassets.workers import ext_pinnacle_loop as L

#: Dummy credential strings. Not a real key; asserted never to leak.
KEY_ID = "test-key-id-not-real"
SECRET = "test-secret-DO-NOT-LEAK-7f3a"


@pytest.fixture(autouse=True)
def _clean():
    SC.reset()
    SUB.reset()
    yield
    sub = SUB.active()
    if sub is not None:
        sub.stop(wait_s=2.0)
    SUB.reset()
    SC.reset()


# ═════════════════════════════════════════════════════════════════════
# THE FAKE VENUE
# ═════════════════════════════════════════════════════════════════════

def _iso(t):
    return (_dt.datetime.fromtimestamp(t, _dt.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z")


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += float(s)


class HandshakeRefused(Exception):
    """Shaped like websockets' InvalidStatus: `.response.status_code`."""

    def __init__(self, status):
        super().__init__("server rejected WebSocket connection: HTTP %d"
                         % status)
        self.response = types.SimpleNamespace(status_code=status)


class VenueError(Exception):
    """Shaped like the SDK's WebSocketError: message + `.request_id`."""

    def __init__(self, message, request_id):
        super().__init__(message)
        self.request_id = request_id


class FakeWS:
    """One connection. `plan` scripts it:

      connect      None to succeed, or an exception instance to raise
      on_subscribe [f(ws, rid, slugs)] run when a subscribe arrives
      ticks        [f(ws)] run one per socket-loop iteration AFTER the first
                   subscribe (the loop reads `is_connected` once per pass)
      then         "close" (peer closes when ticks run out; default),
                   "stop" (the test ends the stream), or "stay" (stays open
                   until closed -- for threaded tests)
    """

    def __init__(self, venue, plan, key_id, secret_key):
        self.venue, self.plan = venue, plan
        self.key_id, self.secret_key = key_id, secret_key
        self.listeners = {}
        self.sent = []
        self.open = False
        self.closed = False
        self._subscribed = False

    def on(self, ev, cb):
        self.listeners.setdefault(ev, []).append(cb)
        return self

    def emit(self, ev, *a):
        for cb in list(self.listeners.get(ev, [])):
            cb(*a)

    async def connect(self):
        err = self.plan.get("connect")
        if err is not None:
            raise err
        self.open = True

    async def subscribe_market_data(self, rid, slugs):
        self.sent.append(("book", rid, list(slugs)))
        self._subscribed = True
        for f in self.plan.get("on_subscribe") or ():
            f(self, rid, list(slugs))

    async def subscribe_trades(self, rid, slugs):
        self.sent.append(("trade", rid, list(slugs)))

    async def close(self):
        self.closed = True
        self.open = False

    @property
    def is_connected(self):
        if not self.open:
            return False
        if self._subscribed:
            ticks = self.plan.setdefault("ticks", [])
            if ticks:
                ticks.pop(0)(self)
            else:
                then = self.plan.get("then", "close")
                if then == "close":
                    self.peer_close()
                elif then == "stop":
                    self.venue.stream._stop = True
        return self.open

    # ── what a test can make the venue do ──
    def book(self, slug, ts=None, *, state="MARKET_STATE_OPEN", no_ts=False):
        md = {"marketSlug": slug, "state": state, "stats": {},
              "bids": [{"px": {"value": "0.60"}, "qty": "400"}],
              "offers": [{"px": {"value": "0.62"}, "qty": "400"}]}
        if not no_ts:
            md["transactTime"] = _iso(ts if ts is not None else time.time())
        self.emit("market_data", {"marketData": md})

    def heartbeat(self):
        self.emit("heartbeat")

    def error(self, message, rid):
        self.emit("error", VenueError(message, rid))

    def peer_close(self):
        self.open = False
        self.emit("close")


class FakeVenue:
    """The transport factory: one FakeWS per connection attempt, from a list
    of plans. Once the plans run out every further attempt is refused at the
    TCP level (ConnectionRefusedError), which is a plain failure."""

    def __init__(self, plans):
        self.plans = list(plans)
        self.sockets = []
        self.credentials_seen = []
        self.stream = None

    def __call__(self, *, key_id, secret_key):
        self.credentials_seen.append((key_id, secret_key))
        plan = (self.plans.pop(0) if self.plans
                else {"connect": ConnectionRefusedError("no venue")})
        ws = FakeWS(self, plan, key_id, secret_key)
        self.sockets.append(ws)
        return ws


class SleepRecorder:
    def __init__(self, hook=None):
        self.calls = []
        self.hook = hook

    async def __call__(self, s):
        self.calls.append(s)
        if self.hook:
            self.hook(len(self.calls))
        await asyncio.sleep(0)


def _sub(venue, *, clock=None, policy=None, wanted=("aec-a",)):
    """A supervisor over the REAL MarketStream on the fake transport, with the
    socket loop left for the test to drive in this thread."""
    s = SUB.MarketSubscription(KEY_ID, SECRET, ws_factory=venue,
                               clock=clock or time.time,
                               policy=policy or {"max_consecutive_failures": 3,
                                                 "base_s": 1.0, "cap_s": 4.0})
    SUB.install(s)
    s.want(list(wanted), now=(clock or time.time)())
    s.start(run_thread=False)
    venue.stream = s.stream
    s.stream._idle_s = 0.0
    s.stream._sleep = SleepRecorder()
    return s


def _drive(s):
    asyncio.run(s.stream._main())


# ═════════════════════════════════════════════════════════════════════
# §1 · LIFECYCLE
# ═════════════════════════════════════════════════════════════════════

def test_it_connects_subscribes_the_wanted_books_and_reaches_current(caplog):
    seen = {}

    def _books(ws, rid, slugs):
        for sl in slugs:
            ws.book(sl)

    def _look(ws):
        seen["a"] = SUB.readiness("aec-a")
        seen["b"] = SUB.readiness("aec-b")

    venue = FakeVenue([{"on_subscribe": [_books], "ticks": [_look],
                        "then": "stop"}])
    caplog.set_level(logging.DEBUG)
    s = _sub(venue, wanted=("aec-a", "aec-b"))
    _drive(s)

    assert seen["a"]["state"] == SUB.CURRENT, seen["a"]
    assert seen["b"]["state"] == SUB.CURRENT, seen["b"]
    assert seen["a"]["reason"] == SUB.R_CURRENT
    # ONE BOOK SUBSCRIPTION, BOTH MARKETS, NO TRADE SUBSCRIPTION: the decision
    # process reads books and nothing else.
    ws = venue.sockets[0]
    assert [k for k, _, _ in ws.sent] == ["book"]
    assert sorted(ws.sent[0][2]) == ["aec-a", "aec-b"]
    # THE CREDENTIAL WENT TO THE TRANSPORT'S CONSTRUCTOR -- the existing path --
    # and nowhere else: not the digest, not a readiness, not a log line.
    assert venue.credentials_seen == [(KEY_ID, SECRET)]
    blob = json.dumps([SUB.heartbeat_digest(), seen, SUB.describe(),
                       s.stream.stats()], default=str)
    assert SECRET not in blob and KEY_ID not in blob
    assert SECRET not in caplog.text and KEY_ID not in caplog.text


def test_a_clean_shutdown_closes_a_live_socket_on_its_own_thread():
    venue = FakeVenue([{"on_subscribe": [lambda ws, rid, sl: ws.book(sl[0])],
                        "then": "stay"}])
    s = SUB.MarketSubscription(KEY_ID, SECRET, ws_factory=venue)
    SUB.install(s)
    s.want(["aec-a"])
    assert s.start()["started"] is True
    s.stream._idle_s = 0.005
    deadline = time.time() + 5.0
    while SUB.readiness("aec-a")["state"] != SUB.CURRENT:
        assert time.time() < deadline, SUB.readiness("aec-a")
        time.sleep(0.01)
    got = s.stop(wait_s=5.0)
    # THE STREAM'S OWN MEASURED VERDICT: the socket was live and was closed.
    assert got["stream"]["shutdown"] == MS.MarketStream.SHUT_CLOSED
    assert got["stream"]["active_close_exercised"] is True
    assert venue.sockets[0].closed is True
    assert s.state == SUB.S_STOPPED
    # AND A STOPPED SUBSCRIPTION NEVER ANSWERS CURRENT.
    r = SUB.readiness("aec-a")
    assert r["state"] == SUB.NOT_SUBSCRIBED and r["reason"] == SUB.R_NOT_RUNNING


# ═════════════════════════════════════════════════════════════════════
# §2 · RECONNECT AND RECOVERY
# ═════════════════════════════════════════════════════════════════════

def test_failed_connects_back_off_exponentially_and_the_loop_ends():
    venue = FakeVenue([])                     # every attempt refused at TCP
    s = _sub(venue, policy={"max_consecutive_failures": 5, "base_s": 1.0,
                            "cap_s": 4.0})
    _drive(s)                                 # RETURNS: the loop is finite
    st = s.stream
    assert st.socket_connect_attempts == 5
    assert st._sleep.calls == [1.0, 2.0, 4.0, 4.0], (
        "doubling from base, capped, and no sleep after the last attempt")
    assert st.gave_up and st.gave_up["consecutive_failures"] == 5
    assert s.state == SUB.S_GAVE_UP
    r = SUB.readiness("aec-a")
    assert (r["state"], r["reason"]) == (SUB.GAP, SUB.R_GAVE_UP)


def test_a_policy_cannot_be_made_unbounded_by_a_zero():
    p = MS._normalise_policy({"max_consecutive_failures": 0, "base_s": -3,
                              "cap_s": 0})
    assert p["max_consecutive_failures"] == 1
    assert p["base_s"] == 0.0 and p["cap_s"] >= p["base_s"]


def test_a_connection_that_delivers_resets_the_bound_and_the_backoff():
    books = {"on_subscribe": [lambda ws, rid, sl: ws.book(sl[0])]}
    # fail, fail, deliver-then-drop, fail, fail, fail -> gives up at 3 in a row
    venue = FakeVenue([{"connect": ConnectionRefusedError()},
                       {"connect": ConnectionRefusedError()},
                       dict(books)])
    s = _sub(venue, policy={"max_consecutive_failures": 3, "base_s": 1.0,
                            "cap_s": 8.0})
    _drive(s)
    # 1, 2 after two failures; the delivering connection RESETS to base (1),
    # then 2, 4 after two more failures, and the third in a row ends it with
    # no further sleep.
    assert s.stream._sleep.calls == [1.0, 2.0, 1.0, 2.0, 4.0]
    assert s.stream.socket_connect_attempts == 6
    assert s.stream.gave_up is not None


def test_a_handshake_refusal_is_the_venue_refusing_the_key_and_is_not_retried():
    venue = FakeVenue([{"connect": HandshakeRefused(403)}])
    s = _sub(venue)
    _drive(s)
    assert s.stream.socket_connect_attempts == 1, "no retry of a refusal"
    assert s.stream._sleep.calls == [], "not even one backoff"
    assert s.stream.venue_refusal["status"] == 403
    assert s.state == SUB.S_REFUSED_BY_VENUE
    r = SUB.readiness("aec-a")
    assert r["state"] == SUB.REFUSED_BY_VENUE
    assert r["reason"] == SUB.R_VENUE_REFUSED_KEY
    assert r["venue_status"] == 403
    d = SUB.heartbeat_digest()
    assert d["subscription_state"] == SUB.S_REFUSED_BY_VENUE
    assert d["venue_refusal"]["status"] == 403
    assert d["by_readiness"] == {SUB.REFUSED_BY_VENUE: 1}


def test_after_a_refusal_one_fresh_attempt_is_made_per_interval_and_no_more():
    clk = Clock()
    venue = FakeVenue([{"connect": HandshakeRefused(401)},
                       {"connect": HandshakeRefused(401)}])
    s = _sub(venue, clock=clk)
    _drive(s)
    assert s.state == SUB.S_REFUSED_BY_VENUE and s._starts == 1
    clk.advance(SUB.RESTART_AFTER_VENUE_REFUSAL_S - 1)
    s.want(["aec-a"])
    assert s._starts == 1, "not before the interval"
    clk.advance(2)
    s.want(["aec-a"])
    assert s._starts == 2, "one fresh, bounded attempt after it"
    s.stream._sleep = SleepRecorder()
    s.stream._idle_s = 0.0
    _drive(s)
    assert s.state == SUB.S_REFUSED_BY_VENUE
    assert len(venue.sockets) == 2


def test_a_reconnect_resubscribes_every_market():
    books = {"on_subscribe": [lambda ws, rid, sl: [ws.book(x) for x in sl]]}
    venue = FakeVenue([dict(books), dict(books, then="stop")])
    s = _sub(venue, wanted=("aec-a", "aec-b"))
    _drive(s)
    first, second = venue.sockets[0], venue.sockets[1]
    assert sorted(first.sent[0][2]) == ["aec-a", "aec-b"]
    assert sorted(second.sent[0][2]) == ["aec-a", "aec-b"], (
        "a subscription does not survive a socket; every market is re-asked")
    assert s.stream.epoch == 2


def test_the_historic_loop_is_unchanged_for_the_callers_that_predate_the_bound():
    """The protected live loop and the incentive observer construct the stream
    with no policy. They keep the old loop: 1 s doubling to 30 s, a refusal
    retried like any failure, no watchdog. Only the new subscription is
    bounded."""
    venue = FakeVenue([{"connect": HandshakeRefused(403)}] * 3)
    st = MS.MarketStream(KEY_ID, SECRET, ws_factory=venue)
    assert st._policy is None

    def _stop_after(n):
        if n >= 7:
            st._stop = True
    st._sleep = SleepRecorder(_stop_after)
    asyncio.run(st._main())
    assert st._sleep.calls == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert st.venue_refusal is None and st.gave_up is None
    assert st.socket_connect_attempts == 7


# ═════════════════════════════════════════════════════════════════════
# §3 · GAP: NOT CURRENT ACROSS A GAP UNTIL RE-SNAPSHOTTED
# ═════════════════════════════════════════════════════════════════════

def test_a_drop_is_a_gap_and_the_book_is_not_current_until_resnapshotted():
    seen = []
    look = lambda tag: (lambda *a: seen.append(
        (tag, SUB.readiness("aec-a")["state"],
         SUB.readiness("aec-a")["reason"])))

    conn1 = {"on_subscribe": [lambda ws, rid, sl: ws.book("aec-a")],
             "ticks": [look("conn1-after-book")], "then": "close"}
    conn2 = {"on_subscribe": [look("conn2-before-send")],
             "ticks": [look("conn2-sent-no-book"),
                       lambda ws: ws.book("aec-a"),
                       look("conn2-after-book")],
             "then": "stop"}
    venue = FakeVenue([conn1, conn2])
    s = _sub(venue)
    s.stream._sleep = SleepRecorder(lambda n: look("during-backoff")())
    _drive(s)
    assert seen == [
        ("conn1-after-book", SUB.CURRENT, SUB.R_CURRENT),
        ("during-backoff", SUB.GAP, SUB.R_CONNECTION_LOST),
        ("conn2-before-send", SUB.GAP, SUB.R_CONNECTION_LOST),
        ("conn2-sent-no-book", SUB.SNAPSHOT_PENDING, SUB.R_AWAITING_RESNAPSHOT),
        ("conn2-after-book", SUB.CURRENT, SUB.R_CURRENT),
    ], seen


def test_the_currency_module_agrees_that_the_old_book_was_discarded():
    """P3 in `bettor_stream_currency` is driven by the same stream: after the
    drop and before the resnapshot it holds nothing for the market."""
    seen = {}
    conn1 = {"on_subscribe": [lambda ws, rid, sl: ws.book("aec-a")]}
    conn2 = {"on_subscribe": [lambda ws, rid, sl: seen.update(
        p3=SC.subscription_state("aec-a")["checks"][
            SC.P3_CONNECTION_CONTINUITY]["met"])],
        "then": "stop"}
    venue = FakeVenue([conn1, conn2])
    s = _sub(venue)
    _drive(s)
    assert seen["p3"] is False


def test_a_backwards_venue_clock_is_a_gap_until_the_clock_recovers():
    t0 = time.time()
    seen = []
    look = lambda *a: seen.append(SUB.readiness("aec-a")["reason"])
    conn = {"on_subscribe": [lambda ws, rid, sl: ws.book("aec-a", t0)],
            "ticks": [look,
                      lambda ws: ws.book("aec-a", t0 - 5.0), look,
                      lambda ws: ws.book("aec-a", no_ts=True), look,
                      lambda ws: ws.book("aec-a", t0 + 1.0), look],
            "then": "stop"}
    venue = FakeVenue([conn])
    s = _sub(venue)
    _drive(s)
    assert seen == [SUB.R_CURRENT,
                    SUB.R_SOURCE_CLOCK_REGRESSED,
                    SUB.R_SOURCE_CLOCK_REGRESSED,   # no clock: cannot recover
                    SUB.R_CURRENT]
    assert s._markets["aec-a"]["regressions"] == 1
    assert s._markets["aec-a"]["resnapshots"] == 1


def test_the_payload_has_no_sequence_so_gaps_are_detected_the_ways_that_exist():
    assert "no sequence number" in SUB.describe()["gap_detection"]
    assert SC.FEED_CONTRACT["has_sequence_number"] is False


# ═════════════════════════════════════════════════════════════════════
# §4 · STALENESS
# ═════════════════════════════════════════════════════════════════════

def test_silence_and_an_old_book_are_stale_and_life_signs_are_not_currency():
    clk = Clock()
    seen = {}

    def _script(ws):
        t = clk()
        seen["t0"] = SUB.readiness("aec-a")["state"]
        seen["silent"] = SUB.readiness("aec-a", now=t + SC.MAX_SILENCE_S + 1)
        clk.advance(SC.MAX_SILENCE_S + 1)
        ws.heartbeat()                      # life, not a book
        seen["alive_again"] = SUB.readiness("aec-a")["state"]
        clk.advance(SC.MAX_SNAPSHOT_AGE_S)
        ws.heartbeat()
        seen["old_book"] = SUB.readiness("aec-a")

    conn = {"on_subscribe": [lambda ws, rid, sl: ws.book("aec-a")],
            "ticks": [_script], "then": "stop"}
    venue = FakeVenue([conn])
    s = _sub(venue, clock=clk)
    _drive(s)
    assert seen["t0"] == SUB.CURRENT
    assert (seen["silent"]["state"], seen["silent"]["reason"]) == (
        SUB.STALE, SUB.R_SILENT)
    assert seen["alive_again"] == SUB.CURRENT
    assert (seen["old_book"]["state"], seen["old_book"]["reason"]) == (
        SUB.STALE, SUB.R_BOOK_OLD), (
        "heartbeats prove the socket, not the book: a book older than the "
        "snapshot bound is STALE however alive the connection is")


def test_a_silent_socket_is_reconnected_by_the_watchdog():
    """An open socket with no frame and no heartbeat past
    `silence_reconnect_s` is treated as dead: closed, and reconnected."""
    off = {"s": 0.0}

    class _Venue(FakeVenue):
        def __call__(self, **kw):
            off["s"] = 0.0                  # a new connection, a fresh clock
            return super().__call__(**kw)

    def _go_silent(ws):
        off["s"] = MS.RECONNECT_POLICY_DEFAULTS["silence_reconnect_s"] + 10

    conn1 = {"on_subscribe": [lambda ws, rid, sl: ws.book("aec-a")],
             "ticks": [_go_silent], "then": "stay"}
    venue = _Venue([conn1, {"then": "stop"}])
    s = _sub(venue)
    s.stream._clock = lambda: time.time() + off["s"]
    seen = []
    s.stream._sleep = SleepRecorder(lambda n: seen.append(
        dict(s._last_disconnect or {})))
    _drive(s)
    assert venue.sockets[0].closed is True
    assert seen[0]["why"] == MS.LOST_SILENCE_WATCHDOG
    assert len(venue.sockets) == 2, "the watchdog forced one reconnect"


def test_a_dead_socket_that_never_said_close_is_detected():
    """The SDK's message loop can end on a non-close exception, emitting
    'error' and never 'close'. `is_connected` false ends the connection."""
    def _die(ws):
        ws.open = False                     # no 'close' event
    conn = {"on_subscribe": [lambda ws, rid, sl: ws.book("aec-a")],
            "ticks": [_die], "then": "stay"}
    venue = FakeVenue([conn, {"then": "stop"}])
    s = _sub(venue)
    _drive(s)
    assert len(venue.sockets) == 2
    assert s._markets["aec-a"]["gap"]["reason"] == SUB.R_CONNECTION_LOST


# ═════════════════════════════════════════════════════════════════════
# §5 · READINESS STATES AND VENUE REFUSAL
# ═════════════════════════════════════════════════════════════════════

def test_readiness_walks_through_its_states_with_a_named_reason_each():
    clk = Clock()
    states = []
    rec = lambda *a: states.append(SUB.readiness("aec-a")["state"])

    s = SUB.MarketSubscription(KEY_ID, SECRET, clock=clk,
                               ws_factory=FakeVenue([]))
    SUB.install(s)
    rec()                                                  # not started
    s.want(["aec-a"])
    s._set_state(SUB.S_CONNECTING, "test")
    rec()                                                  # no connection
    s._on_lifecycle("connected", {"epoch": 1})
    rec()                                                  # not yet sent
    s._on_lifecycle("subscribe_sent", {"kind": "book", "slugs": ["aec-a"]})
    rec()
    s._on_book("aec-a", {"source_ts": _iso(clk()), "state": "MARKET_STATE_OPEN"})
    rec()
    clk.advance(SC.MAX_SILENCE_S + 1)
    rec()
    s._on_lifecycle("disconnected", {"why": "CLOSED_BY_PEER"})
    rec()
    s._on_lifecycle("error", {"request_id": "bk-9", "slugs": ["aec-a"],
                              "error": "permission denied for market data"})
    rec()
    assert states == [SUB.NOT_SUBSCRIBED, SUB.SUBSCRIBING, SUB.SUBSCRIBING,
                      SUB.SNAPSHOT_PENDING, SUB.CURRENT, SUB.STALE, SUB.GAP,
                      SUB.REFUSED_BY_VENUE]
    assert set(states) == set(SUB.READINESS_STATES)


def test_a_venue_error_naming_the_request_refuses_exactly_those_markets():
    def _refuse_b(ws, rid, slugs):
        ws.book("aec-a")
        # the venue answers a SECOND batch (b only) with an entitlement error
    conn = {"on_subscribe": [_refuse_b],
            "ticks": [lambda ws: ws.error(
                "market data entitlement required for this market",
                ws.sent[0][1])],
            "then": "stop"}
    venue = FakeVenue([conn])
    s = _sub(venue, wanted=("aec-a",))
    s.want(["aec-b"])
    _drive(s)
    # Both went in ONE batch, so the error names both: fail closed.
    a, b = SUB.readiness("aec-a"), SUB.readiness("aec-b")
    assert b["state"] == SUB.REFUSED_BY_VENUE
    assert b["reason"] == SUB.R_VENUE_ENTITLEMENT
    assert "entitlement required" in b["venue_error"]
    assert a["state"] == SUB.REFUSED_BY_VENUE, (
        "an error naming this market's request refuses it even after a book")
    d = SUB.heartbeat_digest()
    assert d["venue_errors"][-1]["reason"] == SUB.R_VENUE_ENTITLEMENT
    assert d["by_reason"] == {SUB.R_VENUE_ENTITLEMENT: 2}


def test_error_texts_are_classified_and_the_unknown_is_still_a_refusal():
    assert SUB.classify_venue_error("Forbidden") == SUB.R_VENUE_ENTITLEMENT
    assert SUB.classify_venue_error("market not found") == \
        SUB.R_VENUE_MARKET_UNKNOWN
    assert SUB.classify_venue_error("weird") == SUB.R_VENUE_ERROR


def test_nothing_installed_is_not_subscribed_and_the_default_is_off(
        monkeypatch):
    r = SUB.readiness("aec-a")
    assert (r["state"], r["reason"]) == (SUB.NOT_SUBSCRIBED, SUB.R_NOT_RUNNING)
    built = []
    monkeypatch.setattr(SUB, "MarketSubscription",
                        lambda *a, **k: built.append(1))
    monkeypatch.delenv(SUB.ENV_FLAG, raising=False)
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=KEY_ID, pmus_secret_key=SECRET))
    assert got["state"] == SUB.S_DISABLED and not built
    monkeypatch.setenv(SUB.ENV_FLAG, "on")
    # The shared venue key is used only when chosen explicitly (see
    # test_the_dedicated_key_is_the_default_and_the_shared_key_is_never_implied).
    monkeypatch.setenv(SUB.ENV_KEY_SOURCE, SUB.KEY_SHARED)
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id="", pmus_secret_key=""))
    assert got["state"] == SUB.S_NO_CREDENTIALS and not built
    assert SUB.heartbeat_digest()["subscription_state"] == SUB.S_NO_CREDENTIALS
    assert SUB.want(["aec-a"])["queued"] == 0


def test_start_default_arms_with_the_configured_key_when_switched_on(
        monkeypatch):
    venue = FakeVenue([{"then": "stay"}])
    monkeypatch.setenv(SUB.ENV_FLAG, "on")
    monkeypatch.setenv(SUB.ENV_KEY_SOURCE, SUB.KEY_SHARED)
    real = SUB.MarketSubscription
    monkeypatch.setattr(SUB, "MarketSubscription",
                        lambda k, s_: real(k, s_, ws_factory=venue))
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=KEY_ID, pmus_secret_key=SECRET))
    assert got["started"] is True
    SUB.active().stream._idle_s = 0.005
    deadline = time.time() + 5.0
    while not venue.sockets:
        assert time.time() < deadline
        time.sleep(0.01)
    assert venue.credentials_seen == [(KEY_ID, SECRET)]
    assert SUB.shutdown_default(wait_s=5.0)["stopped"] is True


def test_the_dedicated_key_is_the_default_and_the_shared_key_is_never_implied(
        monkeypatch):
    """THE PROTECTED WORKER STREAMS WITH THE SHARED VENUE KEY, and the venue's
    per-key connection limit is not established. Switching the subscription
    on must therefore never open a second connection on that key by default:
    with only the shared key configured it WAITS, by name, and builds nothing.
    """
    built = []
    monkeypatch.setattr(SUB, "MarketSubscription",
                        lambda *a, **k: built.append(a))
    monkeypatch.setenv(SUB.ENV_FLAG, "on")
    monkeypatch.delenv(SUB.ENV_KEY_SOURCE, raising=False)
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=KEY_ID, pmus_secret_key=SECRET,
        pmus_md_key_id="", pmus_md_secret_key=""))
    assert got["state"] == SUB.S_WAITING_FOR_DEDICATED_KEY and not built
    assert got["credential_source"] == SUB.KEY_DEDICATED
    d = SUB.heartbeat_digest()
    assert d["subscription_state"] == SUB.S_WAITING_FOR_DEDICATED_KEY
    assert d["credential_source"] == SUB.KEY_DEDICATED
    assert SECRET not in repr(d) and KEY_ID not in repr(d)
    # Any other value of the source variable is still the dedicated default.
    monkeypatch.setenv(SUB.ENV_KEY_SOURCE, "SHARED-please")
    SUB.reset()
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=KEY_ID, pmus_secret_key=SECRET))
    assert got["state"] == SUB.S_WAITING_FOR_DEDICATED_KEY and not built


def test_the_market_data_key_is_never_an_execution_identity(monkeypatch):
    """The institutional market-data identity and the retail execution
    identity are never mixed: a PMUS_MD_KEY_ID equal to the retail mirror key
    or to the funded key refuses by name and builds nothing."""
    built = []
    monkeypatch.setattr(SUB, "MarketSubscription",
                        lambda *a, **k: built.append(a))
    monkeypatch.setenv(SUB.ENV_FLAG, "on")
    monkeypatch.delenv(SUB.ENV_KEY_SOURCE, raising=False)
    monkeypatch.setenv("PMUS_EXECMIRROR_KEY_ID", "retail-" + KEY_ID)
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=KEY_ID, pmus_secret_key=SECRET,
        pmus_md_key_id="retail-" + KEY_ID, pmus_md_secret_key="x"))
    assert got["state"] == SUB.S_MD_KEY_IS_EXECUTION_KEY and not built
    SUB.reset()
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=KEY_ID, pmus_secret_key=SECRET,
        pmus_md_key_id=KEY_ID, pmus_md_secret_key="x"))
    assert got["state"] == SUB.S_MD_KEY_IS_EXECUTION_KEY and not built
    assert KEY_ID not in repr(SUB.heartbeat_digest())


def test_the_dedicated_key_is_what_the_stream_is_built_with(monkeypatch):
    venue = FakeVenue([{"then": "stay"}])
    monkeypatch.setenv(SUB.ENV_FLAG, "on")
    monkeypatch.delenv(SUB.ENV_KEY_SOURCE, raising=False)
    real = SUB.MarketSubscription
    monkeypatch.setattr(SUB, "MarketSubscription",
                        lambda k, s_: real(k, s_, ws_factory=venue))
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=KEY_ID, pmus_secret_key=SECRET,
        pmus_md_key_id="md-" + KEY_ID, pmus_md_secret_key="md-" + SECRET))
    assert got["started"] is True
    SUB.active().stream._idle_s = 0.005
    deadline = time.time() + 5.0
    while not venue.sockets:
        assert time.time() < deadline
        time.sleep(0.01)
    assert venue.credentials_seen == [("md-" + KEY_ID, "md-" + SECRET)]
    assert SUB.heartbeat_digest()["credential_source"] == SUB.KEY_DEDICATED
    assert SUB.shutdown_default(wait_s=5.0)["stopped"] is True


# ═════════════════════════════════════════════════════════════════════
# §6 · THE DECISION: PASSES CURRENCY ONLY WHEN CURRENT, REFUSED BY NAME
# ═════════════════════════════════════════════════════════════════════

SLUG = "aec-mlb-sub-test-2026-10-02"


def _p5_documented(monkeypatch):
    """SIMULATES THE VENUE'S ANSWER TO P5, for the test only -- the same flip
    test_m1_is_verified_not_asserted §7 makes. Production is not changed."""
    status = {k: dict(v) for k, v in SC.PRECONDITION_STATUS.items()}
    status[SC.P5_DOCUMENTED_TIMING]["available"] = True
    monkeypatch.setattr(SC, "PRECONDITION_STATUS", status)
    monkeypatch.setattr(SC, "MISSING_PRECONDITIONS", ())
    monkeypatch.setattr(SC, "M1_STATUS", SC.M1_AVAILABLE)


def _venue_book(monkeypatch):
    lvl = lambda p, q: {"px": {"value": "%.2f" % p, "currency": "USD"},
                        "qty": str(q)}
    monkeypatch.setattr(L, "_read_book_blocking", lambda _slug: {
        "marketData": {"offers": [lvl(0.62, 400), lvl(0.64, 300)],
                       "bids": [lvl(0.60, 400)],
                       "transactTime": _iso(time.time() - 2.0)}})
    monkeypatch.setattr(L, "_read_venue_rules_blocking",
                        lambda _slug, **_k: {
                            "tick_size": "0.01",
                            "tick_field": "orderPriceMinTickSize",
                            "read_at": time.time(), "from_cache": False})


class _Conn:
    pass


def _decide(slug=SLUG):
    """EXACTLY what the entry lane does at its venue read: the one seam, then
    `venue_quote` with whatever mechanism the seam supplied."""
    cev = L.book_currency_evidence(slug)
    vq = asyncio.run(L.venue_quote(
        _Conn(), us_slug=slug, intent="ORDER_INTENT_BUY_LONG",
        subscription=cev.get("subscription"),
        revalidation=cev.get("revalidation")))
    return cev, vq


def _live_subscription(slug=SLUG):
    """The REAL stream on its own thread, on the fake venue, held open, with
    `slug` CURRENT. The decision process asks for the market through the seam,
    which is how production subscribes it."""
    venue = FakeVenue([{"on_subscribe": [lambda ws, rid, sl: [
        ws.book(x) for x in sl]], "then": "stay"}])
    s = SUB.MarketSubscription(KEY_ID, SECRET, ws_factory=venue)
    SUB.install(s)
    s.start()
    s.stream._idle_s = 0.005
    L.book_currency_evidence(slug)          # the decision process asks
    deadline = time.time() + 5.0
    while SUB.readiness(slug)["state"] != SUB.CURRENT:
        assert time.time() < deadline, SUB.readiness(slug)
        time.sleep(0.01)
    return s, venue


def test_a_current_subscription_establishes_currency_once_p5_holds(
        monkeypatch):
    _p5_documented(monkeypatch)
    _venue_book(monkeypatch)
    s, venue = _live_subscription()
    cev, vq = _decide()
    assert cev["m1"]["readiness"]["state"] == SUB.CURRENT
    assert cev["subscription"] is not None
    assert vq["ok"] is True, vq.get("refusal")
    assert vq["book_currency"]["verdict"] == VC.ESTABLISHED
    assert vq["book_currency"]["mechanism"] == VC.M1_LIVE_SUBSCRIPTION
    # AND THE DECISION-TIME RECHECK RE-AGES THE SAME EVIDENCE AND AGREES.
    fr = L._entry_freshness({}, vq, time.time())
    assert fr["venue_age_s"] is not None

    # THEN THE PEER DROPS: the very next decision is refused, by name.
    venue.sockets[0].peer_close()
    deadline = time.time() + 5.0
    while SUB.readiness(SLUG)["state"] == SUB.CURRENT:
        assert time.time() < deadline
        time.sleep(0.01)
    s.stop(wait_s=5.0)
    cev, vq = _decide()
    assert cev["subscription"] is None
    assert vq["ok"] is False
    assert vq["refusal"] == L.R_BOOK_CURRENCY_NOT_ESTABLISHED


def test_the_hedge_candidate_path_passes_only_on_the_same_evidence(
        monkeypatch):
    _p5_documented(monkeypatch)
    _venue_book(monkeypatch)
    _live_subscription()
    got = asyncio.run(L._candidate_quote(_Conn(), SLUG,
                                         "ORDER_INTENT_BUY_LONG"))
    assert got.get("ok") is not False, got.get("refusal")
    assert got["book_currency"]["mechanism"] == VC.M1_LIVE_SUBSCRIPTION


def test_today_a_current_subscription_is_still_refused_and_p5_is_named(
        monkeypatch):
    """THE PRODUCTION STATE WITH ENTITLEMENT: the subscription works and the
    market is CURRENT, and the decision is still refused -- by the same name
    as today -- because the venue has not documented its timing."""
    _venue_book(monkeypatch)
    _live_subscription()
    cev, vq = _decide()
    assert cev["subscription"] is None
    assert cev["m1"]["readiness"]["state"] == SUB.CURRENT
    assert cev["m1"]["subscription_refusal"] == SUB.M1_FEED_TIMING_NOT_DOCUMENTED
    assert vq["ok"] is False
    assert vq["refusal"] == L.R_BOOK_CURRENCY_NOT_ESTABLISHED
    assert vq["m1_subscription"]["m1_refusal"] == \
        SUB.M1_FEED_TIMING_NOT_DOCUMENTED
    assert vq["m1_subscription"]["feed_level_missing"] == [
        SC.P5_DOCUMENTED_TIMING]
    # AND IT IS STILL RECORDED FOR CALIBRATION ONLY, as today.
    assert vq["refusal"] in L.CALIBRATION_ONLY_AFTER


@pytest.mark.parametrize("drive, state, name", [
    ("not_installed", SUB.NOT_SUBSCRIBED, SUB.M1_SUBSCRIPTION_NOT_RUNNING),
    ("first_ask", SUB.SUBSCRIBING, SUB.M1_SUBSCRIPTION_PENDING),
    ("sent", SUB.SNAPSHOT_PENDING, SUB.M1_SNAPSHOT_PENDING),
    ("silent", SUB.STALE, SUB.M1_SUBSCRIPTION_STALE),
    ("dropped", SUB.GAP, SUB.M1_SUBSCRIPTION_GAP),
    ("refused", SUB.REFUSED_BY_VENUE, SUB.M1_SUBSCRIPTION_REFUSED_BY_VENUE),
    ("key_refused", SUB.REFUSED_BY_VENUE,
     SUB.M1_SUBSCRIPTION_REFUSED_BY_VENUE),
])
def test_every_state_but_current_is_refused_by_name_even_with_p5(
        monkeypatch, drive, state, name):
    """P5 SIMULATED AS DOCUMENTED, and the currency module's own P2-P4 all
    SATISFIED (a live message for this market a second ago) -- so the ONLY
    thing that can refuse is the subscription's readiness. It does, and the
    refusal names which state it was in."""
    _p5_documented(monkeypatch)
    _venue_book(monkeypatch)
    now = time.time()
    SC.connection_opened(now=now - 5)
    SC.heartbeat(now=now - 0.5)
    SC.message_received(SLUG, now=now - 1.0, payload_slug=SLUG)

    if drive != "not_installed":
        s = SUB.MarketSubscription(KEY_ID, SECRET, ws_factory=FakeVenue([]))
        SUB.install(s)
        s._set_state(SUB.S_CONNECTING, "test")
        if drive != "first_ask":
            s.want([SLUG])
            s._on_lifecycle("connected", {"epoch": 1})
        if drive in ("sent", "silent", "dropped", "refused"):
            s._on_lifecycle("subscribe_sent", {"kind": "book",
                                               "slugs": [SLUG]})
        if drive in ("silent", "dropped", "refused"):
            s._on_book(SLUG, {"source_ts": _iso(now - 1)})
        if drive == "silent":
            s._last_life_at = time.time() - SC.MAX_SILENCE_S - 5
        if drive == "dropped":
            s._on_lifecycle("disconnected", {"why": "CLOSED_BY_PEER"})
        if drive == "refused":
            s._on_lifecycle("error", {"request_id": "bk-1", "slugs": [SLUG],
                                      "error": "not entitled"})
        if drive == "key_refused":
            s.want([SLUG])
            s._on_lifecycle("venue_refused", {"status": 401})

    cev, vq = _decide()
    assert cev["m1"]["readiness"]["state"] == state, cev["m1"]["readiness"]
    assert cev["subscription"] is None
    assert cev["m1"]["subscription_refusal"] == name
    assert vq["ok"] is False
    # TODAY'S REFUSAL, BY TODAY'S NAME, so the calibration-only path and every
    # tally keyed on it are unchanged ...
    assert vq["refusal"] == L.R_BOOK_CURRENCY_NOT_ESTABLISHED
    # ... WITH THE MISSING PART OF M1 NAMED BESIDE IT.
    assert vq["m1_subscription"]["m1_refusal"] == name
    assert vq["m1_subscription"]["readiness"]["state"] == state


def test_with_nothing_installed_the_seam_answers_exactly_as_before():
    ev = L.book_currency_evidence(SLUG)
    assert ev["subscription"] is None and ev["revalidation"] is None
    assert ev["m1"]["refusal"] == SC.M1_NOT_AVAILABLE
    assert ev["why_none"] == SC.evidence_for(SLUG).get("why")
    assert ev["m1"]["subscription_refusal"] == SUB.M1_SUBSCRIPTION_NOT_RUNNING


def test_the_heartbeat_carries_the_subscription_digest():
    captured = {}

    class _HB:
        async def execute(self, sql, key, blob):
            captured["payload"] = json.loads(blob)

    clk = Clock()
    s = SUB.MarketSubscription(KEY_ID, SECRET, clock=clk,
                               ws_factory=FakeVenue([]))
    SUB.install(s)
    s._set_state(SUB.S_CONNECTING, "test")
    s.want(["aec-a", "aec-b"])
    s._on_lifecycle("connected", {"epoch": 1})
    s._on_lifecycle("subscribe_sent", {"kind": "book",
                                       "slugs": ["aec-a", "aec-b"]})
    s._on_book("aec-a", {"source_ts": _iso(clk())})
    SUB.m1_refusal_detail("aec-b")
    asyncio.run(L._heartbeat(_HB(), {"state": "LIVE"}))
    d = captured["payload"]["market_subscription"]
    assert d["subscription_state"] == SUB.S_CONNECTED
    assert d["by_readiness"] == {SUB.CURRENT: 1, SUB.SNAPSHOT_PENDING: 1}
    assert d["current_sample"] == ["aec-a"]
    assert d["not_current_sample"] == [{"slug": "aec-b",
                                        "state": SUB.SNAPSHOT_PENDING,
                                        "reason": SUB.R_AWAITING_FIRST_BOOK}]
    assert d["decision_m1_refusals_since_start"] == {
        SUB.M1_SNAPSHOT_PENDING: 1}
    assert d["currency_gate"]["feed_level_missing"] == [
        SC.P5_DOCUMENTED_TIMING]
    assert SECRET not in json.dumps(captured["payload"])


def test_no_order_path_is_reachable_from_the_subscription():
    """The module imports nothing that can send an order: its imports are
    enumerated, not grepped for words."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(SUB))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {"%s%s" % ("." * node.level, node.module or "")
                      + ":" + a.name for a in node.names}
    allowed = {"logging", "os", "threading", "time", "__future__:annotations",
               ".:bettor_market_stream", ".:bettor_stream_currency",
               ".config:settings"}
    assert names <= allowed, names - allowed
    assert SUB.describe()["submits_orders"] is False
