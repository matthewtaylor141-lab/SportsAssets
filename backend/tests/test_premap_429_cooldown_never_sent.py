"""THE CATALOGUE SWEEP READS OUR GATE'S 429 COOLDOWN AS WHAT IT IS (P0-429,
independent review of the lane).

THE DEFECT. The escalating 429 cooldown (venue_pace / venue_request_gate,
P0-429) refuses a normal-lane read at the transport with
venue_request_gate.VenueGateRefusal(VENUE_429_COOLDOWN_NORMAL_READ_DEFERRED):
nothing is sent. The premap catalogue sweep (workers/premap.refresh, in the
workers process, through pmus._get_client() and so through PacedTransport)
walks events.list through that transport, and its `_rate_limited` knew only
the venue's own 429 (venue_http_error.describe: http_status 429 or a
rate-limit class name). Our refusal is neither, so the sweep read it as a
GENERIC failure:

  * at the probe, every rung of the variant ladder was refused in turn and
    the sweep fell into the markets.list fallback (mode "markets") that R30A
    forbids after a 429 -- degraded, title-keyed rows over good ones, and,
    when the cooldown ran out during the ladder, real extra requests into a
    limiter that had just said no;
  * mid-walk, in a partition bucket and in a calendar slice, the pass was
    labelled REQUEST_FAILED instead of rate-limited, counted as a request it
    never sent, and the calendar lane went on asking slice after slice;
  * the per-event detail repair kept claiming reads after the refusal, each
    counted as a request.

Production evidence (render-ops logs run 37926975729, sportsassets-workers,
11:40-11:42Z): premap's GET /v1/events?limit=100&offset=1615 between the
gateway's 429s -- the sweep runs inside the window the cooldown now covers.

WHAT THESE PIN (every one fails on the lane's 0b92a844): our gate's refusal is
read as rate-limited and NEVER SENT -- the ladder ends at once, no fallback,
the pass stops RATE_LIMITED_BY_VENUE with the refusal named in its error, the
refused read is not a request on the receipt, the detail repair stops, the
calendar lane stops -- and the venue's own 429 is handled exactly as before.

REVIEW ROUND 2: A SHORT COOLDOWN IS WAITED OUT, NOT A STOP. Stopping the pass
on ANY cooldown -- one another walker's book 429 armed for 5 s -- turned a
catalogue receipt that is COMPLETE in production into PARTIAL (the reviewer
reproduced it: 0e331f05 COMPLETE after the 5 s hold wait, 6f47d4a4 PARTIAL,
RATE_LIMITED), and catalogue_completeness needs the latest full and calendar
receipts COMPLETE. The sweep's reads now opt into venue_pace.
wait_out_the_cooldown(): a cooldown that ends within MAX_UNDEADLINED_WAIT_S
(one budget with the hard hold) is waited out -- ZERO requests reach the venue
inside it -- and the pass ends naturally, COMPLETE; only a cooldown that
outlasts the cap stops it, refused at once by the cap's name
(VENUE_COOLDOWN_EXCEEDS_THE_UNDEADLINED_WAIT_CAP), with everything above.
The refusal tests below therefore arm a cooldown PAST the cap (the venue's
Retry-After of 60 s); the wait tests (section 1) fail on the lane's 6f47d4a4.

Sections 1 and 2 drive the pinned venue SDK over OUR PacedTransport over a
mock socket (only the socket and the clock are fake); section 3 uses the
catalogue suite's fake venue behind the same `cooldown_check` the transport
calls.

NO TEST HERE SUBMITS AN ORDER.
"""
from __future__ import annotations

import threading

import httpx
import pytest

from sportsassets import venue_catalogue as vc
from sportsassets import venue_pace as VP
from sportsassets import venue_request_gate as GRT
from sportsassets import venue_sdk
from sportsassets.workers import premap
from tests.test_catalogue_recursive_partition import _window_board
from tests.test_venue_catalogue_is_complete import (DSN, NOW, FakeVenue,
                                                    _Http429, _Http502,
                                                    _event, _two_sided, _tx,
                                                    _wire, build_board)

pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

