"""COUNTEREXAMPLES THROUGH THE ACTUAL SCHEDULED READ PATH.

── WHY THIS FILE EXISTS AND THE PREVIOUS TESTS DID NOT SUFFICE ──────
The existing attempt-accounting tests call `http.get()` on a bare httpx
client with our observer attached. That exercises the recorder. It does
NOT exercise:

  * the venue SDK's own request path, including the retry logic whose
    default turned one paced call into three HTTP attempts;
  * `pmus.book_read`, where the failure is classified and the cooldown
    armed;
  * `venue_request_gate.PacedTransport`, where the not-before instant is
    enforced immediately before dispatch;
  * `ext_pinnacle_loop._read_book_blocking`, which is what the scheduler
    actually calls and where the final diagnostic is assembled;
  * the restart boundary, where a hold either survives or does not.

Every test below drives the REAL chain:

    _read_book_blocking  ->  pmus.book_read  ->  polymarket_us 1.0.2
      ->  httpx.Client  ->  PacedTransport (ours)  ->  MockTransport

Only the socket is replaced. The SDK is the pinned build, its error
mapping is its own, and the gate is the one production installs.

── THE CENTRAL ASSERTION ────────────────────────────────────────────
`test_no_request_is_dispatched_before_the_permitted_instant` records the
wall-clock instant of every dispatch inside the mock transport and compares
it against the hold. That is the property the previous change CLAIMED and
did not have: a reduced rate that doubled a 0.35 s gap to 0.7 s was
reported as a 600-second cooldown.

NO TEST HERE SUBMITS AN ORDER. The path under test reads a book.
"""

from __future__ import annotations

import base64
import threading
import time

import httpx
import pytest

from sportsassets import pmus
from sportsassets import venue_cooldown_store as VCS
from sportsassets import venue_http_observer as VHO
from sportsassets import venue_pace as VP
from sportsassets import venue_request_gate as GRT
from sportsassets import venue_sdk
from sportsassets.workers import ext_pinnacle_loop as LOOP

SLUG = "aec-mlb-chc-sd-2026-09-30"
BOOK_PATH = "/v1/markets/%s/book" % SLUG

#: A payload shaped like the venue's, minimal but genuinely parsed by the
#: code under test: `book_read` requires `marketData` to be present or it
#: returns NO_MARKET_DATA_IN_PAYLOAD.
GOOD_BOOK = {"marketData": {"bids": [{"price": 0.41, "qty": 120}],
                            "asks": [{"price": 0.44, "qty": 90}],
                            "transactTime": "2026-09-28T14:00:00Z"}}


class Venue:
    """A scripted venue that records WHEN each request arrived.

    The arrival instants are the evidence for the gate: a hold that is
    merely reported changes no arrival time, and a hold that is enforced
    moves the next one.
    """

    def __init__(self, script):
        self.script = list(script)
        self.dispatches = []          # (monotonic, wall, path)
        #: Requests that arrived after the script ran out. A silent default
        #: here would hide an over-dispatch behind a plausible success, so
        #: exhaustion is COUNTED and answered with a status that is neither
        #: retryable nor a rate limit -- an extra request therefore shows up
        #: as itself rather than as a different failure.
        self.exhausted = 0
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.dispatches.append((time.monotonic(), time.time(),
                                    request.url.path))
            if self.script:
                step = self.script.pop(0)
            else:
                self.exhausted += 1
                step = ("ok", 418, {"message": "SCRIPT_EXHAUSTED"})
        kind = step[0]
        if kind == "raise":
            raise step[1]
        status, body, headers = step[1], step[2], (step[3] if len(step) > 3
                                                  else {})
        return httpx.Response(status, json=body, headers=headers,
                              request=request)

    @property
    def count(self) -> int:
        with self.lock:
            return len(self.dispatches)


#: A THROWAWAY Ed25519 seed, generated here and never stored anywhere.
#:
#: WHY ANY CREDENTIAL AT ALL. The authenticated endpoints -- positions,
#: balances, the order reads -- are refused by the SDK LOCALLY when no key is
#: set, before a request is built, so a test of their retry behaviour would
#: measure nothing. This is 32 zero bytes base64-encoded: a syntactically
#: valid seed the signing code accepts, with no relationship to any real key,
#: and the mock transport never checks a signature. It is not a secret and
#: could not authenticate against anything.
_FAKE_KEY_ID = "00000000-0000-0000-0000-000000000000"
_FAKE_SECRET = base64.b64encode(bytes(32)).decode()


