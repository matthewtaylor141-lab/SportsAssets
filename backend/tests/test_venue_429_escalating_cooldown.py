"""P0-429 -- VENUE RATE-LIMIT RECOVERY: the escalating 429 cooldown.

PRODUCTION (sportsassets-workers, RC6 732cc0c6, 2026-10-09):

  04:20:08.9-04:20:16.9Z  gateway.polymarket.us answered 429 nine times in
                          8 s on nine different markets at ~0.7 s spacing,
                          bbo-then-book pairs on one slug;
  11:40:00-11:42:00Z      24 of 64 gateway requests were 429s, in runs of
                          up to seven at 0.7-1 s;
  03:44:18-03:44:24Z      `data-api 429 — backing off 0s` seventeen times
                          in six seconds, attempts 1 to 4.

0.7 s is venue_pace.MIN_GAP_S x PENALTY_MULT: the "circuit" only doubled
the gap; the not-before hold was checked BEFORE the pacer's queue, so every
walker thread already queued dispatched into the limit; bbo_read sent its
second feed into the limit the first had been refused by; and polite_get
slept the venue's Retry-After of zero, per caller.

What these pin, every one through the shipped code (the pinned venue SDK,
`PacedTransport`, `pmus`, the walkers, `ratelimit.Throttle`) on a FAKE
clock -- only the socket is a mock:

  * nine consecutive 429s produce strictly increasing cooldowns up to the
    cap, and ZERO requests reach the venue inside any of them; the 04:20Z
    run replayed at its own offsets reaches the venue twice, not ten times;
  * a successful read resets the escalation, a 2xx that left before the
    429 does not;
  * a longer Retry-After is honoured;
  * a NORMAL-lane read is refused by name (VENUE_429_COOLDOWN_NORMAL_READ_
    DEFERRED) -- and so is one queued in the pacer when the 429 arrived;
  * a PRIORITY-lane claim waits at most PRIORITY_COOLDOWN_MAX_WAIT_S and is
    sent: never refused, never starved;
  * bbo_read makes ONE request on a 429; book_read does not sleep a walker
    through the hold to retry into the limit;
  * the walkers stop their pass and count what they skipped;
  * the readback names the cooldown, the consecutive count, the deferred
    and skipped reads and the last 429 per source -- on the workers'
    walkers' beats, and in the API process through its transport totals;
  * the data-api throttle: never a zero-second retry, the same escalation
    per host, other callers held, priority bounded; the exit worker's
    direct reads (whale_exits) arm and lift the same host cooldown.

NO TEST HERE SUBMITS AN ORDER.
"""
from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from sportsassets import pmus
from sportsassets import ratelimit
from sportsassets import venue_pace as VP
from sportsassets import venue_request_gate as GRT
from sportsassets import venue_sdk

SLUG = "aec-atp-timder-omajas-2026-10-08"
#: the refusal by its literal name (the taxonomy row and the readback key on it)
R = "VENUE_429_COOLDOWN_NORMAL_READ_DEFERRED"
GOOD_BOOK = {"marketData": {"bids": [{"px": {"value": "0.41"}, "qty": "120"}],
                            "offers": [{"px": {"value": "0.44"}, "qty": "90"}],
                            "bestBid": {"value": "0.41"},
                            "bestAsk": {"value": "0.44"},
                            "state": "MARKET_STATE_OPEN"}}


# ───────────────────────────────────────────────────────── the harness

class Clock:
    """The cooldown's fake monotonic clock; `sleep` is the transport's
    bounded-wait sleep, and advances it."""

    def __init__(self, t=1000.0):
        self.t = float(t)
        self.sleeps: list = []

    def mono(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(float(s), 6))
        self.t += float(s)


class Venue:
    """A scripted socket that records every request that reached it."""

    def __init__(self, default=("ok", 429, {"e": "slow down"}, {})):
        self.script: list = []
        self.default = default
        self.paths: list = []
        self.lock = threading.Lock()

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.paths.append(request.url.path)
            step = self.script.pop(0) if self.script else self.default
        status, body = step[1], step[2]
        headers = step[3] if len(step) > 3 else {}
        return httpx.Response(status, json=body, headers=headers,
                              request=request)

    @property
    def count(self) -> int:
        with self.lock:
            return len(self.paths)


def _client(venue: Venue, pace=None):
    """The pinned SDK client, its own retries off as production builds it,
    over OUR transport over a mock socket."""
    from polymarket_us import PolymarketUS

    client = PolymarketUS(gateway_base_url="https://gateway.test",
                          api_base_url="https://api.test",
                          **venue_sdk.client_kwargs())
    client._http = httpx.Client(transport=GRT.PacedTransport(
        httpx.MockTransport(venue.handler), pace=pace))
    return client


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(VP, "_clock", c.mono, raising=False)
    monkeypatch.setattr(VP, "_rand", lambda: 0.5, raising=False)
    monkeypatch.setattr(GRT, "_cooldown_sleep", c.sleep, raising=False)
    return c


@pytest.fixture(autouse=True)
def _clean():
    """Every process-wide control this path touches, both ways."""
    def _clear():
        if hasattr(VP, "reset_rate_limit_state"):
            VP.reset_rate_limit_state()
        GRT.clear_hold()
        GRT.bind_read(None)
        VP._penalty_until = 0.0
        VP._penalty_until_epoch = 0.0
        try:
            from sportsassets import venue_cooldown_store as VCS
            VCS.reset_pending()
        except Exception:                                      # noqa: BLE001
            pass
    _clear()
    yield
    _clear()


def _rate_limit_error():
    from polymarket_us.errors import RateLimitError
    return RateLimitError


# ════════════════════════════════════════════════════════════════════
# 1 · NINE CONSECUTIVE 429s: STRICTLY INCREASING, CAPPED, NOTHING SENT
# ════════════════════════════════════════════════════════════════════