#: the walkers' refusal by its literal name (the taxonomy row and the
#: readback key)
R = "VENUE_429_COOLDOWN_NORMAL_READ_DEFERRED"
#: the sweep's refusal when the cooldown outlasts the undeadlined wait cap it
#: opted into, by its literal name (review round 2)
R_CAP = "VENUE_COOLDOWN_EXCEEDS_THE_UNDEADLINED_WAIT_CAP"
#: a cooldown past the cap: the venue's own Retry-After of 60 s
LONG_RETRY_AFTER_S = 60.0
#: the receipt notes carrying the sweep's cooldown waits, by literal name
NOTE_WAITS = "VENUE_429_COOLDOWN_WAITS"
NOTE_WAITED_MS = "VENUE_429_COOLDOWN_WAITED_MS"
#: the receipt note a read our gate withheld is counted under, by its
#: literal name (premap.NOTE_NOT_SENT_BY_OUR_GATE), "<note>:<refusal>"
NOTE = "REQUEST_NOT_SENT_BY_OUR_VENUE_GATE"


# ───────────────────────────────────────────────────────── the harness

class Clock:
    """The cooldown's fake monotonic clock: it moves only when a test moves
    it, or when the transport sleeps a bounded wait (`sleep`, which advances
    it and records the wait). The hard hold's sleep (`hold_sleep`) records
    its wait, advances the clock and lifts the hold -- the hold's remainder
    slept is the hold expired (its instant is wall time, which a fake sleep
    does not move)."""

    def __init__(self, t=5000.0):
        self.t = float(t)
        self.sleeps: list = []
        self.hold_sleeps: list = []

    def mono(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(float(s), 6))
        self.t += float(s)

    def hold_sleep(self, s):
        self.hold_sleeps.append(round(float(s), 3))
        self.t += float(s)
        GRT.clear_hold()


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(VP, "_clock", c.mono, raising=False)
    monkeypatch.setattr(VP, "_rand", lambda: 0.5, raising=False)
    monkeypatch.setattr(GRT, "_cooldown_sleep", c.sleep, raising=False)
    monkeypatch.setattr(GRT, "_hold_sleep", c.hold_sleep, raising=False)
    return c


@pytest.fixture(autouse=True)
def _clean():
    def _clear():
        VP.reset_rate_limit_state()
        GRT.clear_hold()
        GRT.bind_read(None)
        VP._penalty_until = 0.0
        VP._penalty_until_epoch = 0.0
    _clear()
    yield
    _clear()


def _arm(clock=None, retry_after_s=None):
    """One gateway 429 read by the transport: the process cooldown armed
    (5.5 s at the floor with the fixed jitter), as any walker's 429 arms it;
    with the venue's `retry_after_s`, that long."""
    if clock is not None:
        clock.t += 0.01
    got = VP.note_rate_limited(method="GET", path="/v1/markets/x/book",
                               source="test_walker",
                               retry_after_s=retry_after_s)
    assert VP.cooldown_left() > 0
    return got


def _arm_long(clock=None):
    """A cooldown PAST the undeadlined wait cap (the venue's Retry-After of
    60 s): the sweep is refused at once and stops."""
    got = _arm(clock, retry_after_s=LONG_RETRY_AFTER_S)
    assert VP.cooldown_left() > GRT.MAX_UNDEADLINED_WAIT_S
    return got