def _install(venue: Venue, monkeypatch, *, authenticated=True) -> object:
    """Build the REAL SDK client over a mock socket, then gate it.

    This is `pmus._get_client`'s own construction with `client_kwargs()`
    applied -- so `max_retries=0` is in force exactly as it is in
    production -- and `_install_request_gate` is the production function,
    not a reimplementation.
    """
    from polymarket_us import PolymarketUS

    creds = ({"key_id": _FAKE_KEY_ID, "secret_key": _FAKE_SECRET}
             if authenticated else {})
    client = PolymarketUS(gateway_base_url="https://gateway.test",
                          api_base_url="https://api.test",
                          **creds,
                          **venue_sdk.client_kwargs())
    # Replace only the socket. Everything above it is the shipped code.
    client._http = httpx.Client(
        transport=httpx.MockTransport(venue.handler))
    got = pmus._install_request_gate(client)
    assert got["installed"] is True, got
    monkeypatch.setattr(pmus, "_client", client, raising=False)
    return client


@pytest.fixture(autouse=True)
def _clean():
    """Every module global this path touches, cleared both ways.

    All four are process-wide, and a hold or a queued cooldown left behind
    makes the NEXT test pass or fail for a reason unrelated to what it
    asserts -- which is how an order-dependent failure gets attributed to
    the wrong change.
    """
    GRT.clear_hold()
    VP.resume_cooldown(0)
    VP._penalty_until = 0.0
    VP._penalty_until_epoch = 0.0
    VHO.reset_attempts()
    VHO.reset()
    VCS.reset_pending()
    yield
    GRT.clear_hold()
    VP._penalty_until = 0.0
    VP._penalty_until_epoch = 0.0
    VHO.reset_attempts()
    VHO.reset()
    VCS.reset_pending()


# ═════════════════════════════════════════════════════════════════════
# 1 · THE PINNED SDK MAKES ONE REQUEST PER LOGICAL READ
# ═════════════════════════════════════════════════════════════════════

def test_one_scheduled_read_makes_exactly_one_request_on_success(monkeypatch):
    """The reproduction was 1 paced call -> 3 HTTP attempts. Not any more.

    This is the whole point of `max_retries=0`: the SDK's internal retry
    loop is what multiplied the request count, and it is the count -- not
    a comment -- that is asserted.
    """
    venue = Venue([("ok", 200, GOOD_BOOK)])
    _install(venue, monkeypatch)

    out = LOOP._read_book_blocking(SLUG,
                                   deadline_epoch_s=time.time() + 30)

    assert out.get("error") is None, out
    assert out["marketData"]["bids"][0]["price"] == 0.41
    assert venue.count == 1, (
        "one logical read made %d requests; the SDK's own retries must be "
        "off so ours is the only retry" % venue.count)
    assert out["attempts"] == 1
    assert out["request_accounting"]["dispatched"] == 1
    assert out["request_accounting"]["responses"] == 1


def test_a_429_on_every_attempt_makes_exactly_two_requests(monkeypatch):
    """OUR retry is bounded at BOOK_READ_MAX_DISPATCHES, and it is ours.

    Two, not three (the SDK's old default) and not one (no retry at all).
    The venue answered 429 twice, so the read fails -- and it fails having
    spent exactly the budget the constant declares.
    """
    venue = Venue([("ok", 429, {"error": "slow down"}, {"retry-after": "1"}),
                   ("ok", 429, {"error": "slow down"}, {"retry-after": "1"})])
    _install(venue, monkeypatch)

    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)

    assert venue.count == pmus.BOOK_READ_MAX_DISPATCHES == 2, venue.count
    assert venue.exhausted == 0, (
        "%d request(s) arrived after the script ran out, so the dispatch "
        "budget was exceeded" % venue.exhausted)
    assert out["marketData"] is None
    d = out["diagnostic"]
    assert d["http_status"] == 429
    assert d["is_rate_limited"] is True
    assert d["retry_after_s"] == 1.0
    assert out["why_no_further_attempt"] == "BOOK_READ_MAX_DISPATCHES_REACHED"
    # The per-read count matches what the venue actually saw.
    assert out["attempts"] == 2


# ═════════════════════════════════════════════════════════════════════
# 2 · 429 THEN SUCCESS
# ═════════════════════════════════════════════════════════════════════

