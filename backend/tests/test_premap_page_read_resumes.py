"""A PAGE READ THAT WAS NEVER SENT IS NOT A FAILED PAGE (P0, 2026-10-09).

PRODUCTION (render-ops 37932260211 / 37932278119 / 37932505159 / 37932513838 /
37932934394, sportsassets-workers, release 732cc0c6). The full sweep that
started with the 03:26:46Z boot read offsets 0, 95, 190, 285 and 380
(03:35:55.459Z) of its window, then:

    03:36:35.277Z premap events path failed mid-sweep after 55060 rows
                  (RuntimeError: TimeoutError: ); keeping them, no fallback
    mode events/partial, 476 events, pages_walked 5 (complete sweeps that
    day: 24 pages, 2,224 / 2,209 events)

The 30 s `asyncio.wait_for` around the page read bounds the worker thread's
WHOLE job -- premap's venue_pace claim, the transport's not-before hold
(armed for every reader by ANY reader's 429: the last one 03:35:55.696Z, a
/book read; the process's next gateway request 03:36:14.856Z), the
transport's own gap -- not the request. No response for offset 475 of that
window was ever logged (03:36-03:45Z) while six other gateway reads completed
in the same 30 s. The read timed out waiting for its turn, was reported as an
anonymous venue failure, counted as a request it had not made, and ended the
sweep.

These tests drive premap.refresh through the real code path -- the
polymarket_us SDK client, the production request gate
(pmus._install_request_gate: venue_request_gate.PacedTransport), an httpx
MockTransport venue -- and pin:
  1. a read held by the gate past its bound (another reader's 429) is never
     sent late, is read again at the same offset once the hold lifts, and the
     sweep completes: no request leaves after its caller gave up;
  2. a read that waited past its bound in the venue_pace queue is never sent
     and is read again;
  3. a read that WAS sent and not answered is re-sent once, counted;
  4. retries are bounded: when they run out the pass stops named (never
     "TimeoutError: "), unsent attempts are not counted as requests, and a
     never-sent probe takes no fallback;
  5. the premap heartbeat publishes completeness: events walked against the
     last complete sweep, pages, rows, truncated, resumed reads, the last
     complete sweep's time -- and a partial sweep is never complete.
"""
from __future__ import annotations

import asyncio
import datetime as _dtmod
import json
import threading
import time

import httpx
import pytest

from sportsassets import venue_catalogue as vc
from sportsassets import venue_pace
from sportsassets import venue_request_gate as grt
from sportsassets.workers import premap
from tests.test_premap_memory_bound import _Lane, _Pool, _Venue, _install

UTC = _dtmod.UTC

#: how a page read that did not answer is named on the receipt and the
#: pass's error (premap.READ_*): part of the contract, so spelled out here
QUEUED = "PAGE_READ_QUEUED_PAST_ITS_BOUND_NEVER_SENT"
REFUSED = "PAGE_READ_REFUSED_BY_OUR_GATE_NEVER_SENT"
SENT_NO_ANSWER = "PAGE_READ_SENT_NO_ANSWER_IN_ITS_BOUND"


class _StatePool(_Pool):
    """_Pool, plus ingestion_state kept and read back (the premap
    heartbeat records), so consecutive refreshes see each other."""

    def __init__(self):
        super().__init__()
        self.state: dict = {}

    async def execute(self, sql, *a):
        if "INSERT INTO ingestion_state" in sql:
            self.state[a[0]] = json.loads(a[1])
        return await super().execute(sql, *a)

    async def fetchval(self, sql, *a):
        if "premap-prior-state" in sql:
            v = self.state.get(a[0])
            return None if v is None else json.dumps(v)
        return await super().fetchval(sql, *a)