def test_nine_consecutive_429s_escalate_strictly_to_the_cap_and_send_nothing_inside_any_cooldown(clock):
    venue = Venue()                       # every request answered 429
    client = _client(venue)
    waits, levels = [], []
    for k in range(9):
        # the cooldown before this one has run out: ONE probe goes
        clock.t += VP.cooldown_left() + 0.001
        before = venue.count
        with pytest.raises(_rate_limit_error()):
            client.markets.book(SLUG)
        assert venue.count == before + 1, "exactly one request per cooldown"
        st = VP.rate_limit_state()
        assert st["consecutive_429"] == k + 1 and st["cooldown_active"] is True
        waits.append(st["cooldown_s"])
        levels.append(st["level_s"])
        # INSIDE the cooldown -- its first instant, its middle, its last --
        # a normal-lane read is refused by name and NOTHING reaches the venue
        left = VP.cooldown_left()
        t0 = clock.t
        for frac in (0.0, 0.5, 0.999):
            clock.t = t0 + left * frac
            with pytest.raises(GRT.VenueGateRefusal) as e:
                client.markets.book(SLUG)
            assert e.value.refusal == R
            assert e.value.detail["stage"] == "BEFORE_THE_PACER"
        assert venue.count == before + 1, (
            "a request reached the venue inside cooldown %d" % (k + 1))
    cap = VP.COOLDOWN_CAP_S
    assert waits[0] == pytest.approx(VP.COOLDOWN_FLOOR_S * 1.1), waits
    assert levels[:6] == [5.0, 10.0, 20.0, 40.0, 80.0, 120.0], levels
    for a, b in zip(waits, waits[1:]):
        assert b > a or a == b == cap, ("not strictly increasing below the cap", waits)
        assert b <= cap
    assert waits[-1] == cap and max(waits) == cap, waits
    assert venue.count == 9
    st = VP.rate_limit_state()
    assert st["deferred_total"] == 27 and st["escalations"] == 9


def test_the_production_run_of_429s_replayed_reaches_the_venue_twice_not_ten_times(clock):
    """sportsassets-workers 04:20:08.9-04:20:16.9Z, replayed at its own
    offsets: ten gateway reads on nine markets, every one answered 429. At
    0e331f05 every one of them reached the venue (nothing armed a hold on a
    plain SDK read, and the 0.7 s circuit is a gap, not a prohibition). Now
    the first 429 arms the floor; the reads inside it are refused by name
    and never sent; the one read after it is the probe, its 429 doubles the
    level, and the rest are refused again."""
    venue = Venue()                       # every request answered 429
    client = _client(venue)
    offsets = [0.0, 0.8, 1.4, 3.1, 3.8, 4.6, 5.2, 6.6, 7.3, 8.0]
    t0 = clock.t
    refused = 0
    for k, off in enumerate(offsets):
        clock.t = t0 + off
        try:
            client.markets.book("slug-%d" % k)
        except GRT.VenueGateRefusal as e:
            assert e.refusal == R
            refused += 1
        except Exception:                                      # noqa: BLE001
            pass                          # the venue's 429, raised by name
    assert venue.count == 2, "%d requests reached the venue" % venue.count
    assert refused == 8
    st = VP.rate_limit_state()
    assert st["consecutive_429"] == 2 and st["level_s"] == 10.0


def test_jitter_never_breaks_the_order_and_never_passes_the_cap():
    for j in (0.0, 0.25, 0.5, 0.999999):
        level, seq = 0.0, []
        for _ in range(9):
            e = VP.escalate(level, None, j)
            level = e["level_s"]
            seq.append(e["cooldown_s"])
        assert all(b > a or a == b == VP.COOLDOWN_CAP_S for a, b in zip(seq, seq[1:])), (j, seq)
        assert seq[0] >= VP.COOLDOWN_FLOOR_S and max(seq) == VP.COOLDOWN_CAP_S


# ════════════════════════════════════════════════════════════════════
# 2 · RESET ONLY BY A SUCCESSFUL READ SENT AFTER THE COOLDOWN WAS ARMED
# ════════════════════════════════════════════════════════════════════

def test_a_successful_read_resets_the_escalation_to_the_floor(clock):
    venue = Venue()
    client = _client(venue)
    for _ in range(3):
        clock.t += VP.cooldown_left() + 0.001
        with pytest.raises(_rate_limit_error()):
            client.markets.book(SLUG)
    assert VP.rate_limit_state()["level_s"] == 20.0
    clock.t += VP.cooldown_left() + 0.001
    venue.script = [("ok", 200, GOOD_BOOK)]
    assert client.markets.book(SLUG)["marketData"]["state"] == "MARKET_STATE_OPEN"
    st = VP.rate_limit_state()
    assert st["consecutive_429"] == 0 and st["level_s"] == 0.0 and st["resets"] == 1
    assert st["cooldown_active"] is False
    # the next 429 starts again at the floor
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)
    assert VP.rate_limit_state()["cooldown_s"] == pytest.approx(5.5)


def test_a_2xx_or_a_429_that_left_before_the_cooldown_was_armed_changes_nothing(clock):
    VP.note_rate_limited(dispatched_mono=clock.t - 0.1)       # armed at t
    armed_until = VP.cooldown_left()
    # a 2xx in flight before the 429 is not evidence the limit cleared
    assert VP.note_read_ok(dispatched_mono=clock.t - 0.05) is False
    assert VP.cooldown_left() == armed_until
    # a 429 in flight before it is the same episode: counted, not escalated
    out = VP.note_rate_limited(dispatched_mono=clock.t - 0.02)
    st = VP.rate_limit_state()
    assert out["escalated"] is False and st["level_s"] == 5.0
    assert st["consecutive_429"] == 2 and st["in_flight_429s"] == 1
    # a 2xx sent after it was armed resets it
    clock.t += 0.5
    assert VP.note_read_ok(dispatched_mono=clock.t) is True
    assert VP.cooldown_left() == 0.0