def test_a_429_followed_by_success_returns_the_book_and_waits_first(
        monkeypatch):
    """The retry works, AND it waited for the venue's own window.

    Both halves matter. A retry that ignores `Retry-After` is how a 429
    storm is sustained; a wait with no retry turns a transient limit into
    a lost decision.
    """
    venue = Venue([("ok", 429, {"error": "slow"}, {"retry-after": "1"}),
                   ("ok", 200, GOOD_BOOK)])
    _install(venue, monkeypatch)

    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)

    assert out.get("error") is None, out
    assert out["marketData"]["asks"][0]["qty"] == 90
    assert venue.count == 2

    # THE GAP BETWEEN THE TWO DISPATCHES IS AT LEAST THE VENUE'S WINDOW.
    # This is the measurement the earlier claim lacked: the observed gap
    # was 0.7 s (0.35 × 2.0), which is a doubled rate, not a one-second
    # prohibition.
    gap = venue.dispatches[1][0] - venue.dispatches[0][0]
    assert gap >= 1.0, (
        "the second request went out %.3f s after the first, inside the "
        "venue's Retry-After of 1 s" % gap)

    # And the read reports the wait it took, rather than leaving the
    # operator to infer it.
    assert out["our_retries"], out
    assert out["our_retries"][0]["http_status"] == 429
    assert out["our_retries"][0]["retry_after_s"] == 1.0
    assert out["our_retries"][0]["waited_s"] >= 0.9


# ═════════════════════════════════════════════════════════════════════
# 3 · A TRANSPORT FAILURE WITH NO RESPONSE AT ALL
# ═════════════════════════════════════════════════════════════════════

def test_a_transport_failure_with_no_response_is_counted_and_retried(
        monkeypatch):
    """`attempts: null` was the bug. A request with no response is still one.

    Counting on the response hook missed these entirely: three attempts
    ending in a connect error produced no responses and reported the count
    as unknown. The transport counts before dispatch, so it cannot.
    """
    venue = Venue([("raise", httpx.ConnectError("no route")),
                   ("ok", 200, GOOD_BOOK)])
    _install(venue, monkeypatch)

    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)

    assert out.get("error") is None, out
    assert venue.count == 2
    assert out["attempts"] == 2
    # TWO DISPATCHES, ONE RESPONSE. The asymmetry is the evidence that the
    # first request really produced nothing, and it is visible rather than
    # smoothed into a single number.
    assert out["request_accounting"]["dispatched"] == 2
    assert out["request_accounting"]["responses"] == 1
    assert out["our_retries"][0]["http_status"] is None


def test_a_transport_failure_is_classified_as_transient_not_as_unknown(
        monkeypatch):
    """A response-less failure is retryable; a 404 is not.

    The distinction is made from the measured response, not from the
    exception's class name at the call site -- the old handler reduced
    everything to `type(exc).__name__` and so could not decide at all.
    """
    venue = Venue([("raise", httpx.ConnectError("no route")),
                   ("raise", httpx.ConnectError("no route"))])
    _install(venue, monkeypatch)

    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)

    d = out["diagnostic"]
    assert d["http_status"] is None
    assert d["has_response"] is False
    assert d["is_transport_failure"] is True
    assert d["transience_is_unclassified"] is False
    assert venue.count == 2
    assert out["why_no_further_attempt"] == "BOOK_READ_MAX_DISPATCHES_REACHED"


def test_a_404_is_an_answer_about_the_market_and_is_not_retried(monkeypatch):
    """Retrying a 404 spends a decision deadline to be told the same thing.

    And the read must still NOT report it as a rate limit or arm a
    cooldown: an absent market is not a busy venue.
    """
    venue = Venue([("ok", 404, {"message": "no such market"})])
    _install(venue, monkeypatch)

    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)

    assert venue.count == 1, "a 404 must not be retried"
    assert out["why_no_further_attempt"] == "THE_FAILURE_IS_NOT_RETRYABLE"
    assert out["diagnostic"]["http_status"] == 404
    assert out["diagnostic"]["is_rate_limited"] is False
    assert GRT.gate_state()["blocking"] is False
    assert VP.penalty_left() == 0.0


# ═════════════════════════════════════════════════════════════════════
# 4 · NO REQUEST BEFORE THE PERMITTED INSTANT
# ═════════════════════════════════════════════════════════════════════