class BoardSocket:
    """The venue's gateway as a mock SOCKET under the pinned SDK and our
    PacedTransport: /v1/events (list, served from a FakeVenue board),
    /v1/events/slug/<slug> (detail), /v1/markets (the fallback). Records
    every request that REACHED it; `on_events_list(n)` runs as the n-th
    events.list request is answered (a 429 arriving elsewhere meanwhile)."""

    def __init__(self, board, on_events_list=None, clock=None):
        self.fake = FakeVenue(board)
        self.paths: list = []
        #: the fake-clock instant each request reached the socket
        self.at: list = []
        self.clock = clock
        self.lists = 0
        self.on_events_list = on_events_list
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        with self.lock:
            self.paths.append(path)
            self.at.append(self.clock.t if self.clock is not None else None)
        if path == "/v1/events":
            q = {k: v for k, v in request.url.params.items()}
            body = self.fake.list(q)
            with self.lock:
                self.lists += 1
                n = self.lists
            if self.on_events_list is not None:
                self.on_events_list(n)
            return httpx.Response(200, json=body, request=request)
        if path.startswith("/v1/events/slug/"):
            return httpx.Response(
                200, json=self.fake.by_slug(path.rsplit("/", 1)[-1]),
                request=request)
        if path == "/v1/markets":
            return httpx.Response(200, json={"markets": []}, request=request)
        return httpx.Response(404, json={"e": "unknown"}, request=request)

    def sent(self, prefix=""):
        with self.lock:
            return [p for p in self.paths if p.startswith(prefix)]


def _sdk_client(socket: BoardSocket):
    """The pinned SDK client, its own retries off as production builds it,
    over OUR transport over the mock socket -- the stack premap reads
    through in the workers process (pmus._get_client + _install_request_gate)."""
    from polymarket_us import PolymarketUS

    client = PolymarketUS(gateway_base_url="https://gateway.test",
                          api_base_url="https://api.test",
                          **venue_sdk.client_kwargs())
    client._http = httpx.Client(transport=GRT.PacedTransport(
        httpx.MockTransport(socket.handler)))
    return client


class GatedVenue(FakeVenue):
    """The catalogue suite's FakeVenue behind OUR gate: every read asks
    venue_request_gate.cooldown_check() first, exactly as PacedTransport
    .handle_request does, so a refused read raises VenueGateRefusal and never
    reaches `calls` (the venue never saw it). `arm_on_list` arms the cooldown
    just before that (1-based) events.list attempt; `arm_on_markets` arms it
    before the fallback's first markets.list."""

    def __init__(self, events, *, arm_on_list=None, arm_on_markets=False,
                 **kw):
        super().__init__(events, **kw)
        self.arm_on_list = arm_on_list
        self.list_attempts = 0
        venue = self

        class _M:
            def list(self, q):
                if arm_on_markets and VP.cooldown_left() <= 0:
                    _arm_long()
                _gate()
                venue.calls.append(("markets", dict(q)))
                return {"markets": []}

        self.client.markets = _M()

    def list(self, q):
        self.list_attempts += 1
        if self.arm_on_list is not None and \
                self.list_attempts == self.arm_on_list:
            _arm_long()
        _gate()
        return super().list(q)

    def by_slug(self, slug):
        _gate()
        return super().by_slug(slug)


def _gate():
    """cooldown_check as the transport calls it. This fake models a REFUSAL
    only: a cooldown the read would WAIT out (the bounded wait) fails the
    test loudly rather than letting the fake 'send' inside it -- the wait
    path is driven through the real transport (section 1)."""
    w = GRT.cooldown_check(stage="TEST_SOCKET")
    assert w == 0.0, "GatedVenue does not model a wait (%.3f s)" % w


def _not_sent_notes(rec):
    return {k: v for k, v in (rec.get("notes") or {}).items()
            if k.startswith(NOTE)}


# ════════════════════════════════════════════════════════════════════
# 0 · THE CLASSIFIER: OUR REFUSAL IS RATE-LIMITED AND NEVER SENT
# ════════════════════════════════════════════════════════════════════

def test_our_gates_refusal_reads_as_rate_limited_and_never_sent(monkeypatch):
    applied = []
    monkeypatch.setattr(VP, "penalize_observed",
                        lambda **k: applied.append(k) or {})
    for code in (VP.R_VENUE_429_COOLDOWN_READ_DEFERRED,
                 GRT.R_HOLD_EXCEEDS_UNDEADLINED_CAP,
                 GRT.R_COOLDOWN_EXCEEDS_DEADLINE):
        got = premap._rate_limited(GRT.VenueGateRefusal(
            code, {"refusal": code, "seconds_left": 4.2}))
        assert got, code
        assert got["never_sent"] is True and got["gate_refusal"] == code
        assert got["is_rate_limited"] is True and got["http_status"] is None
    # OUR refusal is no new word from the venue: the circuit is not applied
    # again for it (the 429 that armed the cooldown already applied it)
    assert applied == []
    # the venue's own 429 is unchanged: described, circuit applied, SENT
    got = premap._rate_limited(_Http429())
    assert got and not got.get("never_sent")
    assert applied and applied[0]["retry_after_s"] == 7.0
    # and any other failure is still not a rate limit
    assert premap._rate_limited(_Http502()) is None
    assert premap._rate_limited(RuntimeError("429")) is None