def test_the_venues_longer_retry_after_is_honoured_and_a_shorter_one_is_not_a_floor(clock):
    venue = Venue(("ok", 429, {"e": 1}, {"retry-after": "30"}))
    client = _client(venue)
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)
    st = VP.rate_limit_state()
    assert st["cooldown_s"] == 30.0 and st["cooldown_is"] == "RETRY_AFTER"
    assert st["retry_after_s"] == 30.0
    VP.reset_rate_limit_state()
    venue.default = ("ok", 429, {"e": 1}, {"retry-after": "0"})
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)
    st = VP.rate_limit_state()
    assert st["cooldown_s"] == pytest.approx(5.5) and st["cooldown_is"] == "ESCALATION"


# ════════════════════════════════════════════════════════════════════
# 3 · THE PRODUCTION DEFECT: A READ QUEUED IN THE PACER WHEN THE 429 LANDS
# ════════════════════════════════════════════════════════════════════

def test_a_read_queued_in_the_pacer_when_a_429_arrives_is_refused_not_sent(clock):
    """The run of 429s at 0.7 s: each walker thread had passed the gate
    before the pacer's queue and dispatched after it regardless. The 429
    arrives (another thread's) while this one waits for its gap."""
    venue = Venue(("ok", 200, GOOD_BOOK))

    def pace_while_another_read_is_429d():
        VP.note_rate_limited(path="/v1/markets/other/book")

    client = _client(venue, pace=pace_while_another_read_is_429d)
    with pytest.raises(GRT.VenueGateRefusal) as e:
        client.markets.book(SLUG)
    assert e.value.refusal == R
    assert e.value.detail["stage"] == "AFTER_THE_PACER_QUEUE"
    assert venue.count == 0, "nothing was sent into the limit"


# ════════════════════════════════════════════════════════════════════
# 4 · PRIORITY CLAIMS: A BOUNDED WAIT, THEN SENT -- NEVER REFUSED
# ════════════════════════════════════════════════════════════════════

def test_priority_claims_wait_at_most_the_bound_and_are_always_sent(clock):
    venue = Venue()                       # 429 to everything
    client = _client(venue)
    # escalate to the cap
    for _ in range(7):
        clock.t += VP.cooldown_left() + 0.001
        with pytest.raises(_rate_limit_error()):
            client.markets.book(SLUG)
    assert VP.rate_limit_state()["cooldown_s"] == VP.COOLDOWN_CAP_S
    sent0 = venue.count
    # three risk-reducing claims in a row, each inside a fresh capped cooldown
    for k in range(3):
        clock.sleeps.clear()
        assert VP.cooldown_left() > VP.PRIORITY_COOLDOWN_MAX_WAIT_S
        with VP.priority_claims():
            with pytest.raises(_rate_limit_error()):
                client.markets.book(SLUG)
        assert venue.count == sent0 + k + 1, "the priority claim was sent"
        assert sum(clock.sleeps) == pytest.approx(VP.PRIORITY_COOLDOWN_MAX_WAIT_S)
        assert max(clock.sleeps) <= VP.PRIORITY_COOLDOWN_MAX_WAIT_S
    st = VP.rate_limit_state()
    assert st["priority_waits"] == 3
    assert st["priority_waited_s"] == pytest.approx(3 * VP.PRIORITY_COOLDOWN_MAX_WAIT_S)
    # and a normal read in the same instant is still refused
    with pytest.raises(GRT.VenueGateRefusal):
        client.markets.book(SLUG)


def test_a_request_that_is_not_a_read_is_never_refused_by_the_cooldown_whatever_its_lane(clock):
    """A cancel or a protective close made outside priority_claims() is
    still never withheld by a MEASUREMENT cooldown: it waits the bound and
    goes (nothing here is a real order -- a mock socket answers)."""
    venue = Venue(("ok", 200, {"ok": True}))
    for _ in range(7):                                  # escalate to the cap
        clock.t += VP.cooldown_left() + 0.001
        VP.note_rate_limited(dispatched_mono=clock.t)
    assert VP.cooldown_left() > 100
    http = httpx.Client(transport=GRT.PacedTransport(
        httpx.MockTransport(venue.handler), pace=None))
    r = http.post("https://gateway.test/v1/order/cancel", json={})
    assert r.status_code == 200 and venue.count == 1
    assert sum(clock.sleeps) == pytest.approx(VP.PRIORITY_COOLDOWN_MAX_WAIT_S)
    # a GET on the same normal lane, the same instant: refused by name
    with pytest.raises(GRT.VenueGateRefusal) as e:
        http.get("https://gateway.test/v1/markets/x/book")
    assert e.value.refusal == R and venue.count == 1


def test_a_priority_claim_at_the_floor_waits_the_cooldown_out_and_a_success_lifts_it(clock):
    venue = Venue()
    client = _client(venue)
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)                   # 5.5 s
    venue.script = [("ok", 200, GOOD_BOOK)]
    with VP.priority_claims():
        client.markets.book(SLUG)
    assert sum(clock.sleeps) == pytest.approx(5.0)  # the bound, of a 5.5 s cooldown
    assert VP.rate_limit_state()["cooldown_active"] is False, "the 2xx sent after it lifted it"


# ════════════════════════════════════════════════════════════════════
# 5 · A DEADLINED READ WAITS INSIDE ITS DEADLINE; OTHERWISE REFUSED
# ════════════════════════════════════════════════════════════════════