class _Board:
    """The venue: a window of `n` events (one market each) served by
    _Venue's generator, every request recorded with the instant it ARRIVED
    and its offset; `before(q)` may delay or arm the gate per request."""

    def __init__(self, n=250, before=None):
        now = _dtmod.datetime.now(UTC)
        self.venue = _Venue(_Lane(n, n, now + _dtmod.timedelta(hours=1),
                                  now + _dtmod.timedelta(hours=40), 10**6))
        self.before = before
        self.seen: list = []         # (path, offset, arrived_at)
        self.lock = threading.Lock()

    def handler(self, request):
        q = dict(request.url.params)
        with self.lock:
            self.seen.append((request.url.path, int(q.get("offset", 0)),
                              time.monotonic()))
        if self.before is not None:
            self.before(request.url.path, q)
        return self.venue.handler(request)

    def events_offsets(self):
        return [o for p, o, _ in self.seen if p.endswith("/events")]


def _wire(monkeypatch, board, pool, *, timeout_s=1.5):
    """The production client wiring with the in-memory venue: the SDK
    client, premap's body release, AND the request gate's transport
    (venue_request_gate.PacedTransport, as pmus._get_client installs it)."""
    from sportsassets import pmus

    client = _install(monkeypatch, board.venue, pool)
    client._http = httpx.Client(transport=httpx.MockTransport(board.handler))
    got = pmus._install_request_gate(client)
    assert got["installed"] is True
    # the transport's own gap is venue_pace's; the gap is not the subject
    # here (venue_pace has its own tests), the hold and the deadline are
    client._http._transport._pace = lambda: None
    monkeypatch.setattr(premap, "LIST_CALL_TIMEOUT_S", timeout_s)
    monkeypatch.setattr(premap, "RESEND_PAUSE_S", raising=False, value=0.05)
    grt.clear_hold()
    return client


def _receipt_requests(summary):
    return summary["completeness"]["requests"]


# ── 1. the production failure: another reader's 429 holds the gate ──────

def test_a_page_held_by_the_gate_is_read_again_and_never_sent_late(monkeypatch):
    """03:35:55.696Z another reader's /book 429 armed the process's
    not-before hold; premap's next page read waited on it inside its 30 s
    bound and timed out. Now the read carries its dispatch deadline, the
    gate refuses it by name at once instead of parking it, the sweep waits
    the hold out and reads THE SAME OFFSET again -- and no request ever
    reaches the venue after its caller gave up."""
    armed = []

    def before(path, q):
        # the probe page answers; the moment it has, another reader's 429
        # arms a hold longer than the page read's bound
        if path.endswith("/events") and "offset" not in q and not armed:
            armed.append(grt.hold_until(until_epoch_s=time.time() + 2.5,
                                        reason="another reader's 429"))

    board = _Board(before=before)
    pool = _StatePool()
    _wire(monkeypatch, board, pool)
    try:
        t0 = time.monotonic()
        summary = asyncio.run(premap.refresh())
        done = time.monotonic()
        time.sleep(3.0)          # long enough for any parked thread to send
    finally:
        grt.clear_hold()
    assert armed, "the hold was never armed"
    assert summary["mode"] == "events" and summary["err"] is None, summary["err"]
    w = summary["completeness"]["passes"][vc.PASS_WINDOW]
    assert w["stopped"] == vc.STOP_SHORT_PAGE and w["complete"] is True
    assert w["reads_unsent"] == 1 and w["resumed_reads"] == 1
    assert w["read_failures"] == {REFUSED: 1}
    assert summary["completeness"]["catalogue_complete"] is True
    # every offset the walk asked for reached the venue exactly once, and
    # nothing arrived after the sweep finished: no orphan request
    offs = board.events_offsets()
    assert len(offs) == len(set(offs)), offs
    assert all(at <= done for _, _, at in board.seen)
    # the receipt counts what was sent, and only that
    assert _receipt_requests(summary) == len(board.seen)
    assert summary["rows"] > 0 and t0 < done


# ── 2. the venue_pace queue outlasts the bound ───────────────────────────