# ════════════════════════════════════════════════════════════════════
# 1 · REVIEW ROUND 2: A COOLDOWN WITHIN THE CAP IS WAITED OUT -- NOTHING
#     SENT INSIDE IT -- AND THE SWEEP COMPLETES (each fails on 6f47d4a4)
# ════════════════════════════════════════════════════════════════════

def _sent_inside(socket, armed_at, until):
    """The requests that reached the socket inside [armed_at, until)."""
    return [(p, t) for p, t in zip(socket.paths, socket.at)
            if t is not None and armed_at <= t < until - 1e-9]


@pg
async def test_a_floor_cooldown_another_walker_arms_mid_walk_is_waited_out_and_the_full_sweep_completes(
        monkeypatch, clock):
    """The reviewer's reproduction, through the real stack: a 150-event
    board (two pages), another walker's book 429 arriving while page 1 is in
    flight. 0e331f05: COMPLETE after the hold. 6f47d4a4: the next page
    refused at once, mode events/partial, stopped RATE_LIMITED_BY_VENUE,
    outcome PARTIAL -- the receipt catalogue_completeness reads. Now: page 2
    waits the 5.5 s cooldown out, is sent after it, and the walk ends on the
    venue's own short page."""
    armed = {}

    def other_walkers_429(n):
        if n == 1:
            _arm(clock)
            armed["at"] = clock.t
            armed["until"] = clock.t + VP.cooldown_left()
    socket = BoardSocket(_window_board(150), on_events_list=other_walkers_429,
                         clock=clock)
    conn, tx = await _tx()
    try:
        _wire(monkeypatch, conn, socket.fake)
        monkeypatch.setattr(premap.pmus, "_get_client",
                            lambda: _sdk_client(socket))
        summary = await premap.refresh()
        rec = summary["completeness"]
        w = rec["passes"][vc.PASS_WINDOW]
        assert summary["mode"] == "events", summary["mode"]
        assert w["stopped"] in vc.NATURAL_ENDS and w["error"] is None
        assert rec["outcome"] == "COMPLETE" and rec["catalogue_complete"] is True
        # ZERO requests inside the cooldown; the next page went after it
        assert _sent_inside(socket, armed["at"], armed["until"]) == []
        assert len(socket.sent("/v1/events")) == 2
        assert socket.at[1] >= armed["until"] - 1e-9
        assert clock.sleeps == [pytest.approx(5.5)]
        assert sum(clock.sleeps) <= GRT.MAX_UNDEADLINED_WAIT_S
        # every read the sweep made was SENT and counted; none withheld
        assert rec["requests"] == w["requests"] == 2
        assert _not_sent_notes(rec) == {}
        # the wait is on the sweep's own receipt and on the readback
        assert rec["notes"][NOTE_WAITS] == 1
        assert rec["notes"][NOTE_WAITED_MS] == 5500
        st = VP.rate_limit_state()
        src = st["by_source"]["premap_full"]
        assert src["cooldown_waits"] == 1 and src["deferred"] == 0
        assert st["bounded_waits"] == 1
        # the 2xx sent after the cooldown was armed lifted it
        assert st["cooldown_active"] is False and st["resets"] == 1
        assert await conn.fetchval("SELECT count(*) FROM us_premap") > 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_hold_and_the_cooldown_a_book_429_arms_are_one_budget_and_the_sweep_completes(
        monkeypatch, clock):
    """A walker's book_read 429 arms BOTH controls: the escalating cooldown
    (at the transport) and the hard not-before hold (penalize_observed). The
    sweep's next read waits out both inside ONE MAX_UNDEADLINED_WAIT_S budget,
    nothing is sent before they lift, and the sweep completes."""
    armed = {}

    def book_read_429(n):
        if n == 1:
            _arm(clock)
            VP.penalize_observed(reason="book_read 429 (another walker)")
            armed["at"] = clock.t
            armed["until"] = clock.t + VP.cooldown_left()
    socket = BoardSocket(_window_board(150), on_events_list=book_read_429,
                         clock=clock)
    conn, tx = await _tx()
    try:
        _wire(monkeypatch, conn, socket.fake)
        monkeypatch.setattr(premap.pmus, "_get_client",
                            lambda: _sdk_client(socket))
        summary = await premap.refresh()
        rec = summary["completeness"]
        assert summary["mode"] == "events"
        assert rec["outcome"] == "COMPLETE"
        assert _sent_inside(socket, armed["at"], armed["until"]) == []
        assert len(clock.hold_sleeps) == 1 and clock.sleeps == [pytest.approx(5.5)]
        assert sum(clock.sleeps) + sum(clock.hold_sleeps) <= \
            GRT.MAX_UNDEADLINED_WAIT_S
        assert rec["requests"] == 2 and _not_sent_notes(rec) == {}
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.parametrize("lane", ["calendar", "fast"])
async def test_the_calendar_and_fast_lanes_wait_a_floor_cooldown_out_and_complete(
        monkeypatch, clock, lane):
    """The other two receipts catalogue_completeness and the plane read: a
    floor cooldown in force when the lane starts is waited out, nothing is
    sent inside it, and every pass ends on the venue's own board."""
    window, ahead, earlier = build_board()
    socket = BoardSocket(window + ahead + earlier, clock=clock)
    _arm(clock)
    armed_at, until = clock.t, clock.t + VP.cooldown_left()
    conn, tx = await _tx()
    try:
        _wire(monkeypatch, conn, socket.fake)
        monkeypatch.setattr(premap.pmus, "_get_client",
                            lambda: _sdk_client(socket))
        got = await (premap.calendar_refresh() if lane == "calendar"
                     else premap.fast_refresh())
        rec = got["completeness"]
        assert rec["outcome"] == "COMPLETE", (rec["outcome"], {
            k: v.get("stopped") for k, v in rec["passes"].items()})
        for name, p in rec["passes"].items():
            assert p["stopped"] in vc.NATURAL_ENDS, (name, p["stopped"])
        assert socket.paths and _sent_inside(socket, armed_at, until) == []
        assert socket.at[0] >= until - 1e-9
        assert clock.sleeps == [pytest.approx(5.5)]
        assert _not_sent_notes(rec) == {}
        assert rec["notes"][NOTE_WAITS] == 1
        src = VP.rate_limit_state()["by_source"]["premap_%s" % lane]
        assert src["cooldown_waits"] == 1 and src["deferred"] == 0
    finally:
        await tx.rollback()
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 2 · A COOLDOWN PAST THE CAP, THROUGH THE SDK AND OUR TRANSPORT: NOTHING
#     SENT, NO WAIT, NO FALLBACK
# ════════════════════════════════════════════════════════════════════