def test_a_deadlined_normal_read_waits_inside_its_deadline_else_is_refused(clock):
    venue = Venue()
    client = _client(venue)
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)                   # 5.5 s
    venue.script = [("ok", 200, GOOD_BOOK)]
    rid = GRT.begin_read(slug=SLUG, deadline_epoch_s=time.time() + 60)
    GRT.bind_read(rid)
    try:
        client.markets.book(SLUG)
    finally:
        GRT.bind_read(None)
        GRT.end_read(rid)
    assert clock.sleeps == [pytest.approx(5.5)] and venue.count == 2
    # a deadline the cooldown outlasts: refused, nothing sent
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)                   # back at the floor
    rid = GRT.begin_read(slug=SLUG, deadline_epoch_s=time.time() + 2)
    GRT.bind_read(rid)
    try:
        with pytest.raises(GRT.VenueGateRefusal) as e:
            client.markets.book(SLUG)
    finally:
        GRT.bind_read(None)
        GRT.end_read(rid)
    assert e.value.refusal == GRT.R_COOLDOWN_EXCEEDS_DEADLINE and venue.count == 3


# ════════════════════════════════════════════════════════════════════
# 5b · THE BOUNDED WAIT (review round 2): an undeadlined read that OPTED IN
#      (venue_pace.wait_out_the_cooldown -- the catalogue sweep) waits a
#      cooldown that ends within MAX_UNDEADLINED_WAIT_S, one budget with
#      the hard hold, and NOTHING reaches the venue inside it; a cooldown
#      past what is left is refused at once by the cap's name. Outside the
#      opt-in a walker is refused at once, as before.
# ════════════════════════════════════════════════════════════════════

def _client_timed(venue: Venue, clock, at: list, pace=None):
    """_client, with the fake-clock instant of every request that reached
    the socket recorded in `at`."""
    inner = venue.handler

    def handler(request):
        at.append(clock.t)
        return inner(request)
    venue.handler = handler
    return _client(venue, pace=pace)


def test_an_opted_in_read_waits_a_floor_cooldown_out_and_nothing_reaches_the_venue_inside_it(clock):
    venue = Venue()
    at: list = []
    client = _client_timed(venue, clock, at)
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)                   # the floor: 5.5 s
    until = clock.t + VP.cooldown_left()
    venue.script = [("ok", 200, GOOD_BOOK)]
    # a walker, the same instant: refused at once, nothing sent, no sleep
    with pytest.raises(GRT.VenueGateRefusal) as e:
        client.markets.book(SLUG)
    assert e.value.refusal == R and venue.count == 1 and clock.sleeps == []
    # the catalogue sweep's read: waits the cooldown out, THEN sends
    with VP.read_source("premap_full"), VP.wait_out_the_cooldown():
        client.markets.book(SLUG)
    assert clock.sleeps == [pytest.approx(5.5)]
    assert venue.count == 2 and at[1] >= until - 1e-9, "sent inside the cooldown"
    st = VP.rate_limit_state()
    assert st["bounded_waits"] == 1 and st["bounded_waited_s"] == pytest.approx(5.5)
    assert st["deadline_waits"] == 0 and st["priority_waits"] == 0
    assert st["cooldown_active"] is False, "the 2xx sent after it lifted it"
    src = st["by_source"]["premap_full"]
    assert src["cooldown_waits"] == 1 and src["cooldown_waited_s"] == pytest.approx(5.5)
    assert src["deferred"] == 0
    assert st["rule"]["normal_lane_opted_in"] == \
        "WAIT_WITHIN_THE_UNDEADLINED_CAP_ELSE_REFUSED"


def test_an_opted_in_read_queued_in_the_pacer_when_a_429_arrives_waits_then_claims_a_fresh_gap(clock):
    """The production defect's shape (a read already queued for its gap when
    another thread's 429 arrives), for an opted-in read: it is not sent on
    its stale gap -- it waits the cooldown out and claims a fresh one."""
    venue = Venue(("ok", 200, GOOD_BOOK))
    gaps: list = []

    def pace():
        gaps.append(clock.t)
        if len(gaps) == 1:
            VP.note_rate_limited(path="/v1/markets/other/book")
    at: list = []
    client = _client_timed(venue, clock, at, pace=pace)
    with VP.wait_out_the_cooldown():
        client.markets.book(SLUG)
    assert venue.count == 1 and clock.sleeps == [pytest.approx(5.5)]
    assert len(gaps) == 2, "a fresh gap after the wait, never the stale one"
    assert at[0] >= gaps[0] + 5.5 - 1e-9


def test_opted_in_reads_escalate_through_bounded_waits_and_a_cooldown_past_the_cap_is_refused_at_once(clock):
    venue = Venue()                       # 429 to everything
    at: list = []
    client = _client_timed(venue, clock, at)
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)                   # rung 1: 5.5 s
    waits = []
    with VP.wait_out_the_cooldown():
        for _ in range(2):                          # rungs 1 and 2 are waited
            clock.sleeps.clear()
            until = clock.t + VP.cooldown_left()
            with pytest.raises(_rate_limit_error()):
                client.markets.book(SLUG)
            waits.append(sum(clock.sleeps))
            assert at[-1] >= until - 1e-9, "a request went inside the cooldown"
        # rung 3: level 20 s, 22 s with the jitter -- past the 20 s cap
        assert VP.cooldown_left() > GRT.MAX_UNDEADLINED_WAIT_S
        clock.sleeps.clear()
        sent = venue.count
        with pytest.raises(GRT.VenueGateRefusal) as e:
            client.markets.book(SLUG)
    assert waits == [pytest.approx(5.5), pytest.approx(11.0)]
    assert e.value.refusal == GRT.R_HOLD_EXCEEDS_UNDEADLINED_CAP
    assert e.value.detail["by"] == "ESCALATING_429_COOLDOWN"
    assert e.value.detail["stage"] == "BEFORE_THE_PACER"
    assert e.value.detail["cap_left_s"] == GRT.MAX_UNDEADLINED_WAIT_S
    assert clock.sleeps == [], "refused at once, never after sleeping part of it"
    assert venue.count == sent == 3
    st = VP.rate_limit_state()
    assert st["deferred_total"] == 1 and st["consecutive_429"] == 3