def test_no_request_is_dispatched_before_the_permitted_instant(monkeypatch):
    """THE PROPERTY THE OLD CODE CLAIMED AND DID NOT HAVE.

    A hold is armed, then a read is made with a deadline that comfortably
    outlasts it. The transport records the wall-clock instant of the
    dispatch, and it must be AT OR AFTER the not-before instant. The
    previous implementation only multiplied the inter-request gap, so this
    comparison would have failed by ~99% of the hold.
    """
    venue = Venue([("ok", 200, GOOD_BOOK)])
    _install(venue, monkeypatch)

    permitted_at = time.time() + 0.75
    armed = GRT.hold_until(until_epoch_s=permitted_at, reason="TEST_HOLD")
    assert armed["applied"] is True

    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)
    assert out.get("error") is None, out
    assert venue.count == 1

    sent_at = venue.dispatches[0][1]
    assert sent_at >= permitted_at, (
        "a request was dispatched %.3f s BEFORE the permitted instant; the "
        "gate reported a hold it did not enforce"
        % (permitted_at - sent_at))


def test_the_hold_is_enforced_on_the_retry_too(monkeypatch):
    """The second dispatch is gated by the hold the FIRST one's 429 armed.

    An SDK retry sleeps its own backoff and then sends, whatever the
    venue asked for. Ours goes through the gate, so the arming and the
    enforcement are the same mechanism rather than two that can disagree.
    """
    venue = Venue([("ok", 429, {"e": 1}, {"retry-after": "2"}),
                   ("ok", 200, GOOD_BOOK)])
    _install(venue, monkeypatch)

    t0 = time.time()
    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=t0 + 30)

    assert out.get("error") is None, out
    assert venue.count == 2
    # The 429 named two seconds; the retry honoured it, not a 0.7 s gap.
    assert venue.dispatches[1][1] - venue.dispatches[0][1] >= 2.0


# ═════════════════════════════════════════════════════════════════════
# 5 · DEADLINE EXPIRY — REFUSE, NEVER OUTLIVE THE CALLER
# ═════════════════════════════════════════════════════════════════════

def test_a_hold_longer_than_the_deadline_refuses_and_sends_nothing(
        monkeypatch):
    """Refused by name, with ZERO dispatches. Not slept out in a thread.

    A background thread that waits out a ten-minute hold and then sends
    delivers a request nobody is waiting for, attributed to a decision
    that was abandoned. The count of dispatches is the proof.
    """
    venue = Venue([("ok", 200, GOOD_BOOK)])
    _install(venue, monkeypatch)

    GRT.hold_until(until_epoch_s=time.time() + 600, reason="LONG_COOLDOWN")

    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 2)

    assert venue.count == 0, "a request was sent despite the refusal"
    assert out["marketData"] is None
    assert out["error"] == GRT.R_COOLDOWN_EXCEEDS_DEADLINE
    assert out["refused_by"] == "OUR_REQUEST_GATE"
    assert out["gate_detail"]["seconds_left"] > 500
    # NAMED AS OURS, not as a venue failure -- otherwise an operator goes
    # looking at the venue for a decision we made.
    assert out["diagnostic"]["stage"] == "REQUEST_GATE"


def test_a_deadline_already_passed_refuses_before_any_dispatch(monkeypatch):
    """No hold at all, but no time either. Still zero requests."""
    venue = Venue([("ok", 200, GOOD_BOOK)])
    _install(venue, monkeypatch)

    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() - 1)

    assert venue.count == 0
    assert out["error"] == GRT.R_DEADLINE_PASSED
    assert out["refused_by"] == "OUR_REQUEST_GATE"


def test_an_undeadlined_caller_will_not_be_parked_indefinitely():
    """A caller with no deadline is capped, not held for the whole hold.

    The desk sweep and the reconciliation reads pass no deadline, so `dl`
    was None and the sleep branch was taken unconditionally: a 600-second
    `Retry-After` would have parked the thread for ten minutes and then
    dispatched. The cap does not pretend to know the caller's deadline; it
    refuses to hold a thread open on a guess.
    """
    GRT.hold_until(until_epoch_s=time.time() + 600, reason="LONG")
    with pytest.raises(GRT.VenueGateRefusal) as caught:
        GRT.check_before_dispatch(read_id=None, sleep=_never_sleep)
    assert caught.value.refusal == GRT.R_HOLD_EXCEEDS_UNDEADLINED_CAP
    assert caught.value.detail["cap_s"] == GRT.MAX_UNDEADLINED_WAIT_S