@pg
async def test_a_cooldown_past_the_cap_at_the_full_sweep_sends_nothing_and_never_falls_back(
        monkeypatch, clock):
    """The first review's reproduction, through the real stack, with a
    cooldown the sweep may not wait out (the venue's Retry-After of 60 s):
    refused at once -- never after sleeping part of it. Before the first fix
    the three probe rungs were each refused, the sweep stopped
    NO_PARAMETER_VARIANT_ANSWERED and ran the markets fallback."""
    window, _, _ = build_board()
    socket = BoardSocket(window, clock=clock)
    _arm_long(clock)
    deferred_before = VP.rate_limit_state()["deferred_total"]
    conn, tx = await _tx()
    try:
        claims = _wire(monkeypatch, conn, socket.fake)
        monkeypatch.setattr(premap.pmus, "_get_client",
                            lambda: _sdk_client(socket))
        summary = await premap.refresh()
        rec = summary["completeness"]
        w = rec["passes"][vc.PASS_WINDOW]
        # NOTHING reached the venue: no rung, no page, no markets.list
        assert socket.paths == []
        assert summary["mode"] == "events/rate_limited"
        assert vc.PASS_MARKETS_FALLBACK not in rec["passes"]
        # the ladder ended on the FIRST refused rung (one claim, one
        # refusal), named rate-limited -- never "no variant answered"
        assert len(claims) == 1
        assert w["stopped"] == vc.STOP_RATE_LIMITED
        assert R_CAP in (w["error"] or "") and R_CAP in (summary["err"] or "")
        # refused at once: the transport slept nothing of it
        assert clock.sleeps == [] and clock.hold_sleeps == []
        # the venue was never asked: the record never says it answered 429
        assert summary["err"].startswith("NOT_SENT: ")
        assert "the venue rate-limited" not in summary["err"]
        # a read our gate withheld is not a request
        assert rec["requests"] == 0 and w["requests"] == 0
        assert w["probe_requests_failed"] == 0
        assert _not_sent_notes(rec) == {
            "%s:%s" % (NOTE, R_CAP): 1}
        assert NOTE_WAITS not in rec["notes"]
        assert rec["outcome"] == "FAILED"
        assert summary["receipt_history"]["appended"] is True
        assert await conn.fetchval("SELECT count(*) FROM us_premap") == 0
        # the transport counted the deferral, attributed to the sweep
        st = VP.rate_limit_state()
        assert st["deferred_total"] == deferred_before + 1
        assert st["by_source"]["premap_full"]["deferred"] == 1
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_cooldown_past_the_cap_armed_mid_walk_stops_the_pass_rate_limited_and_counts_only_what_was_sent(
        monkeypatch, clock):
    """A cooldown past the cap is armed while the probe page is in flight
    (another walker's 429, the venue's Retry-After of 60 s). The 200 already
    in flight changes nothing; the per-event detail repair and the next page
    are refused by the transport at once. Before the first fix both were
    counted as requests and the pass stopped REQUEST_FAILED."""
    board = _window_board(150)
    # an event the listing serves short (2 of 3 markets): the detail repair
    t0 = NOW
    full = [_two_sided("aec-aaa-short-%d" % i, "Q%d" % i,
                       "table_tennis_match_winner", t0, "A", "B")
            for i in range(3)]
    board.append(_event("aaa-short-%s" % t0.strftime("%Y-%m-%d"),
                        "Short vs. Listing", t0, full[:2], count=3,
                        full_markets=full))
    socket = BoardSocket(
        board, on_events_list=lambda n: _arm_long(clock) if n == 1 else None,
        clock=clock)
    conn, tx = await _tx()
    try:
        _wire(monkeypatch, conn, socket.fake)
        monkeypatch.setattr(premap.pmus, "_get_client",
                            lambda: _sdk_client(socket))
        summary = await premap.refresh()
        rec = summary["completeness"]
        w = rec["passes"][vc.PASS_WINDOW]
        # ONE request reached the venue: the probe page
        assert socket.sent() == ["/v1/events"]
        assert w["stopped"] == vc.STOP_RATE_LIMITED
        assert R_CAP in (w["error"] or "")
        assert clock.sleeps == []
        # the receipt counts what was SENT, nothing our gate withheld
        assert rec["requests"] == w["requests"] == 1
        assert rec["requests_outside_page_walks"].get("event_detail", 0) == 0
        assert _not_sent_notes(rec) == {
            "%s:%s" % (NOTE, R_CAP): 2}
        # rows read before the cooldown are kept; no fallback
        assert summary["mode"] == "events/partial"
        assert vc.PASS_MARKETS_FALLBACK not in rec["passes"]
        assert await conn.fetchval("SELECT count(*) FROM us_premap") > 0
    finally:
        await tx.rollback()
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 3 · THE OTHER WALKS, A COOLDOWN PAST THE CAP: PARTITION, CALENDAR,
#     FALLBACK
# ════════════════════════════════════════════════════════════════════