def test_a_page_queued_past_its_bound_on_the_venue_gap_is_never_sent(monkeypatch):
    """premap claims venue_pace's NORMAL lane, FIFO behind every other
    measurement reader of the process (at twice the gap while the 429
    circuit holds). A claim that outlasts the read's bound must not send
    when it finally gets the gap, and the page is read again."""
    real_pace = venue_pace.pace
    calls = []

    def queued_pace(gap=venue_pace.MIN_GAP_S, **k):
        calls.append(gap)
        if len(calls) == 2:          # the first page after the probe
            time.sleep(2.5)          # the queue ahead of it
        return real_pace(0.0)

    board = _Board()
    pool = _StatePool()
    _wire(monkeypatch, board, pool)
    monkeypatch.setattr(venue_pace, "pace", queued_pace)
    summary = asyncio.run(premap.refresh())
    done = time.monotonic()
    time.sleep(2.0)
    assert summary["mode"] == "events" and summary["err"] is None, summary["err"]
    w = summary["completeness"]["passes"][vc.PASS_WINDOW]
    assert w["read_failures"] == {QUEUED: 1}
    assert w["reads_unsent"] == 1 and w["resumed_reads"] == 1
    assert w["complete"] is True
    offs = board.events_offsets()
    assert len(offs) == len(set(offs)), offs
    assert all(at <= done for _, _, at in board.seen)
    assert _receipt_requests(summary) == len(board.seen)


# ── 3. sent, and not answered ────────────────────────────────────────────

def test_a_page_sent_and_not_answered_is_resent_once_and_counted(monkeypatch):
    slow = []

    def before(path, q):
        if path.endswith("/events") and q.get("offset") == "95" and not slow:
            slow.append(1)
            time.sleep(2.0)          # answers after the read's bound

    board = _Board(before=before)
    pool = _StatePool()
    _wire(monkeypatch, board, pool)
    summary = asyncio.run(premap.refresh())
    time.sleep(1.0)                  # the slow answer lands, discarded
    assert summary["mode"] == "events" and summary["err"] is None, summary["err"]
    w = summary["completeness"]["passes"][vc.PASS_WINDOW]
    assert w["read_failures"] == {SENT_NO_ANSWER: 1}
    assert w["reads_resent"] == 1 and w["resumed_reads"] == 1
    assert w["reads_unsent"] == 0 and w["complete"] is True
    # offset 95 reached the venue twice (the slow one and the re-send), and
    # BOTH are requests on the receipt
    assert board.events_offsets().count(95) == 2
    assert _receipt_requests(summary) == len(board.seen)


# ── 4. bounded, named, and no fallback for a read never sent ─────────────

def test_a_hold_longer_than_the_pass_has_left_truncates_it_by_name(
        monkeypatch):
    """A hold the venue asked for that outlasts the pass's wall-time bound:
    the page is not waited for past the bound and not sent -- the pass is
    WALL_TIME_BUDGET_EXHAUSTED (truncated, named, the refusal on its
    error), never "TimeoutError: ", the unsent attempts are no requests, and
    the sweep is TRUNCATED -- never complete."""
    def before(path, q):
        if path.endswith("/events") and "offset" not in q:
            # every later read meets a hold longer than the pass has left
            grt.hold_until(until_epoch_s=time.time() + 30.0,
                           reason="another reader's 429")

    board = _Board(before=before)
    pool = _StatePool()
    _wire(monkeypatch, board, pool, timeout_s=1.2)
    monkeypatch.setattr(premap, "WINDOW_MAX_SECONDS", 4.0)
    monkeypatch.setattr(premap, "PARTITION_MAX_SECONDS", 0.5)
    try:
        t0 = time.monotonic()
        summary = asyncio.run(premap.refresh())
        took = time.monotonic() - t0
    finally:
        grt.clear_hold()
    w = summary["completeness"]["passes"][vc.PASS_WINDOW]
    assert took < 20.0, "the 30 s hold was waited out past the pass's bound"
    assert summary["truncated"] is True and summary["mode"] == "events"
    assert w["stopped"] in (vc.STOP_WALL_TIME, vc.STOP_PARTITION_UNRESOLVED)
    assert w.get("stopped_before_partition", w["stopped"]) == vc.STOP_WALL_TIME
    assert REFUSED in w["error"]
    assert "TimeoutError: " not in json.dumps(summary, default=str)
    assert w["reads_unsent"] >= 1 and w["resumed_reads"] == 0
    # only the probe reached the venue, and only it is a request
    assert board.events_offsets() == [0]
    assert _receipt_requests(summary) == 1
    sc = summary["sweep_completeness"]
    assert sc["complete"] is False and sc["status"] == "TRUNCATED"
    assert sc["truncated"] is True and sc["reads_unsent"] >= 1
    assert summary["prune_skipped"]


