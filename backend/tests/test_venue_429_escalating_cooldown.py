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
    assert src == {"rate_limited": 1, "deferred": 1, "skipped_by_walker": 4,
                   "last_429": src["last_429"],
                   "last_deferred_epoch": src["last_deferred_epoch"]}
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
        return {"CANDIDATES_ELIGIBLE": 5, "CANDIDATES_IN_SLICE": 5,
                "BUCKET_SHARE": 5, "SLICE_TRUNCATED": False,
                "SLICE_TRUNCATED_BY": 0, "SELECTED": list(subjects),
                "CYCLE": 1}

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