@pg
async def test_a_cooldown_refusal_in_a_partition_bucket_aborts_it_rate_limited_never_counted(
        monkeypatch, clock):
    """The window truncates (3 requests), the partition walks its first
    bucket -- refused by our gate. Before the fix the bucket stopped
    REQUEST_FAILED with a request it never sent."""
    venue = GatedVenue(_window_board(400), arm_on_list=4)
    monkeypatch.setattr(premap, "PARTITION_MAX_PAGES", 40)
    conn, tx = await _tx()
    try:
        _wire(monkeypatch, conn, venue)
        summary = await premap.refresh(max_pages=3)
        rec = summary["completeness"]
        w = rec["passes"][vc.PASS_WINDOW]
        sent = [c for c in venue.calls if c[0] == "events"]
        assert len(sent) == 3 and venue.list_attempts == 4
        part = w["partition"]
        assert part["aborted_by"] == vc.STOP_RATE_LIMITED
        bucket = [b for b in part["buckets"] if not b["root"]][0]
        assert bucket["stopped"] == vc.STOP_RATE_LIMITED
        assert bucket["requests"] == 0
        assert rec["requests"] == len(sent) == 3
        assert w["truncated"] is True and rec["catalogue_complete"] is False
        assert [c for c in venue.calls if c[0] == "markets"] == []
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_cooldown_in_force_stops_the_calendar_lane_named_and_sends_nothing(
        monkeypatch, clock):
    """Before the fix the AHEAD pass's slices were each refused, labelled
    REQUEST_FAILED and counted, and the lane went on to STARTED_EARLIER."""
    window, ahead, earlier = build_board()
    venue = GatedVenue(window + ahead + earlier)
    _arm_long(clock)
    conn, tx = await _tx()
    try:
        _wire(monkeypatch, conn, venue)
        cal = await premap.calendar_refresh()
        rec = cal["completeness"]
        assert venue.calls == [] and venue.list_attempts == 1
        assert rec["passes"][vc.PASS_AHEAD]["stopped"] == vc.STOP_RATE_LIMITED
        assert rec["passes"][vc.PASS_STARTED_EARLIER]["stopped"] == \
            vc.STOP_NOT_RUN
        assert rec["requests"] == 0
        assert cal["mode"] == "calendar/partial" and R_CAP in (cal["err"] or "")
        assert rec["outcome"] == "FAILED"
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_markets_fallback_never_counts_a_page_our_gate_withheld(
        monkeypatch, clock):
    """A DEAD events board (502 on every rung, no cooldown) takes the
    fallback, as R30A allows; a 429 elsewhere arms the cooldown as its first
    markets.list page is due. Before the fix that page was REQUEST_FAILED
    and counted as sent."""
    venue = GatedVenue([], fail_on=lambda q: _Http502(), arm_on_markets=True)
    conn, tx = await _tx()
    try:
        _wire(monkeypatch, conn, venue)
        summary = await premap.refresh()
        rec = summary["completeness"]
        fb = rec["passes"][vc.PASS_MARKETS_FALLBACK]
        assert summary["mode"] == "markets"
        assert [c for c in venue.calls if c[0] == "markets"] == []
        assert fb["stopped"] == vc.STOP_RATE_LIMITED
        assert R_CAP in (fb["error"] or "")
        assert fb["requests"] == 0
        # the three dead rungs WERE sent: still counted
        assert rec["requests"] == 3
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_venues_own_429_on_the_probe_is_unchanged(monkeypatch):
    """The R30A path stays as it was: a 429 the venue sent is one request,
    counted, the circuit applied, no fallback."""
    venue = FakeVenue([], fail_on=lambda q: _Http429())
    monkeypatch.setattr(VP, "penalize_observed", lambda **k: {})
    conn, tx = await _tx()
    try:
        _wire(monkeypatch, conn, venue)
        summary = await premap.refresh()
        rec = summary["completeness"]
        assert summary["mode"] == "events/rate_limited"
        assert rec["requests"] == 1
        assert rec["passes"][vc.PASS_WINDOW]["probe_requests_failed"] == 1
        assert _not_sent_notes(rec) == {}
    finally:
        await tx.rollback()
        await conn.close()