def test_an_opted_in_read_waits_the_hold_and_the_cooldown_on_one_budget_never_past_the_cap(clock, monkeypatch):
    """MAX_UNDEADLINED_WAIT_S is the longest the gate sleeps an undeadlined
    caller IN ALL: a read that waited 11 s of the cooldown has 9 s left for
    the hard hold, and a 12 s hold is then refused by name, nothing sent.
    The same 12 s hold with no cooldown is waited, as it always was."""
    holds: list = []
    monkeypatch.setattr(GRT, "_hold_sleep",
                        lambda s: holds.append(round(float(s), 1)))
    venue = Venue(("ok", 200, GOOD_BOOK))
    client = _client(venue)
    VP.note_rate_limited(path="/v1/markets/x/book")
    VP.note_rate_limited(path="/v1/markets/x/book")     # rung 2: 11 s
    assert VP.cooldown_left() == pytest.approx(11.0)
    GRT.hold_until(until_epoch_s=time.time() + 12.0, reason="TEST_HOLD")
    with VP.wait_out_the_cooldown():
        with pytest.raises(GRT.VenueGateRefusal) as e:
            client.markets.book(SLUG)
    assert clock.sleeps == [pytest.approx(11.0)] and holds == []
    assert e.value.refusal == GRT.R_HOLD_EXCEEDS_UNDEADLINED_CAP
    assert e.value.detail["cap_left_s"] == pytest.approx(9.0)
    assert venue.count == 0
    # no cooldown, the same hold: waited within the cap and sent (unchanged)
    VP.reset_rate_limit_state()
    with VP.wait_out_the_cooldown():
        client.markets.book(SLUG)
    assert len(holds) == 1 and 10.0 <= holds[0] <= 12.0 and venue.count == 1


def test_a_deadlined_read_inside_the_opt_in_keeps_its_deadline_rule(clock):
    """The opt-in never widens a deadline: a deadline the cooldown outlasts
    is still refused R_COOLDOWN_EXCEEDS_DEADLINE, nothing slept or sent."""
    venue = Venue()
    client = _client(venue)
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)                   # 5.5 s
    rid = GRT.begin_read(slug=SLUG, deadline_epoch_s=time.time() + 2)
    GRT.bind_read(rid)
    try:
        with VP.wait_out_the_cooldown():
            with pytest.raises(GRT.VenueGateRefusal) as e:
                client.markets.book(SLUG)
    finally:
        GRT.bind_read(None)
        GRT.end_read(rid)
    assert e.value.refusal == GRT.R_COOLDOWN_EXCEEDS_DEADLINE
    assert venue.count == 1 and clock.sleeps == []


# ════════════════════════════════════════════════════════════════════
# 6 · bbo_read / _bbo_quotes / book_read: ONE REQUEST ON A 429
# ════════════════════════════════════════════════════════════════════

def test_bbo_read_makes_one_request_on_a_429_and_none_inside_the_cooldown(clock):
    venue = Venue()
    client = _client(venue)
    out = pmus.bbo_read(client, SLUG)
    assert venue.paths == ["/v1/markets/%s/bbo" % SLUG], (
        "the book feed was asked straight into the limit: %s" % venue.paths)
    assert out == {"bid": None, "ask": None, "state": None, "error": "RateLimitError"}
    # inside the cooldown: nothing at all, and the refusal is OURS by name
    out2 = pmus.bbo_read(client, SLUG)
    assert venue.count == 1 and out2["error"] == R
    # _bbo_quotes, the 2-tuple slug_bid reads: one request on a 429 too
    VP.reset_rate_limit_state()
    assert pmus._bbo_quotes(client, SLUG) == (None, None)
    assert venue.count == 2 and venue.paths[-1].endswith("/bbo")
    assert R == VP.R_VENUE_429_COOLDOWN_READ_DEFERRED


def test_a_runtimeerror_naming_429_in_its_text_is_not_a_rate_limit():
    assert pmus._limit_ends_the_read(RuntimeError("429")) is False

    class _S(Exception):
        status_code = 429
    assert pmus._limit_ends_the_read(_S()) is True
    assert pmus._limit_ends_the_read(GRT.VenueGateRefusal(R, {})) is True


def test_book_read_does_not_sleep_a_walker_through_the_hold_to_retry_into_the_limit(clock, monkeypatch):
    slept: list = []
    monkeypatch.setattr(GRT.time, "sleep", lambda s: slept.append(s))
    venue = Venue()
    client = _client(venue)
    out = pmus.book_read(client, SLUG)
    assert venue.count == 1, "one request: the retry is the cooldown's to decide"
    assert out["error"] == "RateLimitError"
    assert out["why_no_further_attempt"] == R
    assert out["retry_deferred"]["refusal"] == R
    assert slept == [], "the walker's thread was parked for the hold"


# ════════════════════════════════════════════════════════════════════
# 7 · THE READBACK: PER SOURCE, ON THE WORKER'S BEAT
# ════════════════════════════════════════════════════════════════════