def _never_sleep(_s):
    raise AssertionError("the gate slept for an undeadlined caller")


# ═════════════════════════════════════════════════════════════════════
# 6 · CONSECUTIVE AND OVERLAPPING READS
# ═════════════════════════════════════════════════════════════════════

def test_consecutive_reads_each_report_their_own_attempt_count(monkeypatch):
    """The per-PATH counter reported 3 then 6. Per-read reports 2 then 2.

    Accumulation is not a cosmetic problem: the second read's diagnostic
    claimed six attempts for a read that made two, which would put an
    operator's rate-limit investigation on the wrong scale.
    """
    venue = Venue([("raise", httpx.ConnectError("x")), ("ok", 200, GOOD_BOOK),
                   ("raise", httpx.ConnectError("x")), ("ok", 200, GOOD_BOOK)])
    _install(venue, monkeypatch)

    first = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)
    second = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)

    assert first["attempts"] == 2
    assert second["attempts"] == 2, (
        "the second read reported %r attempts; a counter that accumulates "
        "across reads describes the process, not the read"
        % (second["attempts"],))
    assert venue.count == 4
    # The process total IS available, and is labelled as a process total.
    tot = GRT.totals()
    assert tot["dispatched"] >= 4
    assert tot["scope"] == "PROCESS_SINCE_IMPORT"
    assert tot["is_not_a_per_read_count"] is True