def test_unsent_retries_run_out_as_a_named_request_failure(monkeypatch):
    calls = []
    real_pace = venue_pace.pace

    def queued_pace(gap=venue_pace.MIN_GAP_S, **k):
        calls.append(gap)
        if len(calls) >= 2:
            time.sleep(1.3)          # every page read outlasts its bound
        return real_pace(0.0)

    board = _Board()
    pool = _StatePool()
    _wire(monkeypatch, board, pool, timeout_s=1.1)
    monkeypatch.setattr(venue_pace, "pace", queued_pace)
    monkeypatch.setattr(premap, "PAGE_READ_UNSENT_RETRIES", raising=False, value=2)
    monkeypatch.setattr(premap, "UNSENT_RETRY_PAUSE_S", raising=False, value=0.05)
    summary = asyncio.run(premap.refresh())
    time.sleep(1.0)
    w = summary["completeness"]["passes"][vc.PASS_WINDOW]
    assert summary["mode"] == "events/partial"
    assert w["stopped"] == vc.STOP_ERROR
    assert QUEUED in w["error"]
    assert "retries exhausted" in w["error"]
    assert w["reads_unsent"] == 3          # the first attempt + 2 retries
    assert board.events_offsets() == [0]   # none of them was ever sent
    assert _receipt_requests(summary) == 1
    assert summary["sweep_completeness"]["complete"] is False


def test_a_window_probe_never_sent_takes_no_fallback(monkeypatch):
    """The markets fallback is for a board that does not answer. A probe
    the process's own gate never let out was not answered by anyone; the
    unbounded rungs (the stale catalogue) and markets.list are not asked."""
    real_pace = venue_pace.pace

    def queued_pace(gap=venue_pace.MIN_GAP_S, **k):
        time.sleep(1.3)              # every read outlasts its bound
        return real_pace(0.0)

    board = _Board()
    pool = _StatePool()
    _wire(monkeypatch, board, pool, timeout_s=1.1)
    monkeypatch.setattr(venue_pace, "pace", queued_pace)
    monkeypatch.setattr(premap, "PAGE_READ_UNSENT_RETRIES", raising=False, value=1)
    monkeypatch.setattr(premap, "UNSENT_RETRY_PAUSE_S", raising=False, value=0.05)
    summary = asyncio.run(premap.refresh())
    time.sleep(1.0)
    assert summary["mode"] == "events/not_sent"
    assert QUEUED in summary["err"]
    assert board.seen == []                 # nothing reached the venue
    rec = summary["completeness"]
    assert rec["requests"] == 0
    assert vc.PASS_MARKETS_FALLBACK not in rec["passes"]
    assert summary["sweep_completeness"]["complete"] is False


# ── 5. the heartbeat says how complete the sweep was ─────────────────────