def test_the_readback_names_the_cooldown_the_count_the_deferred_reads_and_the_last_429_per_source(clock):
    venue = Venue()
    client = _client(venue)
    with VP.read_source("bettor_state"):
        with pytest.raises(_rate_limit_error()):
            client.markets.book(SLUG)
        with pytest.raises(GRT.VenueGateRefusal):
            client.markets.book(SLUG)
        VP.note_walker_skipped(4)
    st = VP.rate_limit_state()
    import json
    json.dumps(st)                                   # strict: no default=
    assert st["cooldown_active"] is True and st["consecutive_429"] == 1
    assert st["deferred_total"] == 1 and st["last_429"]["path"].endswith("/book")
    src = st["by_source"]["bettor_state"]
    # (review round 2) the source's cooldown WAITS ride beside its
    # deferrals: a walker that is refused waits nothing
    assert src == {"rate_limited": 1, "deferred": 1, "skipped_by_walker": 4,
                   "last_429": src["last_429"],
                   "last_deferred_epoch": src["last_deferred_epoch"],
                   "cooldown_waits": 0, "cooldown_waited_s": 0.0}
    assert src["last_429"]["source"] == "bettor_state"
    assert st["rule"]["priority_lane"] == "BOUNDED_WAIT_THEN_PROCEED"
    # the walker's pre-read gate reads it too (institutional_md's probe)
    from sportsassets.workers import institutional_md as IMD
    g = IMD._default_gate()
    assert g["blocking"] is True and g["by"] == "ESCALATING_429_COOLDOWN"
    assert GRT.gate_state()["blocking"] is False, "the hard-hold readout is unchanged"


# ════════════════════════════════════════════════════════════════════
# 8 · THE WALKERS STOP THEIR PASS AND COUNT WHAT THEY SKIPPED
# ════════════════════════════════════════════════════════════════════

def _arm():
    VP.note_rate_limited(path="/v1/markets/x/book")


def test_shadow_rn1_stops_its_pass_and_leaves_the_rest_pending(clock, monkeypatch):
    from sportsassets.workers import shadow_rn1 as W
    pending = [{"marketId": "c%d" % i, "outcomeLeg": "yes"} for i in range(5)]
    reads, decided = [], []

    async def _pending(pool, within_s, limit):
        return pending

    async def _slug(pool, market_id):
        return "slug-" + market_id, None

    def _read(slug):
        reads.append(slug)
        _arm()                                         # the venue says 429
        return {"bid": None, "ask": None, "state": None, "error": "RateLimitError"}

    async def _decide(observation, state, pool=None):
        decided.append(observation["marketId"])
        return "d", True

    monkeypatch.setattr(W.shadow_rn1, "pending_observations", _pending)
    monkeypatch.setattr(W, "_us_slug", _slug)
    monkeypatch.setattr(W, "_read_quote", _read)
    monkeypatch.setattr(W.shadow_rn1, "write_decision", _decide)
    stats = asyncio.run(W.tick(object()))
    assert reads == ["slug-c0"], "one read, then the cooldown stopped the pass"
    assert decided == ["c0"]
    assert stats["skippedCooldown"] == 4 and stats["status"] == "venue_429_cooldown"
    assert VP.rate_limit_state()["by_source"]["unattributed"]["skipped_by_walker"] == 4


def test_shadow_bettor_never_falls_back_to_bbo_after_a_429_and_stops_its_pass(clock, monkeypatch):
    from sportsassets.workers import shadow_bettor as W
    bbo_calls: list = []
    monkeypatch.setattr(W, "pace", lambda *a, **k: 0.0)
    monkeypatch.setattr(W.pmus, "_get_client", lambda: object())
    monkeypatch.setattr(W.pmus, "book_read", lambda c, s: {
        "marketData": None, "feed": None, "error": "RateLimitError",
        "error_detail": {"http_status": 429, "is_rate_limited": True}})
    monkeypatch.setattr(W.pmus, "bbo_read", lambda c, s: bbo_calls.append(s) or {
        "bid": None, "ask": None, "state": None, "error": "RateLimitError"})
    q = W._read_quote(SLUG)
    assert bbo_calls == [], "two more requests into the limit (bbo, then book)"
    assert q["error"] == "RateLimitError" and q["bid"] is None
    # an empty, unlimited book still falls back exactly as before
    monkeypatch.setattr(W.pmus, "book_read", lambda c, s: {
        "marketData": None, "feed": "book", "error": "NO_MARKET_DATA_IN_PAYLOAD"})
    W._read_quote(SLUG)
    assert bbo_calls == [SLUG]

    # the tick: the cooldown in force before the first subject stops the pass
    async def _orphans(pool):
        return 0

    async def _universe(pool, limit):
        return [{"symbol": "s%d" % i} for i in range(6)]
    monkeypatch.setattr(W.ops, "annotate_orphans", _orphans)
    monkeypatch.setattr(W.bettor, "universe", _universe)
    looked: list = []
    monkeypatch.setattr(W, "_read_quote", lambda s: looked.append(s) or {})
    _arm()
    stats = asyncio.run(W.tick(object()))
    assert looked == [] and stats["looked"] == 0
    assert stats["skippedCooldown"] == 6 and stats["status"] == "venue_429_cooldown"


def test_shadow_experimental_stops_its_focus_pass(clock, monkeypatch):
    from sportsassets.workers import shadow_experimental as W

    async def _focus(pool, size, hold_s):
        return [{"symbol": "s%d" % i} for i in range(4)]
    monkeypatch.setattr(W.xstore, "focus_set", _focus)
    read: list = []
    monkeypatch.setattr(W, "_read_quote", lambda s: read.append(s) or {})
    _arm()
    stats = asyncio.run(W.sample_focus(object()))
    assert read == [] and stats["skippedCooldown"] == 4
    assert stats["status"] == "venue_429_cooldown"


def test_mirror_shadow_skips_the_tick_before_any_venue_read(clock, monkeypatch):
    from sportsassets.workers import mirror_shadow as W
    monkeypatch.setattr(W, "_backoff_until", 0.0)
    _arm()
    stats = asyncio.run(W.tick_once(None, None, now_ts=5000.0, allow_short=False))
    assert stats["skipped_cooldown"] is True and stats["status"] == "venue_429_cooldown"
    assert stats["markets"] == 0 and stats["rows"] == 0
    assert stats["cooldown"]["refusal"] == R