def test_two_overlapping_reads_do_not_share_or_reset_each_others_counters(
        monkeypatch):
    """Two threads reading the SAME slug. A path key would race; ids do not.

    The old reset was per path and a shared reset makes each caller clobber
    the other: whichever resets last hands the other a count of zero for
    requests it really made.
    """
    started = threading.Barrier(2)
    venue = Venue([])

    def handler(request):
        with venue.lock:
            venue.dispatches.append((time.monotonic(), time.time(),
                                     request.url.path))
        # Hold both requests in flight simultaneously, so the reads really
        # overlap rather than merely being consecutive.
        try:
            started.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        return httpx.Response(200, json=GOOD_BOOK, request=request)

    venue.handler = handler                     # type: ignore[method-assign]
    _install(venue, monkeypatch)

    results: dict = {}

    def read(name):
        results[name] = LOOP._read_book_blocking(
            SLUG, deadline_epoch_s=time.time() + 30)

    threads = [threading.Thread(target=read, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert set(results) == {"a", "b"}, results
    for name, out in results.items():
        assert out.get("error") is None, (name, out)
        assert out["attempts"] == 1, (
            "read %s reported %r attempts for its single request; the "
            "counters are not isolated per logical read"
            % (name, out["attempts"]))
        assert out["request_accounting"]["responses"] == 1
    assert venue.count == 2


# ═════════════════════════════════════════════════════════════════════
# 7 · THE RESTART BOUNDARY, FOR REAL
# ═════════════════════════════════════════════════════════════════════

def test_an_observed_429_queues_a_cooldown_for_durable_storage(monkeypatch):
    """The read path has no database, so it hands the hold over.

    And it does NOT claim the hold is durable yet. A queued save that
    reported itself as saved would be the same overclaim as a reduced rate
    reported as a cooldown.
    """
    venue = Venue([("ok", 429, {"e": 1}, {"retry-after": "1"}),
                   ("ok", 429, {"e": 1}, {"retry-after": "1"})])
    _install(venue, monkeypatch)

    LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)

    pend = VCS.pending()
    assert pend is not None, "the observed 429 queued nothing to persist"
    assert pend["not_before_epoch_s"] > time.time()
    assert pend["reduced_rate_until_epoch_s"] > pend["not_before_epoch_s"], (
        "the prohibition and the reduced-rate period must be two distinct "
        "instants; storing one under both names is the conflation this "
        "repair exists to end")
    assert pend["retry_after_s"] == 1.0


def test_a_restart_restores_the_hold_from_storage_and_still_refuses(
        monkeypatch):
    """END TO END ACROSS A SIMULATED PROCESS BOUNDARY, with no database.

    A real PostgreSQL round trip is covered by the funded-pg18 job; what
    this pins is the part that is pure logic and was the actual defect --
    that BOTH controls die with the process and NOTHING read them back. The
    connection is a double; the store, the gate, the pacer and the reader
    are the shipped modules.

    THE SEQUENCE:
      1 · a 429 arms both controls and queues them;
      2 · the loop's drain writes them to `ingestion_state`;
      3 · the process "dies": every module global is cleared, which is
          exactly what a restart does;
      4 · a read at that moment would be permitted -- asserted, so step 5
          is not vacuous;
      5 · startup reads the row back and re-arms;
      6 · the same read is now refused, and sends nothing.
    """
    import asyncio

    stored: dict = {}

    class FakeConn:
        """Just enough of asyncpg for the two statements the store runs."""

        async def execute(self, sql, key, value):
            assert "ingestion_state" in sql
            stored[key] = value

        async def fetchval(self, sql, key):
            assert "ingestion_state" in sql
            return stored.get(key)

    venue = Venue([("ok", 429, {"e": 1}, {"retry-after": "120"}),
                   ("ok", 429, {"e": 1}, {"retry-after": "120"})])
    _install(venue, monkeypatch)

    # 1 · the 429.
    LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 30)
    assert VCS.pending() is not None

    # 2 · the drain the loop performs when it has a connection.
    conn = FakeConn()
    drained = asyncio.run(VCS.drain_pending(conn))
    assert drained["drained"] is True, drained
    assert VCS.pending() is None, "a successful write must clear the queue"
    assert stored, "nothing reached storage"

    # 3 · the process dies. These globals are the whole of the old state.
    GRT.clear_hold()
    VP._penalty_until = 0.0
    VP._penalty_until_epoch = 0.0
    assert GRT.gate_state()["blocking"] is False
    assert VP.penalty_left() == 0.0

    # 4 · WITHOUT THE READ-BACK THE RESTART IS FREE TO SEND. Asserted so
    #     step 6 cannot pass for the wrong reason.
    before = venue.count
    venue.script = [("ok", 200, GOOD_BOOK)]
    permitted = LOOP._read_book_blocking(SLUG,
                                         deadline_epoch_s=time.time() + 30)
    assert permitted.get("error") is None
    assert venue.count == before + 1, (
        "the fresh process did not send, so this test would prove nothing "
        "about the read-back")

    # 5 · startup.
    resumed = asyncio.run(VCS.load_and_resume(conn))
    assert resumed["stored"] is True, resumed
    assert resumed["resumed"] is True, resumed
    assert resumed["both_controls_restored"] is True, resumed
    assert resumed["row_predates_the_split"] is False
    assert GRT.gate_state()["blocking"] is True
    assert VP.penalty_left() > 0.0, "the reduced rate was not restored"

    # 6 · and now the same read is refused, having sent nothing.
    after = venue.count
    out = LOOP._read_book_blocking(SLUG, deadline_epoch_s=time.time() + 2)
    assert venue.count == after, (
        "the resumed hold did not prevent a dispatch")
    assert out["error"] == GRT.R_COOLDOWN_EXCEEDS_DEADLINE
    assert out["refused_by"] == "OUR_REQUEST_GATE"


def test_a_failed_write_leaves_the_cooldown_queued_for_the_next_cycle():
    """A transient database error must not silently forget a hold.

    Dropping the observation on a failed write would turn "we could not
    record it" into "there was nothing to record" -- the same fail-open
    shape as treating a failed read as no cooldown.
    """
    import asyncio

    class BrokenConn:
        async def execute(self, *a):
            raise RuntimeError("connection reset")

    VCS.queue_save({"not_before_epoch_s": time.time() + 30,
                    "reduced_rate_until_epoch_s": time.time() + 600,
                    "reason": "TEST"})
    res = asyncio.run(VCS.drain_pending(BrokenConn()))
    assert res["drained"] is False
    assert res["saved"] is False
    assert "connection reset" in res["why"]
    assert VCS.pending() is not None, (
        "a failed write discarded the queued cooldown")
    assert VCS.drain_stats()["save_failed"] == 1


def test_a_storage_read_that_fails_is_unknown_and_not_permission():
    """`read_failed` is not `no cooldown`. The caller must be able to tell."""
    import asyncio

    class BrokenConn:
        async def fetchval(self, *a):
            raise RuntimeError("relation does not exist")

    res = asyncio.run(VCS.load_and_resume(BrokenConn()))
    assert res["resumed"] is False
    assert res["read_failed"] is True
    assert "relation does not exist" in res["why"]
    assert "stored" not in res, (
        "a failed read must not report on whether a row exists")