def test_the_heartbeat_publishes_completeness_and_a_partial_is_never_complete(
        monkeypatch):
    board = _Board()
    pool = _StatePool()
    _wire(monkeypatch, board, pool)
    first = asyncio.run(premap.refresh())
    sc1 = first["sweep_completeness"]
    assert sc1["complete"] is True and sc1["status"] == "COMPLETE"
    assert sc1["events_walked"] == first["completeness"]["events"]["seen"] == 250
    # no complete sweep before it: nothing to measure against, by name
    assert sc1["events_expected"] is None
    assert sc1["events_expected_basis"].startswith("UNMEASURED")
    assert sc1["pages"] == first["completeness"]["pages_read"] == 3
    assert sc1["rows"] == first["rows"] and sc1["truncated"] is False
    assert sc1["last_complete_sweep_at"]
    assert pool.state["premap_last"]["sweep_completeness"] == sc1

    # the next sweep fails after its first page (a non-retried failure)
    class _Http500(Exception):
        def __init__(self):
            super().__init__("500 Internal Server Error")
            self.status_code = 500
            self.response = type("R", (), {"status_code": 500,
                                           "headers": {}})()

    def before(path, q):
        if path.endswith("/events") and q.get("offset") == "95":
            raise _Http500()

    board.before = before
    progress = []
    real_record = premap._record_last

    async def spy(pool_, summary, key="premap_last"):
        progress.append(json.loads(json.dumps(summary, default=str)))
        return await real_record(pool_, summary, key)

    monkeypatch.setattr(premap, "_record_last", spy)
    second = asyncio.run(premap.refresh())
    sc2 = second["sweep_completeness"]
    assert second["mode"] == "events/partial"
    assert sc2["complete"] is False and sc2["status"] == "PARTIAL"
    assert sc2["events_walked"] == 100 < sc2["events_expected"] == 250
    assert sc2["last_complete_sweep_at"] == sc1["last_complete_sweep_at"]
    assert sc2["stopped"] == vc.STOP_ERROR and sc2["error"]
    # every record of the sweep in flight carried it, never complete
    inflight = [p["sweep_completeness"] for p in progress[:-1]]
    assert inflight and all(c["complete"] is False
                            and c["status"] == "IN_PROGRESS"
                            and c["last_complete_sweep_at"]
                            == sc1["last_complete_sweep_at"]
                            for c in inflight)
    assert pool.state["premap_last"]["sweep_completeness"] == sc2


def test_with_no_state_the_last_complete_sweep_comes_from_the_receipts(
        monkeypatch):
    """The first refresh after this lands has no carried record: the
    append-only receipts' latest COMPLETE row of the lane is the reference."""
    class _ReceiptPool(_StatePool):
        async def fetch(self, sql, *a):
            if "premap-last-complete" in sql:
                assert a == ("full",)
                return [{"id": 1363,
                         "finished_at": _dtmod.datetime(2026, 10, 9, 5, 10, 13,
                                                        tzinfo=UTC),
                         "events_seen": 2209, "pages_read": 24,
                         "requests": 25, "sides_written": 77178}]
            return await super().fetch(sql, *a)

    board = _Board()
    pool = _ReceiptPool()
    _wire(monkeypatch, board, pool)
    s = asyncio.run(premap.refresh())
    sc = s["sweep_completeness"]
    assert sc["events_expected"] == 2209
    assert "2026-10-09T05:10:13" in sc["events_expected_basis"]
    # this refresh was complete: it becomes the last complete sweep
    assert sc["last_complete_sweep"]["source"] == "this refresh"


def test_the_failure_names_are_the_modules():
    assert (premap.READ_QUEUED_PAST_BOUND, premap.READ_REFUSED_BY_OUR_GATE,
            premap.READ_SENT_NO_ANSWER) == (QUEUED, REFUSED, SENT_NO_ANSWER)


def test_progress_records_never_claim_completion():
    c = premap.sweep_completeness(lane="full", status="IN_PROGRESS",
                                  complete=False, events_walked=476,
                                  rows=55060, pages=5, last_complete=None)
    assert c["complete"] is False and c["events_expected"] is None
    assert c["events_expected_basis"].startswith("UNMEASURED")