def test_bettor_state_stops_its_pass_without_spending_budget(clock, monkeypatch):
    from sportsassets.workers import bettor_state as W
    subjects = [{"symbol": "m%d" % i, "marketId": "m%d" % i,
                 "outcomeLeg": "yes"} for i in range(5)]

    async def _cands(pool):
        return subjects

    def _select(cands, at=None, tick=None, **kw):
        # the whole selection record, the sampling rule's window fields
        # included (an unbound cap: every subject in one window)
        return {"CANDIDATES_ELIGIBLE": 5, "CANDIDATES_IN_SLICE": 5,
                "BUCKET_SHARE": 5, "SLICE_TRUNCATED": False,
                "SLICE_TRUNCATED_BY": 0, "SELECTED": list(subjects),
                "CYCLE": 1, "CAP_BINDING": False, "SLICE_VISIT": 0,
                "VISITS_TO_COVER_SLICE": 1, "WINDOW_START": 0}

    async def _zero(*a, **k):
        return 0

    async def _none(*a, **k):
        return []

    async def _rec_state(row, pool=None):
        return "o", True

    async def _rec_tick(rec, pool=None):
        ticks.append(rec)

    ticks: list = []
    reads: list = []

    def _read(slug, pacing=None):
        reads.append(slug)
        _arm()                                          # the venue says 429
        return {"marketData": None, "feed": None, "error": "RateLimitError"}

    monkeypatch.setattr(W, "_candidates", _cands)
    monkeypatch.setattr(W.sc, "select", _select)
    monkeypatch.setattr(W.sc, "state_record", lambda subject, **kw: {
        "BOOK_READABILITY_STATUS": "UNREADABLE" if kw.get("read_error") else "READABLE"})
    monkeypatch.setattr(W.sstore, "mids_outstanding", _zero)
    monkeypatch.setattr(W.sstore, "mids_due", _none)
    monkeypatch.setattr(W.sstore, "history", _none)
    monkeypatch.setattr(W.sstore, "record_state", _rec_state)
    monkeypatch.setattr(W.sstore, "record_tick", _rec_tick)
    monkeypatch.setattr(W, "_read_book", _read)
    stats = asyncio.run(W.tick(object()))
    assert reads == ["m0"], "one read, then the cooldown stopped the pass"
    assert stats["rateLimited"] == 1 and stats["obsSkippedCooldown"] == 4
    assert stats["status"] == "venue_429_cooldown"
    assert ticks and ticks[0]["OBS_NEVER_ATTEMPTED"] == 4


# ════════════════════════════════════════════════════════════════════
# 9 · THE API PROCESS'S OWN READBACK: ITS TRANSPORT TOTALS CARRY IT
# ════════════════════════════════════════════════════════════════════

def test_the_api_processs_heartbeat_digest_carries_its_own_cooldown(clock):
    """The API process has its own pacer and its own cooldown (venue_pace is
    per process); its one readback of its own transport is the ext_pinnacle
    heartbeat's venue_rate_controls, whose process_request_totals are
    venue_request_gate.totals(). The cooldown rides there."""
    import json

    venue = Venue()
    client = _client(venue)
    with pytest.raises(_rate_limit_error()):
        client.markets.book(SLUG)
    tot = GRT.totals()
    assert "escalating_429_cooldown" in tot, sorted(tot)
    cd = tot["escalating_429_cooldown"]
    assert cd["cooldown_active"] is True and cd["consecutive_429"] == 1
    assert cd["scope"] == "THIS_PROCESS"
    assert cd["by_source"]["unattributed"]["rate_limited"] == 1
    assert tot["scope"] == "PROCESS_SINCE_IMPORT" and tot["rate_limited"] >= 1
    json.dumps(tot)                                  # strict: no default=
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    digest = LOOP._rate_control_digest()
    got = digest["process_request_totals"]["escalating_429_cooldown"]
    assert got["cooldown_active"] is True and got["last_429"]["path"].endswith("/book")


# ════════════════════════════════════════════════════════════════════
# 10 · THE DATA-API HOST: NEVER A ZERO-SECOND RETRY, THE SAME ESCALATION
# ════════════════════════════════════════════════════════════════════

class AClock:
    """ratelimit's fake clock (as tests/test_e10_priority_lane.py's)."""

    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    async def sleep(self, d):
        await asyncio.sleep(0)
        self.t += max(0.0, float(d))


@pytest.fixture
def aclock(monkeypatch):
    c = AClock()
    monkeypatch.setattr(ratelimit, "_clock", c.now)
    monkeypatch.setattr(ratelimit, "_sleep", c.sleep)
    monkeypatch.setattr(VP, "_rand", lambda: 0.5, raising=False)
    monkeypatch.setattr(ratelimit, "_throttle", ratelimit.Throttle(6.0))
    return c


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class _Http:
    def __init__(self, clock, statuses, headers=None):
        self.clock = clock
        self.statuses = list(statuses)
        self.headers = headers or {}
        self.at: list = []
        self.cooldowns: list = []

    async def get(self, url, params=None):
        self.at.append(self.clock.t)
        state = getattr(ratelimit.data_api_throttle(), "cooldown_state", None)
        self.cooldowns.append(state()["cooldown_s"] if state else None)
        st = self.statuses.pop(0) if self.statuses else 429
        return _Resp(st, self.headers if st == 429 else {})


def test_polite_get_never_retries_a_429_at_zero_seconds(aclock):
    http = _Http(aclock, [429, 200], headers={"Retry-After": "0"})
    resp = asyncio.run(ratelimit.polite_get(http, "/trades", params={"user": "0xabc"}))
    assert resp.status_code == 200
    gap = http.at[1] - http.at[0]
    assert gap >= 5.0, "retried %.3f s after a 429" % gap
    assert VP.COOLDOWN_FLOOR_S == 5.0
    st = ratelimit.data_api_throttle().cooldown_state()
    assert st["consecutive_429"] == 0 and st["resets"] == 1 and st["cooldown_active"] is False
    assert st["last_429"]["path"] == "/trades", "the path alone -- never the wallet in the query"