# ═════════════════════════════════════════════════════════════════════
# 8 · THE SERVICING AND RECONCILIATION READS KEEP THEIR RETRY
# ═════════════════════════════════════════════════════════════════════
#
# `max_retries=0` removes the SDK's two retries from EVERY call on the
# shared client, not only from the book read that has a retry of its own.
# Without `pmus.paced_read` those reads would have gone from three attempts
# to one, silently -- and they are the reconciliation and servicing reads,
# the ones that must keep working while the entry lane is being throttled,
# because a venue we cannot read is a position we cannot manage.

def test_a_servicing_read_retries_a_transient_failure(monkeypatch):
    """`account.balances` survives one dropped connection, as it did before.

    Asserted through `balances()` itself rather than through `paced_read`,
    because the question is whether the CALL SITE has the retry, not whether
    the helper works in isolation.
    """
    venue = Venue([("raise", httpx.ConnectError("reset")),
                   ("ok", 200, {"balances": [{"asset": "USDC",
                                              "available": "0"}]})])
    _install(venue, monkeypatch)

    got = pmus.balances()

    assert venue.count == 2, (
        "the servicing read made %d attempt(s); turning off the SDK's retry "
        "without replacing it here would have left it at 1" % venue.count)
    assert venue.exhausted == 0
    assert got is not None


def test_a_servicing_read_still_gives_up_and_re_raises(monkeypatch):
    """Bounded, and the caller's own fallback still decides.

    `paced_read` re-raises rather than returning a sentinel, so every
    existing `try/except` fallback -- a stale snapshot, a fail-CLOSED hold,
    a None that makes the caller refuse -- keeps deciding exactly as before.
    A helper that swallowed the error would have quietly taken those
    decisions over.
    """
    venue = Venue([("raise", httpx.ConnectError("reset")),
                   ("raise", httpx.ConnectError("reset"))])
    _install(venue, monkeypatch)

    with pytest.raises(Exception) as caught:
        pmus.paced_read(lambda: pmus._get_client().account.balances(),
                        endpoint="account.balances")
    assert "APIConnectionError" in type(caught.value).__name__ or \
           isinstance(caught.value, httpx.ConnectError)
    assert venue.count == 2


def test_a_429_on_a_servicing_read_arms_the_same_cooldown(monkeypatch):
    """One limiter, one cooldown. A hole in either path is a hole in both.

    A 429 on `portfolio.positions` counts against the same venue limiter as
    a 429 on `markets.book`, so a cooldown armed by one and not the other is
    a control with a gap exactly where the traffic is.
    """
    venue = Venue([("ok", 429, {"e": 1}, {"retry-after": "3"}),
                   ("ok", 429, {"e": 1}, {"retry-after": "3"})])
    _install(venue, monkeypatch)

    # `account_holds` fails CLOSED on an unreadable venue, which is its own
    # correct behaviour and not what is under test here.
    pmus.account_holds(SLUG)

    g = GRT.gate_state()
    assert g["blocking"] is True, (
        "a 429 on a servicing read armed no prohibition")
    assert g["seconds_left"] >= 2.5
    assert VP.penalty_left() > 0.0, "the reduced rate was not armed either"
    pend = VCS.pending()
    assert pend is not None and pend["retry_after_s"] == 3.0
    assert "PORTFOLIO_POSITIONS" in (pend["reason"] or ""), pend


def test_no_order_submission_path_can_reach_the_retry_helper():
    """CONTAINMENT, read from the AST rather than from intent.

    The venue has no idempotency key, so retrying an ambiguous order
    submission can duplicate it. `paced_read` is for IDEMPOTENT reads only,
    and this pins the set of functions that may call it -- so a later edit
    that wires it into a submission path fails here instead of in
    production.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pmus))
    callers = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(node):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "paced_read"):
                    callers.add(node.name)
                    break
    assert callers == {"position_side", "account_holds", "open_orders",
                       "balances"}, (
        "only idempotent reads may use the paced retry; got %r"
        % (sorted(callers),))
    # And none of them is a submission path, by name and by what they call.
    for name in callers:
        src = inspect.getsource(getattr(pmus, name))
        for forbidden in ("orders.create", "orders.cancel", "orders.modify",
                          "close_position"):
            assert forbidden not in src, "%s reaches %s" % (name, forbidden)