def test_nine_consecutive_data_api_429s_escalate_strictly_to_the_cap_with_no_request_inside(aclock):
    http = _Http(aclock, [429] * 9)
    resp = asyncio.run(ratelimit.polite_get(http, "/trades", retries=8))
    assert resp.status_code == 429 and len(http.at) == 9
    thr = ratelimit.data_api_throttle()
    # the cooldown each request was preceded by: strictly increasing to the cap
    waits = http.cooldowns[1:] + [thr.cooldown_state()["cooldown_s"]]
    for a, b in zip(waits, waits[1:]):
        assert b > a or a == b == VP.COOLDOWN_CAP_S, waits
    assert waits[0] == pytest.approx(5.5) and waits[-1] == VP.COOLDOWN_CAP_S
    # and no request went inside one: each gap is at least its cooldown
    gaps = [b - a for a, b in zip(http.at, http.at[1:])]
    assert all(g >= w - 1e-6 for g, w in zip(gaps, waits)), (gaps, waits)
    assert thr.cooldown_state()["consecutive_429"] == 9


def test_inside_a_data_api_cooldown_normal_callers_are_held_and_priority_is_bounded(aclock):
    async def go():
        thr = ratelimit.data_api_throttle()
        out = thr.note_429(retry_after_s=None, path="/trades")
        armed = aclock.t
        until = armed + out["seconds_left"]
        served: list = []

        async def one(name, prio):
            await thr.acquire(priority=prio)
            served.append((name, aclock.t))
        tasks = [asyncio.create_task(one("n1", False)),
                 asyncio.create_task(one("p1", True)),
                 asyncio.create_task(one("n2", False))]
        await asyncio.gather(*tasks)
        return served, armed, until, thr
    served, armed, until, thr = asyncio.run(go())
    by = dict(served)
    assert by["p1"] <= armed + VP.PRIORITY_COOLDOWN_MAX_WAIT_S + 1e-6, "priority starved"
    assert by["p1"] >= armed + VP.PRIORITY_COOLDOWN_MAX_WAIT_S - 1e-6
    assert by["n1"] >= until and by["n2"] >= until, "a normal slot inside the cooldown"
    assert thr.cooldown_state()["priority_served_in_cooldown"] == 1


# ════════════════════════════════════════════════════════════════════
# 11 · THE EXIT WORKER'S DIRECT DATA-API READS FEED THE HOST'S COOLDOWN
# ════════════════════════════════════════════════════════════════════

class _PosResp:
    def __init__(self, status, rows=None, headers=None):
        self.status_code = status
        self._rows = rows or []
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._rows


class _PosHttp:
    def __init__(self, clock, answers):
        self.clock = clock
        self.answers = list(answers)
        self.at: list = []
        self.params: list = []

    async def get(self, path, params=None):
        self.at.append(self.clock.t)
        self.params.append(dict(params or {}))
        return self.answers.pop(0) if self.answers else _PosResp(429)


def test_the_exit_workers_positions_walk_arms_the_host_cooldown_and_never_retries_into_it(aclock, monkeypatch):
    """whale_exits' walk takes its slot and GETs directly (no polite_get).
    Its 429 was invisible to the host's cooldown and its retry went 2 s
    later; now the 429 arms the cooldown every data-api caller waits on, the
    retry waits it out, and the 2xx that answers lifts it."""
    from sportsassets.workers import whale_exits as WE
    orig = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda d: orig(0))   # the walk's own 2 s
    rows = [{"asset": "a%d" % i, "size": 1} for i in range(3)]
    http = _PosHttp(aclock, [_PosResp(429, headers={"Retry-After": "0"}),
                             _PosResp(200, rows)])
    sizes, sibs, seen = asyncio.run(WE._fetch_positions(http, "0xw"))
    assert seen == 3 and len(http.at) == 2
    gap = http.at[1] - http.at[0]
    assert gap >= 5.0, "the retry went %.3f s after a 429" % gap
    assert VP.COOLDOWN_FLOOR_S == 5.0
    st = ratelimit.data_api_throttle().cooldown_state()
    assert st["rate_limited_total"] == 1 and st["resets"] == 1
    assert st["cooldown_active"] is False and st["consecutive_429"] == 0
    assert st["last_429"]["path"] == "/positions", "the path alone, never the wallet"


def test_the_mirrors_per_market_read_arms_the_host_cooldown_and_holds_the_other_callers(aclock):
    from sportsassets.workers import whale_exits as WE

    async def go():
        http = _PosHttp(aclock, [_PosResp(429)])
        got = await WE.market_positions(http, "0xw", "0xcond", priority=True)
        armed = aclock.t
        # the poller's next /trades, a normal caller, is held to the end
        other = _Http(aclock, [200])
        await ratelimit.polite_get(other, "/trades")
        return got, armed, other
    got, armed, other = asyncio.run(go())
    assert got is None, "a 429 is still unreadable: fail closed, as before"
    held = other.at[0] - armed
    assert held >= 5.0, "the next caller went %.3f s after the 429" % held
    st = ratelimit.data_api_throttle().cooldown_state()
    assert st["rate_limited_total"] == 1 and st["resets"] == 1
    assert held >= st["cooldown_s"] - 1e-6


def test_a_stand_in_throttle_without_the_cooldown_is_left_alone():
    class _Bare:
        async def wait(self):
            return None
    assert ratelimit.observe_data_api_response(_PosResp(429), throttle=_Bare()) is None
    assert ratelimit.observe_data_api_response(_PosResp(200), throttle=_Bare()) is None
    assert ratelimit.observe_data_api_response(object(), throttle=_Bare()) is None
